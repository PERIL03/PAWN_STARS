import base64
import bz2
import hmac
import json
import lzma
import re
import struct
import hashlib
import os
import random
import logging
import time
import zlib
from time import time as current_time
from math import log2, floor
from typing import Optional, Dict, Any

import chess
from chess import pgn, Board, Move
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 390000
ENVELOPE_MAGIC = b"RH2"
ENVELOPE_VERSION_V1 = 1
ENVELOPE_VERSION_V2 = 2
COMPRESSION_ALGORITHM_IDS = {
    "none": 0,
    "zlib": 1,
    "bz2": 2,
    "lzma": 3,
}
OPENING_BOOK = [
    ["e2e4", "e7e5", "g1f3", "b8c6"],
    ["d2d4", "d7d5", "c2c4", "e7e6"],
    ["c2c4", "e7e5", "b1c3", "g8f6"],
    ["g1f3", "d7d5", "d2d4", "g8f6"],
]
PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
CENTER_SQUARES = {"d4", "e4", "d5", "e5"}
HIDDEN_HEADER_KEYS = {
    "Seed",
    "DataBits",
    "Engine",
    "ExpiryTime",
    "OpeningBookUCI",
    "EngineGuided",
    "SeedMode",
}
HIDDEN_COMMENT_PREFIX = "RHMDv3:"
HIDDEN_METADATA_VERSION = 3
HIDDEN_TRAILER_SENTINEL = 0xA55A
HIDDEN_TRAILER_VERSION = 2
HIDDEN_TRAILER_VERSION_AUTH_COMPACT = 4
MAX_PLIES_PER_GAME = 300

try:
    import chess_engine as _rust

    RUST_ENGINE_AVAILABLE = True
    logger.info("Rust chess engine loaded — using accelerated encode path")
except ImportError:
    RUST_ENGINE_AVAILABLE = False
    logger.info("Rust chess engine not available — using pure-Python encode path")


def read_input_file(file_path: str) -> bytes:
    if not os.path.exists(file_path):
        logger.error("Input file does not exist: %s", file_path)
        raise ValueError("Input file does not exist")
    with open(file_path, "rb") as input_file:
        file_bytes = input_file.read()
    if not file_bytes:
        logger.error("Input file is empty")
        raise ValueError("Input file is empty")
    return file_bytes


