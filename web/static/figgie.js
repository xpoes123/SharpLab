/* Figgie — WebSocket Client + Trading Floor UI */

const params = new URLSearchParams(window.location.search);
const token = params.get('t');
const pathParts = window.location.pathname.split('/').filter(Boolean);
const gamePath = pathParts[0] || '';
const roomId = pathParts.length > 1 ? pathParts[pathParts.length - 1] : null;
const hasRoomId = roomId && roomId !== gamePath;

let ws;
let myId = null;
let isHost = false;
let gamePhase = 'lobby';
let myHand = {};
let myChips = 200;

const SUITS = ['\u2660', '\u2665', '\u2666', '\u2663'];
const SUIT_CLASS = {'\u2660': 'spades', '\u2665': 'hearts', '\u2666': 'diamonds', '\u2663': 'clubs'};
const SUIT_MAP = {'s': '\u2660', 'h': '\u2665', 'd': '\u2666', 'c': '\u2663'};

// ── WebSocket ───────────────────────────────────────────────────────────────

function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/figgie/${roomId}?t=${token}`;
    ws = new WebSocket(url);
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
        case 'room_state':      onRoomState(msg); break;
        case 'game_start':      onGameStart(msg); break;
        case 'hand':            onHand(msg); break;
        case 'round_start':     onRoundStart(msg); break;
        case 'round_end':       onRoundEnd(msg); break;
        case 'timer':           onTimer(msg); break;
        case 'order_posted':    showToast(`${msg.player_name}: ${msg.action} ${msg.suit} @${msg.price}`, ''); break;
        case 'trade_executed':  onTrade(msg); break;
        case 'market_state':    onMarketState(msg); break;
        case 'game_over':       onGameOver(msg); break;
        case 'error':           showToast(msg.message, 'error'); break;
    }
}

function showPhase(id) {
    gamePhase = id;
    document.querySelectorAll('.phase').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

// ── Room State (lobby) ──────────────────────────────────────────────────────

function initJoinScreen(game) {
    const screen = document.getElementById('join-screen');
    screen.classList.add('active');
    const nameInput = document.getElementById('player-name');
    const createBtn = document.getElementById('create-room-btn');
    const joinBtn = document.getElementById('join-room-btn');
    const errorEl = document.getElementById('join-error');
    if (hasRoomId) { createBtn.style.display = 'none'; joinBtn.style.display = 'block'; }
    nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') (hasRoomId ? joinBtn : createBtn).click(); });
    createBtn.addEventListener('click', async () => {
        const name = nameInput.value.trim();
        if (!name) { errorEl.textContent = 'Enter a name'; return; }
        createBtn.disabled = true;
        try {
            const resp = await fetch(`/api/v1/${game}/public/create`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({display_name: name}) });
            if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed');
            const data = await resp.json();
            window.location.href = `/${gamePath}/${data.room_id}?t=${data.token}`;
        } catch (e) { errorEl.textContent = e.message; createBtn.disabled = false; }
    });
    joinBtn.addEventListener('click', async () => {
        const name = nameInput.value.trim();
        if (!name) { errorEl.textContent = 'Enter a name'; return; }
        joinBtn.disabled = true;
        try {
            const resp = await fetch(`/api/v1/${game}/public/join/${roomId}`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({display_name: name}) });
            if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed');
            const data = await resp.json();
            window.location.href = data.url;
        } catch (e) { errorEl.textContent = e.message; joinBtn.disabled = false; }
    });
}

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
            const conn = p.connected ? '\u2022' : '\u25CB';
            const cls = p.connected ? 'connected' : 'disconnected';
            li.innerHTML = `<span><span class="${cls}">${conn}</span> ${esc(p.name)}${p.is_host ? ' <span class="host-badge">HOST</span>' : ''}</span><span class="wager">${p.wager}c</span>`;
            list.appendChild(li);
        });
        const startBtn = document.getElementById('start-btn');
        startBtn.style.display = isHost ? 'block' : 'none';
        startBtn.disabled = msg.players.filter(p => p.connected).length < 3;
        document.getElementById('waiting-text').style.display = isHost ? 'none' : 'block';
        const inviteSection = document.getElementById('invite-section');
        if (inviteSection) {
            inviteSection.style.display = 'block';
            const inviteUrl = `${window.location.origin}/${gamePath}/${msg.room_id}`;
            document.getElementById('invite-url').value = inviteUrl;
            document.getElementById('copy-invite-btn').onclick = () => {
                navigator.clipboard.writeText(inviteUrl);
                document.getElementById('copy-invite-btn').textContent = 'Copied!';
                setTimeout(() => document.getElementById('copy-invite-btn').textContent = 'Copy', 2000);
            };
        }
    }
}

// ── Game Start ──────────────────────────────────────────────────────────────

function onGameStart(msg) {
    showPhase('game');
    myChips = msg.player_chips[myId] || 200;
    document.getElementById('my-chips').textContent = myChips;
}

function onHand(msg) {
    myHand = msg.hand;
    myChips = msg.chips;
    document.getElementById('my-chips').textContent = myChips;
    renderHand();
}

function renderHand() {
    const panel = document.getElementById('hand-panel');
    panel.innerHTML = '';
    SUITS.forEach(suit => {
        const card = document.createElement('div');
        card.className = `hand-card ${SUIT_CLASS[suit]}`;
        card.innerHTML = `<span class="suit-icon">${suit}</span><span class="count">${myHand[suit] || 0}</span>`;
        panel.appendChild(card);
    });
}

// ── Round Start/End ─────────────────────────────────────────────────────────

function onRoundStart(msg) {
    showPhase('game');
    document.getElementById('round-num').textContent = msg.round_num;
    document.getElementById('timer').textContent = msg.time_limit;
    document.getElementById('timer').classList.remove('urgent');
    // Remove any closed banner
    const banner = document.querySelector('.round-closed-banner');
    if (banner) banner.remove();
}

function onRoundEnd(msg) {
    document.getElementById('timer').textContent = '0';
    // Show closed banner
    const game = document.getElementById('game');
    const existing = document.querySelector('.round-closed-banner');
    if (!existing) {
        const banner = document.createElement('div');
        banner.className = 'round-closed-banner';
        banner.textContent = msg.round_num < 6 ? 'Trading closed \u2014 next round in 3s...' : 'Trading closed \u2014 calculating scores...';
        game.insertBefore(banner, game.querySelector('.trade-panel'));
    }
}

function onTimer(msg) {
    const el = document.getElementById('timer');
    el.textContent = msg.remaining;
    if (msg.remaining <= 10) el.classList.add('urgent');
}

// ── Market State ────────────────────────────────────────────────────────────

function onMarketState(msg) {
    renderMarket(msg.market);
    renderTrades(msg.trades);
    renderStandings(msg.players);
}

function renderMarket(market) {
    const grid = document.getElementById('market-grid');
    grid.innerHTML = '';
    SUITS.forEach(suit => {
        const col = document.createElement('div');
        col.className = 'market-col';
        const m = market[suit] || {};
        const bid = m.best_bid != null ? m.best_bid : '-';
        const ask = m.best_ask != null ? m.best_ask : '-';
        col.innerHTML = `
            <div class="suit-header">${suit}</div>
            <div class="bid-ask">
                <div><div class="label">Bid</div><div class="bid">${bid}</div></div>
                <div><div class="label">Ask</div><div class="ask">${ask}</div></div>
            </div>
        `;
        grid.appendChild(col);
    });
}

function renderTrades(trades) {
    const list = document.getElementById('trade-log-list');
    list.innerHTML = '';
    (trades || []).slice(-8).reverse().forEach(t => {
        const entry = document.createElement('div');
        entry.className = 'trade-entry';
        entry.innerHTML = `<span>${esc(t.buyer_name)} \u2190 <span class="trade-suit">${t.suit}</span> \u2190 ${esc(t.seller_name)}</span><span class="trade-price">${t.price}</span>`;
        list.appendChild(entry);
    });
}

function renderStandings(players) {
    const el = document.getElementById('player-standings');
    el.innerHTML = '';
    const sorted = Object.entries(players || {}).sort((a, b) => b[1].chips - a[1].chips);
    sorted.forEach(([uid, p]) => {
        const row = document.createElement('div');
        row.className = 'standing-row';
        const me = uid === myId ? ' (you)' : '';
        row.innerHTML = `<span>${esc(p.name)}${me}</span><span><span class="standing-cards">${p.card_count} cards</span> <span class="standing-chips">${p.chips} chips</span></span>`;
        el.appendChild(row);
    });
}

// ── Trade Execution ─────────────────────────────────────────────────────────

function onTrade(msg) {
    showToast(`Trade: ${msg.buyer_name} bought ${msg.suit} from ${msg.seller_name} @${msg.price}`, 'success');
}

// ── Game Over ───────────────────────────────────────────────────────────────

function onGameOver(msg) {
    showPhase('results');
    const reveal = document.getElementById('goal-reveal');
    reveal.innerHTML = `
        <div class="goal-suit">${msg.goal_suit}</div>
        <div class="goal-label">Goal Suit</div>
        <div class="goal-name">${msg.goal_suit_name} (10 pts each)</div>
        <div class="goal-label" style="margin-top:8px">Common: ${msg.common_suit} (12 cards) \u2022 Goal: ${msg.goal_suit} (10 cards)</div>
    `;

    const tbody = document.getElementById('results-body');
    tbody.innerHTML = '';
    const medals = ['\uD83E\uDD47', '\uD83E\uDD48', '\uD83E\uDD49'];
    msg.results.forEach((r, i) => {
        const tr = document.createElement('tr');
        const badge = i < 3 ? medals[i] : (i + 1);
        const netClass = r.net > 0 ? 'positive' : r.net < 0 ? 'negative' : '';
        const handStr = SUITS.map(s => `${s}${r.hand[s] || 0}`).join(' ');
        tr.innerHTML = `
            <td>${badge}</td>
            <td>${esc(r.display_name)}</td>
            <td class="num" style="font-size:0.8rem">${handStr}</td>
            <td class="num">${r.chips}</td>
            <td class="num">${r.goal_cards}\u00d710</td>
            <td class="num" style="font-weight:600">${r.score}</td>
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

