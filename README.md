# WRP — Workflow Reliability Platform

**An out-of-band sentinel for your automations.** WRP watches your n8n, Make, and Zapier workflows — and your AI agents — for the failures that don't announce themselves.

---

## The Problem

Automation platforms are great at running workflows. They're not great at telling you when those workflows *silently* stop working — a workflow that "succeeds" but processes zero records, a scheduled job that just stops firing, an OAuth token that quietly expires and takes an entire integration down with it.

By the time you notice, it's usually because a customer, a report, or a downstream system told you first.

## What WRP Does

WRP sits outside your automation stack and watches it from the outside in three ways:

- **Active polling** — continuously pulls execution history from connected platforms (n8n today; Make/Zapier planned) and diagnoses failures using a rule engine that understands common root causes (auth failures, rate limits, infra errors) — with a catch-all safety net so nothing goes undiagnosed, even errors it hasn't seen before.
- **Heartbeat monitoring** — a dead-man's-switch pattern for jobs that fail by going *silent* rather than throwing an error. Your workflow pings a unique URL on every successful run; if the ping doesn't arrive within its grace period, WRP knows before you do.
- **Credential expiry tracking** — watches OAuth tokens and API credentials for upcoming expiry, so an integration doesn't go dark because nobody remembered to refresh a token.

When something breaks, WRP alerts you in Slack with the root cause and a suggested fix — not just "something went wrong."

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│   Next.js    │────▶│   FastAPI     │────▶│   PostgreSQL    │
│  Frontend    │     │   Backend     │     │   (Supabase)    │
└─────────────┘     └──────┬───────┘     └─────────────────┘
                            │
                     ┌──────▼───────┐     ┌─────────────────┐
                     │    Celery     │────▶│      Redis       │
                     │ Worker + Beat │     │    (Upstash)     │
                     └──────┬───────┘     └─────────────────┘
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
              n8n         Slack       Clerk
           (polling)    (alerts)      (auth)
```

**Stack**

| Layer | Technology |
|---|---|
| Backend | FastAPI, SQLAlchemy (async), asyncpg |
| Task queue | Celery + Redis (Upstash) |
| Database | PostgreSQL (Supabase, via transaction pooler) |
| Auth | Clerk (JWT verified via JWKS) |
| Alerts | Slack (Block Kit) |
| Frontend | Next.js |
| Deployment | Render |

## Getting Started

### Prerequisites

- Python 3.13+
- Node.js
- Docker Desktop (for local n8n testing)
- Accounts: Supabase, Upstash, Clerk, Slack (incoming webhook)

### Backend setup

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash)
pip install -e ".[dev]"

cp .env.example .env
# fill in DATABASE_URL (Supabase transaction pooler, port 6543),
# REDIS_URL, CLERK_SECRET_KEY, JWT_ISSUER, SLACK_WEBHOOK_URL, etc.

alembic upgrade head
```

### Running locally

You'll need four processes running concurrently:

```bash
# Terminal 1 — API
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Celery worker
celery -A app.worker.celery_app worker --loglevel=info -Q polling,beat --pool=solo

# Terminal 3 — Celery beat
celery -A app.worker.celery_app beat --loglevel=info

# Terminal 4 — Frontend
cd frontend && npm install && npm run dev
```

Visit `http://localhost:8000/health` — should report `db: ok, redis: ok`.
Visit `http://localhost:3000` — sign in via Clerk to reach the dashboard.

### Testing the n8n integration path

```bash
docker run -it --rm -p 5678:5678 docker.n8n.io/n8nio/n8n
```

Generate an API key in n8n's settings, connect it as an integration in WRP, and build a workflow designed to fail. Within one polling cycle, WRP should detect the failure and alert you in Slack.

> **Windows note:** `--pool=solo` is required for Celery on Windows; the prefork pool isn't supported.

## Project Status

Phase 1 (core detection engine, alerting pipeline, auth, and CRUD APIs) is functionally complete and covered by unit and integration tests. Currently in active manual testing ahead of Phase 2, which will expand platform support (Make, Zapier) and add LLM-assisted diagnosis for errors outside the current rule set.

## Contributing

This is currently a solo-founder project in active development. Issues and discussion are welcome; the codebase follows a "surgical changes only" philosophy — PRs that refactor unrelated code alongside a fix will be asked to split.

## License

_TBD_