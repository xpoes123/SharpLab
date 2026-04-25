/* Solitaire Chess — client-side game logic + WebSocket */

const PIECE_EMOJI = {K:'\u265a', Q:'\u265b', R:'\u265c', B:'\u265d', N:'\u265e', P:'\u265f'};
const PIECE_NAME = {K:'King', Q:'Queen', R:'Rook', B:'Bishop', N:'Knight', P:'Pawn'};
const DIFF_LABELS = {easy:'Easy (4 pieces)',medium:'Medium (5 pieces)',hard:'Hard (6 pieces)',expert:'Expert (7 pieces)'};
const BS = 4; // board size

// ── State ───────────────────────────────────────────────────────────────────

let ws = null;
let myId = null;
let isHost = false;
let board = null;       // my current 4x4 board
let selected = null;    // [r, c] or null
let validTargets = [];  // [[r,c], ...]
let moveHistory = [];   // for undo tracking (client-side count)
let phase = 'lobby';

// ── DOM refs ────────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);
const phases = {lobby: $('lobby'), game: $('game'), roundResult: $('round-result'), results: $('results')};
const statusBar = $('connection-status');

// ── Piece movement (client-side for instant highlighting) ───────────────────

function inBounds(r, c) { return r >= 0 && r < BS && c >= 0 && c < BS; }

function getCaptures(b, r, c) {
    const piece = b[r][c];
    if (!piece) return [];
    const targets = [];

    if (piece === 'K') {
        for (let dr = -1; dr <= 1; dr++)
            for (let dc = -1; dc <= 1; dc++) {
                if (!dr && !dc) continue;
                const nr = r+dr, nc = c+dc;
                if (inBounds(nr,nc) && b[nr][nc]) targets.push([nr,nc]);
            }
    } else if (piece === 'Q') {
        for (let dr = -1; dr <= 1; dr++)
            for (let dc = -1; dc <= 1; dc++) {
                if (!dr && !dc) continue;
                for (let d = 1; d < BS; d++) {
                    const nr = r+dr*d, nc = c+dc*d;
                    if (!inBounds(nr,nc)) break;
                    if (b[nr][nc]) { targets.push([nr,nc]); break; }
                }
            }
    } else if (piece === 'R') {
        for (const [dr,dc] of [[-1,0],[1,0],[0,-1],[0,1]])
            for (let d = 1; d < BS; d++) {
                const nr = r+dr*d, nc = c+dc*d;
                if (!inBounds(nr,nc)) break;
                if (b[nr][nc]) { targets.push([nr,nc]); break; }
            }
    } else if (piece === 'B') {
        for (const [dr,dc] of [[-1,-1],[-1,1],[1,-1],[1,1]])
            for (let d = 1; d < BS; d++) {
                const nr = r+dr*d, nc = c+dc*d;
                if (!inBounds(nr,nc)) break;
                if (b[nr][nc]) { targets.push([nr,nc]); break; }
            }
    } else if (piece === 'N') {
        for (const [dr,dc] of [[-2,-1],[-2,1],[-1,-2],[-1,2],[1,-2],[1,2],[2,-1],[2,1]]) {
            const nr = r+dr, nc = c+dc;
            if (inBounds(nr,nc) && b[nr][nc]) targets.push([nr,nc]);
        }
    } else if (piece === 'P') {
        for (const [dr,dc] of [[-1,-1],[-1,1],[1,-1],[1,1]]) {
            const nr = r+dr, nc = c+dc;
            if (inBounds(nr,nc) && b[nr][nc]) targets.push([nr,nc]);
        }
    }
    return targets;
}

// ── Rendering ───────────────────────────────────────────────────────────────

function showPhase(name) {
    phase = name;
    for (const [k,el] of Object.entries(phases))
        el.classList.toggle('active', k === name);
}

