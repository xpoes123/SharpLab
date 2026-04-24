/* SharpLab Leaderboard — Frontend Logic */

const API = '/api/v1';

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmt(n) {
    if (n == null) return '-';
    return Number(n).toLocaleString();
}

function fmtSign(n) {
    if (n == null) return '-';
    const s = Number(n).toLocaleString();
    return n > 0 ? '+' + s : s;
}

function profitClass(n) {
    if (n > 0) return 'positive';
    if (n < 0) return 'negative';
    return '';
}

function rankBadge(i) {
    if (i === 0) return '\uD83E\uDD47';
    if (i === 1) return '\uD83E\uDD48';
    if (i === 2) return '\uD83E\uDD49';
    return (i + 1).toString();
}

function rankClass(i) {
    if (i === 0) return 'rank rank-1';
    if (i === 1) return 'rank rank-2';
    if (i === 2) return 'rank rank-3';
    return 'rank';
}

function avatarImg(url) {
    if (url) return `<img class="avatar" src="${url}" alt="" loading="lazy">`;
    return '<div class="avatar"></div>';
}

function spinner() {
    return '<div class="loading"><div class="spinner"></div>Loading...</div>';
}

function empty(msg) {
    return `<div class="empty">${msg}</div>`;
}

// ── Tab Switching ───────────────────────────────────────────────────────────

function initTabs() {
    const tabs = document.querySelectorAll('.tab');
    const contents = document.querySelectorAll('.tab-content');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(tab.dataset.tab).classList.add('active');

            // Load data for the tab if not loaded
            const target = tab.dataset.tab;
            if (target === 'casino' && !casinoLoaded) loadCasinoLeaderboard();
            if (target === 'per-game' && !gamesListLoaded) loadGamesList();
            if (target === 'trading' && !tradingLoaded) loadTradingLeaderboard();
        });
    });
}

// ── Casino Leaderboard ──────────────────────────────────────────────────────

let casinoLoaded = false;

async function loadCasinoLeaderboard() {
    const el = document.getElementById('casino-table');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/casino/leaderboard?limit=50`);
        const data = await res.json();
        if (!data.leaderboard.length) { el.innerHTML = empty('No data yet'); return; }
        casinoLoaded = true;
        el.innerHTML = renderCasinoTable(data.leaderboard);
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderCasinoTable(rows) {
    let html = `<table class="leaderboard-table">
        <thead><tr>
            <th class="rank">#</th>
            <th>Player</th>
            <th>Balance</th>
            <th>Net P/L</th>
            <th class="hide-mobile">Rounds</th>
        </tr></thead><tbody>`;

    rows.forEach((r, i) => {
        html += `<tr onclick="location.href='/player/${r.discord_user}'">
            <td class="${rankClass(i)}">${rankBadge(i)}</td>
            <td><div class="user-cell">${avatarImg(r.avatar_url)}<span>${esc(r.username)}</span></div></td>
            <td class="num">${fmt(r.balance)}c</td>
            <td class="num ${profitClass(r.net_profit)}">${fmtSign(r.net_profit)}c</td>
            <td class="num hide-mobile text-muted">${fmt(r.rounds)}</td>
        </tr>`;
    });

    return html + '</tbody></table>';
}

// ── Per-Game Leaderboard ────────────────────────────────────────────────────

let gamesListLoaded = false;

async function loadGamesList() {
    try {
        const res = await fetch(`${API}/games`);
        const data = await res.json();
        const select = document.getElementById('game-select');
        data.games.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g.key;
            opt.textContent = g.label;
            select.appendChild(opt);
        });
        gamesListLoaded = true;
        // Load first game
        if (data.games.length) {
            select.value = data.games[0].key;
            loadGameLeaderboard(data.games[0].key);
        }
    } catch (e) {
        console.error('Failed to load games list', e);
    }
}

async function loadGameLeaderboard(game) {
    const el = document.getElementById('game-table');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/casino/leaderboard/${game}?limit=50`);
        const data = await res.json();
        if (!data.leaderboard.length) { el.innerHTML = empty('No data for this game'); return; }
        el.innerHTML = renderGameTable(data.leaderboard);
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderGameTable(rows) {
    let html = `<table class="leaderboard-table">
        <thead><tr>
            <th class="rank">#</th>
            <th>Player</th>
            <th>Net P/L</th>
            <th>ROI</th>
            <th class="hide-mobile">Rounds</th>
        </tr></thead><tbody>`;

    rows.forEach((r, i) => {
        html += `<tr onclick="location.href='/player/${r.discord_user}'">
            <td class="${rankClass(i)}">${rankBadge(i)}</td>
            <td><div class="user-cell">${avatarImg(r.avatar_url)}<span>${esc(r.username)}</span></div></td>
            <td class="num ${profitClass(r.net_profit)}">${fmtSign(r.net_profit)}c</td>
            <td class="num ${profitClass(r.roi)}">${r.roi > 0 ? '+' : ''}${r.roi}%</td>
            <td class="num hide-mobile text-muted">${fmt(r.rounds)}</td>
        </tr>`;
    });

    return html + '</tbody></table>';
}

