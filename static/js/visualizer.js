let game = new Chess(); // Remove import and use global Chess object
let board = null;
let currentMoveIndex = -1;
let moves = [];
let autoPlayInterval = null;
let isAutoPlaying = false;

function onDragStart(source, piece, position, orientation) {
    return false;
}

function initializeBoard() {
    const config = {
        draggable: true,
        position: 'start',
        onDragStart: onDragStart,
        pieceTheme: 'https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png'
    };
    board = Chessboard('board', config);
    $(window).resize(() => {
        board.resize();
    });
}

function loadPGN(pgn) {
    try {
        pauseAutoPlay();
        const parsedMoves = parsePGNMoves(pgn);
        if (parsedMoves.length > 0) {
            moves = parsedMoves;
            currentMoveIndex = -1;
            updateBoard();
            displayMoves();
            updateControls();
            return true;
        }
        return false;
    } catch (error) {
        console.error('Error loading PGN:', error);
        return false;
    }
}

function stripHeadersAndComments(pgnText) {
    let body = pgnText.replace(/\r/g, '\n');
    body = body
        .split('\n')
        .filter((line) => !line.trim().startsWith('['))
        .join(' ');
    body = body.replace(/\{[^}]*\}/g, ' ');
    body = body.replace(/\([^)]*\)/g, ' ');
    body = body.replace(/\$\d+/g, ' ');
    body = body.replace(/\d+\.(\.\.\.)?/g, ' ');
    return body;
}

function parsePGNMoves(pgnText) {
    const trimmed = (pgnText || '').trim();
    if (!trimmed) {
        return [];
    }

    const parsed = [];

    const game = new Chess();
    if (game.load_pgn(trimmed)) {
        return game.history({ verbose: true });
    }

    const fallbackGame = new Chess();
    const body = stripHeadersAndComments(trimmed);
    const tokens = body.split(/\s+/).filter(Boolean);

    for (const token of tokens) {
        if (token === '1-0' || token === '0-1' || token === '1/2-1/2' || token === '*') {
            continue;
        }

        const move = fallbackGame.move(token, { sloppy: true });
        if (move) {
            parsed.push(move);
        } else {
            // In fallback mode, reject the entire PGN if any SAN token is invalid.
            return [];
        }
    }

    return parsed;
}

function updateBoard() {
    game = new Chess();
    if (currentMoveIndex >= 0) {
        for (let i = 0; i <= currentMoveIndex; i++) {
            const applied = game.move(moves[i].san, { sloppy: true });
            if (!applied && moves[i].from && moves[i].to) {
                game.move({ from: moves[i].from, to: moves[i].to, promotion: moves[i].promotion });
            }
        }
    }
    board.position(game.fen());
}

function displayMoves() {
    const movesList = document.getElementById('movesList');
    movesList.innerHTML = '';
    
    moves.forEach((move, index) => {
        const moveElement = document.createElement('div');
        moveElement.className = 'move-item';
        moveElement.innerHTML = `
            <span class="move-number">${Math.floor(index/2) + 1}.</span>
            <span class="move-notation">${move.san}</span>
        `;
        moveElement.addEventListener('click', () => {
            pauseAutoPlay();
            currentMoveIndex = index;
            updateBoard();
            highlightCurrentMove();
            updateControls();
        });
        movesList.appendChild(moveElement);
    });
}

function highlightCurrentMove() {
    const moveItems = document.querySelectorAll('.move-item');
    moveItems.forEach((item, index) => {
        item.classList.toggle('active', index === currentMoveIndex);
    });
}

function enableControls() {
    updateControls();
}

function updateControls() {
    const hasMoves = moves.length > 0;
    const atStart = currentMoveIndex <= -1;
    const atEnd = currentMoveIndex >= moves.length - 1;

    document.getElementById('prevBtn').disabled = !hasMoves || atStart;
    document.getElementById('nextBtn').disabled = !hasMoves || atEnd;
    document.getElementById('playBtn').disabled = !hasMoves || isAutoPlaying || atEnd;
    document.getElementById('pauseBtn').disabled = !isAutoPlaying;
}

function previousMove() {
    if (currentMoveIndex > -1) {
        pauseAutoPlay();
        currentMoveIndex--;
        updateBoard();
        highlightCurrentMove();
        updateControls();
    }
}

function nextMove() {
    if (currentMoveIndex < moves.length - 1) {
        currentMoveIndex++;
        updateBoard();
        highlightCurrentMove();
        updateControls();
    }
}

function startAutoPlay() {
    if (!moves.length || isAutoPlaying || currentMoveIndex >= moves.length - 1) {
        updateControls();
        return;
    }

    isAutoPlaying = true;
    updateControls();

    autoPlayInterval = setInterval(() => {
        if (currentMoveIndex < moves.length - 1) {
            nextMove();
        } else {
            pauseAutoPlay();
        }
    }, 1000);
}

function pauseAutoPlay() {
    if (autoPlayInterval) {
        clearInterval(autoPlayInterval);
        autoPlayInterval = null;
    }

    isAutoPlaying = false;
    updateControls();
}

document.addEventListener('DOMContentLoaded', () => {
    initializeBoard();
    
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.pgn';
    fileInput.className = 'file-input';
    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const pgn = e.target.result;
                if (loadPGN(pgn)) {
                } else {
                    alert('Invalid PGN file');
                }
            };
            reader.readAsText(file);
        }
    });
    
    const uploadContainer = document.createElement('div');
    uploadContainer.className = 'file-upload';
    uploadContainer.innerHTML = `
        <label for="pgnFile" class="file-label">
            <i class="fas fa-upload"></i> Upload PGN File
        </label>
        <p class="file-info">Supported format: .pgn</p>
    `;
    
    const boardContainer = document.querySelector('.board-container');
    boardContainer.insertBefore(uploadContainer, boardContainer.firstChild);
    uploadContainer.querySelector('.file-label').addEventListener('click', () => {
        fileInput.click();
    });
    
    document.getElementById('prevBtn').addEventListener('click', previousMove);
    document.getElementById('playBtn').addEventListener('click', startAutoPlay);
    document.getElementById('pauseBtn').addEventListener('click', pauseAutoPlay);
    document.getElementById('nextBtn').addEventListener('click', nextMove);

    updateControls();
});