function renderBoard() {
    const el = $('board');
    el.innerHTML = '';
    if (!board) return;

    for (let r = 0; r < BS; r++) {
        for (let c = 0; c < BS; c++) {
            const cell = document.createElement('div');
            const isLight = (r + c) % 2 === 0;
            cell.className = 'cell ' + (isLight ? 'light' : 'dark');

            const piece = board[r][c];
            if (piece) {
                cell.textContent = PIECE_EMOJI[piece];
                cell.classList.add('has-piece');
            }

            if (selected && selected[0] === r && selected[1] === c) {
                cell.classList.add('selected');
            }

            if (validTargets.some(([tr,tc]) => tr === r && tc === c)) {
                cell.classList.add('valid-target');
                if (piece) cell.classList.add('has-piece');
            }

            cell.addEventListener('click', () => onCellClick(r, c));
            el.appendChild(cell);
        }
    }

    // Update pieces count
    let count = 0;
    for (const row of board) for (const cell of row) if (cell) count++;
    $('my-pieces').textContent = count;
    $('undo-btn').disabled = moveHistory.length === 0;
}

function onCellClick(r, c) {
    if (phase !== 'game' || !board) return;

    // If clicking a valid target, make the move
    if (selected && validTargets.some(([tr,tc]) => tr === r && tc === c)) {
        sendMove(selected[0], selected[1], r, c);
        selected = null;
        validTargets = [];
        return;
    }

    // If clicking a piece, select it
    if (board[r][c]) {
        selected = [r, c];
        validTargets = getCaptures(board, r, c);
    } else {
        selected = null;
        validTargets = [];
    }
    renderBoard();
}

function renderPlayerStatus(players) {
    const el = $('player-status');
    el.innerHTML = '';
    if (!players) return;

    for (const p of players) {
        const row = document.createElement('div');
        row.className = 'ps-row';
        if (p.solved) row.classList.add('solved');
        if (p.gave_up) row.classList.add('gaveup');

        let status;
        if (p.solved) status = `\u2705 Solved (${p.move_count} moves)`;
        else if (p.gave_up) status = '\u{1f3f3}\ufe0f Gave up';
        else if (p.pieces_left !== undefined) status = `${p.pieces_left} pieces (${p.move_count} moves)`;
        else status = 'Waiting...';

        row.innerHTML = `<span>${p.name}</span><span>${status}</span>`;
        el.appendChild(row);
    }
}

function renderScoreboard(sb) {
    const el = $('scoreboard');
    el.innerHTML = '';
    if (!sb) return;
    for (const entry of sb) {
        const row = document.createElement('div');
        row.className = 'sb-row';
        row.innerHTML = `<span>${entry.name}</span><span>${entry.rounds_won}W</span>`;
        el.appendChild(row);
    }
}

function showHint(msg, isError) {
    const el = $('hint-toast');
    el.textContent = msg;
    el.classList.remove('hidden', 'error');
    if (isError) el.classList.add('error');
    setTimeout(() => el.classList.add('hidden'), 4000);
}

// ── Public Join ─────────────────────────────────────────────────────────────

const _params = new URLSearchParams(window.location.search);
const _token = _params.get('t');
const _pathParts = window.location.pathname.split('/').filter(Boolean);
const _gamePath = _pathParts[0] || '';
const _roomId = _pathParts.length > 1 ? _pathParts[_pathParts.length - 1] : null;
const _hasRoomId = _roomId && _roomId !== _gamePath;

function initJoinScreen(game) {
    const screen = document.getElementById('join-screen');
    screen.classList.add('active');
    const nameInput = document.getElementById('player-name');
    const createBtn = document.getElementById('create-room-btn');
    const joinBtn = document.getElementById('join-room-btn');
    const errorEl = document.getElementById('join-error');
    if (_hasRoomId) { createBtn.style.display = 'none'; joinBtn.style.display = 'block'; }
    nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') (_hasRoomId ? joinBtn : createBtn).click(); });
    createBtn.addEventListener('click', async () => {
        const name = nameInput.value.trim();
        if (!name) { errorEl.textContent = 'Enter a name'; return; }
        createBtn.disabled = true;
        try {
            const resp = await fetch(`/api/v1/${game}/public/create`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({display_name: name}) });
            if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed');
            const data = await resp.json();
            window.location.href = `/${_gamePath}/${data.room_id}?t=${data.token}`;
        } catch (e) { errorEl.textContent = e.message; createBtn.disabled = false; }
    });
    joinBtn.addEventListener('click', async () => {
        const name = nameInput.value.trim();
        if (!name) { errorEl.textContent = 'Enter a name'; return; }
        joinBtn.disabled = true;
        try {
            const resp = await fetch(`/api/v1/${game}/public/join/${_roomId}`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({display_name: name}) });
            if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed');
            const data = await resp.json();
            window.location.href = data.url;
        } catch (e) { errorEl.textContent = e.message; joinBtn.disabled = false; }
    });
}

