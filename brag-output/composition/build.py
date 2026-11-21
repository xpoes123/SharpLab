#!/usr/bin/env python3
"""Generates index.html. Timing lives here so every cut stays on the track's 0.625 s beat."""
import json

KICKS = [5.03, 5.23, 5.68, 6.33, 6.84, 7.11, 7.44, 7.88, 8.13, 8.45, 8.77, 9.09, 9.79, 10.06, 10.68,
         11.32, 12.1, 13.18, 13.77, 13.97, 14.4, 14.59, 15.02, 15.65, 15.87, 16.11, 16.47, 16.71, 17.1,
         17.52, 17.76, 18.07, 18.39, 18.64, 19.03, 19.28, 19.59, 20.01, 20.21, 20.85, 21.3, 21.47, 21.91]

# id, kind, src, start, dur, media zoom/origin/x, caption, strip, exhibit, filename, caption side
S = [
    ("plinko", "video", "plinko", 5.0, 1.25, (1.04, 1.1, "50% 40%", 0), "Stochastic research", "server-settled wagers.", "A", "casino.html", "l"),
    ("crash", "video", "crash", 6.25, 1.25, (1.1, 1.22, "50% 45%", 0), "Risk management", "busted at 1.41×.", "B", "casino.html", "l"),
    ("horse", "video", "horse", 7.5, 1.875, (1.12, 1.2, "50% 60%", 0), "Equine research", "real odds. fake horses.", "C", "horserace.html", "l"),
    ("pack", "video", "pack", 9.375, 0.625, (2.3, 2.7, "50% 46%", 0), "", "", "D", "cards.html", "l"),
    ("flips", "video", "flips", 10.0, 1.875, (1.7, 1.85, "50% 30%", 0), "Alternative assets", "pull-rate odds, per set.", "D", "cards.html", "l"),
    ("mew", "img", "mew.png", 11.875, 1.875, (1.9, 2.05, "23.4% 51.7%", 0), "Mew plays point guard", "“Tiny, though.”", "E", "pokehoops.html", "r"),
    ("court", "video", "court", 13.75, 1.875, (1.28, 1.38, "50% 58%", 300), "3v3 to 11", "moves. stamina. weather.", "F", "pokehoops.html", "l"),
    ("pig", "video", "pig", 15.625, 1.25, (1.3, 1.4, "50% 55%", 300), "Pork futures", "provably solvable boards.", "G", "trappig.html", "l"),
    ("wall", "video", "wall", 16.875, 2.5, (1.42, 1.56, "25% 58%", 0), "Live election TV", "14 hours on air. one GPU.", "H", "wall.html", "l"),
]
CUTS = [s[3] for s in S] + [19.375]

SFX = [("sfx-line5", "impactPunch_heavy_000.ogg", 3.0, 0.65, 0.8), ("sfx-pack", "cards-pack-open-1.ogg", 9.375, 0.9, 0.85),
       ("sfx-flip", "card-place-1.ogg", 10.0, 0.5, 0.8), ("sfx-81", "impactPlate_heavy_000.ogg", 20.625, 0.49, 0.85),
       ("sfx-81b", "error_005.ogg", 20.65, 0.5, 0.5), ("sfx-title", "impactBell_heavy_000.ogg", 22.5, 1.48, 0.8)]

LINE3 = "A personal quantitative sports betting research lab."
TYPE_START, TYPE_END = 0.35, 2.35


def clip(s, track):
    id_, kind, src, start, dur = s[:5]
    if kind == "video":
        return (f'<video id="v-{id_}" class="clip shot" muted playsinline data-start="{start}" data-duration="{dur}" '
                f'data-track-index="{track}" src="assets/clips/{src}.mp4"></video>')
    return (f'<img id="v-{id_}" class="clip shot" alt="" data-start="{start}" data-duration="{dur}" '
            f'data-track-index="{track}" src="assets/img/{src}">')


def caption(s, track):
    id_, _, _, start, dur, _, cap, strip, ex, fname, side = s
    lab = f'<div class="exh"><span>Exhibit {ex}</span><span>{fname}</span></div>'
    body = ""
    if cap:
        body = f'<div class="cap {side}"><h2 id="c-{id_}">{cap}</h2><p id="p-{id_}">{strip}</p></div>'
    return f'<div id="o-{id_}" class="clip over" data-start="{start}" data-duration="{dur}" data-track-index="{track}">{lab}{body}</div>'


