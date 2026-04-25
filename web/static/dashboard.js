/* SharpLab Sports Dashboard — Frontend Logic */

const API = '/api/v1';
let currentSport = 'all';

// ── Helpers ─────────────────────────────────────────────────────────────────

function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

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

function americanToProb(odds) {
    if (odds == null) return null;
    if (odds < 0) return Math.abs(odds) / (Math.abs(odds) + 100);
    return 100 / (odds + 100);
}

function fmtProb(odds) {
    if (odds == null) return null;
    return (americanToProb(odds) * 100).toFixed(1) + '%';
}

function fmtSpread(n) {
    if (n == null) return null;
    return n > 0 ? '+' + n : String(n);
}

function fmtGameTime(isoStr) {
    const d = new Date(isoStr);
    return d.toLocaleString('en-US', {
        timeZone: 'America/New_York',
        weekday: 'short', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit'
    }) + ' ET';
}

function fmtShortTime(isoStr) {
    const d = new Date(isoStr);
    return d.toLocaleString('en-US', {
        timeZone: 'America/New_York',
        hour: 'numeric', minute: '2-digit'
    }) + ' ET';
}

function staleness(isoStr) {
    if (!isoStr) return '';
    const diffMs = Date.now() - new Date(isoStr).getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m ago`;
}

function spinner() {
    return '<div class="loading"><div class="spinner"></div>Loading...</div>';
}

function empty(msg) {
    return `<div class="empty">${msg}</div>`;
}

function avatarImg(url) {
    if (url) return `<img class="avatar" src="${url}" alt="" loading="lazy">`;
    return '<div class="avatar"></div>';
}

function rankBadge(i) {
    if (i === 0) return '\uD83E\uDD47';
    if (i === 1) return '\uD83E\uDD48';
    if (i === 2) return '\uD83E\uDD49';
    return (i + 1).toString();
}

// Team abbreviation lookup (subset for display)
const TEAM_ABBR_NBA = {
    'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN',
    'Charlotte Hornets': 'CHA', 'Chicago Bulls': 'CHI', 'Cleveland Cavaliers': 'CLE',
    'Dallas Mavericks': 'DAL', 'Denver Nuggets': 'DEN', 'Detroit Pistons': 'DET',
    'Golden State Warriors': 'GSW', 'Houston Rockets': 'HOU', 'Indiana Pacers': 'IND',
    'Los Angeles Clippers': 'LAC', 'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM',
    'Miami Heat': 'MIA', 'Milwaukee Bucks': 'MIL', 'Minnesota Timberwolves': 'MIN',
    'New Orleans Pelicans': 'NOP', 'New York Knicks': 'NYK', 'Oklahoma City Thunder': 'OKC',
    'Orlando Magic': 'ORL', 'Philadelphia 76ers': 'PHI', 'Phoenix Suns': 'PHX',
    'Portland Trail Blazers': 'POR', 'Sacramento Kings': 'SAC', 'San Antonio Spurs': 'SAS',
    'Toronto Raptors': 'TOR', 'Utah Jazz': 'UTA', 'Washington Wizards': 'WAS',
};
const TEAM_ABBR_MLB = {
    'Arizona Diamondbacks': 'ARI', 'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL',
    'Boston Red Sox': 'BOS', 'Chicago Cubs': 'CHC', 'Chicago White Sox': 'CWS',
    'Cincinnati Reds': 'CIN', 'Cleveland Guardians': 'CLE', 'Colorado Rockies': 'COL',
    'Detroit Tigers': 'DET', 'Houston Astros': 'HOU', 'Kansas City Royals': 'KC',
    'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA',
    'Milwaukee Brewers': 'MIL', 'Minnesota Twins': 'MIN', 'New York Mets': 'NYM',
    'New York Yankees': 'NYY', 'Oakland Athletics': 'OAK', 'Philadelphia Phillies': 'PHI',
    'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SD', 'San Francisco Giants': 'SF',
    'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL', 'Tampa Bay Rays': 'TB',
    'Texas Rangers': 'TEX', 'Toronto Blue Jays': 'TOR', 'Washington Nationals': 'WSH',
};

function teamAbbr(name, sport) {
    if (sport === 'mlb') return TEAM_ABBR_MLB[name] || name;
    return TEAM_ABBR_NBA[name] || name;
}

const SOURCE_ORDER = ['draftkings', 'fanduel', 'betmgm', 'pinnacle', 'kalshi', 'polymarket'];
const SOURCE_LABELS = {
    draftkings: 'DraftKings', fanduel: 'FanDuel', betmgm: 'BetMGM',
    pinnacle: 'Pinnacle', kalshi: 'Kalshi', polymarket: 'Polymarket',
};

// ── Slate ───────────────────────────────────────────────────────────────────

async function loadSlate() {
    const el = document.getElementById('slate-content');
    const upd = document.getElementById('slate-updated');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/dashboard/slate?sport=${currentSport}`);
        const data = await res.json();
        upd.textContent = 'Updated ' + staleness(data.updated_at);
        if (!data.games.length) {
            el.innerHTML = empty('No games scheduled');
            return;
        }
        el.innerHTML = data.games.map(renderGameCard).join('');
    } catch (e) {
        el.innerHTML = empty('Failed to load slate');
    }
}

