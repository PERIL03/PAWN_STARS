import base64
import bz2
import gzip
import hashlib
import hmac
import io
import json
import lzma
import logging
import os
import random
import re
import struct
import zlib
from math import floor, log2
from time import time

import chess
from chess import Board, Move, pgn
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
CENTER_SQUARES = {"d4", "e4", "d5", "e5"}
ENVELOPE_MAGIC = b"RH2"
ENVELOPE_VERSION_V1 = 1
ENVELOPE_VERSION_V2 = 2
COMPRESSION_ALGORITHM_IDS = {
    0: "none",
    1: "zlib",
    2: "bz2",
    3: "lzma",
}
HIDDEN_TRAILER_SENTINEL = 0xA55A
HIDDEN_TRAILER_VERSION = 2
HIDDEN_COMMENT_PREFIX = "RHMDv3:"
HIDDEN_METADATA_VERSION = 3
HIDDEN_TRAILER_VERSION_AUTH_COMPACT = 4


def build_expiry_signature(expiry_time: int, signing_secret: str) -> str:
    message = f"rookhide-expiry-v1:{expiry_time}".encode("utf-8")
    key = signing_secret.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _enforce_expiry(
    headers: dict[str, str],
    expiry_signing_secret: str | None,
    allow_unsigned_expiry: bool,
) -> None:
    expiry_raw = (headers.get("ExpiryTime") or "").strip()
    expiry_signature = (headers.get("ExpirySignature") or "").strip()

    if expiry_signature and not expiry_raw:
        raise ValueError("Expiry metadata is invalid")

    if not expiry_raw:
        return

    try:
        expiry_time = int(expiry_raw)
    except ValueError as exc:
        raise ValueError("Invalid ExpiryTime header") from exc

    if expiry_signing_secret:
        if not expiry_signature:
            if not allow_unsigned_expiry:
                raise ValueError("Expiry metadata signature is missing")
        else:
            expected_signature = build_expiry_signature(expiry_time, expiry_signing_secret)
            if not hmac.compare_digest(expiry_signature, expected_signature):
                raise ValueError("Expiry metadata signature is invalid")

    current_time = int(time())
    if current_time > expiry_time:
        time_diff = current_time - expiry_time
        if time_diff < 60:
            time_msg = f"{time_diff} seconds"
        elif time_diff < 3600:
            time_msg = f"{time_diff // 60} minutes"
        else:
            time_msg = f"{time_diff // 3600} hours"
        raise ValueError(f"This file has expired {time_msg} ago and can no longer be decrypted")


def safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Failed to remove %s: %s", path, str(exc))


def is_rust_encoded_pgn(pgn_content: str) -> bool:
    match = re.search(r'\[Engine\s+"([^"]+)"\]', pgn_content)
    return bool(match and "rust" in match.group(1).lower())


def rust_decode_supported(pgn_content: str) -> bool:
    return True


def maybe_decompress_payload(payload: bytes, headers: dict[str, str]) -> bytes:
    compression = (headers.get("Compression") or "").strip().lower()
    if not compression:
        return payload

    try:
        if compression == "zlib":
            decompressed = zlib.decompress(payload)
        elif compression == "bz2":
            decompressed = bz2.decompress(payload)
        elif compression == "lzma":
            decompressed = lzma.decompress(payload)
        else:
            raise ValueError(f"Unsupported compression format: {compression}")
    except (zlib.error, OSError, lzma.LZMAError) as exc:
        raise ValueError(f"Failed to decompress payload: {exc}") from exc

    original_size = headers.get("OriginalSize")
    if original_size:
        try:
            expected = int(original_size)
        except ValueError as exc:
            raise ValueError("Invalid OriginalSize header") from exc
        if len(decompressed) != expected:
            raise ValueError(
                f"Decompressed payload size mismatch: got {len(decompressed)}, expected {expected}"
            )

    return decompressed


