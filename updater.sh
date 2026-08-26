#!/bin/sh
set -u
STATE=/updater
PROJECT=/project
REQ="$STATE/update.request"
LOG="$STATE/update.log"
STATUS="$STATE/status"
mkdir -p "$STATE" "$PROJECT/backups"
touch "$LOG"
echo "starting" > "$STATUS"
log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

log "Updater service starting"
if ! command -v git >/dev/null 2>&1; then
  log "ERROR: git is not installed"
  echo "failed: git missing" > "$STATUS"
  exit 1
fi
if ! docker version >/dev/null 2>&1; then
  log "ERROR: cannot talk to Docker daemon via /var/run/docker.sock"
  echo "failed: docker socket" > "$STATUS"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  log "ERROR: docker compose plugin is unavailable"
  echo "failed: compose missing" > "$STATUS"
  exit 1
fi
if [ ! -d "$PROJECT/.git" ]; then
  log "ERROR: /project is not a Git checkout"
  echo "failed: git checkout missing" > "$STATUS"
  exit 1
fi

git config --global --add safe.directory "$PROJECT" >/dev/null 2>&1 || true
log "Dependencies OK: $(git --version); $(docker --version); $(docker compose version)"
echo "idle" > "$STATUS"
log "Updater service ready"

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
        echo "failed: backup" > "$STATUS"
        sleep 2
        continue
      fi
    else
      log "No database file found yet; continuing"
    fi

    cd "$PROJECT" || {
      log "ERROR: cannot enter project directory"
      echo "failed: project directory" > "$STATUS"
      sleep 2
      continue
    }

    git config --global --add safe.directory "$PROJECT" >/dev/null 2>&1 || true
    if git fetch origin main >>"$LOG" 2>&1 && git reset --hard origin/main >>"$LOG" 2>&1; then
      log "GitHub files updated to $(git rev-parse --short HEAD)"
    else
      log "ERROR: git update failed"
      echo "failed: git update" > "$STATUS"
      sleep 2
      continue
    fi

    if docker compose -f "$PROJECT/docker-compose.yml" config >/dev/null 2>>"$LOG"; then
      log "Compose configuration validated"
    else
      log "ERROR: docker compose configuration is invalid"
      echo "failed: compose config" > "$STATUS"
      sleep 2
      continue
    fi

    # Build first while the current app keeps running.
    if ! docker compose -f "$PROJECT/docker-compose.yml" build inventory-manager >>"$LOG" 2>&1; then
      log "ERROR: Inventory Manager image build failed"
      echo "failed: build" > "$STATUS"
      sleep 2
      continue
    fi
    log "Inventory Manager image built successfully"

    # Replace only the app container. Explicit removal avoids Compose project-name
    # conflicts when the stack was originally created from a different working directory.
    if docker ps -a --format '{{.Names}}' | grep -qx 'inventory-manager'; then
      log "Removing previous inventory-manager container"
      if ! docker rm -f inventory-manager >>"$LOG" 2>&1; then
        log "ERROR: could not remove previous inventory-manager container"
        echo "failed: remove old container" > "$STATUS"
        sleep 2
        continue
      fi
    fi

    if docker compose -f "$PROJECT/docker-compose.yml" up -d --no-deps inventory-manager >>"$LOG" 2>&1; then
      log "Inventory Manager replacement container started"
      echo "complete" > "$STATUS"
    else
      log "ERROR: Inventory Manager replacement container failed to start"
      echo "failed: start replacement" > "$STATUS"
    fi
  fi
  sleep 5
done
