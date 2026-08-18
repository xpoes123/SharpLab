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
