/* Trading Floor — WebSocket Client + Market Simulation UI */

const params = new URLSearchParams(window.location.search);
const token = params.get('t');
const roomId = window.location.pathname.split('/').filter(Boolean).pop();

let ws;
let myId = null;
let isHost = false;
let gamePhase = 'lobby';
let selectedQty = 1;
let selectedTicker = 'CHIP';
let leverageMode = 1;
let lastStockPrices = {};
let settingsRounds = 8;
let settingsTime = 45;
let totalRounds = 8;
let myPositions = {};  // ticker -> qty, for sell/short button label

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
        case 'intel':           onIntel(msg); break;
        case 'trade_executed':  onTradeExecuted(msg); break;
        case 'analyst_pick':    onAnalystPick(msg); break;
        case 'prices_updated':  onPricesUpdated(msg); break;
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
            const conn = p.connected ? '\u25cf' : '\u25cb';
            const cls = p.connected ? 'connected' : 'disconnected';
            li.innerHTML = `<span><span class="${cls}">${conn}</span> ${esc(p.name)}${p.is_host ? ' <span class="host-badge">HOST</span>' : ''}</span><span class="wager">${p.wager}c</span>`;
            list.appendChild(li);
        });
        const startBtn = document.getElementById('start-btn');
        startBtn.style.display = isHost ? 'block' : 'none';
        startBtn.disabled = msg.players.filter(p => p.connected).length < 1;
        document.getElementById('waiting-text').style.display = isHost ? 'none' : 'block';
        document.getElementById('host-settings').style.display = isHost ? 'block' : 'none';
    }
}

// ── Game Start ────────────────────────────────────────────────────────────

function onGameStart(msg) {
    showPhase('game');
    totalRounds = msg.num_rounds || 8;
    document.getElementById('my-cash').textContent = fmt(10000);
    document.getElementById('tip-banner').style.display = 'none';
    document.getElementById('event-reveal').style.display = 'none';
    // Reset analyst slots
    for (let i = 0; i < 2; i++) {
        const slot = document.getElementById(`analyst-${i}`);
        if (slot) slot.querySelector('.analyst-call').textContent = '\u2014';
    }
    lastStockPrices = {};
}

// ── Round Start/End ───────────────────────────────────────────────────────

function onRoundStart(msg) {
    showPhase('game');
    document.getElementById('round-num').textContent = `${msg.round_num}/${totalRounds}`;
    document.getElementById('timer').textContent = msg.time_limit;
    document.getElementById('timer').classList.remove('urgent');
    document.getElementById('event-reveal').style.display = 'none';
    // Reset analyst calls for new round
    for (let i = 0; i < 2; i++) {
        const slot = document.getElementById(`analyst-${i}`);
        if (slot) {
            slot.querySelector('.analyst-call').textContent = 'Analyzing...';
            slot.className = 'analyst-slot';
        }
    }
    // Remove closed banner
    const banner = document.querySelector('.round-closed-banner');
    if (banner) banner.remove();
    // Remove any price flash animations
    document.querySelectorAll('.stock-card').forEach(c => {
        c.classList.remove('flash-up', 'flash-down');
    });
}

