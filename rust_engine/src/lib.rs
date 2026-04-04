use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use chess::{Board, MoveGen, ChessMove, BoardStatus, Square, Piece, Rank, File};
use rand::prelude::*;
use rand::seq::SliceRandom;
use rand_chacha::ChaCha8Rng;
use std::time::Instant;
use std::collections::HashMap;
const MAX_PLIES_PER_GAME: usize = 300;


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

    #[inline(always)]
    fn next_bounded(&mut self, bound: usize) -> usize {
        ((self.next_u64() as u128 * bound as u128) >> 64) as usize
    }
}


#[inline]
fn fast_shuffle(slice: &mut [ChessMove], rng: &mut SplitMix64) {
    let len = slice.len();
    if len <= 1 { return; }
    let ptr = slice.as_mut_ptr();
    for i in (1..len).rev() {
        let j = rng.next_bounded(i + 1);
        debug_assert!(j <= i && i < len);
        unsafe { std::ptr::swap(ptr.add(i), ptr.add(j)); }
    }
}


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


#[inline]
fn san_base(board: &Board, m: ChessMove, legal_moves: &[ChessMove]) -> String {
    let src = m.get_source();
    let dst = m.get_dest();
    let piece = board.piece_on(src).expect("san_base: no piece on source");

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

#[inline]
fn append_check_suffix(san: &mut String, board_after: &Board) {
    if board_after.checkers().popcnt() > 0 {
        if board_after.status() == BoardStatus::Checkmate { san.push('#'); }
        else { san.push('+'); }
    }
}

#[inline]
fn write_pgn_header(out: &mut String, key: &str, value: &str) {
    out.push('[');
    out.push_str(key);
    out.push_str(" \"");
    out.push_str(value);
    out.push_str("\"]\n");
}


#[inline]
fn parse_san_move(board: &Board, san: &str, legal_moves: &[ChessMove]) -> Result<ChessMove, String> {
    let bytes = san.as_bytes();
    let mut len = bytes.len();
    while len > 0 && (bytes[len - 1] == b'+' || bytes[len - 1] == b'#') {
        len -= 1;
    }
    let s = &bytes[..len];

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
    if i > 0 && s[i - 1] == b'x' { i -= 1; }

    let mut j = 0usize;
    let piece = match s.first() {
        Some(b'N') => { j = 1; Piece::Knight }
        Some(b'B') => { j = 1; Piece::Bishop }
        Some(b'R') => { j = 1; Piece::Rook }
        Some(b'Q') => { j = 1; Piece::Queen }
        Some(b'K') => { j = 1; Piece::King }
        _ => Piece::Pawn,
    };

    let mut src_file: Option<usize> = None;
    let mut src_rank: Option<usize> = None;
    for &c in &s[j..i] {
        if c >= b'a' && c <= b'h' { src_file = Some((c - b'a') as usize); }
        else if c >= b'1' && c <= b'8' { src_rank = Some((c - b'1') as usize); }
    }

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

#[inline]
fn fill_sorted_legal_moves(board: &Board, buf: &mut Vec<ChessMove>) {
    fill_legal_moves(board, buf);
    buf.sort_unstable_by_key(|m| move_sort_key(m));
}


#[inline(always)]
fn read_bits(data: &[u8], bit_offset: usize, count: usize) -> usize {
    debug_assert!(count > 0 && count <= 8);
    let byte_idx = bit_offset >> 3;
    let bit_idx  = bit_offset & 7;
    if byte_idx + 1 >= data.len() {
        return 0;
    }
    let hi = data[byte_idx] as u32;
    let lo = data[byte_idx + 1] as u32;
    let word = (hi << 8) | lo;
    ((word >> (16 - bit_idx - count)) & ((1u32 << count) - 1)) as usize
}

struct BitWriter {
    bytes: Vec<u8>,
    current: u32,
    bit_count: u32,
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

#[inline(always)]
fn derive_game_seed(root_seed: u64, game_number: u64) -> u64 {
    let mixed = root_seed ^ game_number.wrapping_mul(0x9e3779b97f4a7c15);
    let mut rng = SplitMix64::new(mixed);
    (rng.next_u64() % 1_000_000) + 1
}

#[inline(always)]
fn piece_value(piece: Piece) -> i32 {
    match piece {
        Piece::Pawn => 1,
        Piece::Knight | Piece::Bishop => 3,
        Piece::Rook => 5,
        Piece::Queen => 9,
        Piece::King => 0,
    }
}

#[inline(always)]
fn is_center_square(sq: Square) -> bool {
    let f = sq.get_file().to_index();
    let r = sq.get_rank().to_index();
    (f == 3 || f == 4) && (r == 3 || r == 4)
}

#[inline]
fn move_score(board: &Board, m: ChessMove) -> i32 {
    let mut score: i32 = 0;

    if let Some(piece) = board.piece_on(m.get_dest()) {
        score += piece_value(piece) * 100;
    }

    let after = board.make_move_new(m);
    if after.checkers().popcnt() > 0 {
        score += 50;
    }

    if let Some(Piece::King) = board.piece_on(m.get_source()) {
        let fdiff = m.get_dest().get_file().to_index() as i32
            - m.get_source().get_file().to_index() as i32;
        if fdiff.abs() > 1 {
            score += 12;
        }
    }

    if m.get_promotion().is_some() {
        score += 80;
    }

    if is_center_square(m.get_dest()) {
        score += 10;
    }

    score
}

#[inline]
fn reduce_to_guided_candidates(board: &Board, moves: &mut Vec<ChessMove>) {
    if moves.len() <= 1 {
        return;
    }

    moves.sort_unstable_by(|a, b| {
        let sa = move_score(board, *a);
        let sb = move_score(board, *b);
        sb.cmp(&sa).then_with(|| move_sort_key(a).cmp(&move_sort_key(b)))
    });

    let capacity = floor_log2(moves.len());
    let top_k = std::cmp::max(2, 1usize << capacity);
    moves.truncate(top_k);
}


#[pyfunction]
fn rust_encode(file_bytes: Vec<u8>) -> PyResult<Vec<(u64, usize, Vec<String>)>> {
    if file_bytes.is_empty() {
        return Err(PyValueError::new_err("Input file is empty"));
    }

    let start = Instant::now();
    let data_bits = file_bytes.len() * 8;

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

        if board.status() != BoardStatus::Ongoing || current_moves.len() >= MAX_PLIES_PER_GAME {
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

    let results: Vec<(u64, usize, Vec<String>)> = game_results
        .into_iter()
        .map(|(s, b, moves)| {
            let uci: Vec<String> = moves.iter().map(|m| format_move_uci(m)).collect();
            (s, b, uci)
        })
        .collect();

    let elapsed = start.elapsed();
    let mps = if elapsed.as_nanos() > 0 { total_moves as f64 / elapsed.as_secs_f64() } else { f64::INFINITY };
    eprintln!(
        "[rust v3] Encoded {} bytes in {:.3}s — {} moves, {} games — {:.0} m/s",
        file_bytes.len(), elapsed.as_secs_f64(), total_moves, results.len(), mps,
    );
    Ok(results)
}


#[pyfunction]
#[pyo3(signature = (file_bytes, engine="rust-v3", expiry_time=None, custom_headers=None, deterministic_seed_root=None, engine_guided=false, opening_book_uci=None))]
fn rust_encode_pgn(
    file_bytes: Vec<u8>,
    engine: &str,
    expiry_time: Option<i64>,
    custom_headers: Option<HashMap<String, String>>,
    deterministic_seed_root: Option<u64>,
    engine_guided: bool,
    opening_book_uci: Option<Vec<String>>,
) -> PyResult<String> {
    if file_bytes.is_empty() {
        return Err(PyValueError::new_err("Input file is empty"));
    }

    let start = Instant::now();
    let data_bits = file_bytes.len() * 8;
    let file_len = file_bytes.len();

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
    let mut game_counter: u64 = 1;
    let mut seed: u64 = if let Some(root) = deterministic_seed_root {
        derive_game_seed(root, game_counter)
    } else {
        (seed_rng.next_u64() % 1_000_000) + 1
    };
    let mut move_rng = SplitMix64::new(seed);
    let mut total_moves: u64 = 0;

    let opening_moves: Vec<ChessMove> = if let Some(opening) = opening_book_uci {
        let mut out = Vec::with_capacity(opening.len());
        for uci in opening {
            out.push(parse_uci(&uci).map_err(PyValueError::new_err)?);
        }
        out
    } else {
        Vec::new()
    };

    if !opening_moves.is_empty() {
        for &m in &opening_moves {
            fill_legal_moves(&board, &mut legal_moves);
            if !legal_moves.iter().any(|lm| *lm == m) {
                return Err(PyValueError::new_err("Opening camouflage contains illegal move"));
            }
            let mut san = san_base(&board, m, &legal_moves);
            board = board.make_move_new(m);
            append_check_suffix(&mut san, &board);
            cur_sans.push(san);
            total_moves += 1;
        }
    }

    while bit_idx < data_bits {
        fill_legal_moves(&board, &mut legal_moves);
        if engine_guided {
            reduce_to_guided_candidates(&board, &mut legal_moves);
        }

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
                game_counter += 1;
                seed = if let Some(root) = deterministic_seed_root {
                    derive_game_seed(root, game_counter)
                } else {
                    (seed_rng.next_u64() % 1_000_000) + 1
                };
                move_rng = SplitMix64::new(seed);

                if !opening_moves.is_empty() {
                    for &m in &opening_moves {
                        fill_legal_moves(&board, &mut legal_moves);
                        if !legal_moves.iter().any(|lm| *lm == m) {
                            return Err(PyValueError::new_err("Opening camouflage contains illegal move"));
                        }
                        let mut san = san_base(&board, m, &legal_moves);
                        board = board.make_move_new(m);
                        append_check_suffix(&mut san, &board);
                        cur_sans.push(san);
                        total_moves += 1;
                    }
                }
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

        if board.status() != BoardStatus::Ongoing || cur_sans.len() >= MAX_PLIES_PER_GAME {
            all_games.push((seed, std::mem::take(&mut cur_sans)));
            cur_sans = Vec::with_capacity(160);
            board = Board::default();
            game_counter += 1;
            seed = if let Some(root) = deterministic_seed_root {
                derive_game_seed(root, game_counter)
            } else {
                (seed_rng.next_u64() % 1_000_000) + 1
            };
            move_rng = SplitMix64::new(seed);

            if !opening_moves.is_empty() {
                for &m in &opening_moves {
                    fill_legal_moves(&board, &mut legal_moves);
                    if !legal_moves.iter().any(|lm| *lm == m) {
                        return Err(PyValueError::new_err("Opening camouflage contains illegal move"));
                    }
                    let mut san = san_base(&board, m, &legal_moves);
                    board = board.make_move_new(m);
                    append_check_suffix(&mut san, &board);
                    cur_sans.push(san);
                    total_moves += 1;
                }
            }
        }
    }

    if !cur_sans.is_empty() {
        all_games.push((seed, cur_sans));
    }

    let num_games = all_games.len();
    let mut pgn = String::with_capacity(total_moves as usize * 6 + num_games * 300);
    let data_bits_str = data_bits.to_string();

    for (game_idx, (game_seed, sans)) in all_games.iter().enumerate() {
        if game_idx > 0 { pgn.push_str("\n\n"); }

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

        for (key, value) in headers {
            let is_reserved = matches!(
                key.as_str(),
                "Event"
                    | "Site"
                    | "Date"
                    | "Round"
                    | "White"
                    | "Black"
                    | "Result"
                    | "Seed"
                    | "DataBits"
                    | "Engine"
                    | "ExpiryTime"
            );

            if !is_reserved {
                write_pgn_header(&mut pgn, key, value);
            }
        }

        pgn.push('\n');

        let mut col: usize = 0;
        for san in sans.iter() {
            let tok_len = san.len();
            let need = if col == 0 { tok_len } else { 1 + tok_len };

            if col > 0 && col + need > 80 {
                pgn.push('\n');
                col = 0;
            }
            if col > 0 { pgn.push(' '); col += 1; }

            pgn.push_str(san);
            col += tok_len;
        }

        let rlen = result.len();
        if col > 0 && col + 1 + rlen > 80 {
            pgn.push('\n');
        } else if col > 0 {
            pgn.push(' ');
        }
        pgn.push_str(result);
    }

    let elapsed = start.elapsed();
    let mps = if elapsed.as_nanos() > 0 {
        total_moves as f64 / elapsed.as_secs_f64()
    } else { f64::INFINITY };
    eprintln!(
        "[rust v3] Encode+PGN {} bytes in {:.3}s — {} moves, {} games — {:.0} m/s",
        file_len, elapsed.as_secs_f64(), total_moves, num_games, mps,
    );

    Ok(pgn)
}


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

    let parsed_games: Vec<(u64, Vec<(ChessMove, u16)>)> = games
        .iter()
        .map(|(seed, moves_uci)| -> PyResult<(u64, Vec<(ChessMove, u16)>)> {
            let parsed = moves_uci
                .iter()
                .map(|uci| {
                    parse_uci(uci)
                        .map(|m| (m, move_to_u16(&m)))
                        .map_err(|e| PyValueError::new_err(format!("Invalid UCI '{}': {}", uci, e)))
                })
                .collect::<PyResult<Vec<(ChessMove, u16)>>>()?;
            Ok((*seed, parsed))
        })
        .collect::<PyResult<Vec<(u64, Vec<(ChessMove, u16)>)>>>()?;

    let mut writer = BitWriter::new(data_bits_count);
    let mut legal_moves: Vec<ChessMove> = Vec::with_capacity(256);
    let mut total_moves: u64 = 0;

    match version {
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
    let mps = if elapsed.as_nanos() > 0 { total_moves as f64 / elapsed.as_secs_f64() } else { f64::INFINITY };
    eprintln!(
        "[rust] Decoded {} bytes in {:.3}s — {} moves — {:.0} m/s (v{})",
        bytes.len(), elapsed.as_secs_f64(), total_moves, mps, version,
    );
    Ok(bytes)
}


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
                if token.ends_with('.') { continue; }
                if token.bytes().all(|b| b.is_ascii_digit() || b == b'.') { continue; }
                if token == "*" || token == "1-0" || token == "0-1" || token == "1/2-1/2" {
                    continue;
                }
                san_moves.push(token);
            }
        }
    }
    if !headers.is_empty() || !san_moves.is_empty() {
        games.push(PgnGame { headers, san_moves });
    }
    games
}


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

    let engine_guided = first_headers
        .get("EngineGuided")
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false);

    let opening_moves: Vec<ChessMove> = if let Some(opening) = first_headers.get("OpeningBookUCI") {
        let mut parsed_opening = Vec::new();
        for token in opening.split_whitespace() {
            parsed_opening.push(parse_uci(token).map_err(PyValueError::new_err)?);
        }
        parsed_opening
    } else {
        Vec::new()
    };

    let mut writer = BitWriter::new(data_bits_count);
    let mut legal_moves: Vec<ChessMove> = Vec::with_capacity(256);
    let mut total_moves: u64 = 0;

    match version {
        v if v >= 3 => {
            'v3p: for game in &parsed {
                let seed: u64 = game.headers.get("Seed")
                    .and_then(|s| s.parse().ok()).unwrap_or(1);
                let mut board = Board::default();
                let mut move_rng = SplitMix64::new(seed);
                let mut opening_index = 0usize;

                for san in &game.san_moves {
                    if opening_index < opening_moves.len() {
                        fill_legal_moves(&board, &mut legal_moves);
                        total_moves += 1;
                        let target = parse_san_move(&board, san, &legal_moves)
                            .map_err(|e| PyValueError::new_err(e))?;
                        if move_to_u16(&target) != move_to_u16(&opening_moves[opening_index]) {
                            return Err(PyValueError::new_err("Opening camouflage mismatch"));
                        }
                        board = board.make_move_new(target);
                        opening_index += 1;
                        continue;
                    }

                    fill_legal_moves(&board, &mut legal_moves);
                    if engine_guided {
                        reduce_to_guided_candidates(&board, &mut legal_moves);
                    }
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
    let mps = if elapsed.as_nanos() > 0 {
        total_moves as f64 / elapsed.as_secs_f64()
    } else { f64::INFINITY };
    eprintln!(
        "[rust] Decode+PGN-parse {} bytes in {:.3}s — {} moves — {:.0} m/s (v{})",
        bytes.len(), elapsed.as_secs_f64(), total_moves, mps, version,
    );

    Ok((bytes, first_headers))
}


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
    let enc_mps = if enc_t.as_nanos() > 0 {
        moves as f64 / enc_t.as_secs_f64()
    } else {
        f64::INFINITY
    };
    let dec_mps = if dec_t.as_nanos() > 0 {
        moves as f64 / dec_t.as_secs_f64()
    } else {
        f64::INFINITY
    };

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


#[pymodule]
fn chess_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_encode, m)?)?;
    m.add_function(wrap_pyfunction!(rust_encode_pgn, m)?)?;
    m.add_function(wrap_pyfunction!(rust_decode, m)?)?;
    m.add_function(wrap_pyfunction!(rust_decode_pgn, m)?)?;
    m.add_function(wrap_pyfunction!(benchmark, m)?)?;
    Ok(())
}
