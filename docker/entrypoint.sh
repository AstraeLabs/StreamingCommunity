#!/bin/sh
# Container entrypoint — handles PUID/PGID remapping, first-run Conf seeding,
# and Django migrations before handing off to the application.
set -e

# ── 1. PUID / PGID remapping ─────────────────────────────────────────────────
# If PUID or PGID is set and differs from the built-in appuser (1000), remap
# the user so that files written to host-mounted volumes are owned by the
# expected uid/gid. This is the standard pattern for rootless NAS containers.
CURRENT_UID=$(id -u appuser)
CURRENT_GID=$(id -g appuser)

if [ -n "$PUID" ] && [ "$PUID" != "$CURRENT_UID" ]; then
    usermod -u "$PUID" appuser
fi

if [ -n "$PGID" ] && [ "$PGID" != "$CURRENT_GID" ]; then
    groupmod -g "$PGID" appuser
fi

if [ -n "$PUID" ] || [ -n "$PGID" ]; then
    chown -R appuser:appuser /app/Video /app/Conf /app/data /app/logs /app/.cache 2>/dev/null || true
fi

# ── 2. First-run Conf seeding ─────────────────────────────────────────────────
# If the Conf volume is empty (first start), seed it from the defaults baked
# into the image at build time. Existing volumes are never touched.
if [ -z "$(ls -A /app/Conf 2>/dev/null)" ]; then
    echo "VibraVid: seeding /app/Conf from image defaults..."
    cp -r /app/Conf.defaults/. /app/Conf/
    chown -R appuser:appuser /app/Conf
fi

# ── 3. Migrations ─────────────────────────────────────────────────────────────
gosu appuser python GUI/manage.py migrate --noinput

# ── 4. Start server ───────────────────────────────────────────────────────────
exec gosu appuser python GUI/manage.py runserver --noreload 0.0.0.0:8000
