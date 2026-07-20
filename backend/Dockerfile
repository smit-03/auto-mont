# Backend Dockerfile for WRP
# Multi-stage build for smaller image size

FROM python:3.12-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir ".[dev]" && \
    pip cache purge

# Runtime stage
FROM python:3.12-alpine AS runtime

# Install runtime dependencies
RUN apk add --no-cache \
    libpq \
    && rm -rf /var/cache/apk/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user
RUN addgroup -g 1000 wrp && \
    adduser -u 1000 -G wrp -s /bin/sh -D wrp

WORKDIR /app

# Copy application code
COPY --chown=wrp:wrp app ./app
COPY --chown=wrp:wrp alembic ./alembic
COPY --chown=wrp:wrp alembic.ini ./alembic.ini
COPY --chown=wrp:wrp pyproject.toml ./pyproject.toml

# Switch to non-root user
USER wrp

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]