// Tall Grass renderer: HD-2D pixel sprites standing at real height in a lit, wind-blown 3D field.
// Everything is instanced, so thousands of units cost one draw call per layer. Sim state is read-only here.
import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { H, TILE_M, W, ZONE, zoneOf } from "./sim.js";

const FW = W * TILE_M, FH = H * TILE_M; // field size in metres
const MIN_BODY = 0.42; // ponytail: the smallest bodies are drawn at 0.42 m or a 0.3 m Rattata is one pixel at wide shot; everything else is true height
const MAX_UNITS = 6000, MAX_PARTICLES = 24000, MAX_BEAMS = 900, MAX_RINGS = 96;
export const TIERS = {
  high: { grass: 340000, particles: 24000, pixels: 5e6, taps: 14, weather: 5000 },
  medium: { grass: 140000, particles: 12000, pixels: 2.6e6, taps: 8, weather: 2800 },
  low: { grass: 50000, particles: 5000, pixels: 1.2e6, taps: 5, weather: 1200 },
};
const SPRITE_ROOT = "/pokehoops/sprites/";

const hex = (h, k = 1) => [((h >> 16) & 255) / 255 * k, ((h >> 8) & 255) / 255 * k, (h & 255) / 255 * k];
export const TYPE_COLOR = {
  normal: 0xd8d4c0, fire: 0xff6a1a, water: 0x38a0ff, electric: 0xffd21f, grass: 0x62e040, ice: 0x80ecff, fighting: 0xe0502a, poison: 0xb84de0,
  ground: 0xd8a040, flying: 0xa0b4ff, psychic: 0xff4d98, bug: 0xb8d820, rock: 0xbfa050, ghost: 0x7a5cd0, dragon: 0x6e44ff, dark: 0x7a6850, steel: 0xc0c8d8, fairy: 0xf490d8,
};

export const BIOMES = {
  meadow: {
    name: "Golden Meadow", top: 0x3d6ea8, horizon: 0xf6b66a, sun: [0.55, 0.24, -0.8], sunColor: 0xffd29a, sunK: 1.55, ambient: 0x6d7f9c, ambientK: 0.72, fog: 0xe8b98a, fogD: 0.0024,
    soil: [0x2c3a15, 0x424a1c], root: 0x173414, tip: 0xa2c24a, grassH: [0.34, 0.8], density: 1, wind: 1, hill: 40, bump: 3, props: "oaks", air: "pollen", cloud: 0.8,
  },
  forest: {
    name: "Viridian Forest", top: 0x1d3b3e, horizon: 0x87a36a, sun: [-0.5, 0.3, -0.75], sunColor: 0xcfe08a, sunK: 1.25, ambient: 0x4d6f5f, ambientK: 0.8, fog: 0x4f6e4a, fogD: 0.0078,
    soil: [0x24301a, 0x3a3f1f], root: 0x12301a, tip: 0x6fa83f, grassH: [0.3, 0.72], density: 1, wind: 0.5, hill: 24, bump: 2, props: "pines", air: "fireflies", cloud: 0.2,
  },
  cave: {
    name: "Mt. Moon Crater", top: 0x070b1d, horizon: 0x24305a, sun: [0.35, 0.6, -0.72], sunColor: 0x9fb8ff, sunK: 0.9, ambient: 0x31406f, ambientK: 0.9, fog: 0x141c38, fogD: 0.0058, stars: 1,
    soil: [0x20222e, 0x303246], root: 0x12182a, tip: 0x4f7a8c, grassH: [0.1, 0.3], density: 0.6, wind: 0.25, hill: 70, bump: 6, props: "crystals", air: "motes", cloud: 0,
  },
  shore: {
    name: "Route 19 Shore", top: 0x2f79bf, horizon: 0xffc39a, sun: [0.25, 0.2, -0.9], sunColor: 0xffb98a, sunK: 1.45, ambient: 0x7f9fc0, ambientK: 0.72, fog: 0xf0c2a4, fogD: 0.0022,
    soil: [0x8a7650, 0xa8946a], root: 0x6d7f3f, tip: 0xd8cf80, grassH: [0.2, 0.55], density: 0.42, wind: 1.3, hill: -22, bump: 1, props: "rocks", air: "spray", cloud: 0.6, liquid: 1,
  },
  snow: {
    name: "Seafoam Ice", top: 0x4a6f9f, horizon: 0xdbe6f2, sun: [-0.4, 0.28, -0.85], sunColor: 0xffe2c8, sunK: 1.45, ambient: 0xa8c4ea, ambientK: 0.95, fog: 0xd2e0ee, fogD: 0.0038,
    soil: [0xb4c6da, 0xdbe6f0], root: 0x8ba4b8, tip: 0xe8f2f8, grassH: [0.12, 0.34], density: 0.55, wind: 1.1, hill: 44, bump: 4, props: "spires", air: "snow", cloud: 0.9,
  },
  ember: {
    name: "Cinnabar Caldera", top: 0x1d1014, horizon: 0xc2452a, sun: [0.5, 0.2, -0.8], sunColor: 0xff8a50, sunK: 1.4, ambient: 0x5a3540, ambientK: 0.85, fog: 0x4a1f1c, fogD: 0.0056,
    soil: [0x1f1a1a, 0x332624], root: 0x241812, tip: 0x8a6a30, grassH: [0.12, 0.36], density: 0.5, wind: 0.8, hill: 38, bump: 5, props: "rocks", air: "embers", cloud: 0.4, liquid: 2,
  },
  dusk: {
    name: "Lavender Dusk", top: 0x241a4d, horizon: 0xc56a92, sun: [-0.35, 0.14, -0.92], sunColor: 0xff9db0, sunK: 1.35, ambient: 0x5a4f8a, ambientK: 0.85, fog: 0x7a4f7f, fogD: 0.0042,
    soil: [0x262a30, 0x363a40], root: 0x18282c, tip: 0x8a9c70, grassH: [0.28, 0.66], density: 0.9, wind: 0.7, hill: 34, bump: 3, props: "oaks", air: "fireflies", cloud: 0.5,
  },
};

const WEATHER_LOOK = {
  rain: { fog: 0x5a6c80, fogK: 1.35, sun: 0.48, amb: 1.05, fall: [34, 1.4, 0.06, 0.8], color: [0.7, 0.8, 1, 0.34], rate: 1, wet: 1 },
  sun: { fog: null, fogK: 0.7, sun: 1.55, amb: 1.1, fall: [0.4, 0, 0, 0], color: [0, 0, 0, 0], rate: 0, wet: 0 },
  sand: { fog: 0xc9975a, fogK: 2.1, sun: 0.7, amb: 1.0, fall: [-1, 16, 0.08, 0.16], color: [0.95, 0.72, 0.42, 0.55], rate: 1, wet: 0 },
};

// ---------- shared GLSL ----------
const V_COMMON = /* glsl */`
  uniform float uTime; uniform vec4 uQuake[4];
  float quakeAt(vec2 p){ float y=0.; for(int i=0;i<4;i++){ vec4 q=uQuake[i]; if(q.w<=0.) continue; float a=uTime-q.z; if(a<0.||a>3.5) continue;
    float f=length(p-q.xy)-a*24.; y+=sin(f*.85)*exp(-f*f*.018)*q.w*exp(-a*1.25);} return y; }`;
const F_COMMON = /* glsl */`
  uniform vec3 uSunDir,uSunColor,uAmbient,uFogColor; uniform float uFogDensity; uniform vec4 uLP[8]; uniform vec4 uLC[8];
  vec3 dyn(vec3 p,vec3 n){ vec3 s=vec3(0.); for(int i=0;i<8;i++){ if(uLC[i].a<=0.) continue; vec3 d=uLP[i].xyz-p; float r=uLP[i].w; float a=max(0.,1.-dot(d,d)/(r*r));
    s+=uLC[i].rgb*uLC[i].a*a*a*(.35+.65*max(dot(d/max(length(d),.001),n),0.)); } return s; }
  vec3 fogged(vec3 c,vec3 p){ float d=length(p-cameraPosition); float f=1.-exp(-pow(d*uFogDensity,1.5)); f*=mix(1.,.6,smoothstep(0.,50.,p.y)); return mix(c,uFogColor,f); }`;

const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
function vnoise(x, y) {
  const h = (i, j) => { const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453; return s - Math.floor(s); };
  const i = Math.floor(x), j = Math.floor(y), f = x - i, g = y - j;
  const u = f * f * (3 - 2 * f), v = g * g * (3 - 2 * g);
  return lerp(lerp(h(i, j), h(i + 1, j), u), lerp(h(i, j + 1), h(i + 1, j + 1), u), v);
}
const fbm = (x, y) => vnoise(x, y) * 0.5 + vnoise(x * 2.1, y * 2.1) * 0.3 + vnoise(x * 4.3, y * 4.3) * 0.2;
const seeded = (s) => () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };

function outside(x, z) {
  const dx = Math.max(Math.abs(x) - (FW / 2 + 3), 0), dz = Math.max(Math.abs(z) - (FH / 2 + 3), 0);
  return Math.hypot(dx, dz);
}

