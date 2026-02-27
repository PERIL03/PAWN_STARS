// Initialize global variables
let game = new Chess(); // Remove import and use global Chess object
let board = null;
let currentMoveIndex = -1;
let moves = [];
let autoPlayInterval = null;

function onDragStart(source, piece, position, orientation) {
    // Disable piece dragging
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
        game = new Chess();
        if (game.load_pgn(pgn)) {
            moves = game.history({ verbose: true });
            currentMoveIndex = -1;
            updateBoard();
            displayMoves();
            enableControls();
            return true;
        }
        return false;
    } catch (error) {
        console.error('Error loading PGN:', error);
        return false;
    }
}

function updateBoard() {
    game = new Chess();
    if (currentMoveIndex >= 0) {
        for (let i = 0; i <= currentMoveIndex; i++) {
            game.move(moves[i]);
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
            currentMoveIndex = index;
            updateBoard();
            highlightCurrentMove();
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
    document.getElementById('prevBtn').disabled = false;
    document.getElementById('playBtn').disabled = false;
    document.getElementById('nextBtn').disabled = false;
}

function previousMove() {
    if (currentMoveIndex > -1) {
        currentMoveIndex--;
        updateBoard();
        highlightCurrentMove();
    }
}

function nextMove() {
    if (currentMoveIndex < moves.length - 1) {
        currentMoveIndex++;
        updateBoard();
        highlightCurrentMove();
    }
}

function toggleAutoPlay() {
    const playBtn = document.getElementById('playBtn');
    if (autoPlayInterval) {
        clearInterval(autoPlayInterval);
        autoPlayInterval = null;
        playBtn.innerHTML = '<i class="fas fa-play"></i> Play';
    } else {
        autoPlayInterval = setInterval(() => {
            if (currentMoveIndex < moves.length - 1) {
                nextMove();
            } else {
                clearInterval(autoPlayInterval);
                autoPlayInterval = null;
                playBtn.innerHTML = '<i class="fas fa-play"></i> Play';
            }
        }, 1000);
        playBtn.innerHTML = '<i class="fas fa-pause"></i> Pause';
    }
}

// Initialize when the page loads
document.addEventListener('DOMContentLoaded', () => {
    initializeBoard();
    
    // Add file input handler
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
                    // Success
                } else {
                    alert('Invalid PGN file');
                }
            };
            reader.readAsText(file);
        }
    });
    
    // Add file upload container
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
    
    // Add control button handlers
    document.getElementById('prevBtn').addEventListener('click', previousMove);
    document.getElementById('playBtn').addEventListener('click', toggleAutoPlay);
    document.getElementById('nextBtn').addEventListener('click', nextMove);
});