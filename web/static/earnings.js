// SharpLab HQ — upcoming earnings calendar (large-caps; server holdings flagged).

const app = document.getElementById("app");
const fmtMc = (mc) => (mc >= 1e12 ? `$${(mc / 1e12).toFixed(1)}T`
  : mc >= 1e9 ? `$${(mc / 1e9).toFixed(0)}B`
  : mc >= 1e6 ? `$${(mc / 1e6).toFixed(0)}M` : "");

async function main() {
  let data;
  try {
    data = await fetch("/api/v1/hq/earnings").then((r) => r.json());
  } catch {
    app.innerHTML = `<div class="hero"><p class="muted">Couldn't load the earnings calendar.</p></div>`;
    return;
  }
  render(data.days || []);
}

function companyRow(c) {
  const hl = c.held ? "background:rgba(122,162,247,.10)" : "";
  return `<a class="earnrow" href="https://finance.yahoo.com/quote/${encodeURIComponent(c.symbol)}" target="_blank" rel="noopener"
    style="display:flex;align-items:center;gap:12px;padding:8px 12px;border-radius:8px;text-decoration:none;color:var(--fg);${hl}">
    <strong style="min-width:72px;font-size:15px">${c.symbol}</strong>
    <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${c.name}</span>
    ${c.held ? `<span style="font-size:11px;font-weight:700;color:var(--accent);background:rgba(122,162,247,.16);padding:2px 8px;border-radius:999px;white-space:nowrap">📍 held</span>` : ""}
    <span class="muted" style="min-width:54px;text-align:right;font-variant-numeric:tabular-nums">${fmtMc(c.mc)}</span>
    <span class="muted" style="font-size:11px">↗</span>
  </a>`;
}

function section(emoji, label, color, list) {
  if (!list.length) return "";
  return `<div style="margin-top:14px">
    <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:${color};padding:0 12px 4px">
      ${emoji} ${label} <span class="muted" style="font-weight:500">(${list.length})</span></div>
    ${list.map(companyRow).join("")}</div>`;
}

function render(days) {
  if (!days.length) {
    app.innerHTML = `<h2>📅 Earnings calendar</h2><div class="card"><p class="muted">No notable earnings in the next week.</p></div>`;
    return;
  }
  let html = `<style>.earnrow:hover{background:var(--panel2) !important}.earnrow:hover strong{color:var(--accent)}</style>
    <div style="display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px">
      <h2 style="margin:0">📅 Earnings calendar</h2>
      <a href="/stocks" class="muted" style="font-size:13px">← back to stocks</a></div>
    <p class="muted" style="margin:6px 0 16px;font-size:13px">Large-caps (≥$2B) over the next week · <span style="color:var(--accent)">📍 held</span> in the server.</p>
    <div style="display:flex;flex-direction:column;gap:14px">`;
  for (const d of days) {
    const n = d.bmo.length + d.amc.length + d.other.length;
    html += `<div class="card" style="padding:16px 8px">
      <div style="font-weight:800;font-size:19px;padding:0 12px">${d.label}
        <span class="muted" style="font-size:13px;font-weight:500">· ${d.date} · ${n} report${n === 1 ? "" : "s"}</span></div>
      ${section("🌅", "Before open", "var(--green)", d.bmo)}
      ${section("🌙", "After close", "var(--accent2)", d.amc)}
      ${section("🕐", "Time TBD", "var(--muted)", d.other)}
    </div>`;
  }
  html += `</div>`;
  app.innerHTML = html;
}

main();
