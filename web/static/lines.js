// SharpLab HQ — betting lines (from the odds pipeline) + closing-line value.

const app = document.getElementById("app");
const SPORT = { nba: "🏀", mlb: "⚾" };
const BOOK_PREF = ["draftkings", "fanduel", "betmgm", "caesars", "fanatics"];

const fmtSpread = (g, src) => {
  const s = src.spread;
  if (s == null) return "—";
  return `${g.home_team} ${s > 0 ? "+" : ""}${s}`;
};
const fmtML = (src) => (src.ml_home == null ? "—" :
  `${src.ml_away > 0 ? "+" : ""}${src.ml_away} / ${src.ml_home > 0 ? "+" : ""}${src.ml_home}`);
const fmtTotal = (src) => (src.total == null ? "—" : `O/U ${src.total}`);

function consensus(odds) {
  for (const b of BOOK_PREF) if (odds[b]) return [b, odds[b]];
  const k = Object.keys(odds)[0];
  return k ? [k, odds[k]] : [null, null];
}

function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  } catch { return ""; }
}

async function main() {
  let slate, results;
  try {
    [slate, results] = await Promise.all([
      fetch("/api/v1/dashboard/slate").then((r) => r.json()),
      fetch("/api/v1/dashboard/results?limit=25").then((r) => r.json()),
    ]);
  } catch {
    app.innerHTML = `<div class="hero"><p class="muted">Couldn't load lines.</p></div>`;
    return;
  }
  render((slate.games || []).filter((g) => g.status !== "final"), results.results || []);
}

function render(upcoming, results) {
  let html = "";

  html += `<h2>🎯 Upcoming — Lines</h2><div class="card" style="padding:0">`;
  if (!upcoming.length) {
    html += `<div class="muted" style="padding:18px">No upcoming games on the board.</div>`;
  } else {
    html += upcoming.map((g) => {
      const [book, src] = consensus(g.odds || {});
      const em = SPORT[g.sport] || "🏟️";
      const lines = src
        ? `<div class="muted" style="font-size:13px;margin-top:4px">
             ${fmtSpread(g, src)} · ${fmtTotal(src)} · ML ${fmtML(src)}
             <span class="pill">${book}</span></div>`
        : `<div class="muted" style="font-size:13px;margin-top:4px">No lines yet.</div>`;
      return `<div class="gamecard">
        <div>${em} <strong>${g.away_team}</strong> @ <strong>${g.home_team}</strong>
          <span class="muted" style="font-size:12px">· ${fmtTime(g.start_time)}</span></div>
        ${lines}
        <details style="margin-top:6px" data-gid="${g.game_id}">
          <summary class="muted" style="cursor:pointer;font-size:13px">line movement & all books</summary>
          <div class="lm muted" style="font-size:13px;padding:8px 2px">Loading…</div>
        </details></div>`;
    }).join("");
  }
  html += `</div>`;

  html += `<h2>📉 Recent Results — Closing Line</h2><div class="card" style="padding:0">`;
  if (!results.length) {
    html += `<div class="muted" style="padding:18px">No completed games yet.</div>`;
  } else {
    html += `<table><thead><tr><th>Game</th><th class="num">Final</th><th>Close</th><th>ATS</th><th>O/U</th></tr></thead><tbody>`;
    html += results.map((g) => {
      const em = SPORT[g.sport] || "";
      const close = g.close || {};
      const closeStr = close.spread != null ? `${g.home_team} ${close.spread > 0 ? "+" : ""}${close.spread}` : "—";
      const ats = g.home_covered == null ? (g.close ? "push" : "—")
        : (g.home_covered ? `${g.home_team} ✓` : `${g.away_team} ✓`);
      const ou = g.total_result ? `${g.total_result}${close.total != null ? " " + close.total : ""}` : "—";
      return `<tr>
        <td>${em} ${g.away_team} @ ${g.home_team}</td>
        <td class="num">${g.away_score}-${g.home_score}</td>
        <td class="muted">${closeStr}</td>
        <td class="muted">${ats}</td>
        <td class="muted">${ou}</td></tr>`;
    }).join("");
    html += `</tbody></table>`;
  }
  html += `</div>`;

  app.innerHTML = html;
  wireLineMovement();
}

function wireLineMovement() {
  app.querySelectorAll("details[data-gid]").forEach((d) => {
    d.addEventListener("toggle", async function () {
      if (!this.open || this.dataset.loaded) return;
      this.dataset.loaded = "1";
      const box = this.querySelector(".lm");
      try {
        const lm = await fetch(`/api/v1/dashboard/line-movement/${this.dataset.gid}`).then((r) => r.json());
        box.innerHTML = renderMovement(lm);
      } catch {
        box.textContent = "Couldn't load movement.";
      }
    });
  });
}

function renderMovement(lm) {
  const snaps = lm.snapshots || [];
  if (!snaps.length) return "No snapshots yet.";
  // group by source → first (open) and last (latest) spread
  const bySource = {};
  for (const s of snaps) (bySource[s.source] ||= []).push(s);
  const rows = Object.entries(bySource).map(([src, arr]) => {
    const open = arr[0], last = arr[arr.length - 1];
    const move = (open.spread != null && last.spread != null) ? (last.spread - open.spread) : null;
    const moveStr = move == null ? "" : ` (${move > 0 ? "+" : ""}${move.toFixed(1)})`;
    const closed = lm.close && lm.close[src];
    const closeStr = closed && closed.spread != null ? ` · close ${closed.spread}` : "";
    return `<div><span class="pill">${src}</span> open ${open.spread ?? "—"} → now ${last.spread ?? "—"}${moveStr}${closeStr}</div>`;
  });
  return rows.join("");
}

main();
