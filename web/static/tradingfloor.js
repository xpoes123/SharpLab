/* Trading Floor — WebSocket Client + Market Simulation UI */

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
let selectedQty = 1;
let selectedTicker = 'CHIP';
let leverageMode = 1;
let lastStockPrices = {};
let settingsRounds = 8;
let settingsTime = 45;
let totalRounds = 8;
let myPositions = {};
let tradeOpen = false;
let timerInterval = null;

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
        case 'intel':           onIntel(msg); break;
        case 'trade_executed':  onTradeExecuted(msg); break;
        case 'analyst_pick':    onAnalystPick(msg); break;
        case 'prices_updated':  onPricesUpdated(msg); break;
        case 'event_reveal':    onEventReveal(msg); break;
        case 'round_recap':     onRoundRecap(msg); break;
        case 'game_over':       onGameOver(msg); break;
        case 'error':           showToast(msg.message, 'error'); break;
    }
}

function showPhase(id) {
    gamePhase = id;
    document.querySelectorAll('.phase').forEach(el => el.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

function setTradeButtons(enabled) {
    tradeOpen = enabled;
    const btns = ['buy-btn', 'sell-btn', 'sell-all-btn'];
    btns.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) {
            btn.disabled = !enabled;
            btn.style.opacity = enabled ? '1' : '0.4';
        }
    });
}

function selectTicker(ticker) {
    selectedTicker = ticker;
    document.querySelectorAll('.ticker-btn').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`.ticker-btn[data-ticker="${ticker}"]`);
    if (btn) btn.classList.add('active');
    updateSellButtonLabel();
}

// ── Public Join ──────────────────────────────────────────────────────────

