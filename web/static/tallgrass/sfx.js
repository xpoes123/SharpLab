// Small synth, no audio files. Everything is throttled so a 500-unit brawl reads as a roar, not a buzz.
export class Sfx {
  constructor() { this.ctx = null; this.muted = false; this.last = {}; }
  unlock() {
    if (this.ctx) return this.ctx.resume?.();
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = this.muted ? 0 : 0.5;
    const comp = this.ctx.createDynamicsCompressor();
    this.master.connect(comp);
    comp.connect(this.ctx.destination);
    const len = this.ctx.sampleRate * 2;
    this.noise = this.ctx.createBuffer(1, len, this.ctx.sampleRate);
    const d = this.noise.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    this.wind = this.loop(320, 0.035);
    this.rainBed = this.loop(2600, 0);
  }
  setMuted(on) {
    this.muted = on;
    if (this.master) this.master.gain.value = on ? 0 : 0.5;
  }
  loop(freq, gain) {
    const src = this.ctx.createBufferSource(), f = this.ctx.createBiquadFilter(), g = this.ctx.createGain();
    src.buffer = this.noise; src.loop = true;
    f.type = "lowpass"; f.frequency.value = freq;
    g.gain.value = gain;
    src.connect(f); f.connect(g); g.connect(this.master);
    src.start();
    return g;
  }
  setWeather(kind) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    this.rainBed.gain.setTargetAtTime(kind === "rain" ? 0.05 : 0, t, 0.6);
    this.wind.gain.setTargetAtTime(kind === "sand" ? 0.11 : 0.035, t, 0.6);
  }
  burst(freq, dur, gain, type = "bandpass", q = 1, sweep = 0) {
    const c = this.ctx, t = c.currentTime;
    const s = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain();
    s.buffer = this.noise;
    s.loop = true;
    s.playbackRate.value = 0.8 + Math.random() * 0.4;
    f.type = type; f.frequency.value = freq; f.Q.value = q;
    if (sweep) f.frequency.exponentialRampToValueAtTime(Math.max(40, freq * sweep), t + dur);
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0008, t + dur);
    s.connect(f); f.connect(g); g.connect(this.master);
    s.start(t, Math.random());
    s.stop(t + dur + 0.02);
  }
  tone(freq, to, dur, gain, type = "sine") {
    const c = this.ctx, t = c.currentTime;
    const o = c.createOscillator(), g = c.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    o.frequency.exponentialRampToValueAtTime(Math.max(20, to), t + dur);
    g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0008, t + dur);
    o.connect(g); g.connect(this.master);
    o.start(t);
    o.stop(t + dur + 0.02);
  }
  play(name) {
    if (!this.ctx || this.muted) return;
    const now = performance.now();
    const gapMs = { hit: 55, hit2: 75, pew: 70, zap: 90, faint: 70, thud: 90, stomp: 170 }[name] ?? 40;
    if (now - (this.last[name] ?? 0) < gapMs) return;
    this.last[name] = now;
    switch (name) {
      case "hit": this.burst(900 + Math.random() * 900, 0.07, 0.14, "bandpass", 1.5); break;
      case "hit2": this.burst(1700, 0.1, 0.22, "bandpass", 2.5); this.tone(620, 180, 0.08, 0.08, "square"); break;
      case "pew": this.tone(1100 + Math.random() * 500, 240, 0.11, 0.05, "triangle"); break;
      case "zap": this.burst(3400, 0.12, 0.13, "highpass", 2); break;
      case "fire": this.burst(700, 0.32, 0.15, "lowpass", 0.6, 2.3); break;
      case "wave": this.burst(650, 0.7, 0.26, "lowpass", 0.7, 3); break;
      case "beam": this.tone(190, 900, 0.5, 0.15, "sawtooth"); this.burst(2200, 0.5, 0.1, "bandpass", 1); break;
      case "quake": this.tone(66, 26, 1.2, 0.72, "sine"); this.burst(120, 1.1, 0.75, "lowpass", 0.5); break;
      case "boom": this.tone(120, 22, 0.8, 0.75, "sine"); this.burst(420, 0.75, 0.75, "lowpass", 0.6, 0.2); break;
      case "thunder": this.burst(5000, 0.16, 0.42, "highpass", 1); this.burst(140, 1, 0.7, "lowpass", 0.5); break;
      case "thunderFar": this.burst(110, 2, 0.32, "lowpass", 0.4); break;
      case "thud": this.tone(100, 40, 0.18, 0.4); break;
      case "stomp": this.tone(72, 34, 0.24, 0.5); break;
      case "faint": this.tone(620 + Math.random() * 180, 150, 0.14, 0.03, "square"); break;
      case "morph": this.tone(300, 1100, 0.34, 0.08, "sine"); this.tone(460, 1500, 0.34, 0.05, "triangle"); break;
      case "evolving": this.tone(480, 840, 0.22, 0.06, "sine"); break;
      case "evolved": [523, 659, 784, 1047].forEach((f, i) => setTimeout(() => this.tone(f, f, 0.34, 0.11, "triangle"), i * 75)); break;
      case "rain": this.setWeather("rain"); break;
      case "wind": break;
      default: break;
    }
  }
}
