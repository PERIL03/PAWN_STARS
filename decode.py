from time import time
from math import log2, floor
from chess import pgn, Board
import io
import os
import random
import logging

logger = logging.getLogger(__name__)

# ── Try to load the Rust-accelerated chess engine ──────────────────────────
try:
    import chess_engine as _rust
    RUST_ENGINE_AVAILABLE = True
    logger.info("Rust chess engine loaded — using accelerated decode path")
except ImportError:
    RUST_ENGINE_AVAILABLE = False
    logger.info("Rust chess engine not available — using pure-Python decode path")


# ══════════════════════════════════════════════════════════════════════════════
#  Rust-accelerated decode path (for files encoded with Engine: rust-v1)
# ══════════════════════════════════════════════════════════════════════════════

def _decode_with_rust(pgn_content, output_file_path):
    """Fast decoding using the Rust chess engine extension.
    PGN parsing + SAN→move + bit extraction all happen in Rust."""
    # ── Single Rust call: PGN parse + decode ─────────────────────
    decoded_bytes, headers = _rust.rust_decode_pgn(pgn_content)

    # ── Check expiry ─────────────────────────────────────────────
    if "ExpiryTime" in headers:
        expiry_time = int(headers["ExpiryTime"])
        current_time = int(time())
        if current_time > expiry_time:
            time_diff = current_time - expiry_time
            if time_diff < 60:
                time_msg = f"{time_diff} seconds"
            elif time_diff < 3600:
                time_msg = f"{time_diff // 60} minutes"
            else:
                time_msg = f"{time_diff // 3600} hours"
            if os.path.exists(output_file_path):
                os.remove(output_file_path)
            raise ValueError(f"This file has expired {time_msg} ago and can no longer be decrypted")

    # Write output
    if os.path.exists(output_file_path):
        os.remove(output_file_path)
    with open(output_file_path, 'wb') as f:
        f.write(bytes(decoded_bytes))

    file_size = os.path.getsize(output_file_path)
    if file_size == 0:
        raise ValueError("Decoded output file is empty")
    logger.debug(f"[RUST] Successfully decoded {file_size} bytes")

def decode(pgn_file_path: str, output_file_path: str) -> None:
    
    try:
        if not os.path.exists(pgn_file_path):
            raise ValueError("Input PGN file does not exist")
            
        # Read PGN file
        with open(pgn_file_path, encoding='utf-8') as pgn_file:
            pgn_content = pgn_file.read()
            
        if not pgn_content.strip():
            raise ValueError("Input PGN file is empty")

        # ── Fast path: dispatch to Rust for rust-encoded files ───
        if RUST_ENGINE_AVAILABLE and 'Engine "rust' in pgn_content:
            _decode_with_rust(pgn_content, output_file_path)
            return

        # ── Slow path: pure-Python decode (non-rust-encoded files) ──
        games = []
        pgn_io = io.StringIO(pgn_content)
        while True:
            game = pgn.read_game(pgn_io)
            if game is None:
                break
            games.append(game)
            
        if not games:
            raise ValueError("No valid chess games found in PGN file")
        
        # Check expiry time if present
        current_time = int(time())
        if "ExpiryTime" in games[0].headers:
            expiry_time = int(games[0].headers.get("ExpiryTime"))
            logger.debug(f"Current time: {current_time}, Expiry time: {expiry_time}")
            
            if current_time > expiry_time:
                time_diff = current_time - expiry_time
                if time_diff < 60:
                    time_msg = f"{time_diff} seconds"
                elif time_diff < 3600:
                    time_msg = f"{time_diff // 60} minutes"
                else:
                    time_msg = f"{time_diff // 3600} hours"
                
                logger.debug(f"File expired {time_msg} ago")
                
                if os.path.exists(output_file_path):
                    os.remove(output_file_path)
                    
                raise ValueError(f"This file has expired {time_msg} ago and can no longer be decrypted")
            else:
                logger.debug(f"File valid for {expiry_time - current_time} more seconds")
        
        # Read expected data bit count from header
        data_bits_count = None
        if "DataBits" in games[0].headers:
            try:
                data_bits_count = int(games[0].headers["DataBits"])
                logger.debug(f"Expected data bits from header: {data_bits_count}")
            except ValueError:
                raise ValueError("Invalid DataBits header value")
        
        if data_bits_count is None or data_bits_count <= 0:
            raise ValueError("DataBits header missing or invalid — cannot determine data size")

        # Clean up any existing output file
        if os.path.exists(output_file_path):
            os.remove(output_file_path)
        
        all_bits = ""
        
        # Extract bits from all games
        for game_index, game in enumerate(games):
            try:
                base_seed = int(game.headers.get("Seed", "1"))
            except ValueError:
                raise ValueError(f"Invalid seed in game {game_index + 1}")
                
            move_random = random.Random(base_seed)
            board = Board()
            
            for move in game.mainline_moves():
                legal_moves = list(board.legal_moves)
                
                if len(legal_moves) <= 1:
                    board.push(move)
                    continue
                    
                move_random.shuffle(legal_moves)
                
                try:
                    move_index = [m.uci() for m in legal_moves].index(move.uci())
                except ValueError:
                    raise ValueError(f"Invalid move found in game {game_index + 1}: {move.uci()}")
                    
                max_bits = floor(log2(len(legal_moves)))
                
                if max_bits > 0:
                    move_bits = format(move_index, f'0{max_bits}b')
                    all_bits += move_bits
                    
                board.push(move)
                
                # Stop early if we already have enough bits
                if len(all_bits) >= data_bits_count:
                    break
            
            if len(all_bits) >= data_bits_count:
                break
        
        logger.debug(f"Total extracted bits: {len(all_bits)}")
        
        # Use exactly the expected number of data bits 
        if len(all_bits) < data_bits_count:
            raise ValueError(f"Not enough bits extracted: got {len(all_bits)}, expected {data_bits_count}")
        
        data_bits = all_bits[:data_bits_count]
        
        logger.debug(f"Data bits length: {len(data_bits)}")
        logger.debug(f"Data bits (first 64): {data_bits[:64] if len(data_bits) >= 64 else data_bits}")
        
        if len(data_bits) % 8 != 0:
            raise ValueError(f"Data bits ({len(data_bits)}) is not a multiple of 8 — corrupted data")
        
        if len(data_bits) == 0:
            raise ValueError("No data found")
        
        # Write decoded bytes to file
        with open(output_file_path, 'wb') as f:
            for i in range(0, len(data_bits), 8):
                byte_bits = data_bits[i:i+8]
                byte_value = int(byte_bits, 2)
                f.write(bytes([byte_value]))
        
        # Verify output file
        if not os.path.exists(output_file_path):
            raise ValueError("Failed to create output file")
        
        file_size = os.path.getsize(output_file_path)
        if file_size == 0:
            raise ValueError("Decoded output file is empty")
        
        logger.debug(f"Successfully decoded {file_size} bytes")
        
    except Exception as e:
        if os.path.exists(output_file_path):
            os.remove(output_file_path)
        raise ValueError(f"Decoding failed: {str(e)}")