// ── Event Listeners ─────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    if (!token) { initJoinScreen('figgie'); return; }
    if (!hasRoomId) { setStatus('Invalid game link', 'disconnected'); return; }
    document.getElementById('join-screen').style.display = 'none';
    connect();

    document.getElementById('start-btn').addEventListener('click', () => send({ type: 'start' }));

    document.getElementById('buy-btn').addEventListener('click', () => {
        const suit = document.getElementById('trade-suit').value;
        const price = parseInt(document.getElementById('trade-price').value);
        if (!price || price < 1) { showToast('Enter a price', 'error'); return; }
        send({ type: 'order', action: 'buy', suit, price });
        document.getElementById('trade-price').value = '';
    });

    document.getElementById('sell-btn').addEventListener('click', () => {
        const suit = document.getElementById('trade-suit').value;
        const price = parseInt(document.getElementById('trade-price').value);
        if (!price || price < 1) { showToast('Enter a price', 'error'); return; }
        send({ type: 'order', action: 'sell', suit, price });
        document.getElementById('trade-price').value = '';
    });

    document.getElementById('cancel-btn').addEventListener('click', () => {
        send({ type: 'cancel_orders' });
    });

    // Keyboard shortcut: Enter to submit buy
    document.getElementById('trade-price').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('buy-btn').click();
        }
    });
});
