# CLAUDE.md

## Core Rules

### Think First
- State assumptions before coding.
- If requirements are ambiguous, ask instead of guessing.
- Mention simpler solutions or push back if appropriate.
- If something is unclear, stop and clarify.

### Keep It Simple
- Build only what was requested.
- Avoid premature abstractions or configurability.
- Don't add impossible-case error handling.
- Prefer the smallest solution that works.

### Make Surgical Changes
- Don't refactor unrelated code.
- Match the existing style.
- Mention unrelated issues instead of fixing them.
- Remove only unused code introduced by your changes.

### Work Toward Verifiable Goals
For multi-step work:
1. Define the goal.
2. Implement.
3. Verify it works.

---

# WRP (Workflow Reliability Platform)

## Stack
FastAPI, Celery, SQLAlchemy async (asyncpg), Postgres (Supabase), Redis (Upstash), Clerk, Slack, Next.js.

Deploy: Render  
Local automation: n8n (Docker)

## Design docs

`BLUEPRINT.md` is the engineering source of truth, but it is **not in this repo** — it lives in `project_docs/`, which is gitignored. A fresh clone will not have it. Same for `DECISIONS.md`.

- If you have those files locally: the Blueprint is authoritative. When implementation and Blueprint differ, flag it and propose the Blueprint edit — don't silently change behavior either way.
- If you don't have them: don't infer design intent from the code alone. Ask.
- Anything that must survive a fresh clone belongs here, in `README.md`, or in `docs/` — not only in the Blueprint.

## Environment
- Activate venv:
  ```sh
  source .venv/Scripts/activate
  ```
- Use Supabase transaction pooler (port 6543), not the direct connection.
- asyncpg requires:
  ```python
  statement_cache_size=0
  ```
- SQLAlchemy uses `NullPool`.
- Windows Celery must run with:
  ```sh
  --pool=solo
  ```
- Beat schedule task names must exactly match registered task names.
- `ENVIRONMENT=development` enables `create_all` and `/docs`.

## Known Gotchas
- Never expose a Pydantic field named `metadata` when mapped to SQLAlchemy.
- Clerk authentication uses JWKS, not a static JWT secret.
- `JWT_ISSUER` must exactly equal the Clerk Frontend API URL.
- Workspace lookup uses Clerk IDs (`org_id` → `sub` fallback), not UUIDs.
- Always pass logged URLs through `redact_url()`.
- Detached SQLAlchemy object changes require an explicit `UPDATE` to persist.
- Declare unique constraints on the ORM model, not only in the migration. `create_all()` runs outside production, so a model-only omission gives dev and prod different schemas — and dev is the one left unprotected.
- Run async code in Celery tasks via `app.worker.loop.run_async()`, never `asyncio.run()`. The latter closes the loop the DB/Redis singletons bound to, so every task after the first fails with "Event loop is closed".
- Relationships are `lazy="raise"`. Load what an endpoint needs with `selectinload()`; do not "fix" a raise by switching it back to `selectin`.
- Create alerts via `services.alerts.record_alert()`, never `session.add(Alert(...))`. Alerts are incident-scoped — a partial unique index on `(workspace_id, dedup_key) WHERE resolved_at IS NULL` collapses repeat detections instead of writing a row per check. A dedup key must exclude anything that varies per detection (e.g. platform run id).
- Alert delivery is per-workspace via `notification_channels`. `SLACK_WEBHOOK_URL` is a development-only fallback; in production a workspace with no channel gets no delivery, by design.

## Default Mindset
- Clarify before implementing.
- Prefer minimal diffs.
- Avoid unnecessary features.
- Keep changes easy to review.
