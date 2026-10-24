#!/usr/bin/env bash
# Vendor the Poké Hoops engine from the TV cartridge (github.com/xpoes123/tv, games/pokehoops) into
# web/static/pokehoops/, byte-for-byte from a COMMITTED revision, so the web arcade, the server-side
# verifier and the TV all run the same sim. Never edit the vendored files here — change them in the
# TV repo, commit there, and re-run this. The revision lands in web/static/pokehoops/VERSION.
#   scripts/sync-pokehoops.sh [ref]          vendor TV ref (default: HEAD of the TV repo's current branch)
#   scripts/sync-pokehoops.sh --check        exit 1 if the vendored files differ from the VERSION revision
# TV_REPO overrides the TV checkout (default ~/code/tv).
set -euo pipefail

TV_REPO="${TV_REPO:-$HOME/code/tv}"
DST="$(cd "$(dirname "$0")/.." && pwd)/web/static/pokehoops"
FILES=(engine.js ratings.js dex.js profiles.js)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

extract() {  # extract <rev> → $TMP/games/pokehoops
  git -C "$TV_REPO" archive "$1" $(printf 'games/pokehoops/%s ' "${FILES[@]}") games/pokehoops/sprites | tar -x -C "$TMP"
}

if [[ "${1:-}" == "--check" ]]; then
  rev="$(cat "$DST/VERSION")"
  extract "$rev"
  drift=0
  for f in "${FILES[@]}"; do cmp -s "$TMP/games/pokehoops/$f" "$DST/$f" || { echo "drift: $f"; drift=1; }; done
  diff -rq "$TMP/games/pokehoops/sprites" "$DST/sprites" || drift=1
  (( drift )) && exit 1
  echo "vendored pokehoops engine matches tv@$rev"; exit 0
fi

rev="$(git -C "$TV_REPO" rev-parse --short "${1:-HEAD}")"
extract "$rev"
mkdir -p "$DST/sprites"
for f in "${FILES[@]}"; do cp "$TMP/games/pokehoops/$f" "$DST/$f"; done
rsync -a --delete "$TMP/games/pokehoops/sprites/" "$DST/sprites/"
echo "$rev" > "$DST/VERSION"
echo "vendored pokehoops engine from tv@$rev → $DST"
