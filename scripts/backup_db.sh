#!/usr/bin/env bash
# Safe online backup of the SharpLab SQLite DB → gzipped, rotated.
# Uses sqlite3 .backup so it's consistent even while the bot/worker/web write.
# Run on the VPS (cron/systemd timer). Off-site copies are pulled separately.
set -euo pipefail

DB="${SHARPLAB_DB_PATH:-/opt/sharplab/data/sharplab.db}"
DIR="${SHARPLAB_BACKUP_DIR:-/opt/sharplab/backups}"
KEEP="${SHARPLAB_BACKUP_KEEP:-14}"

mkdir -p "$DIR"
TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="$DIR/sharplab-$TS.db"

sqlite3 "$DB" ".backup '$OUT'"
gzip -f "$OUT"

# Rotation: keep the newest $KEEP gzipped snapshots.
ls -1t "$DIR"/sharplab-*.db.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

echo "backup: $OUT.gz ($(du -h "$OUT.gz" | cut -f1)); $(ls -1 "$DIR"/sharplab-*.db.gz | wc -l) kept"