class Atlas {
  constructor() {
    this.canvas = document.createElement("canvas");
    this.canvas.width = this.canvas.height = 2048;
    this.ctx = this.canvas.getContext("2d");
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.magFilter = THREE.NearestFilter;
    this.tex.minFilter = THREE.LinearMipmapLinearFilter;
    this.tex.generateMipmaps = true;
    this.tex.anisotropy = 4;
    this.rects = new Map();
    this.loading = new Map();
    this.temp = document.createElement("canvas");
    this.temp.width = this.temp.height = 96;
    this.tempCtx = this.temp.getContext("2d", { willReadFrequently: true });
  }
  load(num) {
    if (this.loading.has(num)) return this.loading.get(num);
    const p = new Promise((done) => {
      const img = new Image();
      img.onload = () => { this.add(num, img); done(); };
      img.onerror = () => done();
      img.src = `${SPRITE_ROOT}${num}.png`;
    });
    this.loading.set(num, p);
    return p;
  }
  add(num, img) {
    const c = this.tempCtx;
    c.clearRect(0, 0, 96, 96);
    c.drawImage(img, 0, 0);
    const a = c.getImageData(0, 0, 96, 96).data;
    let x0 = 96, x1 = 0, y0 = 96, y1 = 0;
    for (let y = 0; y < 96; y++) for (let x = 0; x < 96; x++) if (a[(y * 96 + x) * 4 + 3] > 40) {
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (x1 < x0) { x0 = y0 = 0; x1 = y1 = 95; }
    const i = num - 1, col = i % 21, row = Math.floor(i / 21), px = col * 96, py = row * 96;
    this.ctx.clearRect(px, py, 96, 96);
    this.ctx.drawImage(img, px, py);
    this.rects.set(num, {
      u0: (px + x0) / 2048, u1: (px + x1 + 1) / 2048, v0: 1 - (py + y1 + 1) / 2048, v1: 1 - (py + y0) / 2048, aspect: (x1 - x0 + 1) / (y1 - y0 + 1),
    });
    this.dirty = true;
  }
  flush() { if (this.dirty) { this.tex.needsUpdate = true; this.dirty = false; } }
}

export class Stage {
  constructor(canvas, overlay, data, opts = {}) {
    this.canvas = canvas; this.overlay = overlay; this.octx = overlay.getContext("2d"); this.data = data;
    this.quality = TIERS[opts.quality] ? opts.quality : "high";
    this.tier = TIERS[this.quality];
    this.maxPixelRatio = opts.maxPixelRatio ?? 1.5;
    this.resScale = 1; this.grassFrac = 1; this.needResize = true;
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance", stencil: false });
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    renderer.toneMapping = THREE.NoToneMapping;
    this.renderer = renderer;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.3, 2400);
    this.camera.position.set(0, 34, 58);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.set(0, 0, -1);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.492;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 190;
    this.controls.screenSpacePanning = false;
    this.controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    this.controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
    this.controls.addEventListener("start", () => { if (this.camMode === "director") this.setCamMode("free"); });
    this.camMode = "director";
    this.onCamMode = () => {};
    this.atlas = new Atlas();
    this.clock = 0; this.real = 0; this.timeScale = 1; this.slowUntil = 0;
    this.queue = []; this.vis = []; this.world = null; this.floats = [];
    this.actionX = 0; this.actionZ = 0; this.actionWeight = 0;
    this.shot = null; this.lastCut = -99; this.shocks = []; this.lightPool = [];
    this.flash = 0; this.chroma = 0; this.follow = null; this.selected = null; this.previewZones = 0;
    this.onEvent = () => {};
    this.sfx = () => {};
    this.U = {
      uTime: { value: 0 }, uQuake: { value: Array.from({ length: 4 }, () => new THREE.Vector4()) }, uSunDir: { value: new THREE.Vector3() },
      uSunColor: { value: new THREE.Color() }, uAmbient: { value: new THREE.Color() }, uFogColor: { value: new THREE.Color() }, uFogDensity: { value: 0.004 },
      uLP: { value: Array.from({ length: 8 }, () => new THREE.Vector4()) }, uLC: { value: Array.from({ length: 8 }, () => new THREE.Vector4()) },
    };
    this.quakeSlot = 0;
    this.buildScars();
    this.buildUnitLayers();
    this.buildParticles();
    this.buildBeams();
    this.buildRings();
    this.buildWeather();
    this.buildZones();
    this.buildPost();
    this.look = { fog: new THREE.Color(), sun: 1, amb: 1, fogK: 1, wet: 0, rate: 0 };
    this.perf = { times: [], skipUntil: 3 };
    this.setBiome("meadow");
    this.applySize();
    addEventListener("resize", () => this.requestResize());
    this.renderer.getContext().canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); this.onContextLost?.(); });
  }

  // ---------- environment ----------
  buildScars() {
    const c = document.createElement("canvas");
    c.width = 512; c.height = Math.round((512 * FH) / FW);
    this.scarCtx = c.getContext("2d");
    this.scarCanvas = c;
    this.scarTex = new THREE.CanvasTexture(c);
    this.scarTex.magFilter = this.scarTex.minFilter = THREE.LinearFilter;
    this.scarTex.generateMipmaps = false;
    this.scarTex.flipY = false;
    this.clearScars();
  }
  clearScars() {
    const x = this.scarCtx;
    x.globalCompositeOperation = "source-over";
    x.fillStyle = "#000";
    x.fillRect(0, 0, this.scarCanvas.width, this.scarCanvas.height);
    x.globalCompositeOperation = "lighter";
    this.scarDirty = true;
  }
  scar(wx, wz, radius, channel, alpha) {
    const c = this.scarCanvas, x = this.scarCtx;
    const px = ((wx + FW / 2) / FW) * c.width, py = ((wz + FH / 2) / FH) * c.height, r = (radius / FW) * c.width;
    x.fillStyle = `rgba(${channel === 0 ? 255 : 0},${channel === 1 ? 255 : 0},${channel === 2 ? 255 : 0},${alpha})`;
    x.beginPath();
    x.arc(px, py, Math.max(r, 0.8), 0, 6.283);
    x.fill();
    this.scarDirty = true;
  }

  setBiome(id) {
    const b = BIOMES[id] ?? BIOMES.meadow;
    this.biomeId = id; this.biome = b;
    const sun = new THREE.Vector3(...b.sun).normalize();
    this.U.uSunDir.value.copy(sun);
    this.baseSun = new THREE.Color(...hex(b.sunColor, b.sunK));
    this.baseAmb = new THREE.Color(...hex(b.ambient, b.ambientK));
    this.baseFog = new THREE.Color(...hex(b.fog));
    this.U.uFogColor.value.copy(this.baseFog);
    this.look.fog.copy(this.baseFog);
    this.envCache ??= new Map();
    for (const m of this.envMeshes ?? []) m.visible = false;
    const cached = this.envCache.get(id);
    if (cached) {
      this.envMeshes = cached.meshes;
      this.grassMesh = cached.grass;
      this.grassCount = cached.count;
      for (const m of this.envMeshes) m.visible = true;
      this.grassMesh.geometry.instanceCount = Math.floor(this.grassCount * this.grassFrac);
    } else {
      this.envMeshes = [];
      this.buildSky(b); this.buildTerrain(b); this.buildGrass(b); this.buildProps(b); if (b.liquid) this.buildLiquid(b);
      this.envCache.set(id, { meshes: this.envMeshes, grass: this.grassMesh, count: this.grassCount });
    }
    if (this.perf) this.perf.skipUntil = (this.real ?? 0) + 2;
    const el = Math.max(sun.y, 0.08);
    const len = Math.min(1.35, 0.42 / el);
    this.uShadowDir.value.set((-sun.x / Math.hypot(sun.x, sun.z)) * len, (-sun.z / Math.hypot(sun.x, sun.z)) * len);
    this.airTimer = 0;
  }
  env(mesh) { this.scene.add(mesh); this.envMeshes.push(mesh); return mesh; }

  buildSky(b) {
    const mat = new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false,
      uniforms: { ...this.U, uTop: { value: new THREE.Color(...hex(b.top)) }, uHor: { value: new THREE.Color(...hex(b.horizon)) }, uStars: { value: b.stars ?? 0 }, uCloud: { value: b.cloud ?? 0 }, uDim: this.uSkyDim ?? (this.uSkyDim = { value: 1 }) },
      vertexShader: `varying vec3 vD; void main(){ vD=normalize(position); vec4 p=projectionMatrix*mat4(mat3(viewMatrix))*vec4(position,1.); gl_Position=p.xyww; }`,
      fragmentShader: /* glsl */`
        varying vec3 vD; uniform vec3 uTop,uHor,uSunDir,uSunColor,uFogColor; uniform float uStars,uCloud,uTime,uDim;
        float h(vec2 p){ return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
        float n(vec2 p){ vec2 i=floor(p),f=fract(p); f=f*f*(3.-2.*f); return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y); }
        float fbm(vec2 p){ float a=.5,s=0.; for(int i=0;i<5;i++){ s+=a*n(p); p=p*2.03+7.1; a*=.5; } return s; }
        void main(){ vec3 d=normalize(vD); float y=max(d.y,0.);
          vec3 c=mix(uHor,uTop,pow(smoothstep(0.,.62,y),.55));
          float s=max(dot(d,uSunDir),0.);
          c+=uSunColor*(pow(s,6.)*.16+pow(s,64.)*.5+smoothstep(.9985,.9993,s)*5.);
          if(uCloud>0.&&d.y>.005){ vec2 p=d.xz/(d.y+.12)*1.5; p.x+=uTime*.012; float f=fbm(p); float k=smoothstep(.48,.78,f)*uCloud*smoothstep(0.,.14,d.y);
            vec3 lit=mix(uHor*1.1,uSunColor*.55,pow(s,3.)); c=mix(c,lit,k*.78); }
          if(uStars>0.&&d.y>0.){ vec2 g=floor(d.xz/(d.y+.3)*260.); float st=step(.9975,h(g)); c+=st*uStars*vec3(.9,.95,1.)*(1.+.6*sin(uTime*2.+h(g+3.)*40.)); }
          c=mix(c,uFogColor,(1.-smoothstep(0.,.18,d.y))*.86);
          gl_FragColor=vec4(c*uDim,1.); }`,
    });
    const m = this.env(new THREE.Mesh(new THREE.SphereGeometry(1000, 32, 20), mat));
    m.frustumCulled = false; m.renderOrder = -10;
  }

  heightOf(b, x, z) {
    const d = outside(x, z);
    if (d <= 0) return 0; // the field itself is flat
    return (fbm(x * 0.011 + 3, z * 0.011) * 1.4 - 0.2) * b.hill * smooth(0, 90, d) ** 1.4 + (fbm(x * 0.06, z * 0.06) - 0.5) * b.bump * smooth(0, 16, d);
  }

  buildTerrain(b) {
    const geo = new THREE.PlaneGeometry(1300, 1300, 260, 260);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) pos.setY(i, this.heightOf(b, pos.getX(i), pos.getZ(i)));
    const mat = new THREE.ShaderMaterial({
      uniforms: { ...this.U, uA: { value: new THREE.Color(...hex(b.soil[0])) }, uB: { value: new THREE.Color(...hex(b.soil[1])) }, uTip: { value: new THREE.Color(...hex(b.tip)) }, uScars: { value: this.scarTex }, uField: { value: new THREE.Vector2(FW, FH) }, uHill: { value: b.hill } },
      vertexShader: `${V_COMMON} varying vec3 vP; void main(){ vec3 p=position; p.y+=quakeAt(p.xz); vP=p; gl_Position=projectionMatrix*viewMatrix*vec4(p,1.); }`,
      fragmentShader: /* glsl */`
        ${F_COMMON}
        varying vec3 vP; uniform vec3 uA,uB,uTip; uniform sampler2D uScars; uniform vec2 uField; uniform float uHill;
        float h(vec2 p){ return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
        float n(vec2 p){ vec2 i=floor(p),f=fract(p); f=f*f*(3.-2.*f); return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y); }
        void main(){ vec2 p=vP.xz; float a=n(p*.35)*.55+n(p*1.3)*.3+n(p*5.)*.15;
          vec3 c=mix(uA,uB,a); float far=smoothstep(40.,190.,length(p)); c=mix(c,mix(uA,uTip,.5+.4*a),far*(uHill>0.?.85:.2));
          c=mix(c,uA*.7+vec3(.14),smoothstep(30.,uHill>0.?uHill*2.2:60.,vP.y)*.6);
          vec2 uv=p/uField+.5; if(uv.x>0.&&uv.x<1.&&uv.y>0.&&uv.y<1.){ vec4 s=texture2D(uScars,uv); c=mix(c,c*.55+vec3(.04,.03,.02),clamp(s.r*.7,0.,.7)); c=mix(c,vec3(.018,.015,.014),clamp(s.g,0.,.92)); c*=1.-clamp(s.b,0.,1.)*.32; }
          vec3 nrm=cross(dFdx(vP),dFdy(vP)); nrm=nrm/max(length(nrm),1e-7); if(nrm.y<0.) nrm=-nrm;
          vec3 l=uAmbient*(.55+.45*nrm.y)+uSunColor*max(dot(nrm,uSunDir),0.)*.82+dyn(vP,nrm)*1.2;
          gl_FragColor=vec4(fogged(c*l,vP),1.); }`,
    });
    const m = this.env(new THREE.Mesh(geo, mat));
    m.frustumCulled = false;
  }

  buildGrass(b) {
    const count = Math.floor(this.tier.grass * b.density);
    const blade = new THREE.BufferGeometry();
    // tapered strip: x is -1..1 across the blade, y is 0..1 along it
    blade.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, 0, 0, 1, 0, 0, -0.8, 0.33, 0, 0.8, 0.33, 0, -0.5, 0.66, 0, 0.5, 0.66, 0, 0, 1, 0]), 3));
    blade.setIndex([0, 1, 2, 2, 1, 3, 2, 3, 4, 4, 3, 5, 4, 5, 6]);
    const geo = new THREE.InstancedBufferGeometry();
    geo.setAttribute("position", blade.attributes.position);
    geo.setIndex(blade.index);
    const aBase = new Float32Array(count * 4), aShape = new Float32Array(count * 4);
    const r = seeded(4242);
    for (let i = 0; i < count; i++) {
      // 3 in 4 blades live on the field; the rest thin out toward the horizon
      let x, z;
      if (r() < 0.76) {
        // feather the edge of the field so the meadow never reads as a carpet
        do { x = (r() - 0.5) * (FW + 44); z = (r() - 0.5) * (FH + 44); } while (r() < smooth(0, 22, outside(x, z)));
      } else { const a = r() * 6.283, d = 18 + r() ** 1.6 * 150; x = Math.cos(a) * d * 1.35; z = Math.sin(a) * d; }
      const patch = vnoise(x * 0.09 + 9, z * 0.09) * 0.72 + 0.14;
      aBase[i * 4] = x; aBase[i * 4 + 1] = z; aBase[i * 4 + 2] = this.heightOf(b, x, z);
      aBase[i * 4 + 3] = lerp(b.grassH[0], b.grassH[1], smooth(0.3, 0.72, patch)) * (0.72 + r() * 0.5);
      aShape[i * 4] = r() * 6.283; aShape[i * 4 + 1] = r() * 0.35 + 0.1; aShape[i * 4 + 2] = r(); aShape[i * 4 + 3] = 0.012 + r() * 0.016;
    }
    geo.setAttribute("aBase", new THREE.InstancedBufferAttribute(aBase, 4));
    geo.setAttribute("aShape", new THREE.InstancedBufferAttribute(aShape, 4));
    geo.instanceCount = count;
    const mat = new THREE.ShaderMaterial({
      side: THREE.DoubleSide,
      uniforms: { ...this.U, uRoot: { value: new THREE.Color(...hex(b.root)) }, uTip: { value: new THREE.Color(...hex(b.tip)) }, uScars: { value: this.scarTex }, uField: { value: new THREE.Vector2(FW, FH) }, uWind: { value: b.wind } },
      vertexShader: /* glsl */`
        ${V_COMMON}
        attribute vec4 aBase; attribute vec4 aShape; uniform sampler2D uScars; uniform vec2 uField; uniform float uWind;
        varying float vT; varying float vTint; varying vec3 vP; varying vec3 vS;
        void main(){ float t=position.y; vec2 xz=aBase.xy; float h=aBase.w;
          vec2 uv=xz/uField+.5; vec4 s=vec4(0.); if(uv.x>0.&&uv.x<1.&&uv.y>0.&&uv.y<1.) s=texture2D(uScars,uv);
          float tr=clamp(s.r,0.,1.); float burn=clamp(s.g*1.6,0.,1.);
          h*=1.-burn*.86;
          float gust=sin(xz.x*.13+xz.y*.09+uTime*.9)*.5+.5; gust*=gust;
          float sway=(sin(uTime*2.1+xz.x*.9+xz.y*.7+aShape.z*6.)*.16+gust*.72)*uWind;
          vec2 wdir=normalize(vec2(1.,.35));
          float ca=cos(aShape.x),sa=sin(aShape.x);
          vec2 lean=vec2(ca,sa)*(aShape.y+tr*1.15)+wdir*sway*(1.-tr*.7);
          float t2=t*t; vec3 p=vec3(xz.x,aBase.z+quakeAt(xz),xz.y);
          p.xz+=lean*t2*h*.92;
          p.y+=h*t*(1.-.62*min(dot(lean,lean)*.75*t,.92));
          vec2 perp=vec2(-sa,ca);
          p.xz+=perp*position.x*aShape.w;
          vP=p; vT=t; vTint=aShape.z; vS=s.rgb; gl_Position=projectionMatrix*viewMatrix*vec4(p,1.); }`,
      fragmentShader: /* glsl */`
        ${F_COMMON}
        uniform vec3 uRoot,uTip; varying float vT; varying float vTint; varying vec3 vP; varying vec3 vS;
        void main(){ vec3 c=mix(uRoot,uTip,pow(max(vT,0.),1.25)); c*=.78+vTint*.44; c=mix(c,vec3(.62,.54,.28)*(uTip.r+uTip.g),smoothstep(.72,1.,vTint)*.28);
          float burn=clamp(vS.g*1.6,0.,1.); c=mix(c,vec3(.016,.014,.012)+vec3(.35,.08,.0)*step(.97,fract(vP.x*9.+vP.z*7.))*burn,burn);
          c*=1.-clamp(vS.b,0.,1.)*.25;
          float sunSide=max(uSunDir.y,0.)*.55+.34;
          vec3 l=uAmbient*(.62+.38*vT)+uSunColor*sunSide*(.46+.62*vT)+dyn(vP,vec3(0.,1.,0.))*1.1;
          gl_FragColor=vec4(fogged(c*l,vP),1.); }`,
    });
    const m = this.env(new THREE.Mesh(geo, mat));
    m.frustumCulled = false;
    geo.instanceCount = Math.floor(count * this.grassFrac);
    this.grassMesh = m;
    this.grassCount = count;
  }

  buildProps(b) {
    const propMat = (color, emissive = 0, sway = 0) => new THREE.ShaderMaterial({
      uniforms: { ...this.U, uC: { value: new THREE.Color(...hex(color)) }, uE: { value: emissive }, uSway: { value: sway } },
      vertexShader: /* glsl */`${V_COMMON} varying vec3 vP; varying vec3 vC; uniform float uSway;
        void main(){ vec4 w=instanceMatrix*vec4(position,1.); vec2 base=instanceMatrix[3].xz; float k=position.y; w.x+=sin(uTime*.9+base.x)*uSway*k*k; w.y+=quakeAt(base)*.7; vP=w.xyz; vC=instanceColor; gl_Position=projectionMatrix*viewMatrix*w; }`,
      fragmentShader: /* glsl */`${F_COMMON} varying vec3 vP; varying vec3 vC; uniform vec3 uC; uniform float uE;
        void main(){ vec3 n=cross(dFdx(vP),dFdy(vP)); n=n/max(length(n),1e-7); vec3 c=uC*vC; vec3 l=uAmbient*(.55+.45*max(n.y,0.))+uSunColor*max(dot(n,uSunDir),0.)*.92+dyn(vP,n);
          gl_FragColor=vec4(fogged(c*l+c*uE,vP),1.); }`,
    });
    const place = (geo, mat, n, minD, maxD, sMin, sMax, yMul = 1, tint = 0.25, seed = 77) => {
      const r = seeded(seed);
      const mesh = new THREE.InstancedMesh(geo, mat, n);
      const mtx = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), col = new THREE.Color();
      for (let i = 0; i < n; i++) {
        let x, z, tries = 0;
        do {
          const a = r() * 6.283, d = minD + r() ** 1.4 * (maxD - minD);
          x = Math.cos(a) * d * 1.45; z = Math.sin(a) * d;
          tries++;
        } while (outside(x, z) < 3 && tries < 12);
        // nothing stands on the field, and the camera's usual side stays open
        if (z > FH / 2 + 2 && Math.abs(x) < 60 && z < 120) z = -z;
        const s = lerp(sMin, sMax, r());
        e.set(0, r() * 6.283, 0); q.setFromEuler(e);
        mtx.compose(new THREE.Vector3(x, this.heightOf(b, x, z) - 0.2, z), q, new THREE.Vector3(s, s * yMul * lerp(0.85, 1.25, r()), s));
        mesh.setMatrixAt(i, mtx);
        const v = 1 - tint / 2 + r() * tint;
        mesh.setColorAt(i, col.setRGB(v, v * lerp(0.92, 1.06, r()), v));
      }
      mesh.frustumCulled = false;
      this.env(mesh);
    };
    const n = { low: 0.4, medium: 0.7, high: 1 }[this.quality] ?? 1;
    if (b.props === "pines") {
      const cone = new THREE.ConeGeometry(0.42, 1, 7); cone.translate(0, 0.62, 0);
      const cone2 = new THREE.ConeGeometry(0.32, 0.72, 7); cone2.translate(0, 0.96, 0);
      const trunk = new THREE.CylinderGeometry(0.05, 0.09, 0.5, 5); trunk.translate(0, 0.2, 0);
      place(cone, propMat(0x1d4a2c, 0, 0.12), 900 * n | 0, 34, 190, 12, 26, 1.1);
      place(cone2, propMat(0x2a6438, 0, 0.12), 900 * n | 0, 34, 190, 12, 26, 1.1);
      place(trunk, propMat(0x3a2a1c, 0, 0.12), 900 * n | 0, 34, 190, 12, 26, 1.1);
    } else if (b.props === "oaks") {
      const crown = new THREE.IcosahedronGeometry(0.5, 1); crown.translate(0, 0.95, 0);
      const trunk = new THREE.CylinderGeometry(0.05, 0.08, 0.7, 5); trunk.translate(0, 0.35, 0);
      place(crown, propMat(this.biomeId === "dusk" ? 0x3a3560 : 0x4a6b2a, 0, 0.05), 150 * n | 0, 62, 300, 7, 15);
      place(trunk, propMat(0x3a2a20, 0, 0.05), 150 * n | 0, 62, 300, 7, 15);
    } else if (b.props === "crystals" || b.props === "spires" || b.props === "rocks") {
      const rock = new THREE.IcosahedronGeometry(1, 2);
      const spike = new THREE.ConeGeometry(0.42, 1.8, 5); spike.translate(0, 0.8, 0);
      const stone = this.biomeId === "ember" ? 0x2a2020 : this.biomeId === "shore" ? 0x7f745f : this.biomeId === "snow" ? 0xbdd0e0 : 0x3a3a55;
      place(rock, propMat(stone), 260 * n | 0, 46, 300, 2, 11, 0.62, 0.25, 31);
      if (b.props === "crystals") place(spike, propMat(0x4f86e8, 0.9), 150 * n | 0, 34, 150, 0.7, 3.4, 1, 0.25, 91);
      if (b.props === "spires") place(spike, propMat(0xd0e6f5, 0.18), 170 * n | 0, 40, 220, 2.5, 11, 1, 0.25, 92);
      if (this.biomeId === "ember") place(spike, propMat(0x241a1a), 150 * n | 0, 40, 240, 2.5, 10, 1, 0.25, 93);
    }
  }

  buildLiquid(b) {
    const geo = new THREE.PlaneGeometry(2600, 2600, 1, 1);
    geo.rotateX(-Math.PI / 2);
    const lava = b.liquid === 2;
    const mat = new THREE.ShaderMaterial({
      uniforms: { ...this.U, uDeep: { value: new THREE.Color(...hex(lava ? 0x4a0d04 : 0x0f4f70)) }, uShallow: { value: new THREE.Color(...hex(lava ? 0xff6a12 : 0x42b5b8)) }, uLava: { value: lava ? 1 : 0 } },
      vertexShader: `varying vec3 vP; void main(){ vP=position; gl_Position=projectionMatrix*viewMatrix*vec4(position,1.); }`,
      fragmentShader: /* glsl */`
        ${F_COMMON}
        varying vec3 vP; uniform vec3 uDeep,uShallow; uniform float uLava,uTime;
        float h(vec2 p){ return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
        float n(vec2 p){ vec2 i=floor(p),f=fract(p); f=f*f*(3.-2.*f); return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y); }
        void main(){ vec2 p=vP.xz; float t=uTime;
          float w=n(p*.09+t*.13)+n(p*.27-t*.2)*.5+n(p*.8+t*.3)*.25;
          if(uLava>.5){ float k=smoothstep(.72,1.25,w); vec3 c=mix(uDeep*.3,uShallow*3.2,k); gl_FragColor=vec4(fogged(c,vP),1.); return; }
          vec3 nrm=normalize(vec3((n(p*.5+t*.25)-.5)*.5,1.,(n(p*.5+7.-t*.2)-.5)*.5));
          vec3 v=normalize(cameraPosition-vP); float fr=pow(1.-max(dot(nrm,v),0.),3.2);
          vec3 c=mix(uShallow,uDeep,smoothstep(.5,1.3,w))*(uAmbient+uSunColor*.4);
          c=mix(c,uFogColor*1.15,fr);
          vec3 hv=normalize(uSunDir+v); c+=uSunColor*pow(max(dot(nrm,hv),0.),160.)*1.6;
          gl_FragColor=vec4(fogged(c,vP),1.); }`,
    });
    const m = this.env(new THREE.Mesh(geo, mat));
    m.position.y = 0;
    m.frustumCulled = false;
    m.position.set(0, -1.1, 0);
    // the shader reads the plane's local position; lift it through the geometry instead so vP stays world-space
    geo.translate(0, -1.1, 0);
    m.position.set(0, 0, 0);
  }

  // ---------- units ----------
  buildUnitLayers() {
    const quad = new THREE.BufferGeometry();
    quad.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-0.5, 0, 0, 0.5, 0, 0, 0.5, 1, 0, -0.5, 1, 0]), 3));
    quad.setIndex([0, 1, 2, 0, 2, 3]);
    this.aPos = new THREE.InstancedBufferAttribute(new Float32Array(MAX_UNITS * 3), 3).setUsage(THREE.DynamicDrawUsage);
    this.aSize = new THREE.InstancedBufferAttribute(new Float32Array(MAX_UNITS * 2), 2).setUsage(THREE.DynamicDrawUsage);
    this.aUV = new THREE.InstancedBufferAttribute(new Float32Array(MAX_UNITS * 4), 4).setUsage(THREE.DynamicDrawUsage);
    this.aFx = new THREE.InstancedBufferAttribute(new Float32Array(MAX_UNITS * 4), 4).setUsage(THREE.DynamicDrawUsage);
    this.aSide = new THREE.InstancedBufferAttribute(new Float32Array(MAX_UNITS * 4), 4).setUsage(THREE.DynamicDrawUsage);
    const layer = (mat, order) => {
      const g = new THREE.InstancedBufferGeometry();
      g.setAttribute("position", quad.attributes.position);
      g.setIndex(quad.index);
      for (const [k, a] of [["iPos", this.aPos], ["iSize", this.aSize], ["iUV", this.aUV], ["iFx", this.aFx], ["iSide", this.aSide]]) g.setAttribute(k, a);
      g.instanceCount = 0;
      const m = new THREE.Mesh(g, mat);
      m.frustumCulled = false; m.renderOrder = order;
      this.scene.add(m);
      return m;
    };
    const uAtlas = { value: this.atlas.tex };
    this.uShadowDir = { value: new THREE.Vector2(1, 0.4) };
    this.uSideA = { value: new THREE.Color(0.5, 0.65, 0.77) };
    this.uSideB = { value: new THREE.Color(0.71, 0.3, 0.3) };
    this.shadowMesh = layer(new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, depthTest: false,
      uniforms: { ...this.U, uAtlas, uShadowDir: this.uShadowDir, uStrength: this.uShadowK = { value: 0.34 } },
      vertexShader: /* glsl */`${V_COMMON} attribute vec3 iPos; attribute vec2 iSize; attribute vec4 iUV; attribute vec4 iFx; uniform vec2 uShadowDir; varying vec2 vUv; varying float vD;
        void main(){ vec2 d=uShadowDir; vec2 perp=normalize(vec2(-d.y,d.x)); vec2 base=iPos.xz;
          vec2 xz=base+perp*position.x*iSize.x+d*position.y*iSize.y;
          float u=position.x+.5; vUv=vec2(mix(iUV.x,iUV.z,iFx.w>.5?1.-u:u),mix(iUV.y,iUV.w,position.y)); vD=iFx.y;
          gl_Position=projectionMatrix*viewMatrix*vec4(xz.x,.02+quakeAt(xz),xz.y,1.); }`,
      fragmentShader: /* glsl */`uniform sampler2D uAtlas; uniform float uStrength; varying vec2 vUv; varying float vD; void main(){ float a=texture2D(uAtlas,vUv).a; if(a<.3) discard; gl_FragColor=vec4(0.,0.,.02,uStrength*(1.-vD)); }`,
    }), 1);
    this.ringMesh = layer(new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, depthTest: false,
      uniforms: { ...this.U, uA: this.uSideA, uB: this.uSideB, uGlow: this.uRingK = { value: 0.8 } },
      vertexShader: /* glsl */`${V_COMMON} attribute vec3 iPos; attribute vec2 iSize; attribute vec4 iSide; attribute vec4 iFx; varying vec2 vL; varying vec4 vS; varying float vD;
        void main(){ float r=iSide.y; vec2 xz=iPos.xz+vec2(position.x*2.,(position.y-.5)*2.)*r; vL=vec2(position.x*2.,(position.y-.5)*2.); vS=iSide; vD=iFx.y; gl_Position=projectionMatrix*viewMatrix*vec4(xz.x,.03+quakeAt(xz),xz.y,1.); }`,
      fragmentShader: /* glsl */`uniform vec3 uA,uB; uniform float uGlow; varying vec2 vL; varying vec4 vS; varying float vD;
        void main(){ float d=length(vL); if(d>1.) discard; float k=(1.-smoothstep(.82,1.,d))*smoothstep(.4,.82,d)+.14*(1.-smoothstep(.2,1.,d)); vec3 c=mix(uA,uB,vS.x); if(vS.w>.5) c=vec3(.45,1.,.62); else if(vS.w<-.5) c=vec3(.86,.38,1.);
          float sel=vS.z; gl_FragColor=vec4(c*(1.+sel*2.5),min(1.,k*(uGlow+sel*2.))*(1.-vD)); }`,
    }), 1.5);
    this.spriteMesh = layer(new THREE.ShaderMaterial({
      transparent: true, depthWrite: true, depthTest: true, blending: THREE.NoBlending,
      uniforms: { ...this.U, uAtlas },
      vertexShader: /* glsl */`${V_COMMON} attribute vec3 iPos; attribute vec2 iSize; attribute vec4 iUV; attribute vec4 iFx; attribute vec4 iSide; varying vec2 vUv; varying vec4 vFx; varying vec3 vP; varying float vSide;
        void main(){ vec3 right=normalize(vec3(viewMatrix[0][0],0.,viewMatrix[2][0])); vec3 away=vec3(-right.z,0.,right.x);
          float tilt=iFx.z; vec3 up=vec3(0.,cos(tilt),0.)+away*sin(tilt);
          vec3 p=iPos+right*position.x*iSize.x+up*position.y*iSize.y; p.y+=quakeAt(iPos.xz);
          float u=position.x+.5; vUv=vec2(mix(iUV.x,iUV.z,iFx.w>.5?1.-u:u),mix(iUV.y,iUV.w,position.y)); vFx=iFx; vP=p; vSide=iSide.x;
          gl_Position=projectionMatrix*viewMatrix*vec4(p,1.); }`,
      fragmentShader: /* glsl */`${F_COMMON} uniform sampler2D uAtlas; varying vec2 vUv; varying vec4 vFx; varying vec3 vP; varying float vSide;
        void main(){ vec4 t=texture2D(uAtlas,vUv); if(t.a<.36) discard;
          float dis=vFx.y; if(dis>0.&&fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453)<dis) discard;
          vec3 c=pow(t.rgb,vec3(2.2)); vec3 n=normalize(vec3(uSunDir.x*.3,.55,.65));
          vec3 l=mix(vec3(1.),uAmbient*.95+uSunColor*(.3+.36*max(dot(n,uSunDir),0.)),.62);
          c=c*l+c*dyn(vP,n)*1.1; c=mix(c,vec3(1.5,1.45,1.38),clamp(vFx.x,0.,1.)*.72);
          gl_FragColor=vec4(mix(c,fogged(c,vP),.55),1.); }`,
    }), 2);
  }

  async setWorld(world, { preview = false } = {}) {
    this.world = world;
    this.queue.length = 0; this.floats.length = 0; this.shot = null; this.selected = null; this.follow = null;
    this.previewZones = preview ? 1 : 0;
    this.zoneMesh.forEach((m) => { m.visible = preview; });
    const nums = new Set();
    for (const u of world.units) {
      let n = u.num;
      while (n && !nums.has(n)) { nums.add(n); n = this.data.species[n].evoTo; }
    }
    this.vis = world.units.map((u) => this.makeVis(u));
    await Promise.all([...nums].map((n) => this.atlas.load(n)));
    this.atlas.flush();
  }

  makeVis(u) {
    const [x, z] = this.tileToWorld(u.x, u.y, u.size);
    const sp = this.data.species[u.tf?.num ?? u.num];
    return {
      id: u.id, side: u.side, num: u.tf?.num ?? u.num, size: u.size, x, z, fx: x, fz: z, tx: x, tz: z, t0: 0, t1: 0,
      h: Math.max(sp.heightm, MIN_BODY), fly: sp.types.includes("flying") || u.ability === "levitate", dir: u.side === 0 ? 1 : -1, dz: 0,
      phase: (u.id * 2.399) % 6.283, alive: true, hp: u.hp, maxHp: u.maxHp, flash: 0, dissolve: 0, tilt: 0, lunge: 0, lungeT: 0, lx: 0, lz: 0, knock: 0, kx: 0, kz: 0, shake: 0,
      status: u.status, evo: null, gone: false, scale: 1,
    };
  }

  syncHp(world) {
    for (const u of world.units) { const v = this.vis[u.id]; if (v) { v.hp = Math.max(0, u.hp); v.maxHp = u.maxHp; v.status = u.status; } }
  }

  tileToWorld(x, y, size) { return [(x + size / 2 - W / 2) * TILE_M, (y + size / 2 - H / 2) * TILE_M]; }
  groundTile(cx, cy) {
    const r = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.camera);
    const t = -ray.ray.origin.y / ray.ray.direction.y;
    if (!(t > 0)) return null;
    const p = ray.ray.origin.clone().addScaledVector(ray.ray.direction, t);
    return { x: Math.round(p.x / TILE_M + W / 2), y: Math.round(p.z / TILE_M + H / 2) };
  }
  pick(cx, cy) {
    const r = this.canvas.getBoundingClientRect(), v = new THREE.Vector3();
    let best = null, bd = 34;
    for (const u of this.vis) {
      if (!u.alive) continue;
      v.set(u.x, u.h / 2 + (u.fly ? 0.5 : 0), u.z).project(this.camera);
      if (v.z > 1) continue;
      const sx = ((v.x + 1) / 2) * r.width + r.left, sy = ((1 - v.y) / 2) * r.height + r.top;
      const d = Math.hypot(sx - cx, sy - cy);
      if (d < bd) { bd = d; best = u; }
    }
    return best;
  }

  writeUnits() {
    let n = 0;
    const pos = this.aPos.array, size = this.aSize.array, uv = this.aUV.array, fx = this.aFx.array, si = this.aSide.array;
    const t = this.clock, cam = this.camera;
    const rx = cam.matrixWorld.elements[0], rz = cam.matrixWorld.elements[2];
    for (const v of this.vis) {
      if (v.gone) continue;
      if (n >= MAX_UNITS) break;
      const rect = this.atlas.rects.get(v.num);
      if (!rect) continue;
      const moving = t < v.t1;
      if (moving) {
        const p = Math.min(1, (t - v.t0) / (v.t1 - v.t0 || 1));
        v.x = lerp(v.fx, v.tx, p); v.z = lerp(v.fz, v.tz, p);
      } else { v.x = v.tx; v.z = v.tz; }
      const hopK = moving && !v.fly ? Math.abs(Math.sin(t * (6 + (v.id % 5)) + v.phase)) : 0;
      const hop = hopK * Math.min(0.28, 0.1 + v.h * 0.1);
      let ox = 0, oz = 0;
      if (v.lunge > 0) {
        const p = (t - v.lungeT) / v.lunge;
        if (p >= 1) v.lunge = 0; else { const s = Math.sin(p * Math.PI); ox += v.lx * s; oz += v.lz * s; }
      }
      if (v.knock > 0) {
        const p = (t - v.knockT) / v.knock;
        if (p >= 1) v.knock = 0; else { const s = 1 - p; ox += v.kx * s * s; oz += v.kz * s * s; }
      }
      if (v.flash > 0) v.flash = Math.max(0, v.flash - this.dtScaled * 9);
      if (v.evo) this.updateEvolution(v);
      if (!v.alive) {
        v.dissolve = Math.min(1, v.dissolve + this.dtScaled * 1.25);
        v.tilt = Math.min(1.42, v.tilt + this.dtScaled * 6);
        if (v.dissolve >= 1) { v.gone = true; continue; }
      }
      const bob = v.fly ? 0.55 + v.h * 0.32 + Math.sin(t * 2.1 + v.phase) * 0.12 : 0;
      const breathe = 1 + Math.sin(t * 2.2 + v.phase) * 0.018;
      const hh = v.h * v.scale;
      const w = hh * rect.aspect, hS = hh * breathe * (1 + hopK * 0.05);
      const i2 = n * 2, i3 = n * 3, i4 = n * 4;
      pos[i3] = v.x + ox; pos[i3 + 1] = bob + hop; pos[i3 + 2] = v.z + oz;
      size[i2] = w; size[i2 + 1] = hS;
      uv[i4] = rect.u0; uv[i4 + 1] = rect.v0; uv[i4 + 2] = rect.u1; uv[i4 + 3] = rect.v1;
      // sprites face left in the art: flip when the unit's heading points screen-right
      const flip = v.dir * rx + v.dz * rz > 0;
      fx[i4] = v.flash; fx[i4 + 1] = v.dissolve; fx[i4 + 2] = v.tilt; fx[i4 + 3] = flip ? 1 : 0;
      const unit = this.world.units[v.id];
      let mood = 0;
      if (unit) for (const x of unit.st) mood += x;
      si[i4] = v.side; si[i4 + 1] = Math.max(0.42, v.size * TILE_M * 0.66); si[i4 + 2] = this.selected === v ? 1 : unit?.lure > 0 ? 0.55 : 0; si[i4 + 3] = mood > 0 ? 1 : mood < 0 ? -1 : 0;
      n++;
    }
    for (const a of [this.aPos, this.aSize, this.aUV, this.aFx, this.aSide]) {
      a.clearUpdateRanges();
      if (n) a.addUpdateRange(0, n * a.itemSize);
      a.needsUpdate = n > 0;
    }
    this.spriteMesh.geometry.instanceCount = this.shadowMesh.geometry.instanceCount = this.ringMesh.geometry.instanceCount = n;
    this.drawn = n;
  }

  // ---------- effects ----------
  buildParticles() {
    const quad = new THREE.PlaneGeometry(1, 1);
    const g = new THREE.InstancedBufferGeometry();
    g.setAttribute("position", quad.attributes.position);
    g.setIndex(quad.index);
    this.pP = new THREE.InstancedBufferAttribute(new Float32Array(MAX_PARTICLES * 4), 4).setUsage(THREE.DynamicDrawUsage); // xyz + t0
    this.pV = new THREE.InstancedBufferAttribute(new Float32Array(MAX_PARTICLES * 4), 4).setUsage(THREE.DynamicDrawUsage); // velocity + life
    this.pC = new THREE.InstancedBufferAttribute(new Float32Array(MAX_PARTICLES * 4), 4).setUsage(THREE.DynamicDrawUsage); // rgb + add
    this.pS = new THREE.InstancedBufferAttribute(new Float32Array(MAX_PARTICLES * 3), 3).setUsage(THREE.DynamicDrawUsage); // size0, size1, gravity
    g.setAttribute("aP", this.pP); g.setAttribute("aV", this.pV); g.setAttribute("aC", this.pC); g.setAttribute("aS", this.pS);
    this.pMax = this.tier.particles;
    g.instanceCount = this.pMax;
    this.particleGeo = g;
    this.pHead = 0; this.pNew = 0;
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false,
      blending: THREE.CustomBlending, blendEquation: THREE.AddEquation, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
      uniforms: { uTime: this.U.uTime },
      vertexShader: /* glsl */`uniform float uTime; attribute vec4 aP,aV,aC; attribute vec3 aS; varying vec2 vL; varying vec4 vC; varying float vA;
        void main(){ float a=uTime-aP.w; float k=a/aV.w; if(k<0.||k>1.||aV.w<=0.){ gl_Position=vec4(2.,2.,2.,1.); return; }
          vec3 p=aP.xyz+aV.xyz*a; p.y-=aS.z*a*a*.5; if(p.y<0.&&aS.z>0.) p.y=0.;
          float s=mix(aS.x,aS.y,k); vec4 mv=viewMatrix*vec4(p,1.); mv.xy+=position.xy*s; vL=position.xy*2.; vC=aC; vA=(1.-k)*(1.-k*k);
          gl_Position=projectionMatrix*mv; }`,
      fragmentShader: /* glsl */`varying vec2 vL; varying vec4 vC; varying float vA;
        void main(){ float d=length(vL); if(d>1.) discard; float a=pow(max(1.-d,0.),1.6)*vA; gl_FragColor=vec4(vC.rgb*a,a*(1.-vC.a)); }`,
    });
    const m = new THREE.Mesh(g, mat);
    m.frustumCulled = false; m.renderOrder = 4;
    this.scene.add(m);
  }
  particle(x, y, z, vx, vy, vz, life, s0, s1, grav, r, g, b, add = 1) {
    const i = this.pHead; this.pHead = (i + 1) % this.pMax; this.pNew++;
    const P = this.pP.array, V = this.pV.array, C = this.pC.array, S = this.pS.array;
    P[i * 4] = x; P[i * 4 + 1] = y; P[i * 4 + 2] = z; P[i * 4 + 3] = this.clock;
    V[i * 4] = vx; V[i * 4 + 1] = vy; V[i * 4 + 2] = vz; V[i * 4 + 3] = life;
    C[i * 4] = r; C[i * 4 + 1] = g; C[i * 4 + 2] = b; C[i * 4 + 3] = add;
    S[i * 3] = s0; S[i * 3 + 1] = s1; S[i * 3 + 2] = grav;
  }
  flushParticles() {
    const n = this.pNew, max = this.pMax;
    if (!n) return;
    this.pNew = 0;
    const end = this.pHead, start = (end - (n % max) + max) % max;
    for (const a of [this.pP, this.pV, this.pC, this.pS]) {
      const size = a.itemSize;
      a.clearUpdateRanges();
      if (n < max) {
        if (start < end) a.addUpdateRange(start * size, n * size);
        else { a.addUpdateRange(start * size, (max - start) * size); a.addUpdateRange(0, end * size); }
      }
      a.needsUpdate = true;
    }
  }
  burst(x, y, z, n, c, { speed = 3, up = 1, life = 0.6, size = 0.18, end = 0.02, grav = 4, add = 1, jitter = 0 } = {}) {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * 6.283, s = speed * (0.35 + Math.random() * 0.75), e = Math.random() * 0.9 + 0.1;
      this.particle(x + (Math.random() - 0.5) * jitter, y + (Math.random() - 0.5) * jitter, z + (Math.random() - 0.5) * jitter,
        Math.cos(a) * s, up * s * e, Math.sin(a) * s, life * (0.55 + Math.random() * 0.7), size, end, grav, c[0], c[1], c[2], add);
    }
  }

  buildBeams() {
    const seg = 18;
    const pos = [], idx = [];
    for (let i = 0; i <= seg; i++) { pos.push(i / seg, -1, 0, i / seg, 1, 0); if (i < seg) { const a = i * 2; idx.push(a, a + 1, a + 2, a + 2, a + 1, a + 3); } }
    const g = new THREE.InstancedBufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setIndex(idx);
    const a = (n) => new THREE.InstancedBufferAttribute(new Float32Array(MAX_BEAMS * n), n).setUsage(THREE.DynamicDrawUsage);
    this.bA = a(4); this.bB = a(4); this.bC = a(4); // from + t0, to + life, rgb + width
    this.bS = a(2); // style (0 bolt, 1 beam, 2 lightning), travel fraction of life
    g.setAttribute("aFrom", this.bA); g.setAttribute("aTo", this.bB); g.setAttribute("aCol", this.bC); g.setAttribute("aStyle", this.bS);
    g.instanceCount = MAX_BEAMS;
    this.bHead = 0;
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      uniforms: { uTime: this.U.uTime },
      vertexShader: /* glsl */`uniform float uTime; attribute vec4 aFrom,aTo,aCol; attribute vec2 aStyle; varying vec2 vL; varying vec3 vC; varying float vK; varying float vS; varying float vHead;
        float h(float x){ return fract(sin(x*91.17)*43758.5453); }
        void main(){ float age=uTime-aFrom.w; float k=age/aTo.w; if(k<0.||k>1.||aTo.w<=0.){ gl_Position=vec4(2.,2.,2.,1.); return; }
          float u=position.x; vec3 p=mix(aFrom.xyz,aTo.xyz,u); vec3 d=aTo.xyz-aFrom.xyz;
          vec3 view=normalize(cameraPosition-p); vec3 side=normalize(cross(d,view)+vec3(1e-4));
          if(aStyle.x>1.5){ float s=floor(uTime*22.)+aFrom.w*13.; float env=sin(u*3.1416); p+=side*(h(floor(u*18.)+s)-.5)*env*length(d)*.11; p+=cross(side,normalize(d))*(h(floor(u*18.)+s+9.)-.5)*env*length(d)*.08; }
          float w=aCol.a*(aStyle.x<.5?1.:(1.-k*.7)); p+=side*position.y*w;
          vL=vec2(u,position.y); vC=aCol.rgb; vK=k; vS=aStyle.x; vHead=aStyle.y; gl_Position=projectionMatrix*viewMatrix*vec4(p,1.); }`,
      fragmentShader: /* glsl */`varying vec2 vL; varying vec3 vC; varying float vK; varying float vS; varying float vHead;
        void main(){ float edge=max(1.-abs(vL.y),0.); float core=pow(edge,2.4); float a;
          if(vS<.5){ float head=vK/max(vHead,.01); float tail=head-.32; a=smoothstep(tail,head,vL.x)*step(vL.x,min(head,1.))*(head>1.32?0.:1.); core=pow(edge,1.5); }
          else if(vS<1.5){ a=smoothstep(0.,.02,vL.x)*(1.-vK*vK); } else { a=(1.-vK)*(step(.5,fract(vK*7.))*.45+.55); }
          gl_FragColor=vec4(vC*core*a*(1.+core*2.2),1.); }`,
    });
    const m = new THREE.Mesh(g, mat);
    m.frustumCulled = false; m.renderOrder = 5;
    this.scene.add(m);
  }
  beam(a, b, c, { width = 0.12, life = 0.4, style = 0, travel = 0.8 } = {}) {
    const i = this.bHead; this.bHead = (i + 1) % MAX_BEAMS;
    const A = this.bA.array, B = this.bB.array, C = this.bC.array, S = this.bS.array;
    A[i * 4] = a[0]; A[i * 4 + 1] = a[1]; A[i * 4 + 2] = a[2]; A[i * 4 + 3] = this.clock;
    B[i * 4] = b[0]; B[i * 4 + 1] = b[1]; B[i * 4 + 2] = b[2]; B[i * 4 + 3] = life;
    C[i * 4] = c[0]; C[i * 4 + 1] = c[1]; C[i * 4 + 2] = c[2]; C[i * 4 + 3] = width;
    S[i * 2] = style; S[i * 2 + 1] = travel;
    this.bDirty = true;
  }

  buildRings() {
    const g = new THREE.InstancedBufferGeometry();
    const q = new THREE.PlaneGeometry(2, 2); q.rotateX(-Math.PI / 2);
    g.setAttribute("position", q.attributes.position); g.setIndex(q.index);
    const a = (n) => new THREE.InstancedBufferAttribute(new Float32Array(MAX_RINGS * n), n).setUsage(THREE.DynamicDrawUsage);
    this.rP = a(4); this.rC = a(4); // x, z, t0, life / rgb, radius
    this.rK = a(1); // 0 thin ring, 1 wave
    g.setAttribute("aPos", this.rP); g.setAttribute("aCol", this.rC); g.setAttribute("aKind", this.rK);
    g.instanceCount = MAX_RINGS;
    this.rHead = 0;
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      uniforms: { uTime: this.U.uTime },
      vertexShader: /* glsl */`uniform float uTime; attribute vec4 aPos,aCol; attribute float aKind; varying vec2 vL; varying float vK; varying vec3 vC; varying float vKind;
        void main(){ float k=(uTime-aPos.z)/aPos.w; if(k<0.||k>1.||aPos.w<=0.){ gl_Position=vec4(2.,2.,2.,1.); return; } float r=aCol.a*pow(k,.62);
          vec3 p=vec3(aPos.x,.14+aKind*.3*sin(k*3.14),aPos.y)+position*r; vL=position.xz; vK=k; vC=aCol.rgb; vKind=aKind; gl_Position=projectionMatrix*viewMatrix*vec4(p,1.); }`,
      fragmentShader: /* glsl */`varying vec2 vL; varying float vK; varying vec3 vC; varying float vKind;
        void main(){ float d=length(vL); if(d>1.) discard; float w=vKind>.5?.34:.1; float a=smoothstep(1.-w,1.,d)*(1.-smoothstep(.985,1.,d))*(1.-vK)*(1.-vK);
          if(vKind>.5){ float foam=fract(sin(dot(floor(vL*42.),vec2(12.9,78.2)))*43758.5); a*=.6+.8*step(.45,foam); }
          gl_FragColor=vec4(vC*a,1.); }`,
    });
    const m = new THREE.Mesh(g, mat);
    m.frustumCulled = false; m.renderOrder = 3;
    this.scene.add(m);
  }
  ring(x, z, radius, c, life = 0.7, kind = 0) {
    const i = this.rHead; this.rHead = (i + 1) % MAX_RINGS;
    const P = this.rP.array, C = this.rC.array;
    P[i * 4] = x; P[i * 4 + 1] = z; P[i * 4 + 2] = this.clock; P[i * 4 + 3] = life;
    C[i * 4] = c[0]; C[i * 4 + 1] = c[1]; C[i * 4 + 2] = c[2]; C[i * 4 + 3] = radius;
    this.rK.array[i] = kind;
    this.rDirty = true;
  }

  buildWeather() {
    const n = TIERS.high.weather;
    const q = new THREE.PlaneGeometry(1, 1);
    const g = new THREE.InstancedBufferGeometry();
    g.setAttribute("position", q.attributes.position); g.setIndex(q.index);
    const seedA = new Float32Array(n * 3);
    for (let i = 0; i < n * 3; i++) seedA[i] = Math.random();
    g.setAttribute("aSeed", new THREE.InstancedBufferAttribute(seedA, 3));
    g.instanceCount = this.tier.weather;
    this.weatherGeo = g;
    this.uWeather = { uFall: { value: new THREE.Vector4(0, 0, 0, 0) }, uRainColor: { value: new THREE.Vector4(0, 0, 0, 0) }, uCenter: { value: new THREE.Vector3() } };
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uTime: this.U.uTime, ...this.uWeather },
      vertexShader: /* glsl */`uniform float uTime; uniform vec4 uFall; uniform vec3 uCenter; attribute vec3 aSeed; varying vec2 vL;
        void main(){ vec3 box=vec3(110.,46.,90.); vec3 p=aSeed*box; p.y-=uTime*uFall.x*(.75+aSeed.x*.5); p.x+=uTime*uFall.y*(.7+aSeed.y*.6);
          p=mod(p,box)-box*.5; p.y+=23.; p.xz+=uCenter.xz; vec4 mv=viewMatrix*vec4(p,1.);
          float len=uFall.x>6.?uFall.w:uFall.z; mv.xy+=vec2(position.x*uFall.z,position.y*len)*(uFall.x>6.?1.:1.+aSeed.z); vL=position.xy*2.; gl_Position=projectionMatrix*mv; }`,
      fragmentShader: /* glsl */`uniform vec4 uRainColor; varying vec2 vL; void main(){ float a=(1.-abs(vL.x))*(1.-abs(vL.y)); gl_FragColor=vec4(uRainColor.rgb*a*uRainColor.a,1.); }`,
    });
    this.weatherMesh = new THREE.Mesh(g, mat);
    this.weatherMesh.frustumCulled = false; this.weatherMesh.renderOrder = 6;
    this.weatherMesh.visible = false;
    this.scene.add(this.weatherMesh);
    this.weather = null;
    this.lightningAt = 0;
  }
  setWeather(kind) {
    this.weather = kind;
    this.weatherMesh.visible = !!kind && !!WEATHER_LOOK[kind]?.rate;
    if (kind && WEATHER_LOOK[kind]) {
      const w = WEATHER_LOOK[kind];
      this.uWeather.uFall.value.set(...w.fall);
      this.uWeather.uRainColor.value.set(...w.color);
    }
    this.lightningAt = this.real + 3;
  }

  buildZones() {
    this.zoneMesh = [0, 1].map((side) => {
      const [a, b] = zoneOf(side);
      const g = new THREE.PlaneGeometry((b - a) * TILE_M, FH); g.rotateX(-Math.PI / 2);
      const m = new THREE.Mesh(g, new THREE.ShaderMaterial({
        transparent: true, depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending,
        uniforms: { uC: side === 0 ? (this.uSideA) : (this.uSideB), uTime: this.U.uTime },
        vertexShader: `varying vec2 vU; void main(){ vU=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
        fragmentShader: `uniform vec3 uC; varying vec2 vU; void main(){ vec2 e=min(vU,1.-vU)*vec2(${(ZONE * TILE_M).toFixed(1)},${FH.toFixed(1)}); float d=min(e.x,e.y); float a=(1.-smoothstep(.03,.18,d))*.9+.04; gl_FragColor=vec4(uC*a,1.); }`,
      }));
      m.position.set(((a + b) / 2 - W / 2) * TILE_M, 0.05, 0);
      m.renderOrder = 0.5; m.visible = false;
      this.scene.add(m);
      return m;
    });
  }

  buildPost() {
    const composer = new EffectComposer(this.renderer);
    composer.addPass(new RenderPass(this.scene, this.camera));
    // Last line of defence: one NaN pixel reaching bloom smears into a full-screen black flash.
    composer.addPass(new ShaderPass(new THREE.ShaderMaterial({
      uniforms: { tDiffuse: { value: null } },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.,1.); }`,
      fragmentShader: `uniform sampler2D tDiffuse; varying vec2 vUv; void main(){ vec3 c=texture2D(tDiffuse,vUv).rgb; if(any(isnan(c))||any(isinf(c))) c=vec3(0.); gl_FragColor=vec4(clamp(c,0.,16.),1.); }`,
    })));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.34, 0.65, 1.55);
    const bloomSize = this.bloom.setSize.bind(this.bloom);
    this.bloom.setSize = (w, h) => bloomSize(Math.max(64, w >> 1), Math.max(64, h >> 1)); // glow is soft anyway: half resolution costs a quarter
    composer.addPass(this.bloom);
    this.finalPass = new ShaderPass(new THREE.ShaderMaterial({
      uniforms: {
        tDiffuse: { value: null }, uFocusY: { value: 0.46 }, uBand: { value: 0.2 }, uBlur: { value: 0.0055 }, uAspect: { value: 1 }, uFlash: { value: 0 }, uFlashC: { value: new THREE.Color(1, 1, 1) },
        uChroma: { value: 0 }, uT: { value: 0 }, uTaps: { value: this.tier.taps }, uShock: { value: Array.from({ length: 3 }, () => new THREE.Vector4()) }, uGrade: { value: new THREE.Vector3(1, 1, 1) },
      },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.,1.); }`,
      fragmentShader: /* glsl */`
        uniform sampler2D tDiffuse; uniform int uTaps; uniform float uFocusY,uBand,uBlur,uAspect,uFlash,uChroma,uT; uniform vec3 uFlashC,uGrade; uniform vec4 uShock[3]; varying vec2 vUv;
        vec3 aces(vec3 x){ return clamp((x*(2.51*x+.03))/(x*(2.43*x+.59)+.14),0.,1.); }
        void main(){ vec2 uv=vUv;
          for(int i=0;i<3;i++){ vec4 s=uShock[i]; if(s.w<=0.) continue; vec2 d=(uv-s.xy)*vec2(uAspect,1.); float r=length(d); float k=(1.-smoothstep(0.,.09,abs(r-s.z)))*s.w; uv-=normalize(d+1e-5)*k*.026/vec2(uAspect,1.); }
          float coc=clamp((abs(uv.y-uFocusY)-uBand)/.42,0.,1.); coc=coc*coc*uBlur;
          vec3 c=vec3(0.); const float G=2.39996;
          if(coc<.00005) c=texture2D(tDiffuse,uv).rgb; // the in-focus band skips the blur entirely
          else { float tw=0.; for(int i=0;i<uTaps;i++){ float f=float(i); float r=sqrt(f/float(uTaps)); vec2 o=vec2(cos(f*G),sin(f*G))*r*coc*vec2(1./uAspect,1.)*8.; c+=texture2D(tDiffuse,uv+o).rgb; tw+=1.; } c/=tw; }
          if(uChroma>0.){ vec2 o=(uv-.5)*uChroma; c.r=texture2D(tDiffuse,uv+o).r; c.b=texture2D(tDiffuse,uv-o).b; }
          c=mix(c,uFlashC*2.2,uFlash);
          float luma=dot(c,vec3(.2126,.7152,.0722)); c=max(mix(vec3(luma),c,1.2),0.);
          c*=uGrade;
          c=aces(c*.94);
          vec2 q=vUv-.5; c*=1.-dot(q,q)*.72;
          c=pow(c,vec3(1./2.2));
          float n=fract(sin(dot(vUv*vec2(913.,711.)+uT,vec2(12.98,78.23)))*43758.5453); c+=(n-.5)*.018;
          gl_FragColor=vec4(c,1.); }`,
    }));
    composer.addPass(this.finalPass);
    this.composer = composer;
  }

  // Resizing clears the canvas, so it only ever happens at the top of a frame, right before drawing.
  requestResize() { this.needResize = true; }
  applySize() {
    this.needResize = false;
    const w = innerWidth, h = innerHeight;
    const fit = Math.sqrt(this.tier.pixels / Math.max(1, w * h));
    this.pixelRatio = Math.max(0.5, Math.min(devicePixelRatio || 1, this.maxPixelRatio, fit) * this.resScale);
    this.renderer.setPixelRatio(this.pixelRatio);
    this.renderer.setSize(w, h, false);
    this.composer.setPixelRatio(this.pixelRatio);
    this.composer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.finalPass.uniforms.uAspect.value = w / h;
    const r = Math.min(devicePixelRatio || 1, 1.5);
    this.overlay.width = w * r; this.overlay.height = h * r;
    this.overlay.style.width = `${w}px`; this.overlay.style.height = `${h}px`;
    this.octx.setTransform(r, 0, 0, r, 0, 0);
    this.w = w; this.h = h;
  }

  // ---------- turn playback ----------
  playTurn(events, duration) {
    this.turnDur = duration;
    const maxOrder = events.reduce((m, e) => Math.max(m, e.order ?? 0), 1);
    const uses = events.reduce((n, e) => n + (e.k === "use" ? 1 : 0), 0);
    this.fxKeep = Math.min(1, 260 / (uses || 1));
    for (const e of events) this.queue.push({ at: this.clock + ((e.order ?? maxOrder) / maxOrder) * duration * 0.86, e });
    this.queue.sort((a, b) => a.at - b.at);
  }

  chest(v) { return [v.x, v.h * 0.55 + (v.fly ? 0.55 + v.h * 0.32 : 0), v.z]; }

  pulseLight(x, y, z, r, c, k, life) {
    this.lightPool.push({ x, y, z, r, c, k, life, t0: this.real });
    if (this.lightPool.length > 16) this.lightPool.splice(0, this.lightPool.length - 16);
  }
  quake(x, z, amp) {
    if (this.reduced) amp *= 0.3;
    const q = this.U.uQuake.value[this.quakeSlot];
    q.set(x, z, this.clock, amp);
    this.quakeSlot = (this.quakeSlot + 1) % 4;
    this.camKick = Math.min(1.2, (this.camKick ?? 0) + amp * 0.45);
  }
  shock(x, y, z, power) {
    if (this.reduced) return;
    const v = new THREE.Vector3(x, y, z).project(this.camera);
    if (v.z > 1) return;
    this.shocks.push({ x: (v.x + 1) / 2, y: (v.y + 1) / 2, t0: this.real, power });
    if (this.shocks.length > 3) this.shocks.shift();
  }
  note(v, k = 1) {
    this.actionX += v.x * k; this.actionZ += v.z * k; this.actionWeight += k;
  }

  handle(e) {
    const V = this.vis;
    switch (e.k) {
      case "move": {
        const v = V[e.u]; if (!v) break;
        const [x, z] = this.tileToWorld(e.x, e.y, v.size);
        v.fx = v.x; v.fz = v.z; v.tx = x; v.tz = z; v.t0 = this.clock; v.t1 = this.clock + Math.max(0.12, this.turnDur * 0.9);
        const dx = x - v.fx, dz = z - v.fz;
        if (Math.abs(dx) + Math.abs(dz) > 0.01) { v.dir = dx; v.dz = dz; }
        // armies part the grass as they march
        const r = Math.max(0.3, v.size * TILE_M * 0.5);
        const steps = Math.max(1, Math.round(Math.hypot(dx, dz) / (r * 1.2)));
        for (let i = 1; i <= steps; i++) this.scar(lerp(v.fx, x, i / steps), lerp(v.fz, z, i / steps), r, 0, v.size > 3 ? 0.5 : 0.17);
        if (v.size >= 5) { this.quake(x, z, 0.03); this.burst(x, 0.1, z, 6, hex(0x9f8f6a, 0.55), { speed: 1.2, up: 0.8, life: 0.9, size: 0.6, end: 1.1, grav: 0.2, add: 0 }); this.sfx("stomp"); }
        break;
      }
      case "use": this.moveFx(e); break;
      case "hit": this.hitFx(e); break;
      case "miss": { const o = V[e.o]; if (o && o.h > 1.2 && Math.random() < 0.4) this.float(o, "miss", "#b8b4a8", 12); break; }
      case "immune": { const o = V[e.o]; if (o) { this.float(o, "immune", "#9fc4dc", 13); this.onEvent(e); } break; }
      case "faint": this.faintFx(e); break;
      case "status": {
        const o = V[e.o]; if (!o) break;
        o.status = e.s;
        const c = { par: 0xffd21f, psn: 0xb84de0, brn: 0xff6a1a, slp: 0x9fb8ff, frz: 0x80ecff, tox: 0xb84de0, conf: 0xf490d8, seed: 0x62e040 }[e.s] ?? 0xffffff;
        const [x, y, z] = this.chest(o);
        this.burst(x, y, z, 8, hex(c, 1.8), { speed: 1.2, up: 1, life: 0.6, size: 0.12, grav: -0.8 });
        this.float(o, { par: "PAR", psn: "PSN", brn: "BRN", slp: "SLP", frz: "FRZ", tox: "TOX", conf: "CONF", seed: "SEED" }[e.s] ?? e.s, `#${c.toString(16).padStart(6, "0")}`, 12);
        break;
      }
      case "boost": {
        const o = V[e.o]; if (!o) break;
        const up = e.n > 0, [x, y, z] = this.chest(o);
        if (o.h >= 0.6 || Math.random() < 0.25) this.float(o, `${{ atk: "ATK", def: "DEF", spa: "SPA", spd: "SPD", spe: "SPE", accuracy: "ACC", evasion: "EVA" }[e.stat] ?? e.stat}${up ? "↑" : "↓"}`, up ? "#7fe6ff" : "#ff8a7a", 12);
        this.onEvent(e);
        for (let i = 0; i < 5; i++) this.particle(x + (Math.random() - 0.5) * o.h * 0.5, y - o.h * 0.3, z + (Math.random() - 0.5) * o.h * 0.5, 0, up ? 1.6 : -0.9, 0, 0.6, 0.11, 0.03, 0, ...(up ? hex(0x62c8ff, 1.6) : hex(0xff5a4a, 1.5)));
        break;
      }
      case "heal": {
        const o = V[e.u]; if (!o) break;
        const [x, y, z] = this.chest(o);
        this.burst(x, y, z, 12, hex(0x7cff9a, 1.8), { speed: 0.8, up: 2, life: 0.8, size: 0.13, grav: -1, jitter: o.h * 0.6 });
        o.hp = Math.min(o.maxHp, o.hp + e.n);
        break;
      }
      case "chip": {
        const o = V[e.o]; if (!o) break;
        o.hp = Math.max(0, o.hp - e.d);
        const c = { psn: 0xb84de0, brn: 0xff6a1a, sand: 0xc9975a, seed: 0x62e040, struggle: 0xd8d4c0, recoil: 0xd8d4c0, confusion: 0xf490d8 }[e.why] ?? 0xffffff;
        if (Math.random() < 0.35) { const [x, y, z] = this.chest(o); this.burst(x, y, z, 3, hex(c, 1.2), { speed: 0.6, up: 1, life: 0.45, size: 0.09, grav: -0.3 }); }
        break;
      }
      case "weather":
        this.setWeather(e.kind);
        this.onEvent(e);
        this.sfx(e.kind === "rain" ? "rain" : "wind");
        break;
      case "evolve": this.startEvolution(e); this.onEvent(e); break;
      case "transform": {
        const v = V[e.u]; if (!v) break;
        v.num = e.to; v.h = Math.max(this.data.species[e.to].heightm, MIN_BODY);
        v.flash = 1;
        const [x, y, z] = this.chest(v);
        this.burst(x, y, z, 26, hex(0xff66d0, 2), { speed: 2.4, up: 1, life: 0.7, size: 0.16, grav: 0 });
        this.ring(v.x, v.z, 2.6, hex(0xff66d0, 1.6), 0.55);
        this.sfx("morph");
        this.onEvent(e);
        break;
      }
      case "roar": {
        const v = V[e.u]; if (!v) break;
        this.ring(v.x, v.z, 5, hex(0xffa050, 1.5), 0.6);
        this.onEvent(e);
        break;
      }
      case "warp": {
        const v = V[e.u]; if (!v) break;
        const [x, y, z] = this.chest(v);
        this.burst(x, y, z, 16, hex(0xff4d98, 2), { speed: 2, up: 1, life: 0.4, size: 0.1, grav: 0 });
        const [nx, nz] = this.tileToWorld(e.x, e.y, v.size);
        v.x = v.fx = v.tx = nx; v.z = v.fz = v.tz = nz; v.t1 = 0;
        break;
      }
      case "screen": {
        const c = { reflect: 0x62c8ff, lightscreen: 0xffd21f, tailwind: 0xcfe4ff, safeguard: 0x8affc8, mist: 0xc8d8ff }[e.s] ?? 0xffffff;
        for (const v of V) if (v.alive && v.side === e.side && Math.random() < 0.25) { const [x, y, z] = this.chest(v); this.burst(x, y, z, 3, hex(c, 1.6), { speed: 0.5, up: 1, life: 0.7, size: 0.2, grav: 0 }); }
        this.onEvent(e);
        break;
      }
      case "support": {
        const v = V[e.u], o = V[e.o];
        if (!v || !o) break;
        const c = { healpulse: 0x7cff9a, helpinghand: 0xffd86a, coaching: 0xff9a5a }[e.m] ?? 0xffffff;
        this.beam(this.chest(v), this.chest(o), hex(c, 2.4), { width: 0.07, life: 0.36, style: 0, travel: 0.7 });
        const [x, y, z] = this.chest(o);
        this.burst(x, y, z, 7, hex(c, 1.9), { speed: 0.7, up: 1.6, life: 0.5, size: 0.09, grav: -1 });
        break;
      }
      case "splash": {
        const v = V[e.u]; if (!v) break;
        v.lunge = 0.35; v.lungeT = this.clock; v.lx = 0; v.lz = 0;
        this.burst(v.x, 0.1, v.z, 5, hex(0x38a0ff, 1.5), { speed: 1, up: 2, life: 0.4, size: 0.07, grav: 8 });
        break;
      }
      case "level": {
        const v = V[e.u]; if (v) { const [x, y, z] = this.chest(v); this.burst(x, y, z, 5, hex(0xffe08a, 1.8), { speed: 0.6, up: 2, life: 0.6, size: 0.08, grav: -0.5 }); }
        break;
      }
      default: break;
    }
  }

  moveFx(e) {
    const V = this.vis, v = V[e.u];
    if (!v) return;
    const D = this.data.moves[e.m] ?? { type: null, cat: "physical", contact: true, target: "normal" };
    const big = ["earthquake", "magnitude", "fissure", "surf", "explosion", "selfdestruct", "hyperbeam", "thunder", "blizzard", "solarbeam", "rockslide"].includes(e.m);
    if (!big && Math.random() > this.fxKeep) return;
    const c = hex(TYPE_COLOR[D.type] ?? 0xffffff, 1.8);
    const [sx, sy, sz] = this.chest(v);
    const t = e.t != null ? V[e.t] : null;
    const [tx, ty, tz] = t ? this.chest(t) : [sx, sy, sz];
    if (t) { v.dir = tx - sx; v.dz = tz - sz; }
    this.onEvent(e);
    this.note(v, big ? 8 : 0.4);

    if (D.cat === "status") {
      if (e.m === "howl") { this.ring(v.x, v.z, 3.4, hex(0xff9a4a, 1.7), 0.5); this.sfx("thud"); return; }
      if (e.m === "followme" || e.m === "ragepowder") { this.ring(v.x, v.z, 2.2, hex(0xff8ad0, 1.8), 0.6); this.burst(sx, sy, sz, 10, hex(0xff8ad0, 1.8), { speed: 1, up: 1.6, life: 0.6, size: 0.1, grav: -1 }); return; }
      if (e.m === "tailwind") {
        for (const a of V) if (a.alive && a.side === v.side && Math.random() < 0.5) this.particle(a.x - a.dir * 0.3, 0.3 + Math.random() * 2, a.z, (a.side === 0 ? 1 : -1) * (8 + Math.random() * 7), 0.2, (Math.random() - 0.5), 0.7, 0.14, 0.02, 0, 1.4, 1.6, 1.9);
        this.sfx("wind");
        return;
      }
      if (t) this.beam([sx, sy, sz], [tx, ty, tz], c, { width: 0.05, life: 0.34, style: 0, travel: 0.7 });
      else this.burst(sx, sy, sz, 7, c, { speed: 0.9, up: 1.6, life: 0.6, size: 0.1, grav: -1, jitter: v.h * 0.5 });
      return;
    }
    if (e.m === "earthquake" || e.m === "magnitude" || e.m === "fissure") {
      this.quake(v.x, v.z, 0.5);
      this.ring(v.x, v.z, 6, hex(0xd8a040, 1.5), 0.8);
      for (let i = 0; i < 46; i++) {
        const a = Math.random() * 6.283, d = Math.random() * 3.4;
        this.particle(v.x + Math.cos(a) * d, 0.1, v.z + Math.sin(a) * d, Math.cos(a) * 1.4, 2 + Math.random() * 4, Math.sin(a) * 1.4, 0.9, 0.18, 0.05, 9, 0.55, 0.4, 0.22, 0);
      }
      this.scar(v.x, v.z, 2.2, 0, 0.9);
      this.flashCam(0xd8a040, 0.06);
      this.sfx("quake");
      return;
    }
    if (e.m === "surf" || e.m === "muddywater") {
      this.ring(v.x, v.z, 6, hex(0x7fd0ff, 2), 0.75, 1);
      this.ring(v.x, v.z, 4.2, hex(0xffffff, 1.3), 0.6, 1);
      for (let i = 0; i < 70; i++) {
        const a = Math.random() * 6.283, s = 5 + Math.random() * 4;
        this.particle(v.x, 0.15, v.z, Math.cos(a) * s, 1 + Math.random() * 3, Math.sin(a) * s, 0.7, 0.26, 0.06, 7, 0.55, 0.85, 1.7);
      }
      this.scar(v.x, v.z, 3.2, 2, 0.55);
      this.sfx("wave");
      return;
    }
    if (e.m === "explosion" || e.m === "selfdestruct") {
      const k = e.m === "explosion" ? 1.5 : 1;
      this.burst(sx, sy, sz, 90 * k, hex(0xffb050, 3.4), { speed: 7 * k, up: 1, life: 0.7, size: 0.5, end: 0.05, grav: 1 });
      this.burst(sx, sy, sz, 40 * k, hex(0x38302a, 0.5), { speed: 3 * k, up: 1.1, life: 1.5, size: 0.9, end: 2.2, grav: -0.5, add: 0 });
      this.ring(v.x, v.z, 5.2 * k, hex(0xffc070, 2.4), 0.5);
      this.pulseLight(sx, 1.4, sz, 15 * k, [1, 0.62, 0.3], 7, 0.5);
      this.quake(v.x, v.z, 0.32 * k);
      this.shock(sx, sy, sz, 1);
      this.scar(v.x, v.z, 2.8 * k, 1, 0.9);
      this.flashCam(0xffc070, 0.035 * k);
      this.chroma = Math.max(this.chroma, 0.0035);
      this.sfx("boom");
      return;
    }
    if (e.m === "thunder" || e.m === "thunderbolt" || e.m === "thundershock" || e.m === "zapcannon") {
      const sky = e.m === "thunder";
      if (!sky && this.real - (this.zapAt ?? 0) < 0.03) return;
      this.zapAt = this.real;
      const from = sky ? [tx + (Math.random() - 0.5) * 2, 34, tz + (Math.random() - 0.5) * 2] : [sx, sy, sz];
      this.beam(from, [tx, ty, tz], hex(0xffec8a, sky ? 3.4 : 1.9), { width: sky ? 0.4 : 0.075, life: sky ? 0.32 : 0.2, style: 2 });
      this.pulseLight(tx, ty + 0.8, tz, sky ? 18 : 5, [1, 0.92, 0.5], sky ? 8 : 1, sky ? 0.32 : 0.16);
      if (sky) { this.flashCam(0xfff2c0, 0.2); this.shock(tx, ty, tz, 0.6); this.sfx("thunder"); } else this.sfx("zap");
      return;
    }
    if (["hyperbeam", "solarbeam", "icebeam", "psybeam", "bubblebeam", "aurorabeam", "dragonbreath", "gigadrain", "absorb", "megadrain", "signalbeam"].includes(e.m)) {
      const w = e.m === "hyperbeam" || e.m === "solarbeam" ? 0.42 : 0.16, life = e.m === "hyperbeam" ? 0.62 : 0.34;
      const beamC = e.m === "hyperbeam" ? hex(0xfff0e0, 3.2) : hex(TYPE_COLOR[D.type], 2.8);
      this.beam([sx, sy, sz], [tx, ty, tz], beamC, { width: w, life, style: 1 });
      this.pulseLight(lerp(sx, tx, 0.5), Math.max(sy, ty), lerp(sz, tz, 0.5), w > 0.3 ? 14 : 5, beamC.map((x) => x / 3), w > 0.3 ? 4 : 1.4, life);
      if (w > 0.3) { this.flashCam(0xfff0e0, 0.05); this.sfx("beam"); } else this.sfx("zap");
      return;
    }
    if (D.type === "fire" && !D.contact) {
      const n = e.m === "fireblast" ? 46 : 24;
      const dx = tx - sx, dy = ty - sy, dz = tz - sz, l = Math.hypot(dx, dy, dz) || 1, T = 0.32;
      for (let i = 0; i < n; i++) {
        const j = 0.4 + Math.random() * 0.8, s = (l / T) * j;
        this.particle(sx, sy, sz, (dx / l) * s + (Math.random() - 0.5) * 2, (dy / l) * s + Math.random() * 1.6, (dz / l) * s + (Math.random() - 0.5) * 2, T * 1.25, 0.1 + Math.random() * 0.1, 0.42, -0.8, 2.9, 1 + Math.random() * 0.6, 0.18);
      }
      this.pulseLight(lerp(sx, tx, 0.6), Math.max(sy, ty), lerp(sz, tz, 0.6), 7, [1, 0.5, 0.15], 2.8, 0.42);
      this.sfx("fire");
      return;
    }
    if (D.type === "ice" && !D.contact) {
      if (e.m === "blizzard") {
        this.ring(v.x, v.z, 6, hex(0xc8f4ff, 2), 0.8);
        for (let i = 0; i < 70; i++) { const a = Math.random() * 6.283, s = 3 + Math.random() * 6; this.particle(v.x, 0.4 + Math.random() * 2, v.z, Math.cos(a) * s, Math.random() * 1.5, Math.sin(a) * s, 0.8, 0.12, 0.04, 0, 1.7, 2.2, 2.4); }
        this.sfx("wind");
        return;
      }
    }
    if (e.m === "rockslide" || e.m === "rockthrow" || e.m === "ancientpower") {
      for (let i = 0; i < (e.m === "rockslide" ? 14 : 5); i++) {
        const ox = t ? tx : sx + (Math.random() - 0.5) * 6, oz = t ? tz : sz + (Math.random() - 0.5) * 6;
        const px = ox + (Math.random() - 0.5) * 2.6, pz = oz + (Math.random() - 0.5) * 2.6;
        this.particle(px, 9 + Math.random() * 4, pz, 0, -5, 0, 0.7, 0.34, 0.34, 18, 0.36, 0.28, 0.17, 0);
      }
      this.sfx("thud");
      return;
    }
    if (D.contact || !t) {
      if (t) {
        const dx = tx - sx, dz = tz - sz, l = Math.hypot(dx, dz) || 1;
        v.lunge = Math.max(0.14, this.turnDur * 0.34); v.lungeT = this.clock;
        const reachLen = Math.min(l * 0.55, 0.4 + v.h * 0.25);
        v.lx = (dx / l) * reachLen; v.lz = (dz / l) * reachLen;
      } else this.burst(sx, sy, sz, 10, c, { speed: 2, up: 1, life: 0.5, size: 0.14 });
      return;
    }
    // everything else ranged is a coloured bolt that travels to the target
    this.beam([sx, sy, sz], [tx, ty, tz], hex(TYPE_COLOR[D.type] ?? 0xffffff, 2.8), { width: 0.1 + Math.min(D.pow, 120) / 800, life: 0.32, style: 0, travel: 0.72 });
    if (D.type === "psychic") { this.ring(tx, tz, 1.6, hex(0xff4d98, 1.7), 0.4); this.shock(tx, ty, tz, 0.3); }
    this.sfx("pew");
  }

  flashCam(color, k) {
    if (this.reduced) return;
    this.finalPass.uniforms.uFlashC.value.setRGB(...hex(color));
    this.flash = Math.max(this.flash, k);
  }

  hitFx(e) {
    const V = this.vis, o = V[e.o], u = V[e.u];
    if (!o) return;
    const D = this.data.moves[e.m] ?? { type: null };
    const c = hex(TYPE_COLOR[D.type] ?? 0xe8e4d8, 1.8);
    o.hp = Math.max(0, o.hp - e.d);
    o.flash = 1;
    const dx = u ? o.x - u.x : 1, dz = u ? o.z - u.z : 0, l = Math.hypot(dx, dz) || 1;
    o.knock = 0.2; o.knockT = this.clock;
    const push = Math.min(0.55, 0.08 + (e.d / Math.max(o.maxHp, 1)) * 0.9) * (e.eff > 1 ? 1.5 : 1);
    o.kx = (dx / l) * push; o.kz = (dz / l) * push;
    const keep = this.fxKeep ?? 1;
    this.note(o, 0.6);
    const [x, y, z] = this.chest(o);
    if (Math.random() < keep) this.burst(x, y, z, e.eff > 1 ? 10 : 5, c, { speed: 2.2 + (e.crit ? 2 : 0), up: 1, life: 0.42, size: 0.09 + Math.min(o.h * 0.05, 0.14), grav: 5 });
    if (D.type === "fire") { this.scar(o.x, o.z, 0.5 + o.size * 0.12, 1, 0.55); this.pulseLight(x, y, z, 3.5, [1, 0.5, 0.2], 1.1, 0.25); }
    if (D.type === "water") this.scar(o.x, o.z, 0.6, 2, 0.4);
    if (e.ohko) { this.flashCam(0xffffff, 0.12); this.shock(x, y, z, 0.8); this.sfx("boom"); this.onEvent({ k: "ohko", u: e.u, o: e.o }); }
    if (e.crit && o.h > 1) this.float(o, `${e.d}!`, "#ffd86a", 16);
    else if (e.eff > 1 && (o.h > 0.9 || Math.random() < 0.08)) this.float(o, String(e.d), "#ff9a6a", 14);
    else if (o.h > 1.5) this.float(o, String(e.d), "#e8e3d8", 12);
    if (o.size >= 5) { this.camKick = Math.min(1, (this.camKick ?? 0) + 0.1); }
    this.sfx(e.eff > 1 ? "hit2" : "hit");
    this.onEvent(e);
  }

  faintFx(e) {
    const v = this.vis[e.o];
    if (!v) return;
    v.alive = false;
    v.tilt = 0.05;
    v.hp = 0;
    const [x, y, z] = this.chest(v);
    // Pokémon faint, they don't die: a red recall flash instead of gore
    this.burst(x, y, z, 6 + Math.min(v.size * 8, 44), hex(0xff5a5a, 1.7), { speed: 1.2 + v.size * 0.3, up: 1.4, life: 0.5, size: 0.1 + v.size * 0.03, grav: -0.4, jitter: v.h * 0.5 });
    if (v.size >= 2) { this.ring(v.x, v.z, 1.2 + v.size * 0.55, hex(0xff5a5a, 1.2), 0.5); this.pulseLight(x, y, z, 2.6 + v.size, [1, 0.3, 0.3], 1.6, 0.3); }
    if (v.size >= 5) { this.quake(v.x, v.z, 0.2); this.burst(v.x, 0.2, v.z, 40, hex(0x9f8f6a, 0.6), { speed: 3.5, up: 0.6, life: 1.4, size: 0.9, end: 2.4, grav: 0, add: 0 }); this.sfx("thud"); }
    this.scar(v.x, v.z, 0.32 + v.size * TILE_M * 0.6, 0, 0.5);
    this.sfx("faint");
    this.note(v, v.size >= 3 ? 6 : 0.8);
    this.onEvent(e);
  }

  startEvolution(e) {
    const v = this.vis[e.u];
    if (!v) return;
    const cut = this.camMode === "director" && this.real - this.lastCut > 7 && !this.reduced;
    const dur = cut ? 1.5 : 0.65;
    v.evo = { t0: this.clock, dur, to: e.to };
    if (cut) {
      this.lastCut = this.real;
      const nh = this.data.species[e.to].heightm;
      this.slow(0.22, 2.6);
      const camD = Math.max(5.5, nh * 2.6 + 3.5), sideK = v.side === 0 ? -1 : 1;
      this.shot = { until: this.real + 3.1, tx: v.x, ty: Math.max(nh * 0.55, 0.5), tz: v.z, px: v.x + sideK * camD * 0.42, py: Math.max(0.75, nh * 0.42), pz: v.z + camD, band: 0.16, blur: 0.009 };
    }
  }
  updateEvolution(v) {
    const p = (this.clock - v.evo.t0) / v.evo.dur;
    if (p >= 1) {
      v.num = v.evo.to;
      v.h = Math.max(this.data.species[v.num].heightm, MIN_BODY);
      const u = this.world.units[v.id];
      v.size = u.size;
      const [x, y, z] = this.chest(v);
      this.burst(x, y, z, 60, hex(0xfff2c0, 3), { speed: 4.2, up: 1.2, life: 0.9, size: 0.17, grav: 1 });
      this.ring(v.x, v.z, 3 + v.h, hex(0xfff2c0, 2.4), 0.7);
      this.pulseLight(x, y, z, 7 + v.h * 2, [1, 0.95, 0.75], 4.5, 0.6);
      this.shock(x, y, z, 0.8);
      v.flash = 1; v.evo = null; v.scale = 1;
      this.sfx("evolved");
      return;
    }
    const pulse = 0.5 + 0.5 * Math.sin(p * p * 46);
    v.flash = 0.45 + pulse * 0.55;
    v.scale = 1 + pulse * 0.16 * p;
    if (Math.random() < 0.5) {
      const [x, y, z] = this.chest(v);
      const a = Math.random() * 6.283, r = v.h * 0.9 + 0.4;
      this.particle(x + Math.cos(a) * r, y - v.h * 0.4, z + Math.sin(a) * r, -Math.cos(a) * r * 1.2, v.h * 0.8, -Math.sin(a) * r * 1.2, 0.7, 0.09, 0.02, 0, 2.6, 2.4, 1.6);
    }
    if (!v.evoSfx || this.real - v.evoSfx > 0.3) { v.evoSfx = this.real; this.sfx("evolving"); }
  }
  slow(k, seconds) { this.timeScale = k; this.slowUntil = this.real + seconds; }

  float(v, text, color, size) {
    if (this.floats.length > 60 || this.real - (v.floatAt ?? -1) < 0.42) return;
    v.floatAt = this.real;
    this.floats.push({ x: v.x + (Math.random() - 0.5) * 0.3, y: v.h + (v.fly ? 0.7 + v.h * 0.32 : 0) + 0.2, z: v.z, text, color, size, t0: this.real });
  }

  ambient(dt) {
    const b = this.biome;
    this.airTimer += dt;
    const every = 0.045;
    while (this.airTimer > every) {
      this.airTimer -= every;
      const x = this.controls.target.x + (Math.random() - 0.5) * 80, z = this.controls.target.z + (Math.random() - 0.5) * 60;
      if (b.air === "pollen") this.particle(x, 0.4 + Math.random() * 3, z, 0.5, 0.08, 0.15, 4.2, 0.05, 0.03, 0, 1.1, 0.9, 0.55);
      else if (b.air === "fireflies") { if (Math.random() < 0.35) this.particle(x, 0.25 + Math.random() * 2, z, (Math.random() - 0.5) * 0.6, 0.12, (Math.random() - 0.5) * 0.6, 3.6, 0.1, 0.03, 0, 2.2, 2.6, 0.8); }
      else if (b.air === "motes") this.particle(x, 0.3 + Math.random() * 6, z, 0.1, 0.1, 0.1, 5, 0.05, 0.02, 0, 0.6, 0.8, 1.7);
      else if (b.air === "snow") this.particle(x, 3 + Math.random() * 11, z, 0.9, -1.3, 0.3, 6, 0.07, 0.07, 0, 1.15, 1.2, 1.3);
      else if (b.air === "embers") { if (Math.random() < 0.6) this.particle(x, 0.2, z, 0.3, 1.3 + Math.random(), 0.2, 3.3, 0.07, 0.02, -0.1, 3, 0.9, 0.2); }
      else if (b.air === "spray") this.particle(x, 0.4 + Math.random() * 2, z, 1.1, 0.14, 0.2, 3.2, 0.05, 0.03, 0, 1, 1, 1);
    }
    if (this.weather === "rain" && this.real > this.lightningAt) {
      this.lightningAt = this.real + 5 + Math.random() * 9;
      const x = this.controls.target.x + (Math.random() - 0.5) * 120;
      const zz = -60 - Math.random() * 90;
      this.beam([x, 150, zz], [x + (Math.random() - 0.5) * 40, 4, zz], [3.8, 3.8, 4.4], { width: 1.1, life: 0.26, style: 2 });
      this.flashCam(0xc8d8ff, 0.22);
      this.sfx("thunderFar");
    }
    if (this.weather === "rain" && Math.random() < 0.5) {
      const x = this.controls.target.x + (Math.random() - 0.5) * 60, z = this.controls.target.z + (Math.random() - 0.5) * 40;
      this.particle(x, 0.08, z, 0, 0.8, 0, 0.18, 0.05, 0.14, 4, 0.55, 0.65, 0.8);
    }
  }

  // ---------- camera ----------
  setCamMode(mode) {
    this.camMode = mode;
    this.shot = null;
    this.onCamMode(mode);
  }
  cut({ px, py, pz, tx, ty, tz, until = Infinity, band = 0.22, blur = 0.0065 }) { this.shot = { px, py, pz, tx, ty, tz, until: this.real + until, band, blur }; }
  frameWide() {
    this.camera.position.set(0, 34, 58); this.controls.target.set(0, 0, -1); this.controls.update();
  }
  directCamera(dt) {
    const ctl = this.controls, cam = this.camera;
    const ease = (rate) => 1 - Math.exp(-rate * dt);
    let want = null;
    if (this.shot && this.real > this.shot.until) this.shot = null;
    if (this.camMode === "follow" && this.follow && !this.follow.gone) {
      const v = this.follow, d = Math.max(5, v.h * 3 + 3.8), s = v.dir >= 0 ? -1 : 1;
      want = { px: v.x + s * d * 0.55, py: Math.max(1, v.h * 0.62) + d * 0.1, pz: v.z + d * 0.86, tx: v.x + s * -1.1, ty: v.h * 0.55 + (v.fly ? 1 : 0), tz: v.z, band: 0.14, blur: 0.0095, rate: 3.5 };
    } else if (this.shot) want = { ...this.shot, rate: 2.2 };
    else if (this.camMode === "director") {
      const ax = this.armyX ?? 0, az = this.armyZ ?? 0;
      const lean = Math.min(0.4, this.actionWeight * 0.02);
      const hx = this.actionWeight > 0.1 ? this.actionX / this.actionWeight : ax, hz = this.actionWeight > 0.1 ? this.actionZ / this.actionWeight : az;
      this.focusX = lerp(ax, hx, lean); this.focusZ = lerp(az, hz, lean);
      this.actionX *= 0.975; this.actionZ *= 0.975; this.actionWeight *= 0.975;
      const fx = this.focusX, fz = this.focusZ;
      const sway = Math.sin(this.real * 0.07) * Math.min(7, (this.directorDist ?? 54) * 0.12);
      const dist = this.directorDist ?? 54;
      want = { px: fx + sway, py: dist * 0.5, pz: fz + dist, tx: fx, ty: 0, tz: fz - 1, band: 0.22, blur: 0.0055, rate: 0.9 };
    }
    if (want) {
      const k = ease(want.rate);
      cam.position.x = lerp(cam.position.x, want.px, k); cam.position.y = lerp(cam.position.y, want.py, k); cam.position.z = lerp(cam.position.z, want.pz, k);
      ctl.target.x = lerp(ctl.target.x, want.tx, k); ctl.target.y = lerp(ctl.target.y, want.ty, k); ctl.target.z = lerp(ctl.target.z, want.tz, k);
      const f = this.finalPass.uniforms;
      f.uBand.value = lerp(f.uBand.value, want.band ?? 0.2, ease(2)); f.uBlur.value = lerp(f.uBlur.value, want.blur ?? 0.0055, ease(2));
    } else {
      const f = this.finalPass.uniforms;
      f.uBand.value = lerp(f.uBand.value, 0.26, ease(2)); f.uBlur.value = lerp(f.uBlur.value, 0.0042, ease(2));
    }
    ctl.enabled = !this.dragLock;
    ctl.update();
    if (this.camKick > 0.001) {
      const k = this.camKick;
      cam.position.x += (Math.random() - 0.5) * k * 0.55; cam.position.y += (Math.random() - 0.5) * k * 0.4;
      this.camKick *= Math.exp(-dt * 6);
    }
    // keep the camera above the grass and the focus band on what it is looking at
    if (cam.position.y < 0.5) cam.position.y = 0.5;
    cam.updateMatrixWorld();
    const v = ctl.target.clone().project(cam);
    this.finalPass.uniforms.uFocusY.value = lerp(this.finalPass.uniforms.uFocusY.value, (v.y + 1) / 2, ease(4));
  }

  // ---------- frame ----------
  frame(dtReal) {
    if (this.needResize) this.applySize();
    const dt = Math.min(dtReal, 0.05);
    this.real += dt;
    if (this.slowUntil && this.real > this.slowUntil) { this.timeScale = 1; this.slowUntil = 0; }
    const scale = this.paused ? 0 : this.timeScale;
    this.dtScaled = dt * scale;
    this.clock += this.dtScaled;
    this.U.uTime.value = this.clock;

    while (this.queue.length && this.queue[0].at <= this.clock) this.handle(this.queue.shift().e);

    this.lookAtWeather(dt);
    this.updateLights();
    this.writeUnits();
    this.atlas.flush();
    if (!this.paused) this.ambient(this.dtScaled);
    this.flushParticles();
    if (this.bDirty) { this.bA.needsUpdate = this.bB.needsUpdate = this.bC.needsUpdate = this.bS.needsUpdate = true; this.bDirty = false; }
    if (this.rDirty) { this.rP.needsUpdate = this.rC.needsUpdate = this.rK.needsUpdate = true; this.rDirty = false; }
    if (this.scarDirty && this.real - (this.scarAt ?? 0) > 0.2) { this.scarTex.needsUpdate = true; this.scarDirty = false; this.scarAt = this.real; }

    this.directCamera(dt);
    this.uWeather.uCenter.value.copy(this.controls.target);
    const f = this.finalPass.uniforms;
    this.flash *= Math.exp(-dt * 7);
    f.uFlash.value = this.flash;
    this.chroma *= Math.exp(-dt * 5);
    f.uChroma.value = this.chroma;
    f.uT.value = this.real % 100;
    for (let i = 0; i < 3; i++) {
      const s = this.shocks[i], u = f.uShock.value[i];
      if (!s) { u.w = 0; continue; }
      const a = this.real - s.t0;
      if (a > 0.75) { u.w = 0; continue; }
      u.set(s.x, s.y, a * 0.75, s.power * (1 - a / 0.75));
    }
    this.composer.render(dt);
    this.drawOverlay();
    this.adapt(dtReal);
  }

  lookAtWeather(dt) {
    const w = this.weather && WEATHER_LOOK[this.weather];
    const k = 1 - Math.exp(-dt * 1.6);
    const L = this.look;
    L.sun = lerp(L.sun, w?.sun ?? 1, k); L.amb = lerp(L.amb, w?.amb ?? 1, k); L.fogK = lerp(L.fogK, w?.fogK ?? 1, k);
    const target = w?.fog != null ? new THREE.Color(...hex(w.fog)) : this.baseFog;
    L.fog.lerp(target, k);
    this.U.uFogColor.value.copy(L.fog);
    this.U.uFogDensity.value = this.biome.fogD * L.fogK;
    this.U.uSunColor.value.copy(this.baseSun).multiplyScalar(L.sun);
    this.U.uAmbient.value.copy(this.baseAmb).multiplyScalar(L.amb);
    this.uSkyDim.value = lerp(this.uSkyDim.value, this.weather === "rain" ? 0.42 : this.weather === "sand" ? 0.72 : this.weather === "sun" ? 1.2 : 1, k);
    this.uShadowK.value = lerp(this.uShadowK.value, this.weather === "rain" || this.weather === "sand" ? 0.14 : 0.34, k);
  }

  updateLights() {
    const P = this.U.uLP.value, C = this.U.uLC.value;
    this.lightPool = this.lightPool.filter((l) => this.real - l.t0 < l.life);
    const live = this.lightPool.map((l) => ({ l, k: l.k * (1 - (this.real - l.t0) / l.life) })).sort((a, b) => b.k - a.k);
    for (let i = 0; i < 8; i++) {
      const it = live[i];
      if (!it) { C[i].w = 0; continue; }
      P[i].set(it.l.x, it.l.y, it.l.z, it.l.r);
      C[i].set(it.l.c[0], it.l.c[1], it.l.c[2], it.k);
    }
  }

  drawOverlay() {
    const c = this.octx, v3 = new THREE.Vector3();
    c.clearRect(0, 0, this.w, this.h);
    c.textAlign = "center"; c.textBaseline = "middle";
    const project = (x, y, z) => {
      v3.set(x, y, z).project(this.camera);
      if (v3.z > 1) return null;
      return [((v3.x + 1) / 2) * this.w, ((1 - v3.y) / 2) * this.h];
    };
    this.floats = this.floats.filter((f) => this.real - f.t0 < 0.9);
    for (const f of this.floats) {
      const a = (this.real - f.t0) / 0.9, p = project(f.x, f.y + a * 0.9, f.z);
      if (!p) continue;
      c.globalAlpha = 1 - a * a;
      c.font = `700 ${f.size}px ui-monospace, Menlo, Consolas, monospace`;
      c.lineWidth = 3; c.strokeStyle = "rgba(10,9,14,.75)";
      c.strokeText(f.text, p[0], p[1]);
      c.fillStyle = f.color;
      c.fillText(f.text, p[0], p[1]);
    }
    c.globalAlpha = 1;
    // health bars only where they stay readable: big bodies, the selected unit, and anything close to the camera
    for (const v of this.vis) {
      if (!v.alive || v.gone) continue;
      const near = this.camera.position.distanceToSquared(v3.set(v.x, 0, v.z)) < 340;
      if (!(v.h >= 1.6 || this.selected === v || near) || v.hp >= v.maxHp && this.selected !== v) continue;
      const top = v.h * v.scale + (v.fly ? 0.75 + v.h * 0.32 : 0) + 0.16, p = project(v.x, top, v.z);
      if (!p) continue;
      const w = Math.min(68, Math.max(16, 11 + v.h * 8)), frac = Math.max(0, v.hp / v.maxHp);
      c.fillStyle = "rgba(10,9,14,.8)";
      c.fillRect(p[0] - w / 2 - 1, p[1] - 3, w + 2, 5);
      c.fillStyle = frac > 0.5 ? this.css?.ok ?? "#8fb573" : frac > 0.2 ? this.css?.warn ?? "#d4a955" : this.css?.bad ?? "#b54c4c";
      c.fillRect(p[0] - w / 2, p[1] - 2, w * frac, 3);
    }
  }

  setQuality(q) {
    if (!TIERS[q]) return;
    this.quality = q;
    this.tier = TIERS[q];
    this.grassFrac = 1; this.resScale = 1;
    for (const { meshes } of this.envCache?.values() ?? []) for (const m of meshes) { this.scene.remove(m); m.geometry.dispose(); m.material.dispose?.(); }
    this.envCache = new Map();
    this.envMeshes = [];
    this.setBiome(this.biomeId);
    this.pMax = this.tier.particles;
    this.pHead = 0; this.pNew = 0;
    this.particleGeo.instanceCount = this.pMax;
    this.weatherGeo.instanceCount = this.tier.weather;
    this.finalPass.uniforms.uTaps.value = this.tier.taps;
    this.requestResize();
  }

  // Keep a playable frame rate: thin the grass first (no reallocation), then lower resolution, then the whole tier.
  adapt(dt) {
    const P = this.perf;
    P.times.push(dt);
    if (P.times.length < 90) return;
    const sorted = P.times.slice().sort((a, b) => a - b), median = sorted[sorted.length >> 1];
    P.times.length = 0;
    this.fps = 1 / median;
    // Median, not mean: one-off stalls (a biome build, a first sprite load) shouldn't cost quality for good.
    if (median <= 0.025 || this.lockRes || this.real < P.skipUntil) return;
    P.skipUntil = this.real + 1;
    if (this.grassFrac > 0.4) {
      this.grassFrac *= 0.72;
      this.grassMesh.geometry.instanceCount = Math.floor(this.grassCount * this.grassFrac);
    } else if (this.resScale > 0.6) { this.resScale *= 0.85; this.requestResize(); }
    else if (this.quality !== "low") this.setQuality(this.quality === "high" ? "medium" : "low");
  }
}