// ── Trading Leaderboard ─────────────────────────────────────────────────────

let tradingLoaded = false;

async function loadTradingLeaderboard() {
    const el = document.getElementById('trading-table');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/trading/leaderboard?limit=50`);
        const data = await res.json();
        if (!data.leaderboard.length) { el.innerHTML = empty('No trades yet'); return; }
        tradingLoaded = true;
        el.innerHTML = renderTradingTable(data.leaderboard);
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderTradingTable(rows) {
    let html = `<table class="leaderboard-table">
        <thead><tr>
            <th class="rank">#</th>
            <th>Player</th>
            <th>Net P/L</th>
            <th>Record</th>
            <th class="hide-mobile">ROI</th>
            <th class="hide-mobile">Avg CLV</th>
        </tr></thead><tbody>`;

    rows.forEach((r, i) => {
        const record = `${r.num_won}W-${r.num_lost}L`;
        const clv = r.avg_clv != null ? `${r.avg_clv > 0 ? '+' : ''}${r.avg_clv}pp` : '-';
        html += `<tr onclick="location.href='/player/${r.discord_user}'">
            <td class="${rankClass(i)}">${rankBadge(i)}</td>
            <td><div class="user-cell">${avatarImg(r.avatar_url)}<span>${esc(r.username)}</span></div></td>
            <td class="num ${profitClass(r.net_profit)}">${fmtSign(r.net_profit)}c</td>
            <td class="num">${record}</td>
            <td class="num hide-mobile ${profitClass(r.roi)}">${r.roi > 0 ? '+' : ''}${r.roi}%</td>
            <td class="num hide-mobile ${profitClass(r.avg_clv)}">${clv}</td>
        </tr>`;
    });

    return html + '</tbody></table>';
}

// ── Player Profile ──────────────────────────────────────────────────────────

async function loadPlayerProfile(userId) {
    const el = document.getElementById('profile-content');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/player/${userId}`);
        if (!res.ok) { el.innerHTML = empty('Player not found'); return; }
        const p = await res.json();
        el.innerHTML = renderProfile(p);
    } catch (e) {
        el.innerHTML = empty('Failed to load profile');
    }
}

