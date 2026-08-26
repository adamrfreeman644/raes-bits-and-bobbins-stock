#!/bin/sh
set -u
STATE=/updater
PROJECT=/project
REQ="$STATE/update.request"
LOG="$STATE/update.log"
STATUS="$STATE/status"
mkdir -p "$STATE" "$PROJECT/backups"
touch "$LOG"
echo "idle" > "$STATUS"
log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }
log "Updater service started"
while true; do
  if [ -f "$REQ" ]; then
    rm -f "$REQ"
    echo "running" > "$STATUS"
    TS=$(date '+%Y%m%d-%H%M%S')
    log "Update requested"
    if [ -f "$PROJECT/data/inventory.db" ]; then
      if cp "$PROJECT/data/inventory.db" "$PROJECT/backups/inventory-$TS.db"; then
        log "Database backup created: inventory-$TS.db"
      else
        log "ERROR: database backup failed"
        echo "failed" > "$STATUS"
        sleep 2
        continue
      fi
    else
      log "No database file found yet; continuing"
    fi
    cd "$PROJECT" || { log "ERROR: cannot enter project directory"; echo "failed" > "$STATUS"; sleep 2; continue; }
    if git fetch origin main >>"$LOG" 2>&1 && git reset --hard origin/main >>"$LOG" 2>&1; then
      log "GitHub files updated"
    else
      log "ERROR: git update failed"
      echo "failed" > "$STATUS"
      sleep 2
      continue
    fi
    if docker compose -f "$PROJECT/docker-compose.yml" up -d --build --remove-orphans >>"$LOG" 2>&1; then
      log "Docker rebuild/restart completed"
      echo "complete" > "$STATUS"
    else
      log "ERROR: Docker rebuild/restart failed"
      echo "failed" > "$STATUS"
    fi
  fi
  sleep 5
done