def decode_payload_envelope(payload: bytes, password: str | None) -> tuple[bytes, bool]:
    if len(payload) < 20 or payload[:3] != ENVELOPE_MAGIC:
        return payload, False

    if len(payload) < 21:
        raise ValueError("Invalid payload envelope header")

    version = payload[3]
    compression_algorithm = "zlib"

    if version == ENVELOPE_VERSION_V1:
        try:
            magic, version, flags, compression_level, salt_len, nonce_len, original_size, kdf_iters = struct.unpack(
                ">3sBBBBBQI",
                payload[:20],
            )
        except struct.error as exc:
            raise ValueError("Invalid payload envelope header") from exc
        offset = 20
    elif version == ENVELOPE_VERSION_V2:
        try:
            (
                magic,
                version,
                flags,
                compression_algorithm_id,
                compression_level,
                salt_len,
                nonce_len,
                original_size,
                kdf_iters,
            ) = struct.unpack(
                ">3sBBBBBBQI",
                payload[:21],
            )
        except struct.error as exc:
            raise ValueError("Invalid payload envelope header") from exc

        compression_algorithm = COMPRESSION_ALGORITHM_IDS.get(compression_algorithm_id)
        if compression_algorithm is None:
            raise ValueError("Unsupported payload compression algorithm")
        offset = 21
    else:
        raise ValueError("Unsupported payload envelope version")

    end_salt = offset + salt_len
    end_nonce = end_salt + nonce_len
    if end_nonce > len(payload):
        raise ValueError("Invalid payload envelope lengths")

    salt = payload[offset:end_salt]
    nonce = payload[end_salt:end_nonce]
    body = payload[end_nonce:]

    encrypted = bool(flags & 0x02)
    compressed = bool(flags & 0x01)

    if encrypted:
        if not password:
            raise ValueError("Password is required to decrypt this file")
        if not salt or not nonce or kdf_iters <= 0:
            raise ValueError("Corrupted encryption envelope")
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=kdf_iters,
        )
        key = kdf.derive(password.encode("utf-8"))
        cipher = AESGCM(key)
        try:
            body = cipher.decrypt(nonce, body, None)
        except Exception as exc:
            raise ValueError("Invalid password or corrupted encrypted payload") from exc

    if compressed:
        try:
            if compression_algorithm == "zlib":
                body = zlib.decompress(body)
            elif compression_algorithm == "bz2":
                body = bz2.decompress(body)
            elif compression_algorithm == "lzma":
                body = lzma.decompress(body)
            elif compression_algorithm == "none":
                pass
            else:
                raise ValueError("Unsupported payload compression algorithm")
        except (zlib.error, OSError, lzma.LZMAError) as exc:
            raise ValueError(f"Failed to decompress payload: {exc}") from exc
        if len(body) != original_size:
            raise ValueError(
                f"Decompressed payload size mismatch: got {len(body)}, expected {original_size}"
            )

    return body, True


def derive_fernet(password: str, salt: bytes, iterations: int) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return Fernet(key)


def maybe_decrypt_payload(payload: bytes, headers: dict[str, str], password: str | None) -> bytes:
    encryption = (headers.get("Encryption") or "").strip().lower()
    if not encryption:
        return payload

    if encryption != "fernet":
        raise ValueError(f"Unsupported encryption format: {encryption}")

    if not password:
        raise ValueError("Password is required to decrypt this file")

    kdf_name = (headers.get("KDF") or "").strip().lower()
    if kdf_name != "pbkdf2-sha256":
        raise ValueError("Unsupported KDF metadata")

    try:
        iterations = int(headers.get("KDFIter", "390000"))
    except ValueError as exc:
        raise ValueError("Invalid KDF iteration metadata") from exc

    salt_b64 = headers.get("KDFSalt")
    if not salt_b64:
        raise ValueError("Missing KDF salt metadata")

    try:
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
    except Exception as exc:
        raise ValueError("Invalid KDF salt metadata") from exc

    try:
        fernet = derive_fernet(password, salt, iterations)
        return fernet.decrypt(payload)
    except InvalidToken as exc:
        raise ValueError("Invalid password or corrupted encrypted payload") from exc


try:
    import chess_engine as _rust

    RUST_ENGINE_AVAILABLE = True
    logger.info("Rust chess engine loaded — using accelerated decode path")
except ImportError:
    RUST_ENGINE_AVAILABLE = False
    logger.info("Rust chess engine not available — using pure-Python decode path")


