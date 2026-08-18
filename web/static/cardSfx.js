// Pack-opening sound effects, synthesized with the Web Audio API — no audio files, works
// offline, and the AudioContext is created on the user's click (autoplay policy satisfied).
// Ported from nsba-markets/frontend/src/pages/cardSfx.ts. Mute persists in localStorage.
// Exposed as window.CardSfx.
(function () {
  let ctx = null;
  function ac() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function isMuted() {
    try { return localStorage.getItem("sharplab-card-mute") === "1"; } catch { return false; }
  }
  function toggleMute() {
    const m = !isMuted();
    try { localStorage.setItem("sharplab-card-mute", m ? "1" : "0"); } catch { /* ignore */ }
    return m;
  }
  // Call on the click that starts a pack open, to unlock audio before the delayed whoosh.
  function primeAudio() { if (!isMuted()) ac(); }

  function tone(freq, start, dur, type, gain) {
    const c = ac(); if (!c) return;
    const o = c.createOscillator(), g = c.createGain();
    o.type = type; o.frequency.value = freq;
    o.connect(g); g.connect(c.destination);
    const t = c.currentTime + start;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(gain, t + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.start(t); o.stop(t + dur + 0.03);
  }

  function noise(dur, start, gain, cutoff) {
    const c = ac(); if (!c) return;
    const n = Math.floor(c.sampleRate * dur);
    const buf = c.createBuffer(1, n, c.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < n; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const src = c.createBufferSource(); src.buffer = buf;
    const filt = c.createBiquadFilter(); filt.type = "lowpass"; filt.frequency.value = cutoff;
    const g = c.createGain(); g.gain.value = gain;
    src.connect(filt); filt.connect(g); g.connect(c.destination);
    src.start(c.currentTime + start);
  }

  // A paper-tear: bandpass noise with an irregular "crackle" envelope.
  function rip(dur, start, gain, freq) {
    const c = ac(); if (!c) return;
    const n = Math.floor(c.sampleRate * dur);
    const buf = c.createBuffer(1, n, c.sampleRate);
    const d = buf.getChannelData(0);
    let crack = 0;
    for (let i = 0; i < n; i++) {
      const t = i / n;
      const env = Math.pow(1 - t, 1.4) * Math.min(1, t * 12);
      if (Math.random() < 0.28) crack = Math.random();
      d[i] = (Math.random() * 2 - 1) * env * (0.25 + 0.75 * crack);
    }
    const src = c.createBufferSource(); src.buffer = buf;
    const bp = c.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = freq; bp.Q.value = 0.7;
    const g = c.createGain(); g.gain.value = gain;
    src.connect(bp); bp.connect(g); g.connect(c.destination);
    src.start(c.currentTime + start);
  }

  // The pack ripping open — a paper tear, then a soft low "pop" as it bursts.
  function sfxPackOpen() {
    if (isMuted()) return;
    rip(0.32, 0, 0.5, 2600);
    rip(0.22, 0.16, 0.35, 3200);
    tone(120, 0.34, 0.35, "sine", 0.14);
  }

  function sfxFlip() {
    if (isMuted()) return;
    tone(520, 0, 0.06, "square", 0.06);
    noise(0.05, 0, 0.05, 3500);
  }

  const RANK = { common: 0, uncommon: 1, rare: 2, epic: 3, moment: 3, legendary: 4 };

  function sweep(f0, f1, start, dur, gain) {
    const c = ac(); if (!c) return;
    const o = c.createOscillator(), g = c.createGain();
    o.type = "sawtooth"; o.connect(g); g.connect(c.destination);
    const t = c.currentTime + start;
    o.frequency.setValueAtTime(f0, t);
    o.frequency.exponentialRampToValueAtTime(f1, t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(gain, t + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.start(t); o.stop(t + dur + 0.03);
  }

  function sparkle(start, count, gain) {
    const notes = [1046.5, 1318.5, 1568, 2093, 2637];
    for (let i = 0; i < count; i++) {
      tone(notes[i % notes.length], start + i * 0.05, 0.4, "sine", gain * (1 - (i / count) * 0.4));
    }
  }

  // A chime scaled to the card — commons tick, epics get an arpeggio + shimmer, legendaries
  // (and any gem) get a full fanfare: low boom, ascending run, pitch sweep, sparkle cascade.
  function sfxReveal(rarity, isHolo, gem) {
    if (isMuted()) return;
    let r = RANK[rarity] != null ? RANK[rarity] : 0;
    if (isHolo) r = Math.max(r, 3);
    if (gem) r = 4;
    const base = 523.25;
    if (r <= 0) { tone(base, 0, 0.09, "sine", 0.04); return; }
    if (r === 1) {
      tone(base, 0, 0.35, "sine", 0.1);
      tone(base * Math.pow(2, 4 / 12), 0.06, 0.4, "sine", 0.09);
      return;
    }
    if (r === 2) {
      [0, 4, 7].forEach((s, i) => tone(base * Math.pow(2, s / 12), i * 0.06, 0.45, "sine", 0.1));
      return;
    }
    if (r === 3) {
      [0, 4, 7, 12].forEach((s, i) => tone(base * Math.pow(2, s / 12), i * 0.05, 0.5, "triangle", 0.11));
      sweep(420, 1700, 0.04, 0.42, 0.06);
      sparkle(0.24, 4, 0.05);
      return;
    }
    tone(base / 2, 0, 0.7, "sine", 0.22);
    tone(base / 2, 0, 0.7, "triangle", 0.08);
    [0, 4, 7, 12, 16, 19].forEach((s, i) => tone(base * Math.pow(2, s / 12), i * 0.07, 0.6, "triangle", 0.12));
    sweep(280, 2600, 0.04, 0.55, 0.07);
    sparkle(0.34, 8, 0.06);
    tone(base * 4, 0.5, 0.9, "sine", 0.05);
  }

  window.CardSfx = { primeAudio, isMuted, toggleMute, sfxPackOpen, sfxFlip, sfxReveal };
})();
