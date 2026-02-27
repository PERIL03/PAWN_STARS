use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use chess::{Board, MoveGen, ChessMove, BoardStatus, Square, Piece, Rank, File};
use rand::prelude::*;
use rand::seq::SliceRandom;
use rand_chacha::ChaCha8Rng;
use std::time::Instant;
use std::collections::HashMap;
use std::fmt::Write;

// ══════════════════════════════════════════════════════════════════════════════
//  SplitMix64 — ultra-fast deterministic PRNG
//  1 add + 3 xorshifts + 2 multiplies per u64  (~10x faster than ChaCha8)
// ══════════════════════════════════════════════════════════════════════════════

struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    #[inline(always)]
    fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    #[inline(always)]
    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        z ^ (z >> 31)
    }

    /// Lemire's nearly-divisionless bounded random [0, bound).
    #[inline(always)]
    fn next_bounded(&mut self, bound: usize) -> usize {
        ((self.next_u64() as u128 * bound as u128) >> 64) as usize
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  Inline Fisher-Yates with unsafe ptr::swap (no bounds checks)
// ══════════════════════════════════════════════════════════════════════════════

#[inline]
fn fast_shuffle(slice: &mut [ChessMove], rng: &mut SplitMix64) {
    let len = slice.len();
    if len <= 1 { return; }
    let ptr = slice.as_mut_ptr();
    for i in (1..len).rev() {
        let j = rng.next_bounded(i + 1);
        unsafe { std::ptr::swap(ptr.add(i), ptr.add(j)); }
    }
}

// ══════════════════════════════════════════════════════════════════════════════
//  Compact move representation: u16 for zero-alloc comparison in hot loop
//  Layout: [src_sq:6][dst_sq:6][promo:4] = 16 bits
// ══════════════════════════════════════════════════════════════════════════════

#[inline(always)]
fn move_to_u16(m: &ChessMove) -> u16 {
    let src = m.get_source().to_index() as u16;
    let dst = m.get_dest().to_index() as u16;
    let promo: u16 = match m.get_promotion() {
        None => 0,
        Some(Piece::Knight) => 1,
        Some(Piece::Bishop) => 2,
        Some(Piece::Rook)   => 3,
        Some(Piece::Queen)  => 4,
        _ => 0,
    };
    (src << 10) | (dst << 4) | promo
}

// ──────────────────────────────────────────────────────────────────────────────
//  UCI formatting — bulk-called AFTER encoding, never in hot loop
// ──────────────────────────────────────────────────────────────────────────────

#[inline]
fn format_move_uci(m: &ChessMove) -> String {
    let src = m.get_source();
    let dst = m.get_dest();
    let mut s = String::with_capacity(5);
    s.push((b'a' + src.get_file().to_index() as u8) as char);
    s.push((b'1' + src.get_rank().to_index() as u8) as char);
    s.push((b'a' + dst.get_file().to_index() as u8) as char);
    s.push((b'1' + dst.get_rank().to_index() as u8) as char);
    if let Some(piece) = m.get_promotion() {
        s.push(match piece {
            Piece::Queen  => 'q',
            Piece::Rook   => 'r',
            Piece::Bishop => 'b',
            Piece::Knight => 'n',
            _ => unreachable!(),
        });
    }
    s
}

// ──────────────────────────────────────────────────────────────────────────────
//  SAN (Standard Algebraic Notation) — generated during PGN encode
// ──────────────────────────────────────────────────────────────────────────────

/// Generate SAN for a move WITHOUT check/checkmate suffix.
/// Board must be the position BEFORE the move.
/// `legal_moves` is the full set of legal moves (used for disambiguation).
#[inline]
fn san_base(board: &Board, m: ChessMove, legal_moves: &[ChessMove]) -> String {
    let src = m.get_source();
    let dst = m.get_dest();
    let piece = board.piece_on(src).expect("san_base: no piece on source");

    // ─── Castling: king moves more than 1 file ──────────────────
    if piece == Piece::King {
        let fdiff = dst.get_file().to_index() as i32 - src.get_file().to_index() as i32;
        if fdiff > 1  { return "O-O".to_string(); }
        if fdiff < -1 { return "O-O-O".to_string(); }
    }

    let is_ep = piece == Piece::Pawn
        && src.get_file() != dst.get_file()
        && board.piece_on(dst).is_none();
    let is_capture = board.piece_on(dst).is_some() || is_ep;

    let mut san = String::with_capacity(7);

    if piece != Piece::Pawn {
        san.push(match piece {
            Piece::Knight => 'N', Piece::Bishop => 'B', Piece::Rook => 'R',
            Piece::Queen  => 'Q', Piece::King   => 'K', _ => unreachable!(),
        });
        // Disambiguation: check if another piece of same type can reach same dest
        let mut same_file = false;
        let mut same_rank = false;
        let mut ambig = false;
        for &lm in legal_moves {
            if lm == m { continue; }
            if lm.get_dest() != dst { continue; }
            if board.piece_on(lm.get_source()) != Some(piece) { continue; }
            ambig = true;
            if lm.get_source().get_file() == src.get_file() { same_file = true; }
            if lm.get_source().get_rank() == src.get_rank() { same_rank = true; }
        }
        if ambig {
            if !same_file {
                san.push((b'a' + src.get_file().to_index() as u8) as char);
            } else if !same_rank {
                san.push((b'1' + src.get_rank().to_index() as u8) as char);
            } else {
                san.push((b'a' + src.get_file().to_index() as u8) as char);
                san.push((b'1' + src.get_rank().to_index() as u8) as char);
            }
        }
    } else if is_capture {
        san.push((b'a' + src.get_file().to_index() as u8) as char);
    }

    if is_capture { san.push('x'); }
    san.push((b'a' + dst.get_file().to_index() as u8) as char);
    san.push((b'1' + dst.get_rank().to_index() as u8) as char);

    if let Some(promo) = m.get_promotion() {
        san.push('=');
        san.push(match promo {
            Piece::Queen  => 'Q', Piece::Rook   => 'R',
            Piece::Bishop => 'B', Piece::Knight => 'N', _ => unreachable!(),
        });
    }

    san
}

/// Append '+' (check) or '#' (checkmate) based on the board state AFTER the move.
#[inline]
fn append_check_suffix(san: &mut String, board_after: &Board) {
    if board_after.checkers().popcnt() > 0 {
        if board_after.status() == BoardStatus::Checkmate { san.push('#'); }
        else { san.push('+'); }
    }
}

/// Write a single PGN header line: [Key "Value"]\n
#[inline]
fn write_pgn_header(out: &mut String, key: &str, value: &str) {
    out.push('[');
    out.push_str(key);
    out.push_str(" \"");
    out.push_str(value);
    out.push_str("\"]\n");
}

// ──────────────────────────────────────────────────────────────────────────────
//  SAN → ChessMove parser (used by rust_decode_pgn)
// ──────────────────────────────────────────────────────────────────────────────

/// Parse a SAN string into a ChessMove given the board state and list of legal moves.
/// Handles castling, disambiguation, captures, promotions, check/mate suffixes.
#[inline]
fn parse_san_move(board: &Board, san: &str, legal_moves: &[ChessMove]) -> Result<ChessMove, String> {
    let bytes = san.as_bytes();
    // Strip check/mate suffixes (+, #)
    let mut len = bytes.len();
    while len > 0 && (bytes[len - 1] == b'+' || bytes[len - 1] == b'#') {
        len -= 1;
    }
    let s = &bytes[..len];

    // ── Castling ──────────────────────────────────────────────────
    if s.starts_with(b"O-O-O") || s.starts_with(b"0-0-0") {
        for &m in legal_moves {
            let src = m.get_source();
            if board.piece_on(src) != Some(Piece::King) { continue; }
            let fdiff = m.get_dest().get_file().to_index() as i32 - src.get_file().to_index() as i32;
            if fdiff < -1 { return Ok(m); }
        }
        return Err(format!("No queenside castle for: {}", san));
    }
    if s.starts_with(b"O-O") || s.starts_with(b"0-0") {
        for &m in legal_moves {
            let src = m.get_source();
            if board.piece_on(src) != Some(Piece::King) { continue; }
            let fdiff = m.get_dest().get_file().to_index() as i32 - src.get_file().to_index() as i32;
            if fdiff > 1 { return Ok(m); }
        }
        return Err(format!("No kingside castle for: {}", san));
    }

    // ── Parse from RIGHT: promotion, destination ─────────────────
    let mut i = len;
    let mut promotion: Option<Piece> = None;
    if i >= 2 && s[i - 2] == b'=' {
        promotion = Some(match s[i - 1] {
            b'Q' => Piece::Queen,  b'R' => Piece::Rook,
            b'B' => Piece::Bishop, b'N' => Piece::Knight,
            _ => return Err(format!("Bad promotion in: {}", san)),
        });
        i -= 2;
    }
    if i < 2 { return Err(format!("SAN too short: {}", san)); }
    let dst_rank = (s[i - 1] - b'1') as usize;
    let dst_file = (s[i - 2] - b'a') as usize;
    i -= 2;
    // Skip capture marker
    if i > 0 && s[i - 1] == b'x' { i -= 1; }

    // ── Parse from LEFT: piece letter ────────────────────────────
    let mut j = 0usize;
    let piece = match s.first() {
        Some(b'N') => { j = 1; Piece::Knight }
        Some(b'B') => { j = 1; Piece::Bishop }
        Some(b'R') => { j = 1; Piece::Rook }
        Some(b'Q') => { j = 1; Piece::Queen }
        Some(b'K') => { j = 1; Piece::King }
        _ => Piece::Pawn,
    };

    // ── Disambiguation (between piece letter and x/destination) ──
    let mut src_file: Option<usize> = None;
    let mut src_rank: Option<usize> = None;
    for &c in &s[j..i] {
        if c >= b'a' && c <= b'h' { src_file = Some((c - b'a') as usize); }
        else if c >= b'1' && c <= b'8' { src_rank = Some((c - b'1') as usize); }
    }

    // ── Find matching legal move ─────────────────────────────────
    let dst = Square::make_square(Rank::from_index(dst_rank), File::from_index(dst_file));
    for &m in legal_moves {
        if m.get_dest() != dst { continue; }
        if m.get_promotion() != promotion { continue; }
        let src = m.get_source();
        if board.piece_on(src) != Some(piece) { continue; }
        if let Some(f) = src_file {
            if src.get_file().to_index() != f { continue; }
        }
        if let Some(r) = src_rank {
            if src.get_rank().to_index() != r { continue; }
        }
        return Ok(m);
    }
    Err(format!("No legal move matches SAN: {}", san))
}

// ──────────────────────────────────────────────────────────────────────────────
//  Move sort key (v1/v2 backward compat only — v3 skips sort)
// ──────────────────────────────────────────────────────────────────────────────

#[inline(always)]
fn move_sort_key(m: &ChessMove) -> u32 {
    let sf = m.get_source().get_file().to_index() as u32;
    let sr = m.get_source().get_rank().to_index() as u32;
    let df = m.get_dest().get_file().to_index() as u32;
    let dr = m.get_dest().get_rank().to_index() as u32;
    let p: u32 = match m.get_promotion() {
        None              => 0,
        Some(Piece::Bishop) => 1,
        Some(Piece::Knight) => 2,
        Some(Piece::Queen)  => 3,
        Some(Piece::Rook)   => 4,
        _                   => 5,
    };
    ((((sf * 8 + sr) * 8 + df) * 8 + dr) * 6) + p
}

// ══════════════════════════════════════════════════════════════════════════════
//  Legal move generation — two paths
// ══════════════════════════════════════════════════════════════════════════════

/// v3: NO sort — chess crate's MoveGen order is deterministic for same Board.
/// Saves ~300ns per position by eliminating sort of 20–30 elements.
/// Uses unsafe fill to skip per-element bounds checks.
#[inline]
fn fill_legal_moves(board: &Board, buf: &mut Vec<ChessMove>) {
    buf.clear();
    let ptr = buf.as_mut_ptr();
    let mut count = 0usize;
    for m in MoveGen::new_legal(board) {
        unsafe { ptr.add(count).write(m); }
        count += 1;
    }
    unsafe { buf.set_len(count); }
}

/// v1/v2: sorted for backward compatibility
#[inline]
fn fill_sorted_legal_moves(board: &Board, buf: &mut Vec<ChessMove>) {
    fill_legal_moves(board, buf);
    buf.sort_unstable_by_key(|m| move_sort_key(m));
}

// ══════════════════════════════════════════════════════════════════════════════
//  Direct bit I/O — eliminates the intermediate 8× bit-buffer allocation
// ══════════════════════════════════════════════════════════════════════════════

/// Read `count` bits (1–7) from byte slice at `bit_offset`, MSB-first.
/// Requires ≥2 bytes of padding beyond the last data bit.
#[inline(always)]
fn read_bits(data: &[u8], bit_offset: usize, count: usize) -> usize {
    debug_assert!(count > 0 && count <= 8);
    let byte_idx = bit_offset >> 3;
    let bit_idx  = bit_offset & 7;
    let hi = unsafe { *data.get_unchecked(byte_idx) } as u32;
    let lo = unsafe { *data.get_unchecked(byte_idx + 1) } as u32;
    let word = (hi << 8) | lo;
    ((word >> (16 - bit_idx - count)) & ((1u32 << count) - 1)) as usize
}

/// Streaming bit writer — packs move indices directly into output bytes.
/// Eliminates the all_bits Vec<u8> (8× memory savings for 1 MB decode).
struct BitWriter {
    bytes: Vec<u8>,
    current: u32,   // accumulator for partial byte
    bit_count: u32,  // bits in current (0–7)
    total_bits: usize,
}

impl BitWriter {
    #[inline]
    fn new(capacity_bits: usize) -> Self {
        Self {
            bytes: Vec::with_capacity(capacity_bits / 8 + 1),
            current: 0,
            bit_count: 0,
            total_bits: 0,
        }
    }

    /// Write `count` bits (1–7) from `value`, MSB-first, directly into output.
    /// At most one byte emitted per call (since max accumulated = 7+7 = 14 < 16).
    #[inline(always)]
    fn write(&mut self, value: usize, count: usize) {
        let combined = (self.current << count) | (value as u32 & ((1u32 << count) - 1));
        let total = self.bit_count + count as u32;
        if total >= 8 {
            let shift = total - 8;
            self.bytes.push((combined >> shift) as u8);
            self.current = combined & ((1u32 << shift) - 1);
            self.bit_count = shift;
        } else {
            self.current = combined;
            self.bit_count = total;
        }
        self.total_bits += count;
    }

    #[inline]
    fn into_bytes(self) -> Vec<u8> {
        self.bytes
    }
}

// ──────────────────────────────────────────────────────────────────────────────
//  Parse UCI string → ChessMove
// ──────────────────────────────────────────────────────────────────────────────

#[inline]
fn parse_uci(uci: &str) -> Result<ChessMove, String> {
    let b = uci.as_bytes();
    if b.len() < 4 {
        return Err(format!("UCI too short: {}", uci));
    }
    let src = Square::make_square(
        Rank::from_index((b[1] - b'1') as usize),
        File::from_index((b[0] - b'a') as usize),
    );
    let dst = Square::make_square(
        Rank::from_index((b[3] - b'1') as usize),
        File::from_index((b[2] - b'a') as usize),
    );
    let promotion = if b.len() > 4 {
        Some(match b[4] {
            b'q' | b'Q' => Piece::Queen,
            b'r' | b'R' => Piece::Rook,
            b'b' | b'B' => Piece::Bishop,
            b'n' | b'N' => Piece::Knight,
            c => return Err(format!("Unknown promo: {}", c as char)),
        })
    } else {
        None
    };
    Ok(ChessMove::new(src, dst, promotion))
}

#[inline(always)]
fn floor_log2(n: usize) -> usize {
    if n < 2 { return 0; }
    (usize::BITS as usize) - 1 - (n.leading_zeros() as usize)
}

// ╔═══════════════════════════════════════════════════════════════════════════╗
// ║  v3 ENCODE                                                               ║
// ║  • No sort (saves ~300ns/move)                                           ║
// ║  • Direct bit reading from padded bytes (no 8× bit buffer)               ║
// ║  • Unsafe shuffle + fill (no bounds checks)                              ║
// ║  • SplitMix64 PRNG                                                       ║
// ╚═══════════════════════════════════════════════════════════════════════════╝

#[pyfunction]
fn rust_encode(file_bytes: Vec<u8>) -> PyResult<Vec<(u64, usize, Vec<String>)>> {
    if file_bytes.is_empty() {
        return Err(PyValueError::new_err("Input file is empty"));
    }

    let start = Instant::now();
    let data_bits = file_bytes.len() * 8;

    // Pad with 4 zero bytes so read_bits can safely read 2 bytes at any offset
    let mut data = Vec::with_capacity(file_bytes.len() + 4);
    data.extend_from_slice(&file_bytes);
    data.extend_from_slice(&[0u8; 4]);

    let mut legal_moves: Vec<ChessMove> = Vec::with_capacity(256);
    let mut game_results: Vec<(u64, usize, Vec<ChessMove>)> = Vec::new();
    let mut current_moves: Vec<ChessMove> = Vec::with_capacity(160);
    let mut bit_idx: usize = 0;
    let mut board = Board::default();

    let mut seed_rng = SplitMix64::new(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64,
    );
    let mut seed: u64 = (seed_rng.next_u64() % 1_000_000) + 1;
    let mut move_rng = SplitMix64::new(seed);
    let mut total_moves: u64 = 0;

    while bit_idx < data_bits {
        fill_legal_moves(&board, &mut legal_moves);

        if legal_moves.len() <= 1 {
            if let Some(&m) = legal_moves.first() {
                current_moves.push(m);
                board = board.make_move_new(m);
                total_moves += 1;
            }
            if legal_moves.is_empty() || board.status() != BoardStatus::Ongoing {
                game_results.push((seed, data_bits, std::mem::take(&mut current_moves)));
                current_moves = Vec::with_capacity(160);
                board = Board::default();
                seed = (seed_rng.next_u64() % 1_000_000) + 1;
                move_rng = SplitMix64::new(seed);
            }
            continue;
        }

        let max_bits = floor_log2(legal_moves.len());
        let move_index = read_bits(&data, bit_idx, max_bits);

        fast_shuffle(&mut legal_moves, &mut move_rng);

        let chosen = legal_moves[move_index];
        current_moves.push(chosen);
        board = board.make_move_new(chosen);
        total_moves += 1;
        bit_idx += max_bits;

        if bit_idx >= data_bits { break; }

        if board.status() != BoardStatus::Ongoing || current_moves.len() >= 150 {
            game_results.push((seed, data_bits, std::mem::take(&mut current_moves)));
            current_moves = Vec::with_capacity(160);
            board = Board::default();
            seed = (seed_rng.next_u64() % 1_000_000) + 1;
            move_rng = SplitMix64::new(seed);
        }
    }

    if !current_moves.is_empty() {
        game_results.push((seed, data_bits, current_moves));
    }

    // Bulk-convert ChessMove → UCI strings (outside hot loop)
    let results: Vec<(u64, usize, Vec<String>)> = game_results
        .into_iter()
        .map(|(s, b, moves)| {
            let uci: Vec<String> = moves.iter().map(|m| format_move_uci(m)).collect();
            (s, b, uci)
        })
        .collect();

    let elapsed = start.elapsed();
    let mps = if elapsed.as_secs_f64() > 0.0 { total_moves as f64 / elapsed.as_secs_f64() } else { f64::INFINITY };
    eprintln!(
        "[rust v3] Encoded {} bytes in {:.3}s — {} moves, {} games — {:.0} m/s",
        file_bytes.len(), elapsed.as_secs_f64(), total_moves, results.len(), mps,
    );
    Ok(results)
}

// ╔═══════════════════════════════════════════════════════════════════════════╗
// ║  v3 ENCODE → PGN  (encode + SAN + PGN formatting in one Rust pass)      ║
// ║  Eliminates the ~2.5 s Python PGN-wrapping bottleneck for 1 MB files.   ║
// ╚═══════════════════════════════════════════════════════════════════════════╝

#[pyfunction]
#[pyo3(signature = (file_bytes, engine="rust-v3", expiry_time=None, custom_headers=None))]
fn rust_encode_pgn(
    file_bytes: Vec<u8>,
    engine: &str,
    expiry_time: Option<i64>,
    custom_headers: Option<HashMap<String, String>>,
) -> PyResult<String> {
    if file_bytes.is_empty() {
        return Err(PyValueError::new_err("Input file is empty"));
    }

    let start = Instant::now();
    let data_bits = file_bytes.len() * 8;
    let file_len = file_bytes.len();

    // Pad with 4 zero bytes so read_bits can safely overshoot
    let mut data = Vec::with_capacity(file_len + 4);
    data.extend_from_slice(&file_bytes);
    data.extend_from_slice(&[0u8; 4]);

    let empty_hdr = HashMap::new();
    let headers = custom_headers.as_ref().unwrap_or(&empty_hdr);

    let mut legal_moves: Vec<ChessMove> = Vec::with_capacity(256);
    let mut all_games: Vec<(u64, Vec<String>)> = Vec::new();
    let mut cur_sans: Vec<String> = Vec::with_capacity(160);
    let mut bit_idx: usize = 0;
    let mut board = Board::default();

    let mut seed_rng = SplitMix64::new(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos() as u64,
    );
    let mut seed: u64 = (seed_rng.next_u64() % 1_000_000) + 1;
    let mut move_rng = SplitMix64::new(seed);
    let mut total_moves: u64 = 0;

    // ── Encode loop (mirrors rust_encode but collects SAN strings) ─────────
    while bit_idx < data_bits {
        fill_legal_moves(&board, &mut legal_moves);

        if legal_moves.len() <= 1 {
            if let Some(&m) = legal_moves.first() {
                let mut san = san_base(&board, m, &legal_moves);
                board = board.make_move_new(m);
                append_check_suffix(&mut san, &board);
                cur_sans.push(san);
                total_moves += 1;
            }
            if legal_moves.is_empty() || board.status() != BoardStatus::Ongoing {
                all_games.push((seed, std::mem::take(&mut cur_sans)));
                cur_sans = Vec::with_capacity(160);
                board = Board::default();
                seed = (seed_rng.next_u64() % 1_000_000) + 1;
                move_rng = SplitMix64::new(seed);
            }
            continue;
        }

        let max_bits = floor_log2(legal_moves.len());
        let move_index = read_bits(&data, bit_idx, max_bits);
        fast_shuffle(&mut legal_moves, &mut move_rng);

        let chosen = legal_moves[move_index];
        let mut san = san_base(&board, chosen, &legal_moves);
        board = board.make_move_new(chosen);
        append_check_suffix(&mut san, &board);
        cur_sans.push(san);
        total_moves += 1;
        bit_idx += max_bits;

        if bit_idx >= data_bits { break; }

        if board.status() != BoardStatus::Ongoing || cur_sans.len() >= 150 {
            all_games.push((seed, std::mem::take(&mut cur_sans)));
            cur_sans = Vec::with_capacity(160);
            board = Board::default();
            seed = (seed_rng.next_u64() % 1_000_000) + 1;
            move_rng = SplitMix64::new(seed);
        }
    }

    if !cur_sans.is_empty() {
        all_games.push((seed, cur_sans));
    }

    // ── Format PGN ─────────────────────────────────────────────────────────
    let num_games = all_games.len();
    let mut pgn = String::with_capacity(total_moves as usize * 6 + num_games * 300);
    let data_bits_str = data_bits.to_string();

    for (game_idx, (game_seed, sans)) in all_games.iter().enumerate() {
        if game_idx > 0 { pgn.push_str("\n\n"); }

        // Seven Tag Roster + custom overrides
        let event  = headers.get("Event").map(|s| s.as_str()).unwrap_or("?");
        let site   = headers.get("Site").map(|s| s.as_str()).unwrap_or("?");
        let date   = headers.get("Date").map(|s| s.as_str()).unwrap_or("????.??.??");
        let white  = headers.get("White").map(|s| s.as_str()).unwrap_or("?");
        let black  = headers.get("Black").map(|s| s.as_str()).unwrap_or("?");
        let result = headers.get("Result").map(|s| s.as_str()).unwrap_or("*");

        let round_owned;
        let round = if let Some(r) = headers.get("Round") {
            r.as_str()
        } else if game_idx > 0 {
            round_owned = (game_idx + 1).to_string();
            &round_owned
        } else {
            "?"
        };

        write_pgn_header(&mut pgn, "Event", event);
        write_pgn_header(&mut pgn, "Site", site);
        write_pgn_header(&mut pgn, "Date", date);
        write_pgn_header(&mut pgn, "Round", round);
        write_pgn_header(&mut pgn, "White", white);
        write_pgn_header(&mut pgn, "Black", black);
        write_pgn_header(&mut pgn, "Result", result);
        write_pgn_header(&mut pgn, "Seed", &game_seed.to_string());
        write_pgn_header(&mut pgn, "DataBits", &data_bits_str);
        write_pgn_header(&mut pgn, "Engine", engine);

        if let Some(exp) = expiry_time {
            write_pgn_header(&mut pgn, "ExpiryTime", &exp.to_string());
        }

        pgn.push('\n'); // blank line between headers and move text

        // Move text with move numbers + ~80-col wrapping
        let mut col: usize = 0;
        for (i, san) in sans.iter().enumerate() {
            let is_white = i % 2 == 0;
            let move_num = i / 2 + 1;
            let num_w = if move_num < 10 { 1 }
                        else if move_num < 100 { 2 }
                        else if move_num < 1000 { 3 } else { 4 };
            let tok_len = if is_white { num_w + 2 + san.len() } else { san.len() };
            let need = if col == 0 { tok_len } else { 1 + tok_len };

            if col > 0 && col + need > 80 {
                pgn.push('\n');
                col = 0;
            }
            if col > 0 { pgn.push(' '); col += 1; }

            if is_white {
                let _ = write!(pgn, "{}. ", move_num);
            }
            pgn.push_str(san);
            col += tok_len;
        }

        // Result terminator
        let rlen = result.len();
        if col > 0 && col + 1 + rlen > 80 {
            pgn.push('\n');
        } else if col > 0 {
            pgn.push(' ');
        }
        pgn.push_str(result);
    }

    let elapsed = start.elapsed();
    let mps = if elapsed.as_secs_f64() > 0.0 {
        total_moves as f64 / elapsed.as_secs_f64()
    } else { f64::INFINITY };
    eprintln!(
        "[rust v3] Encode+PGN {} bytes in {:.3}s — {} moves, {} games — {:.0} m/s",
        file_len, elapsed.as_secs_f64(), total_moves, num_games, mps,
    );

    Ok(pgn)
}

// ╔═══════════════════════════════════════════════════════════════════════════╗
// ║  DECODE — v1/v2/v3 dispatch with BitWriter (no intermediate bit Vec)     ║
// ╚═══════════════════════════════════════════════════════════════════════════╝

#[pyfunction]
#[pyo3(signature = (games, data_bits_count, version = 3))]
fn rust_decode(games: Vec<(u64, Vec<String>)>, data_bits_count: usize, version: u32) -> PyResult<Vec<u8>> {
    if games.is_empty() {
        return Err(PyValueError::new_err("No games to decode"));
    }
    if data_bits_count == 0 || data_bits_count % 8 != 0 {
        return Err(PyValueError::new_err(format!(
            "Invalid data_bits_count: {}", data_bits_count
        )));
    }

    let start = Instant::now();

    // Pre-parse all UCI → (ChessMove, u16 key)
    let parsed_games: Vec<(u64, Vec<(ChessMove, u16)>)> = games
        .iter()
        .map(|(seed, moves_uci)| {
            let parsed: Vec<(ChessMove, u16)> = moves_uci
                .iter()
                .map(|uci| {
                    let m = parse_uci(uci).expect("Invalid UCI");
                    (m, move_to_u16(&m))
                })
                .collect();
            (*seed, parsed)
        })
        .collect();

    let mut writer = BitWriter::new(data_bits_count);
    let mut legal_moves: Vec<ChessMove> = Vec::with_capacity(256);
    let mut total_moves: u64 = 0;

    match version {
        // ── v3: no sort + SplitMix64 (fastest) ──────────────────
        v if v >= 3 => {
            'v3: for (seed, moves) in &parsed_games {
                let mut board = Board::default();
                let mut move_rng = SplitMix64::new(*seed);

                for &(target_move, target_key) in moves {
                    fill_legal_moves(&board, &mut legal_moves);
                    total_moves += 1;

                    if legal_moves.len() <= 1 {
                        board = board.make_move_new(target_move);
                        continue;
                    }

                    fast_shuffle(&mut legal_moves, &mut move_rng);

                    let max_bits = floor_log2(legal_moves.len());
                    let idx = legal_moves.iter()
                        .position(|m| move_to_u16(m) == target_key)
                        .ok_or_else(|| PyValueError::new_err("Move not found"))?;

                    writer.write(idx, max_bits);
                    board = board.make_move_new(target_move);
                    if writer.total_bits >= data_bits_count { break 'v3; }
                }
            }
        }
        // ── v2: sorted + SplitMix64 ─────────────────────────────
        2 => {
            'v2: for (seed, moves) in &parsed_games {
                let mut board = Board::default();
                let mut move_rng = SplitMix64::new(*seed);

                for &(target_move, target_key) in moves {
                    fill_sorted_legal_moves(&board, &mut legal_moves);
                    total_moves += 1;

                    if legal_moves.len() <= 1 {
                        board = board.make_move_new(target_move);
                        continue;
                    }

                    fast_shuffle(&mut legal_moves, &mut move_rng);

                    let max_bits = floor_log2(legal_moves.len());
                    let idx = legal_moves.iter()
                        .position(|m| move_to_u16(m) == target_key)
                        .ok_or_else(|| PyValueError::new_err("Move not found"))?;

                    writer.write(idx, max_bits);
                    board = board.make_move_new(target_move);
                    if writer.total_bits >= data_bits_count { break 'v2; }
                }
            }
        }
        // ── v1: sorted + ChaCha8Rng (backward compat) ───────────
        _ => {
            'v1: for (seed, moves) in &parsed_games {
                let mut board = Board::default();
                let mut move_rng = ChaCha8Rng::seed_from_u64(*seed);

                for &(target_move, target_key) in moves {
                    fill_sorted_legal_moves(&board, &mut legal_moves);
                    total_moves += 1;

                    if legal_moves.len() <= 1 {
                        board = board.make_move_new(target_move);
                        continue;
                    }

                    legal_moves.shuffle(&mut move_rng);

                    let max_bits = floor_log2(legal_moves.len());
                    let idx = legal_moves.iter()
                        .position(|m| move_to_u16(m) == target_key)
                        .ok_or_else(|| PyValueError::new_err("Move not found"))?;

                    writer.write(idx, max_bits);
                    board = board.make_move_new(target_move);
                    if writer.total_bits >= data_bits_count { break 'v1; }
                }
            }
        }
    }

    if writer.total_bits < data_bits_count {
        return Err(PyValueError::new_err(format!(
            "Not enough bits: {} < {}", writer.total_bits, data_bits_count
        )));
    }

    let bytes = writer.into_bytes();
    let elapsed = start.elapsed();
    let mps = if elapsed.as_secs_f64() > 0.0 { total_moves as f64 / elapsed.as_secs_f64() } else { f64::INFINITY };
    eprintln!(
        "[rust] Decoded {} bytes in {:.3}s — {} moves — {:.0} m/s (v{})",
        bytes.len(), elapsed.as_secs_f64(), total_moves, mps, version,
    );
    Ok(bytes)
}

// ╔═══════════════════════════════════════════════════════════════════════════╗
// ║  PGN TEXT PARSER — parse raw PGN string into (headers, SAN tokens)       ║
// ╚═══════════════════════════════════════════════════════════════════════════╝

struct PgnGame<'a> {
    headers: HashMap<String, String>,
    san_moves: Vec<&'a str>,
}

