#!/bin/sh
# nas-update.sh — host-side VibraVid auto-updater
#
# Watches for the sentinel file written by the in-app "Update now" button and
# performs a docker compose pull + container recreation when it appears.
#
# Installation (choose one):
#
#   systemd (Linux):
#     Copy to /usr/local/bin/vibravid-updater.sh, then create a unit file:
#     See scripts/README.md for a ready-to-use unit example.
#
#   Synology Scheduled Task (DSM 7):
#     Control Panel → Task Scheduler → Create → Triggered task → Boot-up
#     User: root
#     Command: /volume1/docker/vibravid/scripts/nas-update.sh
#
#   cron (any Linux):
#     @reboot /path/to/scripts/nas-update.sh >> /var/log/vibravid-updater.log 2>&1
#
# ── Configuration ──────────────────────────────────────────────────────────────

# Absolute path to the directory that holds docker-compose.yml
COMPOSE_DIR="${VIBRAVID_COMPOSE_DIR:-/volume1/docker/vibravid}"

# Path on the HOST where the vibravid_db volume is mounted.
# The in-app updater writes the sentinel file to /app/data inside the container;
# set this to the corresponding host path so this script can find it.
DB_HOST_DIR="${VIBRAVID_DB_HOST_DIR:-/volume1/docker/vibravid/db}"

# Name of the sentinel file (must match views.trigger_update)
SENTINEL="${DB_HOST_DIR}/.update_requested"

# Lock file — prevents concurrent update runs
LOCK="/tmp/vibravid-updater.lock"

# Polling interval in seconds
INTERVAL=60

# ── Main loop ─────────────────────────────────────────────────────────────────

echo "[vibravid-updater] Started. Watching ${SENTINEL} (poll every ${INTERVAL}s)"

while true; do
    if [ -f "$SENTINEL" ]; then
        # Acquire lock
        if [ -f "$LOCK" ]; then
            echo "[vibravid-updater] Update already running, skipping."
            sleep "$INTERVAL"
            continue
        fi
        touch "$LOCK"

        echo "[vibravid-updater] Sentinel found — pulling latest image and recreating container..."
        rm -f "$SENTINEL"

        cd "$COMPOSE_DIR" || { echo "[vibravid-updater] ERROR: COMPOSE_DIR not found: $COMPOSE_DIR"; rm -f "$LOCK"; sleep "$INTERVAL"; continue; }

        docker compose pull && \
            docker compose up -d --remove-orphans && \
            echo "[vibravid-updater] Update complete." || \
            echo "[vibravid-updater] ERROR: docker compose failed."

        rm -f "$LOCK"
    fi
    sleep "$INTERVAL"
done