function initJoinScreen() {
    const screen = document.getElementById('join-screen');
    screen.classList.add('active');
    const nameInput = document.getElementById('player-name');
    const createBtn = document.getElementById('create-room-btn');
    const joinBtn = document.getElementById('join-room-btn');
    const errorEl = document.getElementById('join-error');

    if (hasRoomId) {
        createBtn.style.display = 'none';
        joinBtn.style.display = 'block';
    }

    nameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') (hasRoomId ? joinBtn : createBtn).click();
    });

    createBtn.addEventListener('click', async () => {
        const name = nameInput.value.trim();
        if (!name) { errorEl.textContent = 'Enter a name'; return; }
        createBtn.disabled = true;
        try {
            const resp = await fetch(`/api/v1/tradingfloor/public/create`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({display_name: name}),
            });
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
            const resp = await fetch(`/api/v1/tradingfloor/public/join/${roomId}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({display_name: name}),
            });
            if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed');
            const data = await resp.json();
            window.location.href = data.url;
        } catch (e) { errorEl.textContent = e.message; joinBtn.disabled = false; }
    });
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
        // Show invite link
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

// ── Game Start ────────────────────────────────────────────────────────────

function onGameStart(msg) {
    showPhase('game');
    totalRounds = msg.num_rounds || 8;
    document.getElementById('my-cash').textContent = fmt(10000);
    document.getElementById('tip-banner').style.display = 'none';
    document.getElementById('event-reveal').style.display = 'none';
    for (let i = 0; i < 2; i++) {
        const slot = document.getElementById(`analyst-${i}`);
        if (slot) slot.querySelector('.analyst-call').textContent = '\u2014';
    }
    lastStockPrices = {};
    setTradeButtons(false);
}

// ── Round Start/End ───────────────────────────────────────────────────────

function onRoundStart(msg) {
    showPhase('game');
    document.getElementById('round-num').textContent = `${msg.round_num}/${totalRounds}`;
    document.getElementById('timer').classList.remove('urgent');
    document.getElementById('event-reveal').style.display = 'none';
    setTradeButtons(true);
    // Reset analyst calls
    for (let i = 0; i < 2; i++) {
        const slot = document.getElementById(`analyst-${i}`);
        if (slot) {
            slot.querySelector('.analyst-call').textContent = 'Analyzing...';
            slot.className = 'analyst-slot';
        }
    }
    const banner = document.querySelector('.round-closed-banner');
    if (banner) banner.remove();
    document.querySelectorAll('.stock-card').forEach(c => {
        c.classList.remove('flash-up', 'flash-down');
    });

    // Client-side timer countdown
    clearInterval(timerInterval);
    let remaining = msg.time_limit;
    document.getElementById('timer').textContent = remaining;
    timerInterval = setInterval(() => {
        remaining--;
        if (remaining < 0) remaining = 0;
        const el = document.getElementById('timer');
        el.textContent = remaining;
        if (remaining <= 10) el.classList.add('urgent');
        if (remaining <= 0) clearInterval(timerInterval);
    }, 1000);
}

function onRoundEnd(msg) {
    clearInterval(timerInterval);
    document.getElementById('timer').textContent = '0';
    setTradeButtons(false);
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
    // Server timer sync (corrects drift)
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
        const sectorTag = s.sector === 'tech' ? 'TECH' : 'ENERGY';
        card.innerHTML = `
            <div class="stock-emoji">${s.emoji}</div>
            <div class="stock-ticker">${ticker} <span class="stock-sector">${sectorTag}</span></div>
            <div class="stock-price">${s.price.toFixed(1)}</div>
            <div class="stock-change ${changeClass}">${changeStr}</div>
            ${s.halted ? '<div style="color:var(--red);font-size:0.7rem;font-weight:600">HALTED</div>' : ''}
        `;
        // Clickable stock cards to select ticker
        card.addEventListener('click', () => selectTicker(ticker));
        card.style.cursor = 'pointer';
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
    if (qty > 0) {
        btn.textContent = 'Sell';
        btn.className = 'trade-btn sell-btn';
    } else {
        btn.textContent = 'Short';
        btn.className = 'trade-btn short-btn';
    }
}

function onPortfolio(msg) {
    document.getElementById('my-cash').textContent = fmt(msg.cash);
    document.getElementById('portfolio-value').textContent = fmt(msg.portfolio_value) + 'c';
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
        const cashAlloc = pv > 0 ? Math.round(msg.cash / pv * 100) : 100;
        const cashRow = document.createElement('div');
        cashRow.className = 'position-row cash-row';
        cashRow.innerHTML = `<span class="pos-ticker">\ud83d\udcb5 CASH <span class="pos-alloc">${cashAlloc}%</span></span><span class="pos-qty">${fmt(msg.cash)}c</span>`;
        posList.appendChild(cashRow);
    }
}

// ── Intel ─────────────────────────────────────────────────────────────────

function onIntel(msg) {
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

// ── Analyst Picks ────────────────────────────────────────────────────────

const analystSlotMap = { 'Cramer': 0, 'Pelosi': 1 };

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
        const overlay = document.createElement('div');
        overlay.className = `price-change-overlay ${change > 0 ? 'up' : 'down'}`;
        overlay.textContent = change > 0 ? `+${change.toFixed(1)}` : change.toFixed(1);
        card.appendChild(overlay);
        const priceEl = card.querySelector('.stock-price');
        if (priceEl) {
            const startVal = data.prev;
            const endVal = data.price;
            const duration = 1500;
            const startTime = performance.now();
            function tick(now) {
                const elapsed = now - startTime;
                const progress = Math.min(elapsed / duration, 1);
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

// ── Event Reveal ─────────────────────────────────────────────────────────

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
    // Keep intel visible alongside event reveal (don't hide it)
}

// ── Round Recap ──────────────────────────────────────────────────────────

function onRoundRecap(msg) {
    // Update analyst slots with accuracy feedback
    (msg.analyst_results || []).forEach(ar => {
        const slotIdx = analystSlotMap[ar.analyst];
        if (slotIdx === undefined) return;
        const slot = document.getElementById(`analyst-${slotIdx}`);
        if (!slot) return;
        const callEl = slot.querySelector('.analyst-call');
        const buyIcon = ar.buy_right ? '\u2705' : '\u274c';
        const sellIcon = ar.sell_right ? '\u2705' : '\u274c';
        callEl.innerHTML = `
            <div class="pick-row"><span class="pick-tag buy">BUY</span><span class="pick-ticker">${esc(ar.buy_ticker)}</span><span>${buyIcon}</span></div>
            <div class="pick-row"><span class="pick-tag sell">SELL</span><span class="pick-ticker">${esc(ar.sell_ticker)}</span><span>${sellIcon}</span></div>
        `;
    });

    // Show lingering effects warning
    if (msg.lingering && msg.lingering.length > 0) {
        const banner = document.getElementById('tip-banner');
        banner.style.display = 'block';
        let html = '<div class="tip-label">\u26a0\ufe0f Lingering Effects</div>';
        msg.lingering.forEach(l => {
            html += `<div style="font-size:0.8rem;color:var(--text-muted)">${esc(l.name)} \u2014 ${l.rounds_left} round${l.rounds_left > 1 ? 's' : ''} remaining (half strength)</div>`;
        });
        banner.innerHTML = html;
    }
}

// ── Game Over ─────────────────────────────────────────────────────────────

function onGameOver(msg) {
    showPhase('results');
    clearInterval(timerInterval);

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
        const winnerClass = r.is_winner ? ' winner-row' : '';
        tr.className = winnerClass;
        tr.innerHTML = `
            <td>${badge}${r.is_winner ? ' \ud83d\udc51' : ''}</td>
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
    if (!token) {
        initJoinScreen();
        return;
    }
    if (!hasRoomId) { setStatus('Invalid game link', 'disconnected'); return; }
    document.getElementById('join-screen').style.display = 'none';
    connect();

    document.getElementById('start-btn').addEventListener('click', () => {
        send({ type: 'start', rounds: settingsRounds, round_seconds: settingsTime });
    });

    document.querySelectorAll('.rounds-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.rounds-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            settingsRounds = parseInt(btn.dataset.rounds);
        });
    });

    document.querySelectorAll('.time-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            settingsTime = parseInt(btn.dataset.time);
        });
    });

    document.querySelectorAll('.leverage-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.leverage-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            leverageMode = parseInt(btn.dataset.lev);
        });
    });

    document.querySelectorAll('.ticker-btn').forEach(btn => {
        btn.addEventListener('click', () => selectTicker(btn.dataset.ticker));
    });

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

    document.getElementById('sell-btn').addEventListener('click', () => {
        send({ type: 'sell_or_short', ticker: selectedTicker, qty: selectedQty * leverageMode });
    });

    document.getElementById('sell-all-btn').addEventListener('click', () => {
        send({ type: 'close_position', ticker: selectedTicker });
    });
});