fn parse_pgn_header_line(line: &str) -> Option<(String, String)> {
    let trimmed = line.trim();
    if !trimmed.starts_with('[') || !trimmed.ends_with(']') {
        return None;
    }
    let inner = &trimmed[1..trimmed.len() - 1];
    let q1 = inner.find('"')?;
    let key = inner[..q1].trim().to_string();
    let rest = &inner[q1 + 1..];
    let q2 = rest.rfind('"')?;
    let value = rest[..q2].to_string();
    Some((key, value))
}

/// Parse a complete PGN text into a list of games.
/// Each game has headers (HashMap) and a Vec of &str SAN tokens.
fn parse_pgn_text(text: &str) -> Vec<PgnGame<'_>> {
    let mut games: Vec<PgnGame> = Vec::new();
    let mut headers = HashMap::new();
    let mut san_moves: Vec<&str> = Vec::new();
    let mut in_headers = false;

    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            if in_headers { in_headers = false; }
            continue;
        }
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            // New header line — if we were collecting move text, finalize previous game
            if !in_headers && (!headers.is_empty() || !san_moves.is_empty()) {
                games.push(PgnGame {
                    headers: std::mem::take(&mut headers),
                    san_moves: std::mem::take(&mut san_moves),
                });
            }
            in_headers = true;
            if let Some((k, v)) = parse_pgn_header_line(trimmed) {
                headers.insert(k, v);
            }
        } else {
            in_headers = false;
            for token in trimmed.split_whitespace() {
                // Skip move numbers: "1.", "12.", "1..."
                if token.ends_with('.') { continue; }
                if token.bytes().all(|b| b.is_ascii_digit() || b == b'.') { continue; }
                // Skip result tokens
                if token == "*" || token == "1-0" || token == "0-1" || token == "1/2-1/2" {
                    continue;
                }
                san_moves.push(token);
            }
        }
    }
    // Final game
    if !headers.is_empty() || !san_moves.is_empty() {
        games.push(PgnGame { headers, san_moves });
    }
    games
}