function renderGameCard(game) {
    const away = esc(game.away_team);
    const home = esc(game.home_team);
    const awayAbbr = teamAbbr(game.away_team, game.sport);
    const homeAbbr = teamAbbr(game.home_team, game.sport);
    const sport = game.sport.toUpperCase();
    const isFinal = game.status === 'final';
    const isLive = game.status === 'live';

    let statusHtml;
    if (isFinal) statusHtml = '<span class="status-final">Final</span>';
    else if (isLive) statusHtml = '<span class="status-live">LIVE</span>';
    else statusHtml = `<span class="status-scheduled">${fmtShortTime(game.start_time)}</span>`;

    let scoreHtml = '';
    if (isFinal && game.home_score != null) {
        const awayWon = game.away_score > game.home_score;
        const homeWon = game.home_score > game.away_score;
        scoreHtml = `
        <div class="game-score-row">
            <div class="score-team"><span class="abbr">${esc(awayAbbr)}</span>${away}</div>
            <div class="score-num ${awayWon ? 'score-winner' : ''}">${game.away_score}</div>
            <div class="score-separator">-</div>
            <div class="score-num ${homeWon ? 'score-winner' : ''}">${game.home_score}</div>
            <div class="score-team"><span class="abbr">${esc(homeAbbr)}</span>${home}</div>
        </div>`;
    }

    const oddsHtml = renderOddsGrid(game.odds, game.sport);
    const hasOdds = Object.keys(game.odds).length > 0;

    return `
    <div class="game-card" data-game-id="${esc(game.game_id)}">
        <div class="game-card-header">
            <div class="game-matchup">
                ${awayAbbr} @ ${homeAbbr}
                <span class="sport-badge">${sport}</span>
            </div>
            <div class="game-meta">
                ${statusHtml}
                <div style="font-size:0.72rem;color:var(--text-muted);margin-top:2px">${fmtGameTime(game.start_time)}</div>
            </div>
        </div>
        ${scoreHtml}
        ${hasOdds ? oddsHtml : '<div class="og-na">No odds data yet</div>'}
        ${hasOdds ? `<button class="expand-btn" onclick="toggleLineMovement(this, '${esc(game.game_id)}')">Line Movement</button>` : ''}
        <div class="line-movement-container"></div>
    </div>`;
}