def _decode_with_rust(
    pgn_content: str,
    output_file_path: str,
    password: str | None,
    expiry_signing_secret: str | None,
    allow_unsigned_expiry: bool,
) -> None:
    decoded_bytes, headers = _rust.rust_decode_pgn(pgn_content)

    _enforce_expiry(headers, expiry_signing_secret, allow_unsigned_expiry)

    output_bytes = bytes(decoded_bytes)
    output_bytes, used_envelope = decode_payload_envelope(output_bytes, password)
    if not used_envelope:
        output_bytes = maybe_decrypt_payload(output_bytes, headers, password)
        output_bytes = maybe_decompress_payload(output_bytes, headers)

    safe_remove(output_file_path)
    with open(output_file_path, "wb") as f:
        f.write(output_bytes)

    file_size = os.path.getsize(output_file_path)
    if file_size == 0:
        raise ValueError("Decoded output file is empty")
    logger.debug("[RUST] Successfully decoded %d bytes", file_size)


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
        scored = sorted(legal_moves, key=lambda m: (-move_score(board, m), m.uci()))
        capacity = floor(log2(len(scored)))
        top_k = max(2, 1 << capacity)
        legal_moves = scored[:top_k]

    move_random.shuffle(legal_moves)
    return legal_moves


def parse_opening_sequence(headers: dict[str, str]) -> list[str]:
    raw = (headers.get("OpeningBookUCI") or "").strip()
    if not raw:
        return []
    return [token for token in raw.split() if token]


def _derive_metadata_auth_key(password: str) -> bytes:
    return hashlib.sha256(f"rookhide-meta-auth:{password}".encode("utf-8")).digest()


