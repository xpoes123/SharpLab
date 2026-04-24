/* Sudoku Sprint — WebSocket Client + Interactive Grid */

const params = new URLSearchParams(window.location.search);
const token = params.get('t');
const roomId = window.location.pathname.split('/').filter(Boolean).pop();

let ws;
let myId = null;
let isHost = false;
let currentPuzzle = null;
let playerGrid = null;  // mutable copy
let selectedCell = null;  // {r, c}
let gamePhase = 'lobby';

// ── WebSocket ───────────────────────────────────────────────────────────────

function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/sudoku/${roomId}?t=${token}`;

    ws = new WebSocket(url);

    ws.onopen = () => {
        setStatus('Connected', 'connected');
    };

    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        handleMessage(msg);
    };

    ws.onclose = (e) => {
        if (e.code === 4001) {
            setStatus('Invalid or expired link', 'disconnected');
        } else if (e.code === 4002) {
            setStatus('Room closed', 'disconnected');
        } else {
            setStatus('Disconnected — reconnecting...', 'disconnected');
            setTimeout(connect, 2000);
        }
    };

    ws.onerror = () => {};
}

function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
    }
}

// ── Message Handling ────────────────────────────────────────────────────────

function handleMessage(msg) {
    switch (msg.type) {
        case 'room_state':    onRoomState(msg); break;
        case 'round_start':   onRoundStart(msg); break;
        case 'player_status': onPlayerStatus(msg); break;
        case 'timer':         onTimer(msg); break;
        case 'round_result':  onRoundResult(msg); break;
        case 'game_over':     onGameOver(msg); break;
        case 'submit_result': onSubmitResult(msg); break;
        case 'error':         showToast(msg.message, 'error'); break;
        case 'pong':          break;
    }
}

// ── Phase Switching ─────────────────────────────────────────────────────────

function showPhase(id) {
    gamePhase = id;
    document.querySelectorAll('.phase').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

// ── Room State (lobby) ──────────────────────────────────────────────────────

function onRoomState(msg) {
    myId = msg.you;
    isHost = msg.players.some(p => p.id === myId && p.is_host);

    if (msg.phase === 'waiting') {
        showPhase('lobby');
        document.getElementById('lobby-pot').textContent = msg.prize_pool + 'c';

        const list = document.getElementById('lobby-players');
        list.innerHTML = '';
        msg.players.forEach(p => {
            const li = document.createElement('li');
            const connClass = p.connected ? 'connected' : 'disconnected';
            const connDot = p.connected ? '\u2022' : '\u25CB';
            li.innerHTML = `
                <span>
                    <span class="${connClass}">${connDot}</span>
                    ${esc(p.name)}
                    ${p.is_host ? '<span class="host-badge">HOST</span>' : ''}
                </span>
                <span class="wager">${p.wager}c</span>
            `;
            list.appendChild(li);
        });

        const startBtn = document.getElementById('start-btn');
        startBtn.style.display = isHost ? 'block' : 'none';
        startBtn.disabled = msg.players.filter(p => p.connected).length < 1;
        document.getElementById('waiting-text').style.display = isHost ? 'none' : 'block';
    }
}

// ── Round Start ─────────────────────────────────────────────────────────────

function onRoundStart(msg) {
    showPhase('game');
    currentPuzzle = msg.puzzle;
    playerGrid = msg.puzzle.map(row => [...row]);
    selectedCell = null;
    solved = false;

    document.getElementById('round-num').textContent = msg.round_num;
    document.getElementById('timer').textContent = msg.time_limit;
    document.getElementById('timer').classList.remove('urgent');

    renderGrid();
    clearPlayerStatus();
}

// ── Grid Rendering ──────────────────────────────────────────────────────────

function renderGrid() {
    const grid = document.getElementById('sudoku-grid');
    grid.innerHTML = '';

    for (let r = 0; r < 4; r++) {
        for (let c = 0; c < 4; c++) {
            const cell = document.createElement('div');
            cell.className = 'cell';
            cell.dataset.row = r;
            cell.dataset.col = c;

            if (currentPuzzle[r][c] !== 0) {
                cell.textContent = currentPuzzle[r][c];
                cell.classList.add('given');
            } else {
                cell.classList.add('blank');
                if (playerGrid[r][c] !== 0) {
                    cell.textContent = playerGrid[r][c];
                }
                cell.addEventListener('click', () => selectCell(r, c));
            }

            grid.appendChild(cell);
        }
    }

    highlightConflicts();
    updateSelectedVisual();
}

function selectCell(r, c) {
    selectedCell = { r, c };
    updateSelectedVisual();
}

function updateSelectedVisual() {
    document.querySelectorAll('.cell.selected').forEach(el => el.classList.remove('selected'));
    if (selectedCell) {
        const idx = selectedCell.r * 4 + selectedCell.c;
        const cells = document.querySelectorAll('.cell');
        if (cells[idx]) cells[idx].classList.add('selected');
    }
}

function placeNumber(num) {
    if (!selectedCell) return;
    const { r, c } = selectedCell;
    if (currentPuzzle[r][c] !== 0) return;  // can't edit given cells

    playerGrid[r][c] = num;
    renderGrid();

    // Auto-submit when all cells are filled
    tryAutoSubmit();
}

function tryAutoSubmit() {
    if (gamePhase !== 'game' || solved) return;
    for (let r = 0; r < 4; r++) {
        for (let c = 0; c < 4; c++) {
            if (playerGrid[r][c] === 0) return;  // still has blanks
        }
    }
    // All filled — send to server for validation
    send({ type: 'submit', grid: playerGrid });
}

function highlightConflicts() {
    const cells = document.querySelectorAll('.cell');
    // Reset
    cells.forEach(c => c.classList.remove('conflict'));

    for (let r = 0; r < 4; r++) {
        for (let c = 0; c < 4; c++) {
            if (playerGrid[r][c] === 0) continue;
            const v = playerGrid[r][c];
            let conflict = false;

            // Row
            for (let c2 = 0; c2 < 4; c2++) {
                if (c2 !== c && playerGrid[r][c2] === v) conflict = true;
            }
            // Col
            for (let r2 = 0; r2 < 4; r2++) {
                if (r2 !== r && playerGrid[r2][c] === v) conflict = true;
            }
            // Box
            const br = Math.floor(r / 2) * 2, bc = Math.floor(c / 2) * 2;
            for (let dr = 0; dr < 2; dr++) {
                for (let dc = 0; dc < 2; dc++) {
                    const r2 = br + dr, c2 = bc + dc;
                    if ((r2 !== r || c2 !== c) && playerGrid[r2][c2] === v) conflict = true;
                }
            }

            if (conflict) cells[r * 4 + c].classList.add('conflict');
        }
    }
}

// ── Player Status ───────────────────────────────────────────────────────────

function clearPlayerStatus() {
    document.getElementById('player-status').innerHTML = '';
}

function onPlayerStatus(msg) {
    const bar = document.getElementById('player-status');
    let chip = document.getElementById('status-' + msg.player_id);
    if (!chip) {
        chip = document.createElement('div');
        chip.id = 'status-' + msg.player_id;
        chip.className = 'status-chip solving';
        bar.appendChild(chip);
    }

    if (msg.status === 'solved') {
        chip.className = 'status-chip solved';
        chip.innerHTML = `<span class="dot"></span>${esc(msg.name)} \u2714 ${msg.solve_time}s`;
    } else {
        chip.className = 'status-chip solving';
        chip.innerHTML = `<span class="dot"></span>${esc(msg.name)} (${msg.attempts})`;
    }
}

// ── Timer ───────────────────────────────────────────────────────────────────

function onTimer(msg) {
    const el = document.getElementById('timer');
    el.textContent = msg.remaining;
    if (msg.remaining <= 15) el.classList.add('urgent');
}

// ── Round Result ────────────────────────────────────────────────────────────

function onRoundResult(msg) {
    showPhase('round-result');

    if (msg.winner_id) {
        document.getElementById('result-title').textContent =
            `${msg.winner_name} wins Round ${msg.round_num}!`;
        document.getElementById('result-time').textContent = `${msg.solve_time}s`;
    } else {
        document.getElementById('result-title').textContent =
            `Round ${msg.round_num} — Timeout`;
        document.getElementById('result-time').textContent = 'Nobody solved it';
    }

    renderScoreboard(document.getElementById('result-scoreboard'), msg.scoreboard);
    document.getElementById('result-next').textContent = 'Next round in 5s...';

    // Auto-switch back to game phase (server sends next round_start)
}

// ── Game Over ───────────────────────────────────────────────────────────────

function onGameOver(msg) {
    showPhase('results');

    const tbody = document.getElementById('results-body');
    tbody.innerHTML = '';

    const medals = ['\uD83E\uDD47', '\uD83E\uDD48', '\uD83E\uDD49'];

    msg.results.forEach((r, i) => {
        const tr = document.createElement('tr');
        const badge = i < 3 ? medals[i] : (i + 1);
        const netClass = r.net > 0 ? 'positive' : r.net < 0 ? 'negative' : '';
        tr.innerHTML = `
            <td>${badge}</td>
            <td>${esc(r.display_name)}</td>
            <td class="num">${r.rounds_won}</td>
            <td class="num">${r.wager}c</td>
            <td class="num">${r.payout}c</td>
            <td class="num ${netClass}">${r.net > 0 ? '+' : ''}${r.net}c</td>
        `;
        tbody.appendChild(tr);
    });
}

// ── Submit Result ───────────────────────────────────────────────────────────

let solved = false;

function onSubmitResult(msg) {
    if (msg.correct) {
        solved = true;
        showToast(`Correct! ${msg.solve_time}s`, 'success');
        // Lock the grid — no more edits
        document.querySelectorAll('.cell.blank').forEach(c => {
            c.style.pointerEvents = 'none';
        });
    } else {
        showToast(msg.hint, 'error');
    }
}

// ── Scoreboard ──────────────────────────────────────────────────────────────

function renderScoreboard(el, scoreboard) {
    el.innerHTML = '';
    scoreboard.forEach(s => {
        const row = document.createElement('div');
        row.className = 'scoreboard-row';
        row.innerHTML = `
            <span>${esc(s.name)}</span>
            <span class="score-wins">${s.rounds_won}/3</span>
        `;
        el.appendChild(row);
    });
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function setStatus(text, cls) {
    const el = document.getElementById('connection-status');
    el.textContent = text;
    el.className = 'status-bar ' + cls;
}

let toastTimer;
function showToast(message, type) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `toast ${type || ''}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.remove(), 3000);
}