function renderOddsGrid(odds, sport) {
    const sources = SOURCE_ORDER.filter(s => odds[s]);
    if (!sources.length) return '';

    // Find best values across sources
    let bestSpread = null, bestSpreadSrc = null;
    let bestMlHome = -Infinity, bestMlHomeSrc = null;
    let bestMlAway = -Infinity, bestMlAwaySrc = null;

    for (const src of sources) {
        const o = odds[src];
        // Best spread = lowest absolute value (closest to pick'em, most advantageous for home)
        if (o.spread != null) {
            if (bestSpread === null || o.spread > bestSpread) {
                bestSpread = o.spread; bestSpreadSrc = src;
            }
        }
        // Best ML = highest implied prob (for respective side)
        if (o.ml_home != null) {
            const p = americanToProb(o.ml_home);
            if (p < americanToProb(bestMlHome === -Infinity ? -100000 : bestMlHome)) {
                bestMlHome = o.ml_home; bestMlHomeSrc = src;
            }
        }
        if (o.ml_away != null) {
            const p = americanToProb(o.ml_away);
            if (p < americanToProb(bestMlAway === -Infinity ? -100000 : bestMlAway)) {
                bestMlAway = o.ml_away; bestMlAwaySrc = src;
            }
        }
    }

    let html = `<div class="odds-grid">
        <div class="og-header">Source</div>
        <div class="og-header">Spread</div>
        <div class="og-header">Moneyline</div>
        <div class="og-header">Total</div>`;

    for (const src of sources) {
        const o = odds[src];
        const label = SOURCE_LABELS[src] || src;

        // Spread cell
        let spreadCell;
        if (o.spread != null) {
            const cls = (src === bestSpreadSrc) ? 'og-best' : '';
            spreadCell = `<span class="${cls}">${fmtSpread(o.spread)} (${fmtProb(o.spread_odds) || ''})</span>`;
        } else {
            spreadCell = '<span class="og-na">-</span>';
        }

        // ML cell — show away% / home%
        let mlCell;
        if (o.ml_home != null || o.ml_away != null) {
            const awayP = fmtProb(o.ml_away);
            const homeP = fmtProb(o.ml_home);
            const awayCls = (src === bestMlAwaySrc) ? 'og-best' : '';
            const homeCls = (src === bestMlHomeSrc) ? 'og-best' : '';
            mlCell = `<span class="${awayCls}">${awayP || '-'}</span> / <span class="${homeCls}">${homeP || '-'}</span>`;
        } else {
            mlCell = '<span class="og-na">-</span>';
        }

        // Total cell
        let totalCell;
        if (o.total != null) {
            totalCell = `${o.total}`;
        } else {
            totalCell = '<span class="og-na">-</span>';
        }

        html += `
        <div class="og-source">${esc(label)}</div>
        <div class="og-cell">${spreadCell}</div>
        <div class="og-cell">${mlCell}</div>
        <div class="og-cell">${totalCell}</div>`;
    }

    html += '</div>';
    return html;
}

// ── Line Movement (expandable) ──────────────────────────────────────────────

async function toggleLineMovement(btn, gameId) {
    const card = btn.closest('.game-card');
    const container = card.querySelector('.line-movement-container');

    if (container.innerHTML) {
        container.innerHTML = '';
        btn.textContent = 'Line Movement';
        return;
    }

    btn.textContent = 'Loading...';
    try {
        const res = await fetch(`${API}/dashboard/line-movement/${gameId}`);
        const data = await res.json();
        container.innerHTML = renderLineMovement(data);
        btn.textContent = 'Hide Movement';
    } catch (e) {
        container.innerHTML = '<div class="empty">Failed to load</div>';
        btn.textContent = 'Line Movement';
    }
}