function onRoundEnd(msg) {
    document.getElementById('timer').textContent = '0';
    const game = document.getElementById('game');
    const existing = document.querySelector('.round-closed-banner');
    if (!existing) {
        const banner = document.createElement('div');
        banner.className = 'round-closed-banner';
        banner.textContent = msg.round_num < totalRounds
            ? 'Trading closed \u2014 settling round...'
            : 'Trading closed \u2014 calculating final results...';
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
    renderRecentTrades(msg.recent_trades);
}

function renderStocks(stocks) {
    const grid = document.getElementById('stock-grid');
    grid.innerHTML = '';
    for (const [ticker, s] of Object.entries(stocks)) {
        const card = document.createElement('div');
        let extraClass = s.halted ? ' halted' : '';
        card.className = 'stock-card' + extraClass;
        card.id = `stock-${ticker}`;
        const changeClass = s.change_pct > 0 ? 'positive' : s.change_pct < 0 ? 'negative' : '';
        const changeStr = s.change_pct > 0 ? `+${s.change_pct}%` : `${s.change_pct}%`;
        card.innerHTML = `
            <div class="stock-emoji">${s.emoji}</div>
            <div class="stock-ticker">${ticker}</div>
            <div class="stock-price">${s.price.toFixed(1)}</div>
            <div class="stock-change ${changeClass}">${changeStr}</div>
            ${s.halted ? '<div style="color:var(--red);font-size:0.7rem;font-weight:600">HALTED</div>' : ''}
        `;
        grid.appendChild(card);
        lastStockPrices[ticker] = s.price;
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

function renderRecentTrades(trades) {
    const list = document.getElementById('trade-log-list');
    list.innerHTML = '';
    (trades || []).slice(-8).reverse().forEach(t => {
        const entry = document.createElement('div');
        entry.className = 'trade-entry';
        entry.innerHTML = `<span><span class="trade-action ${t.action}">${t.action.toUpperCase()}</span> ${t.ticker} \u00d7${t.qty} by ${esc(t.player)}</span><span class="trade-price">${t.price}</span>`;
        list.appendChild(entry);
    });
}

// ── Portfolio ─────────────────────────────────────────────────────────────

function updateSellButtonLabel() {
    const btn = document.getElementById('sell-btn');
    if (!btn) return;
    const qty = myPositions[selectedTicker] || 0;
    btn.textContent = qty > 0 ? 'Sell' : 'Short';
}

function onPortfolio(msg) {
    document.getElementById('my-cash').textContent = fmt(msg.cash);
    document.getElementById('portfolio-value').textContent = fmt(msg.portfolio_value) + 'c';
    // Track positions for sell/short button label
    myPositions = {};
    (msg.positions || []).forEach(p => { myPositions[p.ticker] = p.qty; });
    updateSellButtonLabel();
    const pnl = msg.pnl;
    const pnlEl = document.getElementById('portfolio-pnl');
    pnlEl.textContent = (pnl > 0 ? '+' : '') + fmt(pnl) + 'c';
    pnlEl.className = 'num ' + (pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '');

    const posList = document.getElementById('positions-list');
    const pv = msg.portfolio_value || 10000;
    if (!msg.positions || msg.positions.length === 0) {
        posList.innerHTML = '<span style="color:var(--text-muted)">No positions \u2014 100% cash</span>';
    } else {
        posList.innerHTML = '';
        msg.positions.forEach(p => {
            const row = document.createElement('div');
            row.className = 'position-row';
            const label = p.qty < 0 ? `SHORT ${Math.abs(p.qty)}` : `LONG ${p.qty}`;
            const pnlClass = p.pnl > 0 ? 'positive' : p.pnl < 0 ? 'negative' : '';
            const pnlStr = p.pnl > 0 ? `+${fmt(p.pnl)}` : fmt(p.pnl);
            const alloc = pv > 0 ? Math.abs(Math.round(p.value / pv * 100)) : 0;
            row.innerHTML = `<span class="pos-ticker">${p.emoji} ${p.ticker} <span class="pos-alloc">${alloc}%</span></span><span class="pos-qty">${label} @ ${p.avg_entry} \u2192 ${p.price} <span class="${pnlClass}">(${pnlStr}c)</span></span>`;
            posList.appendChild(row);
        });
        // Show cash allocation
        const cashAlloc = pv > 0 ? Math.round(msg.cash / pv * 100) : 100;
        const cashRow = document.createElement('div');
        cashRow.className = 'position-row cash-row';
        cashRow.innerHTML = `<span class="pos-ticker">\ud83d\udcb5 CASH <span class="pos-alloc">${cashAlloc}%</span></span><span class="pos-qty">${fmt(msg.cash)}c</span>`;
        posList.appendChild(cashRow);
    }
}

// ── Tip ───────────────────────────────────────────────────────────────────

function onTip(msg) {
    // Legacy tip handler (kept for compatibility)
    const banner = document.getElementById('tip-banner');
    banner.style.display = 'block';
    banner.innerHTML = `<div class="tip-label">\ud83d\udd12 Insider Tip \u2014 Round ${msg.round}</div>${esc(msg.text)}`;
    setTimeout(() => { banner.style.display = 'none'; }, 35000);
}

function onIntel(msg) {
    // Range intel: 1 stock with a range estimate
    const banner = document.getElementById('tip-banner');
    banner.style.display = 'block';
    const low = msg.low;
    const high = msg.high;
    const midpoint = (low + high) / 2;
    const cls = midpoint > 0 ? 'positive' : midpoint < 0 ? 'negative' : '';
    const lowStr = low > 0 ? `+${low}%` : `${low}%`;
    const highStr = high > 0 ? `+${high}%` : `${high}%`;
    banner.innerHTML = `<div class="tip-label">\ud83d\udd0d Intel Report \u2014 Round ${msg.round}</div><div class="intel-effects"><span class="intel-effect ${cls}"><strong>${esc(msg.ticker)}</strong> ${lowStr} to ${highStr}</span></div>`;
}

// ── Trade Executed ────────────────────────────────────────────────────────

function onTradeExecuted(msg) {
    showToast(`${msg.player_name}: ${msg.action} ${msg.qty}\u00d7 ${msg.ticker} @ ${msg.price}`, 'success');
}

// ── Analyst Picks (2 persistent slots) ───────────────────────────────────

const analystSlotMap = {
    'Cramer': 0,
    'Pelosi': 1,
};

function onAnalystPick(msg) {
    const slotIdx = analystSlotMap[msg.analyst];
    if (slotIdx === undefined) return;
    const slot = document.getElementById(`analyst-${slotIdx}`);
    if (!slot) return;

    const callEl = slot.querySelector('.analyst-call');
    callEl.innerHTML = `
        <div class="pick-row"><span class="pick-tag buy">BUY</span><span class="pick-ticker">${esc(msg.buy_ticker)}</span><span class="pick-reason">${esc(msg.buy_reason)}</span></div>
        <div class="pick-row"><span class="pick-tag sell">SELL</span><span class="pick-ticker">${esc(msg.sell_ticker)}</span><span class="pick-reason">${esc(msg.sell_reason)}</span></div>
    `;

    slot.classList.add('pick-flash');
    setTimeout(() => slot.classList.remove('pick-flash'), 600);
}

// ── Price Update Animation ───────────────────────────────────────────────

function onPricesUpdated(msg) {
    for (const [ticker, data] of Object.entries(msg.stocks)) {
        const card = document.getElementById(`stock-${ticker}`);
        if (!card) continue;

        const change = data.change;
        const cls = change > 0 ? 'flash-up' : change < 0 ? 'flash-down' : '';
        if (cls) card.classList.add(cls);

        // Floating change indicator
        const overlay = document.createElement('div');
        overlay.className = `price-change-overlay ${change > 0 ? 'up' : 'down'}`;
        overlay.textContent = change > 0 ? `+${change.toFixed(1)}` : change.toFixed(1);
        card.appendChild(overlay);

        // Smooth count animation: price rolls from old to new over 1.5s
        const priceEl = card.querySelector('.stock-price');
        if (priceEl) {
            const startVal = data.prev;
            const endVal = data.price;
            const duration = 1500;
            const startTime = performance.now();
            function tick(now) {
                const elapsed = now - startTime;
                const progress = Math.min(elapsed / duration, 1);
                // Ease out quad
                const eased = 1 - (1 - progress) * (1 - progress);
                const current = startVal + (endVal - startVal) * eased;
                priceEl.textContent = current.toFixed(1);
                if (progress < 1) requestAnimationFrame(tick);
            }
            requestAnimationFrame(tick);
        }

        setTimeout(() => {
            overlay.remove();
            card.classList.remove('flash-up', 'flash-down');
        }, 2500);
    }
}

// ── Event Reveal (animate on stock cards) ────────────────────────────────

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
    document.getElementById('tip-banner').style.display = 'none';
}

// ── Game Over ─────────────────────────────────────────────────────────────

function onGameOver(msg) {
    showPhase('results');

    const stockResults = document.getElementById('stock-results');
    stockResults.innerHTML = '';
    for (const [ticker, s] of Object.entries(msg.stocks)) {
        const card = document.createElement('div');
        card.className = 'stock-result-card';
        const retClass = s.return > 0 ? 'positive' : s.return < 0 ? 'negative' : '';
        const retStr = s.return > 0 ? `+${s.return}%` : `${s.return}%`;
        card.innerHTML = `
            <div>${s.emoji}</div>
            <div class="result-ticker">${ticker}</div>
            <div class="result-price">${s.final_price.toFixed(1)}c</div>
            <div class="result-return ${retClass}">${retStr}</div>
        `;
        stockResults.appendChild(card);
    }

    const tbody = document.getElementById('results-body');
    tbody.innerHTML = '';
    const medals = ['\ud83e\udd47', '\ud83e\udd48', '\ud83e\udd49'];
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
    const chars = '\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588';
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

    document.getElementById('start-btn').addEventListener('click', () => {
        send({ type: 'start', rounds: settingsRounds, round_seconds: settingsTime });
    });

    // Rounds selector
    document.querySelectorAll('.rounds-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.rounds-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            settingsRounds = parseInt(btn.dataset.rounds);
        });
    });

    // Round time selector
    document.querySelectorAll('.time-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            settingsTime = parseInt(btn.dataset.time);
        });
    });

    // Leverage selector (3 buttons)
    document.querySelectorAll('.leverage-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.leverage-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            leverageMode = parseInt(btn.dataset.lev);
        });
    });

    // Ticker selector (4 buttons)
    document.querySelectorAll('.ticker-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.ticker-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            selectedTicker = btn.dataset.ticker;
            updateSellButtonLabel();
        });
    });

    // Quantity selector
    document.querySelectorAll('.qty-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.qty-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            selectedQty = parseInt(btn.dataset.qty);
        });
    });

    document.getElementById('buy-btn').addEventListener('click', () => {
        send({ type: 'buy', ticker: selectedTicker, qty: selectedQty * leverageMode });
    });

    // Sell button: sells if you have shares, shorts if you don't
    document.getElementById('sell-btn').addEventListener('click', () => {
        send({ type: 'sell_or_short', ticker: selectedTicker, qty: selectedQty * leverageMode });
    });

    // Close position: sells all longs or covers all shorts
    document.getElementById('sell-all-btn').addEventListener('click', () => {
        send({ type: 'close_position', ticker: selectedTicker });
    });

});
