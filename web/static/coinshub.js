// Shared coins hub — makes the nav coin chip (.coinschip) clickable on any page,
// opening a "where did my coins come from" history + ways-to-earn panel. Self-contained:
// injects its own styles, fetches /api/v1/cards/coins, and delegates clicks. Load it on
// pages whose own script doesn't already wire a coins hub (games, daily, trappig, …).
(function () {
  if (window.__coinsHubLoaded) return;         // don't double-wire if included twice
  window.__coinsHubLoaded = true;

  var num = function (n) { return n == null ? "0" : Number(n).toLocaleString(); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var ago = function (iso) {
    var t = Date.parse(iso);
    if (!t) return "";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  };

  var EARN_WAYS = [
    ["⬆️", "Level up", "100 coins for every level you reach"],
    ["🏅", "Unlock achievements", "150 coins per achievement"],
    ["🎮", "Win multiplayer games", "50 coins for winning a /play multiplayer game"],
    ["💬", "Chat in the server", "5 coins per message, up to 500 a day"],
    ["🎯", "Log a bet", "50 coins for logging a sports bet with /bet log"],
    ["📈", "Log a trade", "50 coins for recording a stock or option trade"],
    ["🏀", "Daily pick'em", "25 coins per pick — plus a payout when your pick wins"],
    ["🃏", "Complete a set", "One-time coin bonus for owning every card in a set"],
    ["♻️", "Quick-sell cards", "Sell cards back for 75% of book value"],
  ];

  // Inject styles once (the overlay CSS isn't present on every page).
  var style = document.createElement("style");
  style.textContent =
    ".hubov{position:fixed;inset:0;z-index:250;display:grid;place-items:center;padding:20px;background:rgba(5,7,11,.6);backdrop-filter:blur(3px)}" +
    ".hubcard{width:min(460px,100%);max-height:86vh;overflow-y:auto;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px 20px 20px;box-shadow:0 24px 60px -20px #000}" +
    ".hubhead{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.hubhead h3{margin:0;font-size:17px}" +
    ".hubx{background:none;border:0;color:var(--muted);font-size:18px;cursor:pointer;line-height:1;padding:4px 8px;border-radius:8px}.hubx:hover{color:var(--fg);background:var(--panel2)}" +
    ".hubrow{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-top:1px solid var(--line)}" +
    ".hubicon{font-size:20px;width:30px;text-align:center;flex:0 0 auto}.hubt{font-weight:700;font-size:14px}.hubd{color:var(--muted);font-size:12.5px;line-height:1.45;margin-top:2px}" +
    ".hubfoot{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:13px;text-align:right}.hubfoot b{color:var(--gold)}" +
    ".hubsub{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin:14px 4px 8px;font-weight:700}.hubsub:first-of-type{margin-top:4px}" +
    ".hubledger{display:flex;flex-direction:column;gap:6px}.ledrow{display:flex;align-items:center;gap:10px;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:8px 11px}" +
    ".ledamt{font-weight:800;color:var(--r-uncommon,#9ece6a);font-variant-numeric:tabular-nums;white-space:nowrap}.ledreason{flex:1;font-size:13px;font-weight:600}.ledtime{font-size:11px;color:var(--muted);white-space:nowrap}" +
    ".hubempty{color:var(--muted);text-align:center;padding:14px;font-size:13px}.coinschip{cursor:pointer}";
  document.head.appendChild(style);

  function close() { var ov = document.querySelector(".hubov"); if (ov) ov.remove(); }

  async function open() {
    if (document.querySelector(".hubov")) return;
    var ov = document.createElement("div");
    ov.className = "hubov";
    var earn = EARN_WAYS.map(function (w) {
      return '<div class="hubrow"><div class="hubicon">' + w[0] + '</div><div><div class="hubt">' +
        esc(w[1]) + '</div><div class="hubd">' + esc(w[2]) + "</div></div></div>";
    }).join("");
    function render(bal, ledger) {
      var recent = ledger && ledger.length
        ? '<div class="hubledger">' + ledger.map(function (e) {
            return '<div class="ledrow"><span class="ledamt">+🪙' + num(e.amount) + '</span><span class="ledreason">' +
              esc(e.reason) + '</span><span class="ledtime">' + ago(e.created_at) + "</span></div>";
          }).join("") + "</div>"
        : '<div class="hubempty">No coins earned yet — here’s how 👇</div>';
      ov.innerHTML = '<div class="hubcard"><div class="hubhead"><h3>Your coins 🪙</h3>' +
        '<button class="hubx" aria-label="Close">✕</button></div>' +
        '<div class="hubsub">Recent earnings</div>' + recent +
        '<div class="hubsub">Ways to earn</div>' + earn +
        '<div class="hubfoot">Your balance: <b>🪙 ' + num(bal) + "</b></div></div>";
    }
    render(0, null);
    ov.addEventListener("click", function (e) {
      if (e.target === ov || e.target.closest(".hubx")) close();
    });
    document.body.appendChild(ov);
    try {
      var r = await fetch("/api/v1/cards/coins", { credentials: "include" });
      var d = r.ok ? await r.json() : {};
      if (document.body.contains(ov)) render(d.balance || 0, d.ledger || []);
    } catch (_e) { /* leave the empty state */ }
  }

  window.showCoinsHub = open;
  // A coin chip may render after load — delegate on document.
  document.addEventListener("click", function (e) {
    var el = e.target.closest(".coinschip");
    if (el) open();
  });
})();