chars = "".join(f'<span class="ch">{c}</span>' for c in LINE3)
keys = []
n = 0
t = TYPE_START
while t < TYPE_END:
    n += 1
    keys.append((f"key{n}", f"keypress-00{(n % 9) + 1}.wav", round(t, 3), 0.25, 0.55))
    t += 0.15

audio = "\n".join(f'<audio id="{i}" data-start="{st}" data-duration="{d}" data-track-index="{20 + k}" data-volume="{v}" src="assets/sfx/{f}"></audio>'
                  for k, (i, f, st, d, v) in enumerate(SFX + keys))

html = f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=1920, height=1080">
<title>SharpLab: Not a gambling product</title>
<script src="assets/gsap.min.js"></script>
<style>
:root{{--ink:#0e0e14;--bone:#f4efe2;--gold:#e0b458;--lab:#a78bd0;--dim:#6f6f82}}
body{{margin:0;background:var(--ink)}}
#root{{position:relative;width:100%;height:100%;overflow:hidden;background:var(--ink);color:var(--bone)}}
.clip{{position:absolute;inset:0}}
#punch{{position:absolute;inset:0;filter:contrast(1.14) saturate(1.28) brightness(1.04)}}
.shot{{width:100%;height:100%;object-fit:cover}}
.over h2{{font:400 176px/0.92 'League Gothic';text-transform:uppercase;margin:0;letter-spacing:.005em;text-shadow:0 6px 44px rgba(8,8,12,.95),0 2px 6px rgba(8,8,12,.9)}}
.over p{{font:700 31px/1.3 'IBM Plex Mono';color:var(--gold);margin:14px 0 0;text-shadow:0 2px 16px rgba(8,8,12,1),0 0 4px rgba(8,8,12,1)}}
.cap{{position:absolute;bottom:112px;max-width:1500px}}
.cap.l{{left:80px}} .cap.r{{right:80px;text-align:right}} #o-mew{{background:linear-gradient(to left,rgba(8,8,12,.95) 0,rgba(8,8,12,.9) 36%,rgba(8,8,12,0) 52%)}} #o-mew .cap{{max-width:800px}}
.exh{{position:absolute;left:80px;right:80px;top:20px;height:40px;display:flex;justify-content:space-between;align-items:center;font:400 28px 'IBM Plex Mono';color:#c9c6d6;z-index:5}}
.exh span:first-child{{color:var(--gold);font-weight:700;text-transform:uppercase;letter-spacing:.12em}}
.bar{{position:absolute;left:0;right:0;height:78px;background:#08080c;z-index:4}}
#vig{{background:radial-gradient(ellipse at 50% 46%,rgba(8,8,12,0) 46%,rgba(8,8,12,.82) 100%),linear-gradient(to top,rgba(8,8,12,.86) 0,rgba(8,8,12,0) 46%)}}
#grain{{position:absolute;inset:0;background:url(assets/img/grain.png);opacity:.075;mix-blend-mode:overlay;pointer-events:none;z-index:9}}
#flash{{position:absolute;inset:0;background:var(--bone);opacity:0;z-index:8;pointer-events:none}}
/* README */
#hook{{background:#101018}}
#doc{{position:absolute;left:150px;top:262px;right:110px;transform-origin:18% 66%}}
.ln{{display:grid;grid-template-columns:96px 1fr;font:400 64px/1.32 'IBM Plex Mono';margin-bottom:38px}}
.ln i{{font-style:normal;color:#9a9aae;font-size:38px;padding-top:16px}}
.dimt{{color:#b4b4c6}}
.ch{{opacity:0}}
#l5{{font:400 216px/1 'League Gothic';text-transform:uppercase;color:var(--gold);letter-spacing:.005em;margin-top:-8px}}
#fname{{position:absolute;left:150px;top:96px;font:400 30px 'IBM Plex Mono';color:#b4b4c6}}
/* audit */
#games{{width:100%;height:100%;object-fit:cover;filter:grayscale(.55) contrast(1.1) brightness(.22)}}
#ledger{{position:absolute;left:110px;right:110px;top:110px}}
#ledger h3{{font:700 30px 'IBM Plex Mono';letter-spacing:.14em;color:var(--gold);margin:0 0 20px;text-transform:uppercase}}
.row{{position:relative;display:flex;justify-content:space-between;align-items:flex-end;border-top:3px solid #55556a;padding:10px 0 0}}
.row b{{font:400 40px 'IBM Plex Mono';color:#dedbe8;padding-bottom:30px}}
.row span{{font:400 190px/1 'League Gothic'}}
#r3{{border-top-color:var(--gold)}}
#r3 b{{color:var(--gold);font-weight:700;font-size:52px;padding-bottom:64px}}
#r3 span{{color:var(--gold);font-size:340px;line-height:.92}}
/* title */
#title{{background:#0a0a10;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}
#word{{font:400 520px/.9 'League Gothic';letter-spacing:.008em}}
#word em{{font-style:normal;color:var(--lab)}}
#tag{{font:700 60px 'IBM Plex Mono';color:var(--gold);margin-top:16px}}
#repo{{font:400 31px 'IBM Plex Mono';color:#b5b3c4;margin-top:30px}}
</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-width="1920" data-height="1080" data-duration="25">
<audio id="music" data-start="0" data-duration="25" data-track-index="10" data-volume="0.5" src="assets/music/close-eyes-15-40.wav"></audio>
{audio}

<div id="hook" class="clip" data-start="0" data-duration="5" data-track-index="1">
  <div id="fname">README.md</div>
  <div id="doc">
    <div class="ln"><i>3</i><div class="dimt">{chars}</div></div>
    <div class="ln" id="row5"><i>5</i><div id="l5">Not a gambling product.</div></div>
  </div>
</div>

<div id="punch">
{chr(10).join(clip(s, 2) for s in S)}
<div id="vig" class="clip" data-start="5" data-duration="14.375" data-track-index="8"></div>
</div>
<div id="bars" class="clip" data-start="5" data-duration="14.375" data-track-index="3"><div class="bar" style="top:0"></div><div class="bar" style="bottom:0"></div></div>
{chr(10).join(caption(s, 4) for s in S)}

<video id="games" class="clip" muted playsinline data-start="19.375" data-duration="3.125" data-track-index="6" src="assets/clips/games.mp4"></video>
<div id="audit" class="clip" data-start="19.375" data-duration="3.125" data-track-index="5">
  <div id="ledger">
    <h3>Audit of findings</h3>
    <div class="row" id="r1"><b>Odds snapshots taken</b><span id="n1">2.9M</span></div>
    <div class="row" id="r2"><b>Casino rounds played</b><span id="n2">7,923</span></div>
    <div class="row" id="r3"><b>Bets logged</b><span id="n3">81</span></div>
  </div>
</div>

<div id="title" class="clip" data-start="22.5" data-duration="2.5" data-track-index="7">
  <div id="word">SHARP<em>LAB</em></div>
  <div id="tag">Not a gambling product.</div>
  <div id="repo">1,027 commits. 144,784 lines of Python.</div>
</div>

<div id="flash"></div>
<div id="grain"></div>
</div>
<script>
const tl = gsap.timeline({{ paused: true }});
const SHOTS = {json.dumps([[s[0], s[3], s[4], s[5]] for s in S])};
const KICKS = {json.dumps(KICKS)};
const CUTS = {json.dumps(CUTS)};
let seed = 1337;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

// hook: type line 3, slam line 5 on the track's hit at 18.0 s (video 3.0)
const chars = gsap.utils.toArray(".ch");
const step = ({TYPE_END} - {TYPE_START}) / chars.length;
chars.forEach((c, i) => {{
  const t = {TYPE_START} + i * step;
  tl.set(c, {{ opacity: 1, backgroundColor: "#e0b458", color: "#101018" }}, t);
  tl.set(c, {{ backgroundColor: "transparent", color: "#b4b4c6" }}, i === chars.length - 1 ? 2.95 : t + step);
}});
gsap.set("#row5", {{ opacity: 0 }});
tl.set("#row5", {{ opacity: 1 }}, 3.0); // beat-locked: 3.00 (track 18.0)
tl.fromTo("#l5", {{ scale: 1.5, x: 120 }}, {{ scale: 1, x: 0, duration: 0.14, ease: "power4.in", transformOrigin: "0% 60%" }}, 2.92);
tl.fromTo("#doc", {{ scale: 1 }}, {{ scale: 1.06, duration: 3.0, ease: "none" }}, 0);
tl.to("#doc", {{ scale: 1.2, duration: 2, ease: "power1.in" }}, 3.0);
tl.fromTo("#hook", {{ x: -14 }}, {{ x: 0, duration: 0.2, ease: "power2.out" }}, 3.0);

// footage: each shot pushes in; captions slam
SHOTS.forEach(([id, st, d, m]) => {{
  const [from, to, origin, x] = m;
  gsap.set("#v-" + id, {{ transformOrigin: origin }});
  tl.fromTo("#v-" + id, {{ scale: from, x }}, {{ scale: to, x, duration: d, ease: "none", immediateRender: true }}, st);
  const h = document.getElementById("c-" + id);
  if (!h) return;
  tl.fromTo(h, {{ scale: 1.34, y: 26, opacity: 0 }}, {{ scale: 1, y: 0, opacity: 1, duration: 0.11, ease: "power4.out", transformOrigin: "50% 100%" }}, st + 0.02);
  tl.fromTo(h, {{ textShadow: "16px 0 0 rgba(224,62,62,.85),-16px 0 0 rgba(90,170,220,.85)" }}, {{ textShadow: "0px 6px 44px rgba(8,8,12,.95),0px 2px 6px rgba(8,8,12,.9)", duration: 0.26, ease: "power2.out" }}, st + 0.02);
  tl.fromTo("#p-" + id, {{ opacity: 0, x: -26 }}, {{ opacity: 1, x: 0, duration: 0.12, ease: "power3.out" }}, st + 0.16);
}});

// footage punches on the measured kick onsets
KICKS.forEach((t) => {{
  if (t >= 19.375) return;
  const a = (rnd() - 0.5) * 22, b = (rnd() - 0.5) * 14;
  tl.fromTo("#punch", {{ scale: 1.035, x: a, y: b }}, {{ scale: 1, x: 0, y: 0, duration: 0.17, ease: "power3.out" }}, t);
}});

// flash frames on the cuts. the drop gets the big one.
gsap.set("#flash", {{ opacity: 0 }});
tl.fromTo("#flash", {{ opacity: 1 }}, {{ opacity: 0, duration: 0.2, ease: "power2.out", immediateRender: false }}, 5.0); // beat-locked: 5.00 (track 20.0)
CUTS.slice(1).forEach((t) => tl.fromTo("#flash", {{ opacity: 0.32 }}, {{ opacity: 0, duration: 0.1, ease: "power2.out", immediateRender: false }}, t));

// audit: one row per beat, the set then holds. beat-grid: 19.375, 20.0, 20.625
[["#r1", 19.45], ["#r2", 20.0], ["#r3", 20.625]].forEach(([s, t]) => {{
  tl.fromTo(s, {{ opacity: 0, x: 70 }}, {{ opacity: 1, x: 0, duration: 0.1, ease: "power4.out" }}, t);
}});
tl.fromTo("#ledger", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.05 }}, 19.375);
tl.fromTo("#n3", {{ scale: 1.5 }}, {{ scale: 1, duration: 0.12, ease: "power4.out", transformOrigin: "100% 70%" }}, 20.625);
tl.fromTo("#audit", {{ x: 26 }}, {{ x: 0, duration: 0.3, ease: "power3.out" }}, 20.625);
tl.fromTo("#ledger", {{ scale: 1 }}, {{ scale: 1.03, duration: 1.875, ease: "none" }}, 20.625);

// title lands on the break. hard end on the re-entry at 25.0.
tl.fromTo("#word", {{ scale: 1.2, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.16, ease: "power4.out" }}, 22.5); // beat-locked: 22.50 (track 37.5)
tl.fromTo("#word", {{ y: 0 }}, {{ y: -6, duration: 2.3, ease: "none" }}, 22.66);
tl.fromTo("#tag", {{ opacity: 0, y: 18 }}, {{ opacity: 1, y: 0, duration: 0.16, ease: "power3.out" }}, 22.95);
tl.fromTo("#repo", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.14, ease: "none" }}, 23.2);

// grain steps at 15 fps. pre-seeded, so seeks are deterministic.
for (let f = 0; f < 375; f++) tl.set("#grain", {{ backgroundPosition: Math.floor(rnd() * 512) + "px " + Math.floor(rnd() * 512) + "px" }}, f / 15);

window.__timelines["main"] = tl;
</script>
</body></html>
"""
open("index.html", "w").write(html)