function renderLineMovement(data) {
    if (!data.snapshots || !data.snapshots.length) {
        return '<div class="empty" style="margin-top:10px">No line history available</div>';
    }

    // Group snapshots by source, get first and last for each
    const bySource = {};
    for (const snap of data.snapshots) {
        if (!bySource[snap.source]) bySource[snap.source] = [];
        bySource[snap.source].push(snap);
    }

    let html = `<table class="line-movement-table">
        <thead><tr>
            <th>Source</th>
            <th>Market</th>
            <th>Open</th>
            <th>Current</th>
            <th>Move</th>
        </tr></thead><tbody>`;

    for (const src of SOURCE_ORDER) {
        if (!bySource[src]) continue;
        const snaps = bySource[src];
        const first = snaps[0];
        const last = snaps[snaps.length - 1];
        const label = SOURCE_LABELS[src] || src;
        let firstRow = true;

        // ML row
        if (first.ml_home != null && last.ml_home != null) {
            const openHome = americanToProb(first.ml_home) * 100;
            const currHome = americanToProb(last.ml_home) * 100;
            const delta = currHome - openHome;
            const moveClass = delta > 0.3 ? 'move-up' : delta < -0.3 ? 'move-down' : 'move-flat';
            const arrow = delta > 0.3 ? ' \u2191' : delta < -0.3 ? ' \u2193' : '';
            html += `<tr>
                <td class="lm-source">${firstRow ? esc(label) : ''}</td>
                <td>ML (Home)</td>
                <td>${fmtProb(first.ml_home)}</td>
                <td>${fmtProb(last.ml_home)}</td>
                <td class="${moveClass}">${delta > 0 ? '+' : ''}${delta.toFixed(1)}pp${arrow}</td>
            </tr>`;
            firstRow = false;
        }

        // Spread row
        if (first.spread != null && last.spread != null) {
            const delta = last.spread - first.spread;
            const moveClass = Math.abs(delta) > 0.1 ? (delta > 0 ? 'move-up' : 'move-down') : 'move-flat';
            const arrow = delta > 0.1 ? ' \u2191' : delta < -0.1 ? ' \u2193' : '';
            html += `<tr>
                <td class="lm-source">${firstRow ? esc(label) : ''}</td>
                <td>Spread</td>
                <td>${fmtSpread(first.spread)}</td>
                <td>${fmtSpread(last.spread)}</td>
                <td class="${moveClass}">${delta > 0 ? '+' : ''}${delta.toFixed(1)}${arrow}</td>
            </tr>`;
            firstRow = false;
        }

        // Total row
        if (first.total != null && last.total != null) {
            const delta = last.total - first.total;
            const moveClass = Math.abs(delta) > 0.1 ? (delta > 0 ? 'move-up' : 'move-down') : 'move-flat';
            const arrow = delta > 0.1 ? ' \u2191' : delta < -0.1 ? ' \u2193' : '';
            html += `<tr>
                <td class="lm-source">${firstRow ? esc(label) : ''}</td>
                <td>Total</td>
                <td>${first.total}</td>
                <td>${last.total}</td>
                <td class="${moveClass}">${delta > 0 ? '+' : ''}${delta.toFixed(1)}${arrow}</td>
            </tr>`;
        }
    }

    html += '</tbody></table>';

    // Close snapshot info
    if (data.close && Object.keys(data.close).length) {
        html += '<div style="margin-top:8px;font-size:0.72rem;color:var(--text-muted)">Closing lines captured: ';
        html += Object.keys(data.close).map(s => esc(SOURCE_LABELS[s] || s)).join(', ');
        html += '</div>';
    }

    return html;
}

// ── Paper Trading Leaderboard ───────────────────────────────────────────────