def _metadata_auth_message(visible_pgn_text: str, hidden_list: list[dict[str, str]]) -> bytes:
    canonical_hidden = json.dumps(hidden_list, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return b"ROOKHIDE-META-V3\n" + visible_pgn_text.encode("utf-8") + b"\n" + canonical_hidden


def _extract_comment_payload(line: str) -> str | None:
    stripped = line.strip()
    prefix = "{" + HIDDEN_COMMENT_PREFIX
    if stripped.startswith(prefix) and stripped.endswith("}"):
        return stripped[len(prefix):-1]
    return None


def _restore_legacy_whitespace_trailer(raw_lines: list[str], password: str | None = None) -> tuple[list[str], list[dict[str, str]]]:
    hidden_list: list[dict[str, str]] = []
    if not raw_lines or not re.fullmatch(r"[ \t]+", raw_lines[-1] or ""):
        return raw_lines, hidden_list

    ws = raw_lines[-1]
    bits = "".join("1" if ch == "\t" else "0" for ch in ws)
    if len(bits) < 48:
        return raw_lines, hidden_list

    sentinel = int(bits[:16], 2)
    if sentinel != HIDDEN_TRAILER_SENTINEL:
        return raw_lines, hidden_list

    payload_len = int(bits[16:48], 2)
    payload_bits_len = payload_len * 8
    if len(bits) < 48 + payload_bits_len:
        raise ValueError("Legacy metadata trailer is truncated")

    payload_bits = bits[48:48 + payload_bits_len]
    payload = bytes(
        int(payload_bits[i:i + 8], 2)
        for i in range(0, payload_bits_len, 8)
    )

    if len(payload) >= 2 and payload[0] == HIDDEN_TRAILER_VERSION:
        decoded = json.loads(zlib.decompress(payload[1:]).decode("utf-8"))
        if not isinstance(decoded, list):
            raise ValueError("Legacy metadata trailer payload is invalid")
        hidden_list = [item for item in decoded if isinstance(item, dict)]
        return raw_lines[:-1], hidden_list

    if len(payload) >= 36 and payload[0] == HIDDEN_TRAILER_VERSION_AUTH_COMPACT:
        auth_alg_id = payload[1]
        auth_tag = payload[2:34]
        compact_body = payload[34:]
        if len(compact_body) < 3:
            raise ValueError("Metadata trailer payload is truncated")

        engine_len = compact_body[0]
        count = struct.unpack(">H", compact_body[1:3])[0]
        base_offset = 3 + engine_len
        expected_len = base_offset + (count * 8)
        if len(compact_body) != expected_len:
            raise ValueError("Metadata trailer payload length mismatch")

        engine_bytes = compact_body[3:base_offset]
        try:
            engine_value = engine_bytes.decode("utf-8")
        except UnicodeDecodeError:
            engine_value = ""

        entries = struct.unpack(f">{count * 2}I", compact_body[base_offset:]) if count else ()
        hidden_list = []
        for idx in range(0, len(entries), 2):
            game_headers = {
                "Seed": str(entries[idx]),
                "DataBits": str(entries[idx + 1]),
            }
            if idx == 0 and engine_value:
                game_headers["Engine"] = engine_value
            hidden_list.append(game_headers)

        visible_pgn = "\n".join(raw_lines[:-1]).strip()
        msg = _metadata_auth_message(visible_pgn, hidden_list)

        if auth_alg_id == 1:
            if not password:
                raise ValueError("Password is required to verify authenticated metadata")
            key = _derive_metadata_auth_key(password)
            expected_tag = hmac.new(key, msg, hashlib.sha256).digest()
        elif auth_alg_id == 0:
            expected_tag = hashlib.sha256(msg).digest()
        else:
            raise ValueError("Unsupported metadata authentication algorithm")

        if not hmac.compare_digest(auth_tag, expected_tag):
            # Backward compatibility: older compact encoders could sign a variant
            # where Engine was included in every game's hidden headers.
            alt_hidden_list: list[dict[str, str]] = []
            if engine_value:
                for item in hidden_list:
                    alt_item = dict(item)
                    alt_item["Engine"] = engine_value
                    alt_hidden_list.append(alt_item)

            if not alt_hidden_list:
                raise ValueError("Metadata authentication failed: file may be tampered")

            alt_msg = _metadata_auth_message(visible_pgn, alt_hidden_list)
            if auth_alg_id == 1:
                if not password:
                    raise ValueError("Password is required to verify authenticated metadata")
                key = _derive_metadata_auth_key(password)
                alt_expected_tag = hmac.new(key, alt_msg, hashlib.sha256).digest()
            elif auth_alg_id == 0:
                alt_expected_tag = hashlib.sha256(alt_msg).digest()
            else:
                raise ValueError("Unsupported metadata authentication algorithm")

            if not hmac.compare_digest(auth_tag, alt_expected_tag):
                raise ValueError("Metadata authentication failed: file may be tampered")

            hidden_list = alt_hidden_list

        return raw_lines[:-1], hidden_list

    if len(payload) >= 4 and payload[0] == 1:
        _, engine_len, count = struct.unpack(">BBH", payload[:4])
        base_offset = 4 + engine_len
        expected_len = base_offset + (count * 8)
        if len(payload) != expected_len:
            raise ValueError("Legacy metadata trailer payload length mismatch")

        engine_bytes = payload[4:base_offset]
        try:
            engine_value = engine_bytes.decode("utf-8")
        except UnicodeDecodeError:
            engine_value = ""

        entries = struct.unpack(f">{count * 2}I", payload[base_offset:]) if count else ()
        hidden_list = []
        for idx in range(0, len(entries), 2):
            game_headers = {
                "Seed": str(entries[idx]),
                "DataBits": str(entries[idx + 1]),
            }
            if idx == 0 and engine_value:
                game_headers["Engine"] = engine_value
            hidden_list.append(game_headers)
        return raw_lines[:-1], hidden_list

    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, list):
        raise ValueError("Legacy metadata trailer payload is invalid")
    hidden_list = [item for item in decoded if isinstance(item, dict)]
    return raw_lines[:-1], hidden_list


