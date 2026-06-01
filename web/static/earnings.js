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
  return `<div style="display:flex;align-items:center;gap:10px;padding:4px 0;font-size:14px">
    <strong style="min-width:64px">${c.symbol}</strong>
    <span class="muted" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${c.name}</span>
    <span class="muted" style="min-width:52px;text-align:right">${fmtMc(c.mc)}</span>
    <span style="width:16px;text-align:center">${c.held ? `<span title="Held in the server">📍</span>` : ""}</span>
  </div>`;
}

function section(emoji, label, list) {
  if (!list.length) return "";
  return `<div style="margin-top:10px">
    <div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:2px">${emoji} ${label}</div>
    ${list.map(companyRow).join("")}</div>`;
}

function render(days) {
  if (!days.length) {
    app.innerHTML = `<h2>📅 Earnings calendar</h2><div class="card"><p class="muted">No notable earnings in the next week.</p></div>`;
    return;
  }
  let html = `<h2>📅 Earnings calendar</h2>
    <p class="muted" style="margin:-6px 0 14px">Large-caps (≥$2B) reporting over the next week · 📍 = held in the server.</p>
    <div style="display:flex;flex-direction:column;gap:12px">`;
  for (const d of days) {
    const n = d.bmo.length + d.amc.length + d.other.length;
    html += `<div class="card">
      <div style="font-weight:800;font-size:16px">${d.label}
        <span class="muted" style="font-size:13px;font-weight:400">${d.date} · ${n} report${n === 1 ? "" : "s"}</span></div>
      ${section("🌅", "Before open", d.bmo)}
      ${section("🌙", "After close", d.amc)}
      ${section("🕐", "Time TBD", d.other)}
    </div>`;
  }
  html += `</div>`;
  app.innerHTML = html;
}

main();
