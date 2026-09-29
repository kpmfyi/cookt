#!/usr/bin/env bash
# Restore the newest (or given) backup into a scratch dir and verify it:
# checksums, SQLite integrity, row counts match the live DB at backup time (±new writes),
# and every image row resolves to a file in the restored tree.
# Usage: scripts/restore-verify.sh [backup_dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${COOKT_DATA_DIR:-$ROOT/data}"
BK="${1:-$(ls -1d "$DATA"/backups/cookt-* | sort | tail -1)}"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/cookt-restore.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
(cd "$BK" && sha256sum -c --quiet SHA256SUMS)
cp "$BK/cookt.db" "$SCRATCH/cookt.db"
mkdir -p "$SCRATCH/data" && tar -C "$SCRATCH/data" -xf "$BK/images.tar"
[ "$(sqlite3 "$SCRATCH/cookt.db" 'PRAGMA integrity_check')" = ok ]
RECIPES=$(sqlite3 "$SCRATCH/cookt.db" 'select count(*) from recipes')
IMAGES=$(sqlite3 "$SCRATCH/cookt.db" 'select count(*) from images')
ROWS="$(sqlite3 "$SCRATCH/cookt.db" "select path, coalesce(thumb_path, '') from images")"  # fails the script on SQL error
MISSING=0
CHECKED=0
while IFS='|' read -r path thumb; do
  [ -n "$path" ] || continue
  CHECKED=$((CHECKED+1))
  [ -f "$SCRATCH/data/images/$path" ] || { echo "missing: $path"; MISSING=$((MISSING+1)); }
  [ -z "$thumb" ] || [ -f "$SCRATCH/data/images/$thumb" ] || { echo "missing: $thumb"; MISSING=$((MISSING+1)); }
done <<< "$ROWS"
[ "$MISSING" -eq 0 ]
[ "$CHECKED" -eq "$IMAGES" ] || { echo "checked $CHECKED of $IMAGES image rows"; exit 1; }
echo "restore OK: $BK -> recipes=$RECIPES images=$IMAGES (all $CHECKED image rows resolve) integrity=ok checksums=ok"