def restore_technical_headers_from_comment(pgn_content: str, password: str | None = None) -> str:
    """Restore technical decode headers from authenticated comment carrier and legacy trailers."""
    raw_lines = pgn_content.splitlines()
    hidden_list: list[dict[str, str]] = []

    carrier_line_idx = None
    carrier_payload_b64 = None
    for idx in range(len(raw_lines) - 1, -1, -1):
        stripped = raw_lines[idx].strip()
        if not stripped:
            continue
        maybe_payload = _extract_comment_payload(stripped)
        if maybe_payload is not None:
            carrier_line_idx = idx
            carrier_payload_b64 = maybe_payload
        break

    if carrier_payload_b64 is not None:
        try:
            carrier_payload = base64.urlsafe_b64decode(carrier_payload_b64.encode("ascii"))
            payload_obj = json.loads(zlib.decompress(carrier_payload).decode("utf-8"))
        except Exception as exc:
            raise ValueError("Metadata carrier is malformed or corrupted") from exc

        if not isinstance(payload_obj, dict):
            raise ValueError("Metadata carrier payload is invalid")

        version = payload_obj.get("version")
        if version != HIDDEN_METADATA_VERSION:
            raise ValueError("Unsupported metadata carrier version")

        hidden = payload_obj.get("hidden")
        auth = payload_obj.get("auth")
        if not isinstance(hidden, list) or not isinstance(auth, dict):
            raise ValueError("Metadata carrier payload is incomplete")

        hidden_list = [item for item in hidden if isinstance(item, dict)]
        auth_alg = auth.get("alg")
        auth_tag_b64 = auth.get("tag")
        if not isinstance(auth_alg, str) or not isinstance(auth_tag_b64, str):
            raise ValueError("Metadata authentication payload is invalid")

        try:
            auth_tag = base64.urlsafe_b64decode(auth_tag_b64.encode("ascii"))
        except Exception as exc:
            raise ValueError("Metadata authentication tag is invalid") from exc

        if carrier_line_idx is None:
            raise ValueError("Metadata carrier location is invalid")

        raw_lines_without_carrier = raw_lines[:carrier_line_idx] + raw_lines[carrier_line_idx + 1:]
        visible_pgn = "\n".join(raw_lines_without_carrier).strip()
        msg = _metadata_auth_message(visible_pgn, hidden_list)

        if auth_alg == "hmac-sha256":
            if not password:
                raise ValueError("Password is required to verify authenticated metadata")
            key = _derive_metadata_auth_key(password)
            expected_tag = hmac.new(key, msg, hashlib.sha256).digest()
            if not hmac.compare_digest(auth_tag, expected_tag):
                raise ValueError("Metadata authentication failed: file may be tampered")
        elif auth_alg == "sha256":
            expected_tag = hashlib.sha256(msg).digest()
            if not hmac.compare_digest(auth_tag, expected_tag):
                raise ValueError("Metadata integrity check failed: file may be corrupted or tampered")
        else:
            raise ValueError("Unsupported metadata authentication algorithm")

        raw_lines = raw_lines_without_carrier
    else:
        raw_lines, hidden_list = _restore_legacy_whitespace_trailer(raw_lines, password=password)

    visible_pgn = "\n".join(raw_lines).strip()
    game_blocks = re.split(r"\n\s*\n(?=\[)", visible_pgn)
    out_blocks: list[str] = []
    header_re = re.compile(r'^\[([^\s]+)\s+"(.*)"\]$')

    for idx, block in enumerate(game_blocks):
        lines = block.splitlines()
        header_lines: list[str] = []
        move_lines: list[str] = []
        in_headers = True

        for line in lines:
            stripped = line.strip()
            if in_headers and stripped.startswith("[") and stripped.endswith("]"):
                header_lines.append(stripped)
                continue

            in_headers = False
            if stripped:
                move_lines.append(stripped)

        move_text = " ".join(move_lines).strip()

        if idx < len(hidden_list):
            for key, value in hidden_list[idx].items():
                if isinstance(key, str) and isinstance(value, str):
                    header_lines.append(f'[{key} "{value}"]')

        move_text = re.sub(r"\{[^{}]*\}", " ", move_text)
        move_text = re.sub(r"\s+", " ", move_text).strip()

        if header_lines:
            out_blocks.append("\n".join(header_lines) + "\n\n" + move_text)
        else:
            out_blocks.append(move_text)

    return "\n\n".join(out_blocks).strip() + "\n"


