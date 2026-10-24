#!/usr/bin/env bash
# Vendor the Poké Hoops engine from the TV cartridge (github.com/xpoes123/tv, games/pokehoops) into
# web/static/pokehoops/, byte-for-byte, so the web arcade, the server-side verifier and the TV all
# run the same sim. Never edit the vendored files here — change them in the TV repo and re-run this.
#   scripts/sync-pokehoops.sh [path/to/tv/games/pokehoops]   (default ~/code/tv/games/pokehoops)
#   scripts/sync-pokehoops.sh --check                          (exit 1 if the vendored copy has drifted)
set -euo pipefail

CHECK=0
if [[ "${1:-}" == "--check" ]]; then CHECK=1; shift; fi
SRC="${1:-$HOME/code/tv/games/pokehoops}"
DST="$(cd "$(dirname "$0")/.." && pwd)/web/static/pokehoops"
FILES=(engine.js ratings.js dex.js profiles.js)

[[ -f "$SRC/engine.js" ]] || { echo "no engine.js in $SRC" >&2; exit 2; }

if (( CHECK )); then
  drift=0
  for f in "${FILES[@]}"; do cmp -s "$SRC/$f" "$DST/$f" || { echo "drift: $f"; drift=1; }; done
  diff -rq "$SRC/sprites" "$DST/sprites" || drift=1
  (( drift )) && exit 1
  echo "pokehoops engine in sync with $SRC"; exit 0
fi

mkdir -p "$DST/sprites"
for f in "${FILES[@]}"; do cp "$SRC/$f" "$DST/$f"; done
rsync -a --delete "$SRC/sprites/" "$DST/sprites/"
rev="$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "$rev" > "$DST/VERSION"
echo "synced pokehoops engine from $SRC @ $rev → $DST"
