# Workflow Reliability Platform - Backend

FastAPI backend with async SQLAlchemy, Celery workers, and n8n polling integration.

## Setup

```bash
# Install torch CPU-only first — plain `pip install -e .` resolves torch's
# default CUDA wheel, several GB larger than needed for local embeddings.
pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu

# Install dependencies
pip install -e .

# Set up environment
cp .env.example .env
# Edit .env with your credentials

# Run migrations
alembic upgrade head

# Start development server
uvicorn app.main:app --reload
```

## Development

```bash
# Run with Docker Compose (recommended)
docker-compose up -d
# From infrastructure folder

# Run tests
pytest

# Lint
ruff check .

# Format
ruff format .
```

## Architecture

- **FastAPI** - Async web framework
- **SQLAlchemy 2.0** - Async ORM with UUID primary keys
- **Celery** - Background task processing with Redis broker
- **PostgreSQL** - Primary database with pgvector extension
- **Redis** - Caching, rate limiting, Celery broker

## Phase 1 Features

- [x] n8n platform adapter
- [x] Rule-based diagnostic engine (AUTH_001, API_001, SILENT_001)
- [x] Heartbeat dead-man's switch
- [x] Google OAuth expiry tracking (48h warning, 6h critical)
- [x] Slack alert delivery
- [x] AES-256-GCM credential encryption