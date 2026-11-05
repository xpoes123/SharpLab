// Ready-made wars. Species by name, levels and moves legal for the species; counts tuned with scripts/tallgrass.mjs --presets.
const s = (side, name, count, level, moves, extra = {}) => ({ side, name, count, level, moves, ...extra });

export const PRESETS = [
  {
    id: "rats", title: "The Rat Tide", blurb: "A river of Rattata meets six of the strongest Pokémon ever cloned.", biome: "meadow",
    squads: [s(0, "Rattata", 212, 19), s(1, "Mewtwo", 6, 70, ["psychic", "blizzard", "recover", "thunderbolt"])],
  },
  {
    id: "ditto", title: "Ditto Protocol", blurb: "Fourteen Ditto walk up to six Mewtwo. Twenty Mewtwo walk away.", biome: "cave",
    squads: [s(0, "Ditto", 14, 40, undefined, { spacing: 5 }), s(1, "Mewtwo", 6, 70, ["psychic", "blizzard", "recover", "icebeam"])],
  },
  {
    id: "storm", title: "Rain vs. Sand", blurb: "Swift Swim sweepers against a Sand Stream fortress. Every point of weather counts.", biome: "shore",
    squads: [
      s(0, "Politoed", 7, 50, ["surf", "icebeam", "hypnosis", "raindance"], { ability: "drizzle", slot: "H" }),
      s(0, "Kingdra", 11, 50, ["surf", "dragonbreath", "icebeam", "raindance"], { ability: "swiftswim" }),
      s(0, "Omastar", 13, 50, ["surf", "ancientpower", "icebeam", "raindance"], { ability: "swiftswim" }),
      s(1, "Tyranitar", 8, 55, ["rockslide", "crunch", "earthquake", "sandstorm"], { ability: "sandstream" }),
      s(1, "Sandslash", 18, 45, ["earthquake", "slash", "swift", "sandstorm"], { ability: "sandveil" }),
      s(1, "Golem", 14, 45, ["earthquake", "rockthrow", "dynamicpunch", "sandstorm"], { ability: "sturdy" }),
    ],
  },
  {
    id: "hive", title: "The Hive", blurb: "Beedrill fill the sky against twenty fire-breathing dragons.", biome: "forest",
    squads: [s(0, "Beedrill", 134, 30, undefined, { ability: "swarm" }), s(1, "Charizard", 20, 50, ["flamethrower", "wingattack", "slash", "firespin"])],
  },
  {
    id: "mines", title: "Minefield", blurb: "Geodude one level from Graveler, and every one of them knows Self-Destruct.", biome: "cave",
    squads: [s(0, "Geodude", 120, 24), s(1, "Machamp", 40, 40, ["crosschop", "submission", "earthquake", "focusenergy"])],
  },
  {
    id: "chain", title: "Chain Lightning", blurb: "Sixty Voltorb one level from Electrode, firing Thunderbolt across a burning caldera.", biome: "ember",
    squads: [s(0, "Voltorb", 60, 29, ["thunderbolt", "swift", "rollout", "lightscreen"]), s(1, "Machamp", 132, 40, ["crosschop", "submission", "strength", "focusenergy"])],
  },
  {
    id: "goliath", title: "Goliath", blurb: "Onix is 8.8 metres of rock. Pikachu is 0.4 metres of bad decisions.", biome: "meadow",
    squads: [s(0, "Pikachu", 80, 25), s(1, "Onix", 6, 45, ["rockthrow", "earthquake", "slam", "sandstorm"])],
  },
  {
    id: "karp", title: "One Thousand Magikarp", blurb: "Thirty-one Gyarados. A thousand future Gyarados.", biome: "shore",
    squads: [s(0, "Magikarp", 1000, 19), s(1, "Gyarados", 31, 50, ["hydropump", "bite", "dragonrage", "hyperbeam"])],
  },
  {
    id: "nido", title: "The Nidoran War", blurb: "250 males, 378 females, level 15. Small stat gaps decide big wars, and everyone evolves mid-fight.", biome: "dusk",
    squads: [s(0, "Nidoran-M", 250, 15), s(1, "Nidoran-F", 378, 15)],
  },
  {
    id: "legends", title: "Birds vs. Beasts", blurb: "Articuno, Zapdos, Moltres against Raikou, Entei, Suicune. No escort.", biome: "snow",
    squads: [
      s(0, "Articuno", 1, 62, ["blizzard", "icebeam", "swift", "agility"]),
      s(0, "Zapdos", 1, 62, ["thunderbolt", "drillpeck", "thunderwave", "agility"]),
      s(0, "Moltres", 1, 62, ["flamethrower", "wingattack", "skyattack", "agility"]),
      s(1, "Raikou", 1, 60, ["thunderbolt", "bite", "reflect", "thunder"]),
      s(1, "Entei", 1, 60, ["flamethrower", "stomp", "sunnyday", "firespin"]),
      s(1, "Suicune", 1, 60, ["surf", "icebeam", "raindance", "bite"]),
    ],
  },
];

const norm = (x) => x.toLowerCase().replace(/[^a-z0-9]/g, "");

export function resolveSquads(preset, data) {
  const byName = new Map(Object.values(data.species).map((sp) => [norm(sp.name), sp.num]));
  return preset.squads.map(({ name, ...rest }, i) => {
    const num = byName.get(norm(name));
    if (!num) throw new Error(`unknown species ${name}`);
    const squad = { ...rest, num, id: i + 1 };
    if (squad.moves === undefined) delete squad.moves;
    if (squad.level === undefined) delete squad.level;
    return squad;
  });
}

// Real Red/Blue/Yellow wild encounter tables, grouped by place.
export const WILDS = [
  ["Route 1", /^kanto-route-1-/], ["Viridian Forest", /^viridian-forest/], ["Mt. Moon", /^mt-moon/], ["Route 4", /^kanto-route-4-area/],
  ["Rock Tunnel", /^rock-tunnel/], ["Diglett's Cave", /^digletts/], ["Pokémon Tower", /^pokemon-tower/], ["Routes 12-15", /^kanto-route-1[2-5]/],
  ["Safari Zone", /^kanto-safari/], ["Seafoam Islands", /^seafoam/], ["Pokémon Mansion", /^pokemon-mansion/], ["Power Plant", /^kanto-power-plant/],
  ["Victory Road", /^kanto-victory-road/], ["Cerulean Cave", /^cerulean-cave/],
].map(([name, pattern]) => ({ name, pattern }));

export function wildHorde(wild, count, data, side, seedRand = Math.random) {
  const rows = [];
  for (const [area, list] of Object.entries(data.areas)) {
    if (!wild.pattern.test(area)) continue;
    for (const r of list) if (!["gift", "npc-trade"].includes(r.method) && r.max < 100) rows.push(r);
  }
  const total = rows.reduce((t, r) => t + r.chance, 0);
  const picks = new Map();
  for (let i = 0; i < count; i++) {
    let x = seedRand() * total, row = rows[0];
    for (const r of rows) { x -= r.chance; if (x <= 0) { row = r; break; } }
    const level = row.min + Math.floor(seedRand() * (row.max - row.min + 1));
    const key = `${row.num}|${level}`;
    picks.set(key, { side, num: row.num, level, count: (picks.get(key)?.count ?? 0) + 1 });
  }
  return [...picks.values()];
}
