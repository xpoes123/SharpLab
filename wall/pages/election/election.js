// Shared by the election data tiles: polls GET data.json (wall/election.py's snapshot) every 30 s, formats, colours.
"use strict";
const E = (() => {
  const PARTY = { D: "Dem", R: "Rep", I: "Ind", O: "Other" };
  const OFFICE = { S: "Senate", H: "House", G: "Governor" };
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const fill = (p, called) => {
    const k = { D: "dem", R: "rep", I: "ind", O: "ind" }[p];
    return k ? `var(--${k}${called ? "" : "-lead"})` : "var(--none)";
  };
  const clock = (t) => {
    const d = t ? new Date(t * 1000) : new Date();
    let h = d.getHours(), m = d.getMinutes();
    return `${((h + 11) % 12) + 1}:${m < 10 ? "0" : ""}${m}${h < 12 ? " AM" : " PM"}`;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const net = (n) => {   // {D: +2, R: -2} → "D +2" / "No change"
    const d = n && n.D || 0, r = n && n.R || 0;
    if (d > 0) return { text: `Dem +${d}`, p: "D" };
    if (r > 0) return { text: `Rep +${r}`, p: "R" };
    return { text: "No net change", p: "" };
  };
  // when nothing is counted yet: the next poll closing (from the races' closing times)
  const nextClose = (data) => {
    const now = data.now || Date.now() / 1000;
    const ts = [...new Set((data.races || []).map((r) => r.closes).filter((t) => t > now))].sort((a, b) => a - b);
    if (!ts.length) return "";
    const sts = [...new Set(data.races.filter((r) => r.closes === ts[0]).map((r) => r.state))].sort();
    return `Next polls close ${clock(ts[0])} · ${sts.slice(0, 8).join(" ")}${sts.length > 8 ? " …" : ""}`;
  };
  const footer = (data) => {
    const by = data.source === "NYT (AP)" ? "AP" : data.source || "—";
    const upd = data.updated ? `updated ${clock(data.updated)}` : "waiting for the feed";
    return `<span class="unofficial caps">Unofficial results</span>` +
      `<span class="muted mono">calls: ${esc(by)} decision desk · data: ${esc(data.source || "—")} · ${upd}</span>` +
      (data.error ? `<span class="err mono">${esc(data.error)}</span>` : "");
  };
  function poll(onData, every = 30000) {   // until the service has its first results: every 3 s
    const go = () => fetch("data.json", { cache: "no-store" }).then((r) => r.json())
      .then((d) => { onData(d); return d.updated ? every : 3000; }, () => 5000)
      .then((ms) => setTimeout(go, ms));
    go();
  }
  function tickClock(el) {
    const f = () => { el.textContent = clock(); };
    f();
    setInterval(f, 5000);
  }
  return { PARTY, OFFICE, css, fill, clock, esc, net, nextClose, footer, poll, tickClock };
})();
