#!/usr/bin/env bash
# Consistent backup of the cookt DB (SQLite online .backup API) + image files + push key.
# Usage: scripts/backup.sh [dest_dir]   (default: data/backups)
# Keeps the newest $COOKT_BACKUP_KEEP (default 14) backups. Safe to run while the app is live.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${COOKT_DATA_DIR:-$ROOT/data}"
DEST="${1:-$DATA/backups}"
KEEP="${COOKT_BACKUP_KEEP:-14}"
STAMP="$(date +%Y%m%dT%H%M%S)"
OUT="$DEST/cookt-$STAMP"
mkdir -p "$OUT"
sqlite3 "$DATA/cookt.db" ".backup '$OUT/cookt.db'"
sqlite3 "$OUT/cookt.db" "PRAGMA integrity_check" | grep -qx ok
tar -C "$DATA" -cf "$OUT/images.tar" images
# Web Push signing key: without it every device's timer-alert subscription stops working.
if [ -f "$DATA/vapid.pem" ]; then install -m 600 "$DATA/vapid.pem" "$OUT/vapid.pem"; fi
(cd "$OUT" && sha256sum cookt.db images.tar $( [ -f vapid.pem ] && echo vapid.pem ) > SHA256SUMS)
# retention
ls -1d "$DEST"/cookt-* 2>/dev/null | sort | head -n -"$KEEP" | xargs -r rm -rf --
echo "$OUT"
