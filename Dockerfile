# Pin to a specific digest in production for reproducible builds:
#   docker pull python:3.11-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim
# Then use: FROM python:3.11-slim@sha256:<digest>
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

WORKDIR /app

# Install su-exec for dropping privileges in entrypoint
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies: requirements.txt is exported from uv.lock with hashes
# (uv export --frozen --no-dev --extra ai --format requirements-txt
# --no-emit-project -o requirements.txt), so the image gets exactly the
# versions CI tested. CI fails if it drifts from the lock.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Create non-root user with Docker socket access. entrypoint.sh also reads
# the socket's group at start and joins it, so DOCKER_GID only sets a default
# (281 is Unraid's docker group).
ARG DOCKER_GID=281
RUN useradd -m appuser && \
    chown -R appuser:appuser /app && \
    if [ "$DOCKER_GID" != "0" ]; then \
        groupadd -g ${DOCKER_GID} dockersock 2>/dev/null || true; \
        usermod -aG ${DOCKER_GID} appuser; \
    fi

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Liveness: the bot touches /tmp/unraidmonitor-heartbeat from its event loop
# every 60s (src/utils/heartbeat.py); stale or missing file = unhealthy.
# Surfaces in `docker ps` and the Unraid dashboard.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import os,sys,time; sys.exit(0 if time.time() - os.stat('/tmp/unraidmonitor-heartbeat').st_mtime < 180 else 1)"]

# Container starts as root; entrypoint drops to PUID:PGID after fixing permissions
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "src.main"]
