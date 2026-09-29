#!/usr/bin/env bash
# Control the cookt server, which runs as the user systemd service cookt-household.service
# (install it from deploy/cookt-household.service).
# Usage: scripts/demo.sh start|stop|restart|status|logs
set -euo pipefail
UNIT=cookt-household.service
PORT="${COOKT_PORT:-8088}"

wait_up() {
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
      echo "up: http://127.0.0.1:$PORT"; return
    fi
    sleep 0.5
  done
  echo "did not come up; see: journalctl --user -u $UNIT -n 80"; exit 1
}

case "${1:-status}" in
  start) systemctl --user start "$UNIT"; wait_up ;;
  stop) systemctl --user stop "$UNIT" && echo "stopped" ;;
  restart) systemctl --user restart "$UNIT"; wait_up ;;
  status) systemctl --user is-active "$UNIT" && curl -fsS "http://127.0.0.1:$PORT/api/health" && echo || echo "not running" ;;
  logs) journalctl --user -u "$UNIT" -n 80 --no-pager ;;
  *) echo "usage: $0 start|stop|restart|status|logs"; exit 2 ;;
esac
