#!/bin/sh
set -u
STATE=/updater
PROJECT=/project
REQ="$STATE/update.request"
CHECKREQ="$STATE/check.request"
LATEST="$STATE/latest_version"
LASTCHECK="$STATE/last_check"
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

# Docker Compose is being executed inside the updater container, but bind mount
# source paths are interpreted by the host Docker daemon. Discover the real
# host path backing /project so recreated app containers always mount the
# persistent Unraid data rather than accidental host /project/* directories.
HOST_PROJECT_DIR=$(docker inspect inventory-updater --format '{{range .Mounts}}{{if eq .Destination "/project"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
if [ -z "$HOST_PROJECT_DIR" ]; then
  log "ERROR: could not determine host project directory"
  echo "failed: host project path" > "$STATUS"
  exit 1
fi
export HOST_PROJECT_DIR
log "Host project directory: $HOST_PROJECT_DIR"

git config --global --add safe.directory "$PROJECT" >/dev/null 2>&1 || true
log "Dependencies OK: $(git --version); $(docker --version); $(docker compose version)"

check_latest(){
  echo "checking" > "$STATUS"
  cd "$PROJECT" || return 1
  if git fetch origin main >>"$LOG" 2>&1; then
    VERSION=$(git show origin/main:VERSION 2>>"$LOG" | tr -d '\r\n')
    if [ -n "$VERSION" ]; then
      printf '%s\n' "$VERSION" > "$LATEST"
      date '+%H:%M:%S' > "$LASTCHECK"
      log "Latest GitHub version is $VERSION"
      echo "idle" > "$STATUS"
      return 0
    fi
  fi
  log "ERROR: could not determine latest version from GitHub"
  echo "failed: version check" > "$STATUS"
  return 1
}

backup_databases(){
  TS="$1"
  mkdir -p "$PROJECT/backups/system"

  if [ -f "$PROJECT/data/platform.db" ]; then
    cp "$PROJECT/data/platform.db" "$PROJECT/backups/system/platform-$TS.db" || return 1
    log "Owner account database backup created"
  fi

  if [ -f "$PROJECT/data/inventory.db" ]; then
    cp "$PROJECT/data/inventory.db" "$PROJECT/backups/system/legacy-inventory-$TS.db" || return 1
    log "Legacy inventory backup created"
  fi

  if [ -d "$PROJECT/data/accounts" ]; then
    for DB in "$PROJECT"/data/accounts/*/inventory.db; do
      [ -f "$DB" ] || continue
      ACCOUNT_ID=$(basename "$(dirname "$DB")")
      DEST="$PROJECT/backups/accounts/$ACCOUNT_ID"
      mkdir -p "$DEST"
      cp "$DB" "$DEST/update-$TS.db" || return 1
      log "Inventory backup created for owner account $ACCOUNT_ID"
    done
  fi
  return 0
}

check_latest || true
log "Updater service ready"

while true; do
  if [ -f "$CHECKREQ" ]; then
    rm -f "$CHECKREQ"
    check_latest || true
  fi

  if [ -f "$REQ" ]; then
    rm -f "$REQ"
    echo "running" > "$STATUS"
    TS=$(date '+%Y%m%d-%H%M%S')
    log "Update requested"

    if backup_databases "$TS"; then
      log "Safety backups complete"
    else
      log "ERROR: database backup failed"
      echo "failed: backup" > "$STATUS"
      sleep 2
      continue
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
      if [ -f "$PROJECT/VERSION" ]; then
        tr -d '\r\n' < "$PROJECT/VERSION" > "$LATEST"
        printf '\n' >> "$LATEST"
        date '+%H:%M:%S' > "$LASTCHECK"
      fi
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

    if ! docker compose -f "$PROJECT/docker-compose.yml" build inventory-manager >>"$LOG" 2>&1; then
      log "ERROR: Inventory Manager image build failed"
      echo "failed: build" > "$STATUS"
      sleep 2
      continue
    fi
    log "Inventory Manager image built successfully"

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
      log "Inventory Manager replacement container started with persistent host bind paths"
      echo "complete" > "$STATUS"
    else
      log "ERROR: Inventory Manager replacement container failed to start"
      echo "failed: start replacement" > "$STATUS"
    fi
  fi
  sleep 3
done
