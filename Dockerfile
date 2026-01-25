FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Create user with docker group access
# The docker group GID on the host may vary, so we pass it at build time
ARG DOCKER_GID=999
RUN groupadd -g ${DOCKER_GID} docker || true && \
    useradd -m -G docker appuser && \
    chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "src.main"]