// ╔═══════════════════════════════════════════════════════════════════════════╗
// ║  DECODE from PGN text — PGN parse + SAN→move + bit extraction in Rust   ║
// ║  Eliminates the ~90 s Python PGN-parsing bottleneck for 1 MB files.     ║
// ╚═══════════════════════════════════════════════════════════════════════════╝

#[pyfunction]
fn rust_decode_pgn(pgn_text: &str) -> PyResult<(Vec<u8>, HashMap<String, String>)> {
    let start = Instant::now();

    let parsed = parse_pgn_text(pgn_text);
    if parsed.is_empty() {
        return Err(PyValueError::new_err("No valid games found in PGN"));
    }

    let first_headers = parsed[0].headers.clone();
    let data_bits_count: usize = first_headers
        .get("DataBits")
        .ok_or_else(|| PyValueError::new_err("DataBits header missing"))?
        .parse()
        .map_err(|_| PyValueError::new_err("Invalid DataBits value"))?;
    if data_bits_count == 0 || data_bits_count % 8 != 0 {
        return Err(PyValueError::new_err(format!(
            "Invalid data_bits_count: {}", data_bits_count
        )));
    }

    let engine = first_headers.get("Engine").map(|s| s.as_str()).unwrap_or("rust-v3");
    let version: u32 = match engine {
        "rust-v1" => 1,
        "rust-v2" => 2,
        _ => 3,
    };

    let mut writer = BitWriter::new(data_bits_count);
    let mut legal_moves: Vec<ChessMove> = Vec::with_capacity(256);
    let mut total_moves: u64 = 0;

    match version {
        // ── v3: no sort + SplitMix64 ─────────────────────────────
        v if v >= 3 => {
            'v3p: for game in &parsed {
                let seed: u64 = game.headers.get("Seed")
                    .and_then(|s| s.parse().ok()).unwrap_or(1);
                let mut board = Board::default();
                let mut move_rng = SplitMix64::new(seed);

                for san in &game.san_moves {
                    fill_legal_moves(&board, &mut legal_moves);
                    total_moves += 1;

                    if legal_moves.len() <= 1 {
                        if let Some(&m) = legal_moves.first() {
                            board = board.make_move_new(m);
                        }
                        continue;
                    }

                    fast_shuffle(&mut legal_moves, &mut move_rng);

                    let target = parse_san_move(&board, san, &legal_moves)
                        .map_err(|e| PyValueError::new_err(e))?;
                    let target_key = move_to_u16(&target);
                    let idx = legal_moves.iter()
                        .position(|m| move_to_u16(m) == target_key)
                        .ok_or_else(|| PyValueError::new_err(
                            format!("Move not in shuffled list: {}", san)
                        ))?;

                    let max_bits = floor_log2(legal_moves.len());
                    writer.write(idx, max_bits);
                    board = board.make_move_new(target);
                    if writer.total_bits >= data_bits_count { break 'v3p; }
                }
            }
        }
        // ── v2: sorted + SplitMix64 ──────────────────────────────
        2 => {
            'v2p: for game in &parsed {
                let seed: u64 = game.headers.get("Seed")
                    .and_then(|s| s.parse().ok()).unwrap_or(1);
                let mut board = Board::default();
                let mut move_rng = SplitMix64::new(seed);

                for san in &game.san_moves {
                    fill_sorted_legal_moves(&board, &mut legal_moves);
                    total_moves += 1;

                    if legal_moves.len() <= 1 {
                        if let Some(&m) = legal_moves.first() {
                            board = board.make_move_new(m);
                        }
                        continue;
                    }

                    fast_shuffle(&mut legal_moves, &mut move_rng);

                    let target = parse_san_move(&board, san, &legal_moves)
                        .map_err(|e| PyValueError::new_err(e))?;
                    let target_key = move_to_u16(&target);
                    let idx = legal_moves.iter()
                        .position(|m| move_to_u16(m) == target_key)
                        .ok_or_else(|| PyValueError::new_err(
                            format!("Move not in shuffled list: {}", san)
                        ))?;

                    let max_bits = floor_log2(legal_moves.len());
                    writer.write(idx, max_bits);
                    board = board.make_move_new(target);
                    if writer.total_bits >= data_bits_count { break 'v2p; }
                }
            }
        }
        // ── v1: sorted + ChaCha8Rng ──────────────────────────────
        _ => {
            'v1p: for game in &parsed {
                let seed: u64 = game.headers.get("Seed")
                    .and_then(|s| s.parse().ok()).unwrap_or(1);
                let mut board = Board::default();
                let mut move_rng = ChaCha8Rng::seed_from_u64(seed);

                for san in &game.san_moves {
                    fill_sorted_legal_moves(&board, &mut legal_moves);
                    total_moves += 1;

                    if legal_moves.len() <= 1 {
                        if let Some(&m) = legal_moves.first() {
                            board = board.make_move_new(m);
                        }
                        continue;
                    }

                    legal_moves.shuffle(&mut move_rng);

                    let target = parse_san_move(&board, san, &legal_moves)
                        .map_err(|e| PyValueError::new_err(e))?;
                    let target_key = move_to_u16(&target);
                    let idx = legal_moves.iter()
                        .position(|m| move_to_u16(m) == target_key)
                        .ok_or_else(|| PyValueError::new_err(
                            format!("Move not in shuffled list: {}", san)
                        ))?;

                    let max_bits = floor_log2(legal_moves.len());
                    writer.write(idx, max_bits);
                    board = board.make_move_new(target);
                    if writer.total_bits >= data_bits_count { break 'v1p; }
                }
            }
        }
    }

    if writer.total_bits < data_bits_count {
        return Err(PyValueError::new_err(format!(
            "Not enough bits: {} < {}", writer.total_bits, data_bits_count
        )));
    }

    let bytes = writer.into_bytes();
    let elapsed = start.elapsed();
    let mps = if elapsed.as_secs_f64() > 0.0 {
        total_moves as f64 / elapsed.as_secs_f64()
    } else { f64::INFINITY };
    eprintln!(
        "[rust] Decode+PGN-parse {} bytes in {:.3}s — {} moves — {:.0} m/s (v{})",
        bytes.len(), elapsed.as_secs_f64(), total_moves, mps, version,
    );

    Ok((bytes, first_headers))
}

