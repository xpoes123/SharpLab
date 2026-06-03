// SharpLab HQ analytics — cookieless. Random visitor id in localStorage.
(function () {
  var ENDPOINT = "/api/v1/analytics/event";
  var sid;
  try {
    sid = localStorage.getItem("sl_sid");
    if (!sid) {
      sid = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
        : "v" + Date.now().toString(36) + Math.random().toString(36).slice(2);
      localStorage.setItem("sl_sid", sid);
    }
  } catch (_e) {
    sid = "eph-" + Math.random().toString(36).slice(2);
  }

  function send(type, extra) {
    var payload = { type: type, sid: sid, page: location.pathname, ref: document.referrer || "", ts: Date.now() };
    if (extra) for (var k in extra) if (Object.prototype.hasOwnProperty.call(extra, k)) payload[k] = extra[k];
    var body = JSON.stringify(payload);
    try {
      if (navigator.sendBeacon) navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }));
      else fetch(ENDPOINT, { method: "POST", body: body, headers: { "Content-Type": "application/json" }, keepalive: true }).catch(function () {});
    } catch (_e) { /* analytics never breaks the page */ }
  }

  send("pageview");

  // Duration — sent once on first hide/unload.
  var start = Date.now(), sent = false;
  function flush() {
    if (sent) return; sent = true;
    send("duration", { ms: Date.now() - start });
  }
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", flush);

  // Owner-only: inject the Analytics nav link if the signed-in user is the owner.
  fetch("/api/v1/hq/me", { credentials: "include" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.is_owner) return;
      var nav = document.querySelector("nav.nav");
      if (nav && !nav.querySelector('a[href="/analytics"]')) {
        var a = document.createElement("a");
        a.href = "/analytics"; a.textContent = "Analytics";
        nav.appendChild(a);
      }
    })
    .catch(function () {});
})();
