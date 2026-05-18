FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nano \
    libicu-dev \
    ffmpeg \
    curl \
    unzip \
    gosu \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r appuser && \
    useradd -r -g appuser -u 1000 -m -d /home/appuser -s /bin/bash appuser

# Bento4 — official prebuilt release (x86_64 Linux)
RUN BENTO4_VERSION="1-6-0-641" && \
    BENTO4_DIR="Bento4-SDK-${BENTO4_VERSION}.x86_64-unknown-linux" && \
    curl -fsSL "https://www.bok.net/Bento4/binaries/${BENTO4_DIR}.zip" -o /tmp/bento4.zip && \
    unzip -q /tmp/bento4.zip -d /tmp/bento4 && \
    mkdir -p /home/appuser/.local/bin/binary && \
    cp /tmp/bento4/${BENTO4_DIR}/bin/mp4decrypt /home/appuser/.local/bin/binary/mp4decrypt && \
    cp /tmp/bento4/${BENTO4_DIR}/bin/mp4dump    /home/appuser/.local/bin/binary/mp4dump && \
    chmod 755 /home/appuser/.local/bin/binary/mp4decrypt \
              /home/appuser/.local/bin/binary/mp4dump && \
    rm -rf /tmp/bento4 /tmp/bento4.zip

# Shaka Packager — official prebuilt release (x86_64 Linux)
RUN SHAKA_VERSION="3.7.2" && \
    curl -fsSL "https://github.com/shaka-project/shaka-packager/releases/download/v${SHAKA_VERSION}/packager-linux-x64" \
         -o /home/appuser/.local/bin/binary/packager && \
    chmod 755 /home/appuser/.local/bin/binary/packager

# Fix ownership of the entire home directory before switching user
RUN chown -R appuser:appuser /home/appuser/.local

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY GUI/requirements.txt ./GUI/requirements.txt
RUN pip install --no-cache-dir -r GUI/requirements.txt

# Copy application code
COPY . .

# Snapshot the default Conf directory so the entrypoint can seed it on first start
# when the Conf volume is empty (e.g., fresh NAS install).
RUN cp -r /app/Conf /app/Conf.defaults

# Install entrypoint script
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create required directories and set permissions
# NOTE: the entrypoint runs as root first to handle PUID/PGID remapping,
# so we do not switch to USER appuser here.
RUN mkdir -p /app/Video /app/logs /app/data /app/.cache \
             /home/appuser/.config && \
    chown -R appuser:appuser /app /home/appuser && \
    chmod -R 755 /app /home/appuser

# Set environment variables
ENV PYTHONPATH="/app:${PYTHONPATH}" \
    HOME=/home/appuser \
    DJANGO_DB_DIR=/app/data \
    PYTHONUNBUFFERED=1

# NOTE: no VOLUME directive on purpose. The persistence strategy is declared
# exclusively in docker-compose.yml using NAMED volumes. A VOLUME directive
# here would:
#   1. Create anonymous volumes (random hash names like e93fdjvndss87...) any
#      time the image is started without an explicit -v mount for that path,
#      and those orphan volumes pile up across rebuilds.
#   2. Snapshot the directory content at build time and overlay it on startup,
#      which silently hides image updates inside long-lived volumes.
# Keeping volume management in docker-compose alone makes the setup
# predictable and easy to reset (`docker-compose down -v`).

EXPOSE 8000

# The entrypoint handles PUID/PGID remapping, first-run Conf seeding,
# and Django migrations, then execs the server as appuser via gosu.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
