/* SharpLab Leaderboard — Frontend Logic */

const API = '/api/v1';

// -- Helpers -----------------------------------------------------------------

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

// -- Tab Switching -----------------------------------------------------------

function initTabs() {
    const tabs = document.querySelectorAll('.tab');
    const contents = document.querySelectorAll('.tab-content');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(tab.dataset.tab).classList.add('active');

            const target = tab.dataset.tab;
            if (target === 'standings' && !standingsLoaded) loadStandings();
            if (target === 'per-game' && !gamesListLoaded) loadEloGamesList();
            if (target === 'trading' && !tradingLoaded) loadTradingLeaderboard();
        });
    });
}

// -- Championship Standings --------------------------------------------------

let standingsLoaded = false;

async function loadStandings() {
    const el = document.getElementById('standings-table');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/elo/standings?limit=50`);
        const data = await res.json();
        if (!data.standings.length) { el.innerHTML = empty('No qualified players yet. Play 5+ games to appear.'); return; }
        standingsLoaded = true;
        el.innerHTML = renderStandingsTable(data.standings);
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderStandingsTable(rows) {
    let html = `<table class="leaderboard-table">
        <thead><tr>
            <th class="rank">#</th>
            <th>Player</th>
            <th>Points</th>
            <th>Best Rating</th>
            <th class="hide-mobile">Games</th>
        </tr></thead><tbody>`;

    rows.forEach((r, i) => {
        const breakdownTip = r.breakdown.map(b => `${b.game}: #${b.position} (${b.points}pts)`).join(', ');
        html += `<tr onclick="location.href='/player/${r.discord_user}'" title="${esc(breakdownTip)}">
            <td class="${rankClass(i)}">${rankBadge(i)}</td>
            <td><div class="user-cell">${avatarImg(r.avatar_url)}<span>${esc(r.username)}</span></div></td>
            <td class="num"><strong>${r.total_points}</strong></td>
            <td class="num">${fmt(r.best_rating)}</td>
            <td class="num hide-mobile text-muted">${fmt(r.total_games)}</td>
        </tr>`;
    });

    return html + '</tbody></table>' +
        '<div class="table-footer">Points: 25/18/15/12/10/8/6/4/2/1 for top 10 in each game. Min 5 games to qualify.</div>';
}

// -- Per-Game ELO Leaderboard ------------------------------------------------

let gamesListLoaded = false;

async function loadEloGamesList() {
    try {
        const res = await fetch(`${API}/elo/games`);
        const data = await res.json();
        const select = document.getElementById('game-select');
        data.games.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g.key;
            opt.textContent = g.label;
            select.appendChild(opt);
        });
        gamesListLoaded = true;
        if (data.games.length) {
            select.value = data.games[0].key;
            loadEloGameLeaderboard(data.games[0].key);
        }
    } catch (e) {
        console.error('Failed to load games list', e);
    }
}

async function loadEloGameLeaderboard(game) {
    const el = document.getElementById('game-table');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/elo/leaderboard/${game}?limit=50`);
        const data = await res.json();
        if (!data.leaderboard.length) { el.innerHTML = empty('No qualified players for this game yet'); return; }
        el.innerHTML = renderEloGameTable(data.leaderboard);
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderEloGameTable(rows) {
    let html = `<table class="leaderboard-table">
        <thead><tr>
            <th class="rank">#</th>
            <th>Player</th>
            <th>Rating</th>
            <th>Record</th>
            <th class="hide-mobile">Peak</th>
        </tr></thead><tbody>`;

    rows.forEach((r, i) => {
        const record = `${r.wins}W-${r.losses}L` + (r.draws ? `-${r.draws}D` : '');
        html += `<tr onclick="location.href='/player/${r.discord_user}'">
            <td class="${rankClass(i)}">${rankBadge(i)}</td>
            <td><div class="user-cell">${avatarImg(r.avatar_url)}<span>${esc(r.username)}</span></div></td>
            <td class="num"><strong>${Math.round(r.rating)}</strong></td>
            <td class="num">${record}</td>
            <td class="num hide-mobile text-muted">${Math.round(r.peak_rating)}</td>
        </tr>`;
    });

    return html + '</tbody></table>';
}

// -- Trading Leaderboard -----------------------------------------------------

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

// -- Player Profile ----------------------------------------------------------

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
    </div>`;

    // ELO Ratings section (primary)
    if (p.elo_ratings && p.elo_ratings.length) {
        html += '<h3 class="section-title">ELO Ratings</h3>';
        html += `<table class="game-breakdown">
            <thead><tr>
                <th>Game</th><th>Rating</th><th>Record</th><th class="hide-mobile">Peak</th>
            </tr></thead><tbody>`;
        p.elo_ratings.forEach(r => {
            const record = `${r.wins}W-${r.losses}L` + (r.draws ? `-${r.draws}D` : '');
            const prov = r.provisional ? '?' : '';
            html += `<tr>
                <td>${esc(r.game_label)}</td>
                <td class="num"><strong>${Math.round(r.rating)}</strong>${prov}</td>
                <td class="num">${record}</td>
                <td class="num hide-mobile text-muted">${Math.round(r.peak_rating)}</td>
            </tr>`;
        });
        html += '</tbody></table>';
    }

    // Quick stats
    const casino = p.casino;
    html += '<h3 class="section-title">Stats</h3>';
    html += '<div class="stats-grid">';
    html += `<div class="stat-card"><div class="label">Balance</div><div class="value">${fmt(p.balance)}c</div></div>`;
    html += `<div class="stat-card"><div class="label">Games Played</div><div class="value">${fmt(casino.rounds)}</div></div>`;
    if (p.duels.wins > 0 || p.duels.losses > 0) {
        html += `<div class="stat-card"><div class="label">Duels</div><div class="value">${p.duels.wins}W-${p.duels.losses}L</div></div>`;
    }
    if (p.tournaments.entries > 0) {
        html += `<div class="stat-card"><div class="label">Tournaments</div><div class="value">${p.tournaments.wins}W / ${p.tournaments.entries}</div></div>`;
    }
    html += '</div>';

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

// -- Escape HTML -------------------------------------------------------------

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// -- Init --------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Index page
    if (document.getElementById('leaderboard-tabs')) {
        initTabs();
        loadStandings();

        const gameSelect = document.getElementById('game-select');
        if (gameSelect) {
            gameSelect.addEventListener('change', () => loadEloGameLeaderboard(gameSelect.value));
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