def decode(
    pgn_file_path: str,
    output_file_path: str,
    password: str | None = None,
    expiry_signing_secret: str | None = None,
    allow_unsigned_expiry: bool = False,
) -> None:
    try:
        if not os.path.exists(pgn_file_path):
            raise ValueError("Input PGN file does not exist")

        with open(pgn_file_path, "rb") as pgn_file:
            raw_pgn_bytes = pgn_file.read()

        if raw_pgn_bytes.startswith(b"\x1f\x8b"):
            try:
                raw_pgn_bytes = gzip.decompress(raw_pgn_bytes)
            except OSError as exc:
                raise ValueError("Input PGN file is not valid gzip data") from exc

        try:
            pgn_content = raw_pgn_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Input PGN file is not valid UTF-8 text") from exc

        pgn_content = restore_technical_headers_from_comment(pgn_content, password=password)

        if not pgn_content.strip():
            raise ValueError("Input PGN file is empty")

        if RUST_ENGINE_AVAILABLE and is_rust_encoded_pgn(pgn_content) and rust_decode_supported(pgn_content):
            _decode_with_rust(
                pgn_content,
                output_file_path,
                password,
                expiry_signing_secret,
                allow_unsigned_expiry,
            )
            return

        games = []
        pgn_io = io.StringIO(pgn_content)
        while True:
            game = pgn.read_game(pgn_io)
            if game is None:
                break
            games.append(game)

        if not games:
            raise ValueError("No valid chess games found in PGN file")

        _enforce_expiry(games[0].headers, expiry_signing_secret, allow_unsigned_expiry)

        data_bits_count = None
        if "DataBits" in games[0].headers:
            try:
                data_bits_count = int(games[0].headers["DataBits"])
            except ValueError:
                raise ValueError("Invalid DataBits header value")

        if data_bits_count is None or data_bits_count <= 0:
            raise ValueError("DataBits header missing or invalid — cannot determine data size")

        engine_guided = bool((games[0].headers.get("EngineGuided") or "").strip())
        opening_sequence = parse_opening_sequence(games[0].headers)

        safe_remove(output_file_path)
        all_bits = ""

        for game_index, game in enumerate(games):
            try:
                base_seed = int(game.headers.get("Seed", "1"))
            except ValueError:
                raise ValueError(f"Invalid seed in game {game_index + 1}")

            move_random = random.Random(base_seed)
            board = Board()
            opening_index = 0

            for move in game.mainline_moves():
                if opening_index < len(opening_sequence):
                    expected = Move.from_uci(opening_sequence[opening_index])
                    if move.uci() != expected.uci():
                        raise ValueError(
                            f"Opening camouflage mismatch in game {game_index + 1}: expected {expected.uci()}, got {move.uci()}"
                        )
                    board.push(move)
                    opening_index += 1
                    continue

                legal_moves = get_candidate_moves(board, move_random, engine_guided)

                if len(legal_moves) <= 1:
                    board.push(move)
                    continue

                try:
                    move_index = [m.uci() for m in legal_moves].index(move.uci())
                except ValueError:
                    raise ValueError(f"Invalid move found in game {game_index + 1}: {move.uci()}")

                max_bits = floor(log2(len(legal_moves)))
                if max_bits > 0:
                    all_bits += format(move_index, f"0{max_bits}b")

                board.push(move)

                if len(all_bits) >= data_bits_count:
                    break

            if len(all_bits) >= data_bits_count:
                break

        if len(all_bits) < data_bits_count:
            raise ValueError(f"Not enough bits extracted: got {len(all_bits)}, expected {data_bits_count}")

        data_bits = all_bits[:data_bits_count]
        if len(data_bits) % 8 != 0:
            raise ValueError(f"Data bits ({len(data_bits)}) is not a multiple of 8 — corrupted data")
        if len(data_bits) == 0:
            raise ValueError("No data found")

        output_payload = bytearray()
        for i in range(0, len(data_bits), 8):
            output_payload.append(int(data_bits[i : i + 8], 2))

        final_payload = bytes(output_payload)
        final_payload, used_envelope = decode_payload_envelope(final_payload, password)
        if not used_envelope:
            final_payload = maybe_decrypt_payload(final_payload, games[0].headers, password)
            final_payload = maybe_decompress_payload(final_payload, games[0].headers)

        with open(output_file_path, "wb") as f:
            f.write(final_payload)

        if not os.path.exists(output_file_path):
            raise ValueError("Failed to create output file")

        file_size = os.path.getsize(output_file_path)
        if file_size == 0:
            raise ValueError("Decoded output file is empty")

        logger.debug("Successfully decoded %d bytes", file_size)

    except ValueError:
        safe_remove(output_file_path)
        raise
    except Exception as e:
        safe_remove(output_file_path)
        raise ValueError(f"Decoding failed: {str(e)}")
