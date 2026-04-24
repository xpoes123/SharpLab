/* Bingo — WebSocket Client + Card Display */

const params = new URLSearchParams(window.location.search);
const token = params.get('t');
const roomId = window.location.pathname.split('/').filter(Boolean).pop();

let ws;
let myId = null;
let isHost = false;
let myCards = [];
let patternTarget = [];
let calledSet = new Set();

const LETTERS = ['B', 'I', 'N', 'G', 'O'];
const RANGES = [[1,15],[16,30],[31,45],[46,60],[61,75]];

// ── WebSocket ───────────────────────────────────────────────────────────────

function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/bingo/${roomId}?t=${token}`);
    ws.onopen = () => setStatus('Connected', 'connected');
    ws.onmessage = (e) => handleMessage(JSON.parse(e.data));
    ws.onclose = (e) => {
        if (e.code === 4001) setStatus('Invalid or expired link', 'disconnected');
        else if (e.code === 4002) setStatus('Room closed', 'disconnected');
        else { setStatus('Reconnecting...', 'disconnected'); setTimeout(connect, 2000); }
    };
}

function send(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function handleMessage(msg) {
    switch (msg.type) {
        case 'room_state':     onRoomState(msg); break;
        case 'game_start':     onGameStart(msg); break;
        case 'cards':          onCards(msg); break;
        case 'number_called':  onNumberCalled(msg); break;
        case 'game_over':      onGameOver(msg); break;
        case 'error':          showToast(msg.message, 'error'); break;
    }
}

function showPhase(id) {
    document.querySelectorAll('.phase').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

// ── Room State (lobby) ──────────────────────────────────────────────────────

function onRoomState(msg) {
    myId = msg.you;
    isHost = msg.players.some(p => p.id === myId && p.is_host);
    patternTarget = msg.pattern_target || [];

    if (msg.phase === 'waiting') {
        showPhase('lobby');
        document.getElementById('pattern-info').textContent = `Pattern: ${msg.pattern}`;
        document.getElementById('lobby-pot').textContent = msg.prize_pool + 'c';

        const list = document.getElementById('lobby-players');
        list.innerHTML = '';
        msg.players.forEach(p => {
            const li = document.createElement('li');
            const conn = p.connected ? '\u2022' : '\u25CB';
            const cls = p.connected ? 'connected' : 'disconnected';
            li.innerHTML = `<span><span class="${cls}">${conn}</span> ${esc(p.name)}${p.is_host ? ' <span class="host-badge">HOST</span>' : ''}</span><span class="wager">${p.num_cards} card${p.num_cards > 1 ? 's' : ''} (${p.wager}c)</span>`;
            list.appendChild(li);
        });

        const startBtn = document.getElementById('start-btn');
        startBtn.style.display = isHost ? 'block' : 'none';
        startBtn.disabled = msg.players.filter(p => p.connected).length < 1;
        document.getElementById('waiting-text').style.display = isHost ? 'none' : 'block';
    }
}

// ── Game Start ──────────────────────────────────────────────────────────────

function onGameStart(msg) {
    showPhase('game');
    patternTarget = msg.pattern_target;
}

function onCards(msg) {
    myCards = msg.cards;
    patternTarget = msg.pattern_target || patternTarget;
    if (msg.called_set) calledSet = new Set(msg.called_set);
    renderCards();
}

// ── Number Called ───────────────────────────────────────────────────────────

function onNumberCalled(msg) {
    showPhase('game');
    calledSet.add(msg.number);

    // Update last called display with animation
    const lastEl = document.getElementById('last-called');
    lastEl.textContent = msg.letter;
    lastEl.style.animation = 'none';
    lastEl.offsetHeight; // reflow
    lastEl.style.animation = 'popIn 0.3s ease';

    document.getElementById('called-count').textContent = `${msg.called_count} called`;

    // Update progress bars
    renderProgress(msg.progress);

    // Update called board
    renderCalledBoard();
}

// ── Card Rendering ──────────────────────────────────────────────────────────

function renderCards() {
    const container = document.getElementById('bingo-cards');
    container.innerHTML = '';

    myCards.forEach((card, idx) => {
        const cardEl = document.createElement('div');
        cardEl.className = 'bingo-card';
        cardEl.id = `card-${idx}`;

        // Header
        const header = document.createElement('div');
        header.className = 'card-header';
        LETTERS.forEach(l => {
            const span = document.createElement('span');
            span.textContent = l;
            header.appendChild(span);
        });
        cardEl.appendChild(header);

        // Grid
        const grid = document.createElement('div');
        grid.className = 'card-grid';
        for (let r = 0; r < 5; r++) {
            for (let c = 0; c < 5; c++) {
                const cell = document.createElement('div');
                cell.className = 'bingo-cell';

                if (r === 2 && c === 2) {
                    cell.classList.add('free', 'marked');
                    cell.textContent = 'FREE';
                } else {
                    const num = card.grid[r][c];
                    cell.textContent = num;

                    if (card.marked[r][c]) {
                        cell.classList.add('marked');
                    }
                    if (patternTarget[r] && patternTarget[r][c]) {
                        cell.classList.add('target');
                    }
                }
                grid.appendChild(cell);
            }
        }
        cardEl.appendChild(grid);

        // Progress bar
        if (patternTarget.length) {
            let marked = 0, total = 0;
            for (let r = 0; r < 5; r++) {
                for (let c = 0; c < 5; c++) {
                    if (patternTarget[r] && patternTarget[r][c]) {
                        total++;
                        if (card.marked[r][c]) marked++;
                    }
                }
            }
            const pct = total > 0 ? (marked / total * 100) : 0;
            const prog = document.createElement('div');
            prog.className = 'card-progress';
            prog.innerHTML = `${marked}/${total} <div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div>`;
            cardEl.appendChild(prog);
        }

        container.appendChild(cardEl);
    });
}

// ── Progress Bars ───────────────────────────────────────────────────────────

function renderProgress(progress) {
    const el = document.getElementById('player-progress');
    el.innerHTML = '';
    const sorted = Object.entries(progress || {}).sort((a, b) => b[1].marked - a[1].marked);
    sorted.forEach(([uid, p]) => {
        const pct = p.total > 0 ? (p.marked / p.total * 100) : 0;
        const me = uid === myId ? ' (you)' : '';
        const row = document.createElement('div');
        row.className = 'progress-row';
        row.innerHTML = `<span class="name">${esc(p.name)}${me}</span><div class="bar-bg"><div class="bar-fill" style="width:${pct}%"></div></div><span class="frac">${p.marked}/${p.total}</span>`;
        el.appendChild(row);
    });
}

// ── Called Board ────────────────────────────────────────────────────────────

function renderCalledBoard() {
    const board = document.getElementById('called-board');
    board.innerHTML = '';
    LETTERS.forEach((letter, i) => {
        const [lo, hi] = RANGES[i];
        const nums = [];
        for (let n = lo; n <= hi; n++) {
            if (calledSet.has(n)) nums.push(n);
        }
        const row = document.createElement('div');
        row.className = 'called-row';
        row.innerHTML = `<span class="letter">${letter}</span><span class="nums">${nums.length ? nums.join(' ') : '\u2014'}</span>`;
        board.appendChild(row);
    });
}

// ── Game Over ───────────────────────────────────────────────────────────────

function onGameOver(msg) {
    showPhase('results');
    const title = document.getElementById('result-title');
    if (msg.results.some(r => r.is_winner && r.discord_user === myId)) {
        title.textContent = 'BINGO! You won!';
    } else {
        title.textContent = `Game Over \u2014 ${msg.pattern}`;
    }
    title.textContent += ` (${msg.numbers_called} numbers)`;

    const tbody = document.getElementById('results-body');
    tbody.innerHTML = '';
    msg.results.forEach((r, i) => {
        const tr = document.createElement('tr');
        const badge = r.is_winner ? '\U0001f3c6' : (i + 1);
        const netClass = r.net > 0 ? 'positive' : r.net < 0 ? 'negative' : '';
        tr.innerHTML = `
            <td>${r.is_winner ? '\uD83C\uDFC6' : (i + 1)}</td>
            <td>${esc(r.display_name)}</td>
            <td class="num">${r.num_cards}</td>
            <td class="num">${r.wager}c</td>
            <td class="num">${r.payout}c</td>
            <td class="num ${netClass}">${r.net > 0 ? '+' : ''}${r.net}c</td>
        `;
        tbody.appendChild(tr);
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

// ── Init ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    if (!token || !roomId) { setStatus('Invalid game link', 'disconnected'); return; }
    connect();
    document.getElementById('start-btn').addEventListener('click', () => send({ type: 'start' }));
});
