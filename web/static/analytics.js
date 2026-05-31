// SharpLab HQ — site analytics dashboard.
const app = document.getElementById("app");

const PRETTY = { "/hq": "Server home", "/hq/stocks": "Stocks", "/hq/lines": "Lines", "/hq/analytics": "Analytics" };
const prettyPage = (p) => PRETTY[p] || (p.startsWith("/hq/") ? p.replace("/hq/", "") : p);
const fmt = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : "" + n);
const dur = (s) => (s == null ? "—" : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);

function host(ref) {
  try { return new URL(ref).hostname.replace(/^www\./, ""); } catch { return ref; }
}
function ago(ms) {
  if (!ms) return "—";
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (m < 1440) return `${Math.floor(m / 60)}h ago`;
  return `${Math.floor(m / 1440)}d ago`;
}

async function load() {
  let s;
  try { s = await fetch("/api/v1/analytics/stats").then((r) => r.json()); }
  catch { app.innerHTML = `<div class="hero"><p class="muted">Couldn't load analytics.</p></div>`; return; }

  const stat = (label, val) => `<div class="card stat"><div class="label">${label}</div><div class="value">${val}</div></div>`;
  let html = `<h2 style="margin:0 0 14px">📊 Site Analytics</h2>
    <div class="grid" style="margin-bottom:20px">
      ${stat("Pageviews (total)", fmt(s.total_pageviews))}
      ${stat("Visitors (total)", fmt(s.unique_visitors))}
      ${stat("Views · 24h", fmt(s.pv_24h))}
      ${stat("Views · 7d", fmt(s.pv_7d))}
      ${stat("Visitors · 7d", fmt(s.uniq_7d))}
    </div>`;

  // 30-day chart
  html += `<div class="card" style="padding:16px;margin-bottom:18px">
    <div class="muted" style="font-size:13px;margin-bottom:8px">Last 30 days · pageviews & visitors</div>
    ${dailyChart(s.daily || [])}</div>`;

  // Top pages
  html += `<div class="card" style="padding:0;margin-bottom:18px"><div class="date-hd">Top pages</div>`;
  html += (s.top_pages || []).length
    ? `<table><thead><tr><th>Page</th><th class="num">Views</th><th class="num">Visitors</th></tr></thead><tbody>`
      + s.top_pages.map((p) => `<tr><td>${prettyPage(p.page)} <span class="muted" style="font-size:11px">${p.page}</span></td>
          <td class="num">${fmt(p.views)}</td><td class="num muted">${fmt(p.visitors)}</td></tr>`).join("")
      + `</tbody></table>`
    : `<div class="muted" style="padding:16px">No pageviews yet.</div>`;
  html += `</div>`;

  // Signed-in members
  html += `<div class="card" style="padding:0;margin-bottom:18px"><div class="date-hd">👤 Signed-in members (Discord OAuth)</div>`;
  html += (s.members || []).length
    ? `<table><thead><tr><th>Member</th><th class="num">Visits</th><th class="num">Logins</th><th class="num">Last seen</th></tr></thead><tbody>`
      + s.members.map((m) => `<tr><td>${m.handle}</td><td class="num">${fmt(m.visits || 0)}</td>
          <td class="num muted">${m.logins || 0}</td><td class="num muted">${ago(m.last_seen)}</td></tr>`).join("")
      + `</tbody></table>`
    : `<div class="muted" style="padding:16px">No one has signed in yet.</div>`;
  html += `</div>`;

  // Two columns: dwell + referrers
  html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">`;
  html += `<div class="card" style="padding:0"><div class="date-hd">Avg time on page</div>`;
  html += (s.dwell || []).length
    ? `<table><tbody>` + s.dwell.map((d) => `<tr><td>${prettyPage(d.page)}</td><td class="num">${dur(d.avg_sec)}</td><td class="num muted">${fmt(d.n)}</td></tr>`).join("") + `</tbody></table>`
    : `<div class="muted" style="padding:16px">No data yet.</div>`;
  html += `</div>`;
  html += `<div class="card" style="padding:0"><div class="date-hd">Referrers</div>`;
  html += (s.referrers || []).length
    ? `<table><tbody>` + s.referrers.map((r) => `<tr><td>${host(r.ref)}</td><td class="num">${fmt(r.count)}</td></tr>`).join("") + `</tbody></table>`
    : `<div class="muted" style="padding:16px">No external referrers yet.</div>`;
  html += `</div></div>`;

  app.innerHTML = html;
}

function dailyChart(daily) {
  if (daily.length < 2) return `<div class="muted" style="padding:10px">Not enough data yet.</div>`;
  const W = 760, H = 160, mL = 34, mR = 10, mT = 10, mB = 22, pw = W - mL - mR, ph = H - mT - mB;
  const views = daily.map((d) => d.views), vmax = Math.max(...views, 1);
  const n = daily.length;
  const X = (i) => mL + (n > 1 ? i / (n - 1) : 0.5) * pw;
  const Y = (v) => mT + ph - (v / vmax) * ph;
  const bw = Math.max(2, pw / n - 3);
  const bars = daily.map((d, i) => {
    const h = (d.views / vmax) * ph;
    return `<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${(mT + ph - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="var(--accent)" opacity="0.55"/>`;
  }).join("");
  const visLine = daily.map((d, i) => `${X(i).toFixed(1)},${Y(d.visitors).toFixed(1)}`).join(" ");
  const ylabels = [0, vmax].map((v) => `<text x="${mL - 6}" y="${(Y(v) + 3).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--muted)">${v}</text>`).join("");
  const xfirst = new Date(daily[0].day).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const xlast = new Date(daily[n - 1].day).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
    ${ylabels}${bars}
    <polyline points="${visLine}" fill="none" stroke="var(--accent2)" stroke-width="2"/>
    <text x="${mL}" y="${H - 6}" font-size="11" fill="var(--muted)">${xfirst}</text>
    <text x="${W - mR}" y="${H - 6}" text-anchor="end" font-size="11" fill="var(--muted)">${xlast}</text>
    <text x="${W / 2}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--muted)">bars = views · line = visitors</text>
  </svg>`;
}

load();
