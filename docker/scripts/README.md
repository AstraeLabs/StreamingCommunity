# VibraVid — Scripts

Helper scripts for self-hosted / NAS deployments.

---

## nas-update.sh — Automatic image updater

`nas-update.sh` watches for the sentinel file written by the in-app **"Update now"** button and performs a `docker compose pull` + container recreation automatically.

### How it works

1. The web UI calls `/api/version/update/` (POST).
2. VibraVid writes a marker file to `/app/data/.update_requested` inside the container.
3. `nas-update.sh` runs on the **host**, polls that file every 60 seconds, and runs `docker compose pull && docker compose up -d` when it appears.

This avoids giving the container access to the Docker socket.

### Configuration

Edit the variables at the top of the script, or set them as environment variables before starting:

| Variable | Default | Description |
|---|---|---|
| `VIBRAVID_COMPOSE_DIR` | `/volume1/docker/vibravid` | Directory containing `docker-compose.yml` |
| `VIBRAVID_DB_HOST_DIR` | `/volume1/docker/vibravid/db` | Host path where the `vibravid_db` volume is mounted |

### Installation

#### systemd (generic Linux)

```ini
# /etc/systemd/system/vibravid-updater.service
[Unit]
Description=VibraVid auto-updater
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/local/bin/vibravid-updater.sh
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=VIBRAVID_COMPOSE_DIR=/opt/vibravid
Environment=VIBRAVID_DB_HOST_DIR=/opt/vibravid/db

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp docker/scripts/nas-update.sh /usr/local/bin/vibravid-updater.sh
sudo chmod +x /usr/local/bin/vibravid-updater.sh
sudo systemctl daemon-reload
sudo systemctl enable --now vibravid-updater
```

#### Synology Scheduled Task (DSM 7)

1. In DSM, go to **Control Panel → Task Scheduler → Create → Triggered task → Boot-up**.
2. Set **User** to `root`.
3. Set **Command** to:
   ```
   VIBRAVID_COMPOSE_DIR=/volume1/docker/vibravid VIBRAVID_DB_HOST_DIR=/volume1/docker/vibravid/db /volume1/docker/vibravid/docker/scripts/nas-update.sh >> /var/log/vibravid-updater.log 2>&1
   ```
4. Click **OK**.

#### cron (any Linux)

```bash
# Add to root's crontab:
@reboot VIBRAVID_COMPOSE_DIR=/opt/vibravid VIBRAVID_DB_HOST_DIR=/opt/vibravid/db /opt/vibravid/docker/scripts/nas-update.sh >> /var/log/vibravid-updater.log 2>&1
```

### Manual update (without the script)

```bash
cd /path/to/vibravid
docker compose pull
docker compose up -d
```
