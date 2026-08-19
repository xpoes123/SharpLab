// SharpLab HQ — Games hub: just renders the nav (login/coins). Games themselves are served
// under /bridge, /chess, /ers, /math24, /zetamac (proxied to the games service).
const navRight = document.getElementById("navRight");
const num = (n) => (n == null ? "0" : Number(n).toLocaleString());
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function main() {
  let me = null;
  try {
    const r = await fetch("/api/v1/hq/me", { credentials: "include" });
    if (r.ok) me = await r.json();
  } catch { /* offline — show sign-in */ }
  if (!me || !me.authenticated) {
    navRight.innerHTML = `<a class="btn" href="/api/v1/auth/discord/login">Sign in with Discord</a>`;
    return;
  }
  const u = me.user;
  const av = u.avatar ? `https://cdn.discordapp.com/avatars/${u.id}/${u.avatar}.png` : null;
  navRight.innerHTML = `<div class="userbar">
    ${av ? `<img class="avatar" src="${av}" alt="">` : `<div class="avatar"></div>`}
    <span>${esc(u.username)}</span>
    <span class="pill" style="color:var(--gold)">🪙 ${num(me.balance)}</span>
    <a class="btn ghost" href="/api/v1/auth/logout">Sign out</a></div>`;
}
main();

// ── Daily strip: show today's puzzle + the player's streak ────────────────────
(async function dailyStrip() {
  try {
    const r = await fetch("/api/v1/daily/today", { credentials: "include" });
    if (!r.ok) return;
    const d = await r.json();
    const icon = document.getElementById("dailyIcon");
    const name = document.getElementById("dailyName");
    const meta = document.getElementById("dailyMeta");
    if (icon && d.game) icon.textContent = d.game.icon || "🗓️";
    if (name && d.game) name.textContent = `${d.game.name} · ${d.difficulty}`;
    if (meta) {
      const played = d.played ? "✅ played today" : "not played yet";
      const streak = d.signed_in && d.streak ? ` · 🔥 ${d.streak}-day streak` : "";
      meta.textContent = `Par ${d.par}${d.par_approx ? "~" : ""} · ${played}${streak}`;
    }
  } catch { /* strip stays on its static default */ }
})();

// ── Search + category filter + sort ───────────────────────────────────────────
// Category is inferred from each tile's .gmeta text so we don't hand-tag 47 tiles:
//   "🪙 table" → gambling (wager a coin table/sim) · "🪙 reward" → solo/brain · else → multiplayer
(function gamesFilter() {
  const grid = document.getElementById("gamegrid");
  const search = document.getElementById("gsearch");
  const chips = document.getElementById("gchips");
  const sort = document.getElementById("gsort");
  const count = document.getElementById("gcount");
  if (!grid || !search) return;

  const tiles = [...grid.querySelectorAll(".gtile")];
  const order = new Map(tiles.map((t, i) => [t, i]));  // remember featured order
  const catOf = (t) => {
    const m = (t.querySelector(".gmeta")?.textContent || "").toLowerCase();
    if (m.includes("table")) return "gambling";           // wagers coins → gambling (even if solo)
    if (m.includes("reward") || m.includes("puzzle") || m.includes("brain")) return "solo";
    return "multi";
  };
  const textOf = (t) =>
    ((t.querySelector(".gname")?.textContent || "") + " " +
     (t.querySelector(".gblurb")?.textContent || "")).toLowerCase();
  tiles.forEach((t) => { t.dataset.cat = catOf(t); t.dataset.text = textOf(t); });

  let filter = "all";
  const apply = () => {
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    tiles.forEach((t) => {
      const ok = (filter === "all" || t.dataset.cat === filter) &&
                 (!q || t.dataset.text.includes(q));
      t.classList.toggle("hidden", !ok);
      if (ok) shown++;
    });
    if (sort.value === "az") {
      [...tiles].sort((a, b) => a.dataset.text.localeCompare(b.dataset.text))
        .forEach((t) => grid.appendChild(t));
    } else {
      [...tiles].sort((a, b) => order.get(a) - order.get(b))
        .forEach((t) => grid.appendChild(t));
    }
    let empty = grid.querySelector(".gempty");
    if (!shown) {
      if (!empty) { empty = document.createElement("p"); empty.className = "gempty"; grid.appendChild(empty); }
      empty.textContent = "No games match.";
    } else if (empty) { empty.remove(); }
    count.textContent = `${shown} game${shown === 1 ? "" : "s"}`;
  };

  search.addEventListener("input", apply);
  sort.addEventListener("change", apply);
  chips.addEventListener("click", (e) => {
    const chip = e.target.closest(".gchip");
    if (!chip) return;
    filter = chip.dataset.filter;
    chips.querySelectorAll(".gchip").forEach((c) => c.classList.toggle("active", c === chip));
    apply();
  });
  apply();
})();