function renderProfile(p) {
    const xpProgress = p.next_level_xp > 0 ? Math.min((p.total_xp / p.next_level_xp) * 100, 100) : 100;
    const casino = p.casino;
    const roi = casino.total_wagered > 0 ? ((casino.net_profit / casino.total_wagered) * 100).toFixed(1) : '0.0';

    let html = `
    <div class="profile-header">
        ${p.avatar_url ? `<img class="profile-avatar" src="${p.avatar_url}" alt="">` : '<div class="profile-avatar"></div>'}
        <div class="profile-info">
            <h2>${esc(p.username)}</h2>
            <div class="profile-level">\u2B50 Level ${p.level}</div>
            <div class="xp-bar-container">
                <div class="xp-bar-bg"><div class="xp-bar-fill" style="width:${xpProgress}%"></div></div>
                <div class="xp-label">${fmt(p.total_xp)} / ${fmt(p.next_level_xp)} XP</div>
            </div>
        </div>
    </div>

    <div class="stats-grid">
        <div class="stat-card"><div class="label">Balance</div><div class="value">${fmt(p.balance)}c</div></div>
        <div class="stat-card"><div class="label">Net Profit</div><div class="value ${profitClass(casino.net_profit)}">${fmtSign(casino.net_profit)}c</div></div>
        <div class="stat-card"><div class="label">Games Played</div><div class="value">${fmt(casino.rounds)}</div></div>
        <div class="stat-card"><div class="label">ROI</div><div class="value ${profitClass(parseFloat(roi))}">${roi}%</div></div>
    </div>`;

    // Per-game breakdown
    if (p.per_game && p.per_game.length) {
        html += '<h3 class="section-title">Per-Game Breakdown</h3>';
        html += `<table class="game-breakdown">
            <thead><tr>
                <th>Game</th><th>Rounds</th><th>Net P/L</th><th class="hide-mobile">Wagered</th>
            </tr></thead><tbody>`;
        p.per_game.forEach(g => {
            html += `<tr>
                <td>${esc(g.game_label)}</td>
                <td class="num">${fmt(g.rounds)}</td>
                <td class="num ${profitClass(g.net_profit)}">${fmtSign(g.net_profit)}c</td>
                <td class="num hide-mobile text-muted">${fmt(g.total_wagered)}c</td>
            </tr>`;
        });
        html += '</tbody></table>';
    }

    // Paper trading
    if (p.paper_trading && p.paper_trading.num_won > 0) {
        const pt = p.paper_trading;
        const ptRoi = pt.total_wagered > 0 ? ((pt.net_profit / pt.total_wagered) * 100).toFixed(1) : '0.0';
        html += '<h3 class="section-title">Paper Trading</h3>';
        html += `<div class="stats-grid">
            <div class="stat-card"><div class="label">Record</div><div class="value">${pt.num_won}W-${pt.num_lost}L</div></div>
            <div class="stat-card"><div class="label">Net Profit</div><div class="value ${profitClass(pt.net_profit)}">${fmtSign(pt.net_profit)}c</div></div>
            <div class="stat-card"><div class="label">ROI</div><div class="value ${profitClass(parseFloat(ptRoi))}">${ptRoi}%</div></div>
            <div class="stat-card"><div class="label">Avg CLV</div><div class="value ${profitClass(pt.avg_clv)}">${pt.avg_clv != null ? (pt.avg_clv > 0 ? '+' : '') + pt.avg_clv.toFixed(2) + 'pp' : '-'}</div></div>
        </div>`;
    }

    // Duels & Tournaments
    if (p.duels.wins > 0 || p.duels.losses > 0 || p.tournaments.entries > 0) {
        html += '<h3 class="section-title">Competitive</h3>';
        html += '<div class="stats-grid">';
        if (p.duels.wins > 0 || p.duels.losses > 0) {
            html += `<div class="stat-card"><div class="label">Duels</div><div class="value">${p.duels.wins}W-${p.duels.losses}L</div></div>`;
        }
        if (p.tournaments.entries > 0) {
            html += `<div class="stat-card"><div class="label">Tournaments</div><div class="value">${p.tournaments.wins}W / ${p.tournaments.entries} played</div></div>`;
        }
        html += '</div>';
    }

    // Achievements
    html += `<h3 class="section-title">Achievements (${p.achievements.filter(a => a.unlocked).length}/${p.achievements.length})</h3>`;
    html += '<div class="achievements-grid">';
    p.achievements.forEach(a => {
        const cls = a.unlocked ? 'achievement unlocked' : 'achievement locked';
        html += `<div class="${cls}">
            <span class="emoji">${a.emoji}</span>
            <div>
                <div class="name">${esc(a.name)}</div>
                <div class="desc">${esc(a.description)}</div>
            </div>
        </div>`;
    });
    html += '</div>';

    return html;
}

// ── Escape HTML ─────────────────────────────────────────────────────────────

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ── Init ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // Index page
    if (document.getElementById('leaderboard-tabs')) {
        initTabs();
        loadCasinoLeaderboard();

        const gameSelect = document.getElementById('game-select');
        if (gameSelect) {
            gameSelect.addEventListener('change', () => loadGameLeaderboard(gameSelect.value));
        }
    }

    // Player page
    const profileEl = document.getElementById('profile-content');
    if (profileEl) {
        const parts = window.location.pathname.split('/');
        const userId = parts[parts.length - 1];
        if (userId) loadPlayerProfile(userId);
    }
});