// ── WebSocket ───────────────────────────────────────────────────────────────

function connect() {
    if (!_token || !_hasRoomId) {
        statusBar.textContent = 'Missing token or room ID';
        statusBar.className = 'status-bar disconnected';
        return;
    }
    document.getElementById('join-screen').style.display = 'none';

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/ws/solitairechess/${_roomId}?t=${_token}`;

    statusBar.textContent = 'Connecting...';
    statusBar.className = 'status-bar connecting';

    ws = new WebSocket(url);

    ws.onopen = () => {
        statusBar.textContent = 'Connected';
        statusBar.className = 'status-bar connected';
    };

    ws.onclose = () => {
        statusBar.textContent = 'Disconnected';
        statusBar.className = 'status-bar disconnected';
        setTimeout(connect, 3000);
    };

    ws.onerror = () => {
        statusBar.textContent = 'Connection error';
        statusBar.className = 'status-bar disconnected';
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };

    // Keepalive
    setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({type: 'ping'}));
        }
    }, 30000);
}

function handleMessage(msg) {
    switch (msg.type) {
        case 'room_state':
            myId = msg.you;
            const hostPlayer = msg.players.find(p => p.is_host);
            isHost = hostPlayer && hostPlayer.id === myId;

            $('lobby-diff').textContent = DIFF_LABELS[msg.difficulty] || msg.difficulty;
            $('lobby-pot').textContent = msg.prize_pool + 'c';

            const list = $('lobby-players');
            list.innerHTML = '';
            for (const p of msg.players) {
                const li = document.createElement('li');
                li.innerHTML = `<span>${p.name} (${p.wager}c)</span>`;
                if (p.is_host) li.innerHTML += '<span class="host-badge">HOST</span>';
                if (p.connected) li.innerHTML += '<span style="color:#4f4">&#9679;</span>';
                list.appendChild(li);
            }

            const startBtn = $('start-btn');
            startBtn.disabled = !isHost;
            $('waiting-text').textContent = isHost
                ? 'You are the host. Click Start when ready!'
                : 'Waiting for host to start...';

            const inviteSection = document.getElementById('invite-section');
            if (inviteSection) {
                inviteSection.style.display = 'block';
                const inviteUrl = `${window.location.origin}/${_gamePath}/${msg.room_id}`;
                document.getElementById('invite-url').value = inviteUrl;
                document.getElementById('copy-invite-btn').onclick = () => {
                    navigator.clipboard.writeText(inviteUrl);
                    document.getElementById('copy-invite-btn').textContent = 'Copied!';
                    setTimeout(() => document.getElementById('copy-invite-btn').textContent = 'Copy', 2000);
                };
            }

            if (msg.phase === 'waiting') showPhase('lobby');
            break;

        case 'round_start':
            board = msg.board;
            selected = null;
            validTargets = [];
            moveHistory = [];
            $('round-num').textContent = msg.round_num;
            $('timer').textContent = msg.time_limit;
            $('timer').classList.remove('low');
            $('hint-toast').classList.add('hidden');
            $('giveup-btn').disabled = false;
            $('hint-btn').disabled = false;
            renderBoard();
            renderPlayerStatus([]);
            showPhase('game');
            break;

        case 'board_update':
            board = msg.board;
            selected = null;
            validTargets = [];
            if (msg.last_move) moveHistory.push(msg.last_move);
            renderBoard();
            if (msg.solved) {
                showHint(`\u{1f3c6} Solved in ${msg.move_count} moves!`, false);
                $('giveup-btn').disabled = true;
                $('hint-btn').disabled = true;
            }
            break;

        case 'player_progress':
            // Update the player status display
            // We accumulate progress from broadcasts
            updatePlayerProgress(msg);
            break;

        case 'timer':
            $('timer').textContent = msg.remaining;
            if (msg.remaining <= 30) $('timer').classList.add('low');
            break;

        case 'hint_result':
            if (msg.stuck) {
                showHint(msg.message, true);
            } else {
                const pEmoji = PIECE_EMOJI[msg.piece] || msg.piece;
                const tEmoji = PIECE_EMOJI[msg.target] || msg.target;
                showHint(`Try: ${pEmoji} ${PIECE_NAME[msg.piece]} \u2192 ${tEmoji}`, false);
                // Highlight the hint on the board
                selected = msg.from;
                validTargets = [msg.to];
                renderBoard();
            }
            break;

        case 'round_result': {
            const title = $('result-title');
            if (msg.winner_name) {
                title.textContent = `\u{1f3c6} ${msg.winner_name} wins! (${msg.moves} moves)`;
            } else {
                title.textContent = 'No winner this round';
            }
            const sb = $('result-scoreboard');
            sb.innerHTML = '';
            if (msg.scoreboard) {
                for (const e of msg.scoreboard) {
                    const row = document.createElement('div');
                    row.className = 'sb-row';
                    row.innerHTML = `<span>${e.name}</span><span>${e.rounds_won}W</span>`;
                    sb.appendChild(row);
                }
            }
            $('result-next').textContent = 'Next round in 5s...';
            showPhase('roundResult');
            break;
        }

        case 'game_over': {
            const tbody = $('results-body');
            tbody.innerHTML = '';
            const medals = ['\u{1f947}', '\u{1f948}', '\u{1f949}'];
            msg.results.forEach((r, i) => {
                const tr = document.createElement('tr');
                const badge = i < 3 ? medals[i] : `${i+1}.`;
                const netClass = r.net > 0 ? 'positive' : r.net < 0 ? 'negative' : '';
                const sign = r.net > 0 ? '+' : '';
                tr.innerHTML = `
                    <td>${badge}</td>
                    <td>${r.display_name}</td>
                    <td>${r.rounds_won}W</td>
                    <td>${r.wager}c</td>
                    <td>${r.payout}c</td>
                    <td class="${netClass}">${sign}${r.net}c</td>
                `;
                tbody.appendChild(tr);
            });
            showPhase('results');
            break;
        }

        case 'error':
            showHint(msg.message, true);
            break;

        case 'pong':
            break;
    }
}

// ── Player progress tracking ────────────────────────────────────────────────

const playerProgressMap = {};

function updatePlayerProgress(msg) {
    playerProgressMap[msg.player_id] = {
        name: msg.name,
        pieces_left: msg.pieces_left,
        move_count: msg.move_count,
        solved: msg.solved,
        gave_up: msg.gave_up,
    };
    renderPlayerStatus(Object.values(playerProgressMap));
}

// ── Send messages ───────────────────────────────────────────────────────────

function sendMove(fr, fc, tr, tc) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({type: 'move', from: [fr, fc], to: [tr, tc]}));
}

// ── Event listeners ─────────────────────────────────────────────────────────

$('start-btn').addEventListener('click', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({type: 'start'}));
    }
});

$('undo-btn').addEventListener('click', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({type: 'undo'}));
        moveHistory.pop();
    }
});

$('hint-btn').addEventListener('click', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({type: 'hint'}));
    }
});

$('giveup-btn').addEventListener('click', () => {
    if (ws && ws.readyState === WebSocket.OPEN && confirm('Give up this round?')) {
        ws.send(JSON.stringify({type: 'giveup'}));
        $('giveup-btn').disabled = true;
        $('hint-btn').disabled = true;
    }
});

// ── Init ────────────────────────────────────────────────────────────────────

if (!_token) {
    initJoinScreen('solitairechess');
} else {
    connect();
}