def derive_root_seed(password: str) -> int:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def deterministic_game_seed(root_seed: int, game_number: int) -> int:
    digest = hashlib.sha256(f"{root_seed}:{game_number}".encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") % 1_000_000) + 1


def derive_aead_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_payload(payload: bytes, password: str) -> tuple[bytes, bytes, bytes]:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = derive_aead_key(password, salt)
    cipher = AESGCM(key)
    encrypted = cipher.encrypt(nonce, payload, None)
    return encrypted, salt, nonce


def build_payload_envelope(
    payload: bytes,
    *,
    compression_used: bool,
    compression_algorithm: str,
    compression_level: int,
    original_size: int,
    encryption_used: bool,
    salt: bytes,
    nonce: bytes,
) -> bytes:
    flags = 0
    if compression_used:
        flags |= 0x01
    if encryption_used:
        flags |= 0x02

    algorithm_id = COMPRESSION_ALGORITHM_IDS.get(compression_algorithm)
    if algorithm_id is None:
        raise ValueError(f"Unsupported compression algorithm for envelope: {compression_algorithm}")

    header = struct.pack(
        ">3sBBBBBBQI",
        ENVELOPE_MAGIC,
        ENVELOPE_VERSION_V2,
        flags,
        algorithm_id,
        compression_level,
        len(salt),
        len(nonce),
        original_size,
        PBKDF2_ITERATIONS if encryption_used else 0,
    )
    return header + salt + nonce + payload


def parse_compression_level(compression_level: Optional[int]) -> tuple[int, bool]:
    if compression_level is None:
        return 9, True
    if compression_level < 0 or compression_level > 9:
        raise ValueError("Compression level must be between 0 and 9")
    if compression_level == 0:
        return 0, False
    return compression_level, True


def prepare_payload_for_encoding(file_bytes: bytes, compression_level: Optional[int] = None) -> tuple[bytes, Dict[str, Any]]:
    level, compression_enabled = parse_compression_level(compression_level)
    compression_meta: Dict[str, Any] = {
        "compression_requested": compression_enabled,
        "compression_level": level,
        "compression_used": False,
        "compression_algorithm": "none",
    }

    if not compression_enabled:
        logger.debug("Compression disabled by user")
        return file_bytes, compression_meta

    candidates: list[tuple[str, bytes]] = [
        ("zlib", zlib.compress(file_bytes, level=level)),
        ("bz2", bz2.compress(file_bytes, compresslevel=level)),
        ("lzma", lzma.compress(file_bytes, preset=level)),
    ]

    algorithm, compressed = min(candidates, key=lambda item: len(item[1]))

    if len(compressed) + 16 < len(file_bytes):
        logger.debug(
            "Using %s compression level %d: %d -> %d bytes (%.2f%%)",
            algorithm,
            level,
            len(file_bytes),
            len(compressed),
            (len(compressed) / len(file_bytes)) * 100,
        )
        compression_meta["compression_used"] = True
        compression_meta["compression_algorithm"] = algorithm
        return compressed, compression_meta

    logger.debug("Skipping compression: no meaningful size reduction")
    return file_bytes, compression_meta


def create_game_record(
    board: Board,
    seed: int,
    data_bits_count: int,
    expiry_time: Optional[int] = None,
    custom_headers: Optional[Dict[str, str]] = None,
) -> str:
    game = pgn.Game()
    game.headers["Seed"] = str(seed)
    game.headers["DataBits"] = str(data_bits_count)
    default_headers = {
        "Event": "?",
        "Site": "?",
        "Date": "????.??.??",
        "Round": "?",
        "White": "?",
        "Black": "?",
        "Result": "*",
    }
    if custom_headers:
        for key, value in custom_headers.items():
            if value:
                default_headers[key] = value
    for key, value in default_headers.items():
        game.headers[key] = value
    if expiry_time is not None:
        game.headers["ExpiryTime"] = str(expiry_time)
    game.add_line(board.move_stack)
    return str(game)


def should_end_game(board: Board) -> bool:
    return (
        board.is_game_over()
        or board.is_insufficient_material()
        or board.can_claim_draw()
        or len(board.move_stack) >= MAX_PLIES_PER_GAME
    )


def verify_pgn_content(
    pgn_content: str,
    expiry_time: Optional[int],
    custom_headers: Optional[Dict[str, str]],
) -> None:
    if expiry_time is not None:
        if str(expiry_time) in pgn_content:
            pass
        elif "ExpiryTime" in HIDDEN_HEADER_KEYS and _has_hidden_carrier(pgn_content):
            pass
        else:
            raise ValueError("ExpiryTime header not present in encoded PGN")

    if custom_headers:
        for key, value in custom_headers.items():
            if key in HIDDEN_HEADER_KEYS:
                continue
            if value and value not in pgn_content:
                raise ValueError(f"Custom header {key} not present in encoded PGN")


def _derive_metadata_auth_key(password: str) -> bytes:
    return hashlib.sha256(f"rookhide-meta-auth:{password}".encode("utf-8")).digest()


def _metadata_auth_message(visible_pgn_text: str, hidden_list: list[dict[str, str]]) -> bytes:
    canonical_hidden = json.dumps(hidden_list, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return b"ROOKHIDE-META-V3\n" + visible_pgn_text.encode("utf-8") + b"\n" + canonical_hidden


def _encode_hidden_comment_payload(hidden_list: list[dict[str, str]], visible_pgn_text: str, password: Optional[str]) -> str:
    msg = _metadata_auth_message(visible_pgn_text, hidden_list)
    if password:
        key = _derive_metadata_auth_key(password)
        tag = hmac.new(key, msg, hashlib.sha256).digest()
        auth_alg = "hmac-sha256"
    else:
        tag = hashlib.sha256(msg).digest()
        auth_alg = "sha256"

    payload_obj = {
        "version": HIDDEN_METADATA_VERSION,
        "hidden": hidden_list,
        "auth": {
            "alg": auth_alg,
            "tag": base64.urlsafe_b64encode(tag).decode("ascii"),
        },
    }
    payload_json = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(zlib.compress(payload_json, level=9)).decode("ascii")
    return f"{{{HIDDEN_COMMENT_PREFIX}{payload_b64}}}"


def _encode_hidden_whitespace_payload(
    hidden_list: list[dict[str, str]],
    visible_pgn_text: str,
    password: Optional[str],
) -> str:
    # Compact legacy v1 payload: [version=1][engine_len][count][engine][seed/databits pairs]
    # Decoder already supports this format and it is much smaller for common cases.
    compact_ready = all(
        isinstance(item, dict)
        and set(item.keys()).issubset({"Seed", "DataBits", "Engine"})
        and isinstance(item.get("Seed", "0"), str)
        and isinstance(item.get("DataBits", "0"), str)
        for item in hidden_list
    )

    payload: bytes
    if compact_ready and hidden_list:
        try:
            engine_value = str(hidden_list[0].get("Engine", ""))
            engine_bytes = engine_value.encode("utf-8")
            if len(engine_bytes) > 255:
                raise ValueError("engine header too long")

            count = len(hidden_list)
            entries: list[int] = []
            for item in hidden_list:
                entries.append(int(item.get("Seed", "0")))
                entries.append(int(item.get("DataBits", "0")))

            compact_body = bytes([len(engine_bytes)]) + struct.pack(">H", count)
            compact_body += engine_bytes
            if entries:
                compact_body += struct.pack(f">{len(entries)}I", *entries)

            msg = _metadata_auth_message(visible_pgn_text, hidden_list)
            if password:
                key = _derive_metadata_auth_key(password)
                tag = hmac.new(key, msg, hashlib.sha256).digest()
                auth_alg_id = 1
            else:
                tag = hashlib.sha256(msg).digest()
                auth_alg_id = 0

            payload = bytes([HIDDEN_TRAILER_VERSION_AUTH_COMPACT, auth_alg_id]) + tag + compact_body
        except Exception:
            compact_ready = False

    if not compact_ready:
        payload_json = json.dumps(hidden_list, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload = bytes([HIDDEN_TRAILER_VERSION]) + zlib.compress(payload_json, level=9)

    bits = (
        format(HIDDEN_TRAILER_SENTINEL, "016b")
        + format(len(payload), "032b")
        + "".join(format(byte, "08b") for byte in payload)
    )
    return "".join("\t" if bit == "1" else " " for bit in bits)


def _has_hidden_carrier(pgn_content: str) -> bool:
    for line in reversed(pgn_content.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{" + HIDDEN_COMMENT_PREFIX) and stripped.endswith("}"):
            return True
        if re.fullmatch(r"[ \t]+", line or ""):
            bits = "".join("1" if ch == "\t" else "0" for ch in line)
            if len(bits) >= 16 and int(bits[:16], 2) == HIDDEN_TRAILER_SENTINEL:
                return True
        return False
    return False


def hide_technical_headers_in_comment(
    pgn_text: str,
    password: Optional[str] = None,
    carrier_style: str = "comment",
) -> str:
    """Move technical headers out of visible PGN headers into an authenticated PGN comment carrier."""
    game_blocks = re.split(r"\n\s*\n(?=\[)", pgn_text.strip())
    out_blocks: list[str] = []
    hidden_list: list[dict[str, str]] = []

    header_re = re.compile(r'^\[([^\s]+)\s+"(.*)"\]$')

    for block in game_blocks:
        lines = block.splitlines()
        header_lines: list[str] = []
        move_lines: list[str] = []
        in_headers = True
        hidden: dict[str, str] = {}

        for line in lines:
            stripped = line.strip()
            if in_headers and stripped.startswith("[") and stripped.endswith("]"):
                m = header_re.match(stripped)
                if m:
                    key, value = m.group(1), m.group(2)
                    if key in HIDDEN_HEADER_KEYS:
                        hidden[key] = value
                    else:
                        header_lines.append(stripped)
                else:
                    header_lines.append(stripped)
                continue

            in_headers = False
            if stripped:
                move_lines.append(stripped)

        move_text = " ".join(move_lines).strip()
        hidden_list.append(hidden)

        if header_lines:
            out_blocks.append("\n".join(header_lines) + "\n\n" + move_text)
        else:
            out_blocks.append(move_text)

    visible_text = "\n\n".join(out_blocks).strip()
    if carrier_style == "whitespace":
        carrier = _encode_hidden_whitespace_payload(hidden_list, visible_text, password)
    else:
        carrier = _encode_hidden_comment_payload(hidden_list, visible_text, password)
    return visible_text + "\n" + carrier + "\n"


def ordered_legal_moves(board: Board) -> list[Move]:
    return sorted(list(board.legal_moves), key=lambda m: m.uci())


def move_score(board: Board, move: Move) -> int:
    score = 0
    target_piece = board.piece_at(move.to_square)
    if target_piece is not None:
        score += PIECE_VALUES.get(target_piece.piece_type, 0) * 100
    if board.gives_check(move):
        score += 50
    if board.san(move).startswith("O-O"):
        score += 12
    if move.promotion is not None:
        score += 80
    if move.uci()[2:4] in CENTER_SQUARES:
        score += 10
    return score


def get_candidate_moves(board: Board, move_random: random.Random, engine_guided: bool) -> list[Move]:
    legal_moves = ordered_legal_moves(board)
    if len(legal_moves) <= 1:
        return legal_moves

    if engine_guided:
        scored = sorted(
            legal_moves,
            key=lambda m: (-move_score(board, m), m.uci()),
        )
        capacity = floor(log2(len(scored)))
        top_k = max(2, 1 << capacity)
        legal_moves = scored[:top_k]

    move_random.shuffle(legal_moves)
    return legal_moves


def choose_opening_sequence(root_seed: int) -> list[str]:
    idx = root_seed % len(OPENING_BOOK)
    return OPENING_BOOK[idx]


def apply_opening_sequence(board: Board, opening_sequence: list[str]) -> None:
    for uci in opening_sequence:
        move = Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"Opening move {uci} is illegal in current position")
        board.push(move)


def _encode_with_rust(
    file_bytes: bytes,
    output_pgn_path: str,
    self_destruct_timer: Optional[int] = None,
    custom_headers: Optional[Dict[str, str]] = None,
    password: Optional[str] = None,
    deterministic_seed_root: Optional[int] = None,
    engine_guided: bool = False,
    opening_book_uci: Optional[list[str]] = None,
    hide_technical_headers: bool = True,
    metadata_carrier_style: str = "comment",
) -> None:
    start_time = current_time()
    logger.debug("[RUST] Starting encoding")

    expiry_time = None
    if self_destruct_timer is not None and self_destruct_timer > 0:
        expiry_time = int(current_time()) + self_destruct_timer

    pgn_string = _rust.rust_encode_pgn(
        list(file_bytes),
        "rust-v3",
        expiry_time,
        dict(custom_headers) if custom_headers else None,
        deterministic_seed_root,
        engine_guided,
        opening_book_uci,
    )

    if hide_technical_headers:
        pgn_string = hide_technical_headers_in_comment(
            pgn_string,
            password=password,
            carrier_style=metadata_carrier_style,
        )

    with open(output_pgn_path, "w", encoding="utf-8") as f:
        f.write(pgn_string)

    elapsed = current_time() - start_time
    logger.debug("[RUST] Encoding completed in %.2fs", elapsed)


def encode(
    file_path: str,
    output_pgn_path: str,
    self_destruct_timer: Optional[int] = None,
    custom_headers: Optional[Dict[str, str]] = None,
    password: Optional[str] = None,
    compression_level: Optional[int] = None,
    engine_guided: bool = False,
    opening_camouflage: bool = False,
    metadata_payload: Optional[str] = None,
    hide_technical_headers: bool = True,
    metadata_carrier_style: str = "comment",
) -> Dict[str, Any]:
    source_bytes = read_input_file(file_path)

    payload_bytes, compression_meta = prepare_payload_for_encoding(
        source_bytes,
        compression_level=compression_level,
    )

    compressed_stage_bytes = len(payload_bytes)

    encryption_used = bool(password)
    salt = b""
    nonce = b""
    if password:
        payload_bytes, salt, nonce = encrypt_payload(payload_bytes, password)

    payload_bytes = build_payload_envelope(
        payload_bytes,
        compression_used=bool(compression_meta["compression_used"]),
        compression_algorithm=str(compression_meta["compression_algorithm"]),
        compression_level=int(compression_meta["compression_level"]),
        original_size=len(source_bytes),
        encryption_used=encryption_used,
        salt=salt,
        nonce=nonce,
    )

    deterministic_seed_mode = bool(password)
    root_seed = derive_root_seed(password) if deterministic_seed_mode else random.randint(1, 2**31 - 1)

    effective_headers = dict(custom_headers) if custom_headers else {}
    if engine_guided:
        effective_headers["EngineGuided"] = "heuristic-v1"

    opening_sequence: list[str] = []
    if opening_camouflage:
        opening_sequence = choose_opening_sequence(root_seed)
        effective_headers["OpeningBookUCI"] = " ".join(opening_sequence)

    if metadata_payload:
        encoded_meta = base64.urlsafe_b64encode(metadata_payload.encode("utf-8")).decode("ascii")
        effective_headers["Annotator"] = encoded_meta[:220]

    can_use_rust = RUST_ENGINE_AVAILABLE

    encode_meta: Dict[str, Any] = {
        "compression_used": compression_meta["compression_used"],
        "compression_requested": compression_meta["compression_requested"],
        "compression_level": compression_meta["compression_level"],
        "compression_algorithm": compression_meta["compression_algorithm"],
        "source_bytes": len(source_bytes),
        "payload_bytes": compressed_stage_bytes,
        "payload_ratio": (compressed_stage_bytes / len(source_bytes)) if source_bytes else 1.0,
        "encoded_bytes": len(payload_bytes),
        "encoded_ratio": (len(payload_bytes) / len(source_bytes)) if source_bytes else 1.0,
        "expansion_bytes": 0,
        "expansion_ratio": 1.0,
        "encryption_used": encryption_used,
        "deterministic_seed_mode": deterministic_seed_mode,
        "engine_guided": engine_guided,
        "opening_camouflage": opening_camouflage,
        "rust_path_used": can_use_rust,
    }

    if can_use_rust:
        _encode_with_rust(
            payload_bytes,
            output_pgn_path,
            self_destruct_timer,
            effective_headers or None,
            password=password,
            deterministic_seed_root=root_seed if deterministic_seed_mode else None,
            engine_guided=engine_guided,
            opening_book_uci=opening_sequence or None,
            hide_technical_headers=hide_technical_headers,
            metadata_carrier_style=metadata_carrier_style,
        )
        final_size = os.path.getsize(output_pgn_path)
        encode_meta["expansion_bytes"] = final_size
        encode_meta["expansion_ratio"] = (final_size / len(source_bytes)) if source_bytes else 1.0
        return encode_meta

    try:
        start_time = current_time()
        logger.debug("Starting pure-Python encoding path")

        data_bits = "".join(format(byte, "08b") for byte in payload_bytes)
        data_bits_count = len(data_bits)
        complete_binary = data_bits + ("0" * 32)
        file_bits_count = data_bits_count

        expiry_time = None
        if self_destruct_timer is not None and self_destruct_timer > 0:
            expiry_time = int(current_time()) + self_destruct_timer
            human_readable = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expiry_time))
            logger.debug("File will expire at: %s", human_readable)

        output_pgns: list[str] = []
        file_bit_index = 0
        chess_board = Board()
        game_number = 1

        base_seed = (
            deterministic_game_seed(root_seed, game_number)
            if deterministic_seed_mode
            else random.randint(1, 1_000_000)
        )
        move_random = random.Random(base_seed)

        if opening_sequence:
            apply_opening_sequence(chess_board, opening_sequence)

        while file_bit_index < file_bits_count:
            legal_moves = get_candidate_moves(chess_board, move_random, engine_guided)

            if len(legal_moves) <= 1:
                if legal_moves:
                    chess_board.push(legal_moves[0])
                if len(legal_moves) == 0 or chess_board.is_game_over():
                    game_headers = effective_headers.copy() if effective_headers else {}
                    if game_number > 1 and "Round" not in game_headers:
                        game_headers["Round"] = str(game_number)
                    output_pgns.append(
                        create_game_record(
                            chess_board,
                            base_seed,
                            data_bits_count,
                            expiry_time,
                            game_headers or None,
                        )
                    )
                    chess_board.reset()
                    game_number += 1
                    base_seed = (
                        deterministic_game_seed(root_seed, game_number)
                        if deterministic_seed_mode
                        else random.randint(1, 1_000_000)
                    )
                    move_random = random.Random(base_seed)
                    if opening_sequence:
                        apply_opening_sequence(chess_board, opening_sequence)
                continue

            max_bits = floor(log2(len(legal_moves)))
            bits = complete_binary[file_bit_index : file_bit_index + max_bits]
            move_index = int(bits, 2)
            if move_index >= len(legal_moves):
                raise ValueError("Invalid move index calculated")

            chosen_move = legal_moves[move_index]
            chess_board.push(chosen_move)
            file_bit_index += max_bits

            if file_bit_index >= file_bits_count:
                break

            if should_end_game(chess_board):
                game_headers = effective_headers.copy() if effective_headers else {}
                if game_number > 1 and "Round" not in game_headers:
                    game_headers["Round"] = str(game_number)
                output_pgns.append(
                    create_game_record(
                        chess_board,
                        base_seed,
                        data_bits_count,
                        expiry_time,
                        game_headers or None,
                    )
                )

                if file_bit_index < file_bits_count:
                    chess_board.reset()
                    game_number += 1
                    base_seed = (
                        deterministic_game_seed(root_seed, game_number)
                        if deterministic_seed_mode
                        else random.randint(1, 1_000_000)
                    )
                    move_random = random.Random(base_seed)
                    if opening_sequence:
                        apply_opening_sequence(chess_board, opening_sequence)

        if chess_board.move_stack:
            game_headers = effective_headers.copy() if effective_headers else {}
            if game_number > 1 and "Round" not in game_headers:
                game_headers["Round"] = str(game_number)
            output_pgns.append(
                create_game_record(
                    chess_board,
                    base_seed,
                    data_bits_count,
                    expiry_time,
                    game_headers or None,
                )
            )

        pgn_output = "\n\n".join(output_pgns)
        if hide_technical_headers:
            pgn_output = hide_technical_headers_in_comment(
                pgn_output,
                password=password,
                carrier_style=metadata_carrier_style,
            )
        verify_pgn_content(pgn_output, expiry_time, effective_headers or None)

        with open(output_pgn_path, "w", encoding="utf-8") as f:
            f.write(pgn_output)

        final_size = os.path.getsize(output_pgn_path)
        encode_meta["expansion_bytes"] = final_size
        encode_meta["expansion_ratio"] = (final_size / len(source_bytes)) if source_bytes else 1.0

        elapsed_time = current_time() - start_time
        logger.debug("Encoding completed successfully in %.2f seconds", elapsed_time)
        logger.debug("Created %d game(s)", len(output_pgns))
        return encode_meta

    except Exception as e:
        logger.error("Encoding error: %s", str(e), exc_info=True)
        raise
