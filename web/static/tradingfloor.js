/* Trading Floor — WebSocket Client + Market Simulation UI */

const params = new URLSearchParams(window.location.search);
const token = params.get('t');
const roomId = window.location.pathname.split('/').filter(Boolean).pop();

let ws;
let myId = null;
let isHost = false;
let gamePhase = 'lobby';
let selectedQty = 1;

// ── WebSocket ────────────────────────────────────────────────────────────

function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/tradingfloor/${roomId}?t=${token}`;
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
        case 'round_start':     onRoundStart(msg); break;
        case 'round_end':       onRoundEnd(msg); break;
        case 'timer':           onTimer(msg); break;
        case 'market_state':    onMarketState(msg); break;
        case 'portfolio':       onPortfolio(msg); break;
        case 'tip':             onTip(msg); break;
        case 'trade_executed':  onTradeExecuted(msg); break;
        case 'event_reveal':    onEventReveal(msg); break;
        case 'game_over':       onGameOver(msg); break;
        case 'error':           showToast(msg.message, 'error'); break;
    }
}

function showPhase(id) {
    gamePhase = id;
    document.querySelectorAll('.phase').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

// ── Lobby ─────────────────────────────────────────────────────────────────

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
            const conn = p.connected ? '●' : '○';
            const cls = p.connected ? 'connected' : 'disconnected';
            li.innerHTML = `<span><span class="${cls}">${conn}</span> ${esc(p.name)}${p.is_host ? ' <span class="host-badge">HOST</span>' : ''}</span><span class="wager">${p.wager}c</span>`;
            list.appendChild(li);
        });
        const startBtn = document.getElementById('start-btn');
        startBtn.style.display = isHost ? 'block' : 'none';
        startBtn.disabled = msg.players.filter(p => p.connected).length < 2;
        document.getElementById('waiting-text').style.display = isHost ? 'none' : 'block';
    }
}

// ── Game Start ────────────────────────────────────────────────────────────

function onGameStart(msg) {
    showPhase('game');
    document.getElementById('my-cash').textContent = fmt(10000);
    // Hide tip and event from previous
    document.getElementById('tip-banner').style.display = 'none';
    document.getElementById('event-reveal').style.display = 'none';
}

// ── Round Start/End ───────────────────────────────────────────────────────

function onRoundStart(msg) {
    showPhase('game');
    document.getElementById('round-num').textContent = msg.round_num;
    document.getElementById('timer').textContent = msg.time_limit;
    document.getElementById('timer').classList.remove('urgent');
    document.getElementById('event-reveal').style.display = 'none';
    // Remove closed banner
    const banner = document.querySelector('.round-closed-banner');
    if (banner) banner.remove();
}

function onRoundEnd(msg) {
    document.getElementById('timer').textContent = '0';
    const game = document.getElementById('game');
    const existing = document.querySelector('.round-closed-banner');
    if (!existing) {
        const banner = document.createElement('div');
        banner.className = 'round-closed-banner';
        banner.textContent = msg.round_num < 8
            ? 'Trading closed — settling round...'
            : 'Trading closed — calculating final results...';
        game.insertBefore(banner, game.querySelector('.trade-panel'));
    }
}

function onTimer(msg) {
    const el = document.getElementById('timer');
    el.textContent = msg.remaining;
    if (msg.remaining <= 10) el.classList.add('urgent');
}

// ── Market State ──────────────────────────────────────────────────────────

function onMarketState(msg) {
    renderStocks(msg.stocks);
    renderStandings(msg.standings);
    renderNews(msg.news);
    renderRecentTrades(msg.recent_trades);
}

function renderStocks(stocks) {
    const grid = document.getElementById('stock-grid');
    grid.innerHTML = '';
    for (const [ticker, s] of Object.entries(stocks)) {
        const card = document.createElement('div');
        card.className = 'stock-card' + (s.halted ? ' halted' : '');
        const changeClass = s.change_pct > 0 ? 'positive' : s.change_pct < 0 ? 'negative' : '';
        const changeStr = s.change_pct > 0 ? `+${s.change_pct}%` : `${s.change_pct}%`;
        let bookHtml = '';
        if (s.best_bid != null || s.best_ask != null) {
            const bid = s.best_bid != null ? s.best_bid : '-';
            const ask = s.best_ask != null ? s.best_ask : '-';
            bookHtml = `<div class="stock-book"><span class="bid">${bid}</span> / <span class="ask">${ask}</span></div>`;
        }
        card.innerHTML = `
            <div class="stock-emoji">${s.emoji}</div>
            <div class="stock-ticker">${ticker}</div>
            <div class="stock-price">${s.price.toFixed(1)}</div>
            <div class="stock-change ${changeClass}">${changeStr}</div>
            <div class="stock-sparkline">${s.sparkline || ''}</div>
            ${bookHtml}
            ${s.halted ? '<div style="color:var(--red);font-size:0.7rem;font-weight:600">HALTED</div>' : ''}
        `;
        grid.appendChild(card);
    }
}

function renderStandings(standings) {
    const el = document.getElementById('standings');
    el.innerHTML = '';
    standings.forEach((s, i) => {
        const row = document.createElement('div');
        row.className = 'standing-row';
        const me = s.id === myId ? ' (you)' : '';
        const pnlClass = s.pnl > 0 ? 'positive' : s.pnl < 0 ? 'negative' : '';
        const pnlStr = s.pnl > 0 ? `+${fmt(s.pnl)}` : fmt(s.pnl);
        row.innerHTML = `<span>${i + 1}. ${esc(s.name)}${me}</span><span><span class="standing-pv">${fmt(s.portfolio_value)}c</span><span class="standing-pnl ${pnlClass}">${pnlStr}</span></span>`;
        el.appendChild(row);
    });
}

function renderNews(news) {
    const list = document.getElementById('news-list');
    list.innerHTML = '';
    (news || []).forEach(n => {
        const item = document.createElement('div');
        item.className = 'news-item';
        item.textContent = n;
        list.appendChild(item);
    });
}

function renderRecentTrades(trades) {
    const list = document.getElementById('trade-log-list');
    list.innerHTML = '';
    (trades || []).slice(-8).reverse().forEach(t => {
        const entry = document.createElement('div');
        entry.className = 'trade-entry';
        entry.innerHTML = `<span><span class="trade-action ${t.action}">${t.action.toUpperCase()}</span> ${t.ticker} ×${t.qty} by ${esc(t.player)}</span><span class="trade-price">${t.price}</span>`;
        list.appendChild(entry);
    });
}

// ── Portfolio ─────────────────────────────────────────────────────────────

function onPortfolio(msg) {
    document.getElementById('my-cash').textContent = fmt(msg.cash);
    document.getElementById('portfolio-value').textContent = fmt(msg.portfolio_value) + 'c';
    const pnl = msg.pnl;
    const pnlEl = document.getElementById('portfolio-pnl');
    pnlEl.textContent = (pnl > 0 ? '+' : '') + fmt(pnl) + 'c';
    pnlEl.className = 'num ' + (pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '');

    const posList = document.getElementById('positions-list');
    if (!msg.positions || msg.positions.length === 0) {
        posList.innerHTML = '<span style="color:var(--text-muted)">No positions</span>';
    } else {
        posList.innerHTML = '';
        msg.positions.forEach(p => {
            const row = document.createElement('div');
            row.className = 'position-row';
            const label = p.qty < 0 ? `SHORT ${Math.abs(p.qty)}` : `LONG ${p.qty}`;
            const valClass = p.value > 0 ? 'positive' : p.value < 0 ? 'negative' : '';
            row.innerHTML = `<span class="pos-ticker">${p.emoji} ${p.ticker}</span><span class="pos-qty">${label} @ ${p.price} = <span class="${valClass}">${fmt(p.value)}c</span></span>`;
            posList.appendChild(row);
        });
    }
}

// ── Tip ───────────────────────────────────────────────────────────────────

function onTip(msg) {
    const banner = document.getElementById('tip-banner');
    banner.style.display = 'block';
    banner.innerHTML = `<div class="tip-label">🔒 Insider Tip — Round ${msg.round}</div>${esc(msg.text)}`;
    // Auto-hide after 30s
    setTimeout(() => { banner.style.display = 'none'; }, 30000);
}

// ── Trade Executed ────────────────────────────────────────────────────────

function onTradeExecuted(msg) {
    showToast(`${msg.player_name}: ${msg.action} ${msg.qty}× ${msg.ticker} @ ${msg.price}`, 'success');
}

// ── Event Reveal ──────────────────────────────────────────────────────────

function onEventReveal(msg) {
    const el = document.getElementById('event-reveal');
    el.style.display = 'block';
    const evt = msg.event;
    let effectsHtml = '';
    for (const [ticker, pct] of Object.entries(evt.effects)) {
        if (pct === 0) continue;
        const cls = pct > 0 ? 'positive' : 'negative';
        const str = pct > 0 ? `+${(pct * 100).toFixed(0)}%` : `${(pct * 100).toFixed(0)}%`;
        effectsHtml += `<span class="${cls}">${ticker} ${str}</span>`;
    }
    el.innerHTML = `
        <div class="event-emoji">${evt.emoji}</div>
        <div class="event-name">${esc(evt.name)}</div>
        <div class="event-desc">${esc(evt.desc)}</div>
        <div class="event-effects">${effectsHtml}</div>
    `;
    // Hide tip banner now that event is revealed
    document.getElementById('tip-banner').style.display = 'none';
}

// ── Game Over ─────────────────────────────────────────────────────────────

function onGameOver(msg) {
    showPhase('results');

    // Stock performance cards
    const stockResults = document.getElementById('stock-results');
    stockResults.innerHTML = '';
    for (const [ticker, s] of Object.entries(msg.stocks)) {
        const card = document.createElement('div');
        card.className = 'stock-result-card';
        const retClass = s.return > 0 ? 'positive' : s.return < 0 ? 'negative' : '';
        const retStr = s.return > 0 ? `+${s.return}%` : `${s.return}%`;
        // Build sparkline from history
        const spark = sparkline(s.history);
        card.innerHTML = `
            <div>${s.emoji}</div>
            <div class="result-ticker">${ticker}</div>
            <div class="result-price">${s.final_price.toFixed(1)}c</div>
            <div class="result-return ${retClass}">${retStr}</div>
            <div class="result-sparkline">${spark}</div>
        `;
        stockResults.appendChild(card);
    }

    // Results table
    const tbody = document.getElementById('results-body');
    tbody.innerHTML = '';
    const medals = ['🥇', '🥈', '🥉'];
    msg.results.forEach((r, i) => {
        const tr = document.createElement('tr');
        const badge = i < 3 ? medals[i] : (i + 1);
        const pnlClass = r.pnl > 0 ? 'positive' : r.pnl < 0 ? 'negative' : '';
        const netClass = r.net > 0 ? 'positive' : r.net < 0 ? 'negative' : '';
        tr.innerHTML = `
            <td>${badge}</td>
            <td>${esc(r.display_name)}</td>
            <td class="num">${fmt(r.final_cash)}c</td>
            <td class="num ${pnlClass}">${r.pnl > 0 ? '+' : ''}${fmt(r.pnl)}</td>
            <td class="num">${r.trades}</td>
            <td class="num ${netClass}">${r.net > 0 ? '+' : ''}${r.net}c</td>
        `;
        tbody.appendChild(tr);
    });
}

// ── Helpers ───────────────────────────────────────────────────────────────

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function fmt(n) {
    return Math.round(n).toLocaleString();
}

function sparkline(history) {
    if (!history || history.length < 2) return '';
    const chars = '▁▂▃▄▅▆▇█';
    const lo = Math.min(...history);
    const hi = Math.max(...history);
    const rng = hi - lo;
    if (rng === 0) return chars[3].repeat(history.length);
    return history.map(v => chars[Math.min(7, Math.floor((v - lo) / rng * 7))]).join('');
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

// ── Event Listeners ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    if (!token || !roomId) { setStatus('Invalid game link', 'disconnected'); return; }
    connect();

    document.getElementById('start-btn').addEventListener('click', () => send({ type: 'start' }));

    // Quantity selector
    document.querySelectorAll('.qty-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.qty-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            selectedQty = parseInt(btn.dataset.qty);
        });
    });

    document.getElementById('buy-btn').addEventListener('click', () => {
        const ticker = document.getElementById('trade-ticker').value;
        send({ type: 'buy', ticker, qty: selectedQty });
    });

    document.getElementById('sell-btn').addEventListener('click', () => {
        const ticker = document.getElementById('trade-ticker').value;
        send({ type: 'sell', ticker, qty: selectedQty });
    });

    document.getElementById('short-btn').addEventListener('click', () => {
        const ticker = document.getElementById('trade-ticker').value;
        send({ type: 'short', ticker, qty: selectedQty });
    });

    document.getElementById('sell-all-btn').addEventListener('click', () => {
        const ticker = document.getElementById('trade-ticker').value;
        send({ type: 'sell_all', ticker });
    });

    document.getElementById('cover-btn').addEventListener('click', () => {
        const ticker = document.getElementById('trade-ticker').value;
        send({ type: 'cover', ticker });
    });

    document.getElementById('cancel-btn').addEventListener('click', () => {
        send({ type: 'cancel_orders' });
    });
});