async function loadPaperTrading() {
    const el = document.getElementById('paper-content');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/trading/leaderboard?limit=5`);
        const data = await res.json();
        if (!data.leaderboard.length) {
            el.innerHTML = empty('No trades yet');
            return;
        }
        el.innerHTML = renderPaperTable(data.leaderboard);
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderPaperTable(rows) {
    let html = `<table class="compact-table">
        <thead><tr>
            <th>#</th>
            <th>Player</th>
            <th style="text-align:right">Net P/L</th>
            <th style="text-align:right">ROI</th>
        </tr></thead><tbody>`;

    rows.forEach((r, i) => {
        html += `<tr onclick="location.href='/player/${r.discord_user}'">
            <td>${rankBadge(i)}</td>
            <td><div class="user-cell">${avatarImg(r.avatar_url)}<span>${esc(r.username)}</span></div></td>
            <td class="num ${profitClass(r.net_profit)}">${fmtSign(r.net_profit)}c</td>
            <td class="num ${profitClass(r.roi)}">${r.roi > 0 ? '+' : ''}${r.roi}%</td>
        </tr>`;
    });

    html += '</tbody></table>';
    html += '<a class="view-all" href="/">View full leaderboard &rarr;</a>';
    return html;
}

// ── Recent Results ──────────────────────────────────────────────────────────

async function loadResults() {
    const el = document.getElementById('results-content');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/dashboard/results?sport=${currentSport}&limit=8`);
        const data = await res.json();
        if (!data.results.length) {
            el.innerHTML = empty('No recent results');
            return;
        }
        el.innerHTML = data.results.map(renderResultCard).join('');
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderResultCard(r) {
    const awayAbbr = teamAbbr(r.away_team, r.sport);
    const homeAbbr = teamAbbr(r.home_team, r.sport);
    const date = new Date(r.start_time).toLocaleDateString('en-US', {
        timeZone: 'America/New_York', month: 'short', day: 'numeric'
    });

    let tags = '';
    if (r.home_covered === true) {
        tags += `<span class="result-tag tag-cover">Cover ${fmtSign(r.ats_margin)}</span>`;
    } else if (r.home_covered === false) {
        tags += `<span class="result-tag tag-miss">Miss ${fmtSign(r.ats_margin)}</span>`;
    } else if (r.ats_margin !== undefined && r.home_covered === null) {
        tags += '<span class="result-tag tag-push">Push</span>';
    }

    if (r.total_result === 'over') {
        tags += `<span class="result-tag tag-over">O ${r.total_margin}</span>`;
    } else if (r.total_result === 'under') {
        tags += `<span class="result-tag tag-under">U ${r.total_margin}</span>`;
    } else if (r.total_result === 'push') {
        tags += '<span class="result-tag tag-push">Push</span>';
    }

    return `
    <div class="result-card">
        <div class="result-matchup">
            ${esc(awayAbbr)} @ ${esc(homeAbbr)}
            <div class="date">${date} &middot; ${r.sport.toUpperCase()}</div>
        </div>
        <div class="result-scores">${r.away_score} - ${r.home_score}</div>
        <div class="result-tags">${tags}</div>
    </div>`;
}

// ── Injuries ────────────────────────────────────────────────────────────────

async function loadInjuries() {
    const el = document.getElementById('injuries-content');
    el.innerHTML = spinner();
    try {
        const res = await fetch(`${API}/dashboard/injuries`);
        const data = await res.json();
        if (!data.injuries.length) {
            el.innerHTML = empty('No active injuries reported');
            return;
        }
        el.innerHTML = renderInjuries(data.injuries);
    } catch (e) {
        el.innerHTML = empty('Failed to load');
    }
}

function renderInjuries(injuries) {
    let html = '<div class="injury-grid">';
    for (const inj of injuries) {
        const statusCls = `injury-status-${inj.status.replace(/\s/g, '-')}`;
        html += `
        <div class="injury-item">
            <div class="injury-status ${statusCls}">${esc(inj.status)}</div>
            <div class="injury-info">
                <div class="injury-name">${esc(inj.player_name)}</div>
                <div class="injury-team">${esc(inj.team)}</div>
            </div>
            ${inj.detail ? `<div class="injury-detail">${esc(inj.detail)}</div>` : ''}
        </div>`;
    }
    html += '</div>';
    return html;
}

// ── Sport Filter ────────────────────────────────────────────────────────────

function initSportFilter() {
    const btns = document.querySelectorAll('.sport-btn');
    btns.forEach(btn => {
        btn.addEventListener('click', () => {
            btns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentSport = btn.dataset.sport;
            loadSlate();
            loadResults();
        });
    });
}

// ── Init ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initSportFilter();

    // Load all sections in parallel
    loadSlate();
    loadPaperTrading();
    loadResults();
    loadInjuries();

    // Auto-refresh slate every 5 minutes
    setInterval(() => loadSlate(), 5 * 60 * 1000);
});