// ── Event Listeners ─────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    if (!token || !roomId) {
        setStatus('Invalid game link', 'disconnected');
        return;
    }

    connect();

    // Start button
    document.getElementById('start-btn').addEventListener('click', () => {
        send({ type: 'start' });
    });

    // Number pad
    document.querySelectorAll('.num-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            placeNumber(parseInt(btn.dataset.num));
        });
    });

    // Keyboard input
    document.addEventListener('keydown', (e) => {
        if (gamePhase !== 'game') return;
        if (e.key >= '1' && e.key <= '4') {
            placeNumber(parseInt(e.key));
        } else if (e.key === 'Backspace' || e.key === 'Delete' || e.key === '0') {
            placeNumber(0);
        } else if (e.key === 'ArrowUp' && selectedCell && selectedCell.r > 0) {
            selectCell(selectedCell.r - 1, selectedCell.c);
        } else if (e.key === 'ArrowDown' && selectedCell && selectedCell.r < 3) {
            selectCell(selectedCell.r + 1, selectedCell.c);
        } else if (e.key === 'ArrowLeft' && selectedCell && selectedCell.c > 0) {
            selectCell(selectedCell.r, selectedCell.c - 1);
        } else if (e.key === 'ArrowRight' && selectedCell && selectedCell.c < 3) {
            selectCell(selectedCell.r, selectedCell.c + 1);
        }
    });
});