// ╔═══════════════════════════════════════════════════════════════════════════╗
// ║  BENCHMARK                                                               ║
// ╚═══════════════════════════════════════════════════════════════════════════╝

#[pyfunction]
fn benchmark(size_bytes: usize) -> PyResult<String> {
    let mut rng = SplitMix64::new(size_bytes as u64 ^ 0xdeadbeef);
    let data: Vec<u8> = (0..size_bytes).map(|_| rng.next_u64() as u8).collect();

    let t0 = Instant::now();
    let games = rust_encode(data.clone())?;
    let enc_t = t0.elapsed();

    let bits = games[0].1;
    let input: Vec<(u64, Vec<String>)> = games.iter().map(|(s, _, m)| (*s, m.clone())).collect();

    let t0 = Instant::now();
    let decoded = rust_decode(input, bits, 3)?;
    let dec_t = t0.elapsed();

    let ok = data == decoded;
    let moves: usize = games.iter().map(|(_, _, m)| m.len()).sum();
    let enc_mps = moves as f64 / enc_t.as_secs_f64();
    let dec_mps = moves as f64 / dec_t.as_secs_f64();

    Ok(format!(
        "Benchmark: {} bytes\n\
         Encode: {:.3}s ({:.0} moves/sec)\n\
         Decode: {:.3}s ({:.0} moves/sec)\n\
         Total:  {:.3}s\n\
         Moves:  {}\n\
         Games:  {}\n\
         Verified: {}",
        size_bytes,
        enc_t.as_secs_f64(), enc_mps,
        dec_t.as_secs_f64(), dec_mps,
        enc_t.as_secs_f64() + dec_t.as_secs_f64(),
        moves, games.len(), ok,
    ))
}

// ╔═══════════════════════════════════════════════════════════════════════════╗
// ║  PyO3 MODULE                                                             ║
// ╚═══════════════════════════════════════════════════════════════════════════╝

#[pymodule]
fn chess_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_encode, m)?)?;
    m.add_function(wrap_pyfunction!(rust_encode_pgn, m)?)?;
    m.add_function(wrap_pyfunction!(rust_decode, m)?)?;
    m.add_function(wrap_pyfunction!(rust_decode_pgn, m)?)?;
    m.add_function(wrap_pyfunction!(benchmark, m)?)?;
    Ok(())
}
