# Workflow Reliability Platform — CTO Implementation Blueprint

**Version:** 1.0  
**Prepared for:** Solo Founder  
**Classification:** Internal Engineering Handbook  
**Build Philosophy:** Out-of-band SaaS sentinel. Minimum viable surface, maximum operational signal. Ship phases, not ambitions.

---

## Part 1: Research Synthesis & Strategic Decisions

### What the Research Confirms

The research establishes one central thesis with high confidence: **businesses judge workflow health by operational data integrity, not execution status codes.** A workflow that exits HTTP 200 but writes zero records, hallucinates a product listing, or silently drops a database row is a critical operational failure — and no existing tool catches it.

The research identifies **seven failure taxonomy classes** that any reliability platform must address:

1. Authentication & Credential Failures (35% of all integration failures)
2. API & Network Gateway Failures (High frequency, 429/504 dominant)
3. Workflow Logic & Orchestration Failures (infinite loops, early termination)
4. Data Quality & Schema Failures (null fields, constraint violations)
5. Infrastructure & Resource Failures (OOM kills, SQLite locks)
6. AI & Agentic Execution Failures (tool bypass, hallucination, deadlocks)
7. Silent Operational Failures (IMAP drops, cron drift, 200s with error bodies)

The research also provides a clear build directive for a solo founder: **avoid complex SDK instrumentation, build an out-of-band sentinel, and focus the MVP wedge on silent failures + credential lifelines.**

### Key Architectural Decisions (Conflicts Resolved)

**Conflict 1: Full OTel pipeline vs. polling-first approach**

The research proposes a sophisticated OTel/Kafka/ClickHouse pipeline AND an out-of-band polling sentinel. These are in tension for an MVP. Building Kafka + ClickHouse on day one is over-engineering that will kill a solo founder before the first customer.

**Decision:** Build the polling sentinel first. Design the data model to be OTel-compatible from day one. Migrate to streaming ingestion in Phase 4 only after revenue justifies it.

**Conflict 2: Neo4j graph database vs. Postgres for the Failure Knowledge Graph**

The research specifies Neo4j for the FKG. Neo4j adds operational complexity (separate managed service, higher cost, different query language) for a solo founder.

**Decision:** Implement the FKG as a property graph schema inside Postgres using JSONB columns for properties and a separate edges table. This preserves graph traversal semantics via recursive CTEs while eliminating the Neo4j dependency entirely in early phases. Migrate to a dedicated graph store at scale if traversal performance degrades.

**Conflict 3: Remediation complexity — autonomous self-correction vs. HITL-first**

The research scores autonomous self-correction as Low feasibility and High risk. HITL is High feasibility and Low risk.

**Decision:** Implement HITL as the primary remediation path for all high-risk playbooks. Autonomous remediation (retry/backoff, OAuth refresh, circuit breaker) limited to safe, idempotent operations only. Never auto-mutate production data without human approval.

---

## Part 2: Product & System Architecture

### Product Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    WORKFLOW RELIABILITY PLATFORM                      │
│                                                                       │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐  │
│  │  Sentinel Core   │   │  Diagnostic       │   │  Remediation     │  │
│  │                  │   │  Engine           │   │  Engine          │  │
│  │  • Polling       │──▶│                  │──▶│                  │  │
│  │  • Heartbeat     │   │  • Rule-Based     │   │  • Auto-Retry    │  │
│  │  • Cred Vault    │   │  • Schema Check   │   │  • OAuth Refresh │  │
│  │  • OTel Receiver │   │  • FKG Traversal  │   │  • HITL Portal   │  │
│  └─────────────────┘   │  • LLM Judge      │   │  • Dead-Letter Q │  │
│                          └──────────────────┘   └──────────────────┘  │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │               Dashboard & Alert Delivery Layer                   │  │
│  │       React SPA + Slack/Email Webhooks + HITL Approval UI       │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### System Architecture (Production Target)

```
┌───────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL SYSTEMS                               │
│   n8n Instance │ Make.com │ Zapier │ LangGraph App │ CrewAI Service   │
└───────┬──────────────┬────────────┬────────────┬───────────┬──────────┘
        │ Polling API  │ Webhook    │ API Poll   │ OTel Push │ OTel Push
        ▼              ▼            ▼            ▼           ▼
┌───────────────────────────────────────────────────────────────────────┐
│                    INGESTION GATEWAY (FastAPI)                         │
│  /api/v1/poll  │  /api/v1/ingest/otel  │  /api/v1/heartbeat/ping     │
└─────────────────────────┬─────────────────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────────────────┐
│                        TASK QUEUE (Celery + Redis)                     │
│   poll_workflow_executions │ validate_heartbeat │ run_diagnostics      │
└──────────────────────────┬────────────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
┌─────────────────┐ ┌──────────────┐ ┌──────────────────────┐
│  Sentinel Core  │ │  Diagnostic   │ │  Remediation Engine  │
│  (Python svc)   │ │  Engine       │ │  (Python svc)        │
└────────┬────────┘ └──────┬───────┘ └──────────┬───────────┘
         │                  │                     │
         ▼                  ▼                     ▼
┌───────────────────────────────────────────────────────────────────────┐
│                    POSTGRES (Primary Data Store)                       │
│  workspaces │ integrations │ executions │ alerts │ fkg_nodes │ fkg_edges│
└───────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────┐
│              REDIS (Cache + Celery Broker + Rate Limiting)             │
└───────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────┐
│              NOTIFICATION DELIVERY                                      │
│   Slack Webhooks │ Email (Resend) │ PagerDuty │ HITL Approval Portal  │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Part 3: Technology Stack Decisions

All decisions are final for Phase 1–3. Revisit only with explicit justification.

### Backend

| Component | Choice | Reason |
|-----------|--------|--------|
| Language | Python 3.12 | Async support, rich ecosystem for data/AI work, solo-founder familiar |
| Web Framework | FastAPI | Native async, auto-generated OpenAPI docs, Pydantic integration |
| Task Queue | Celery 5 + Redis | Proven, battle-tested for polling loops and async job dispatch |
| ORM | SQLAlchemy 2.0 (async) | Async-native, raw SQL escape hatches for complex FKG queries |
| Migrations | Alembic | Standard SQLAlchemy migration tool |
| Validation | Pydantic v2 | Schema validation, auto-serialization of all API I/O |
| HTTP Client | httpx (async) | Async-native, supports both sync and async, good timeout controls |

### Frontend

| Component | Choice | Reason |
|-----------|--------|--------|
| Framework | Next.js 15 (App Router) | React Server Components reduce client bundle; API routes available |
| Styling | Tailwind CSS v4 | Utility-first, no design system overhead |
| State Management | Zustand | Minimal boilerplate for solo founder |
| Charts | Recharts | Lightweight, no license issues |
| API Client | TanStack Query | Server state caching, background refetch for live dashboards |

### Infrastructure

| Component | Choice | Reason |
|-----------|--------|--------|
| Primary DB | Postgres 16 (Supabase managed) | JSONB for flexible payloads, FKG via CTEs, managed backups |
| Cache/Broker | Redis (Upstash serverless) | Celery broker + rate limiting; serverless scales to zero |
| Hosting | Render (initial) | Free tier: 750 hrs/month for web services, native background worker support, Docker deploys, predictable costs for solo founder |
| Object Storage | Cloudflare R2 | Cheap trace payload archival; S3-compatible |
| CDN/Edge | Cloudflare | Free tier sufficient for Phase 1–2 |
| Email | Resend | Modern API, generous free tier, excellent deliverability |
| Auth | Clerk | Managed auth with org/team support; maps to multi-tenant B2B |

### AI/LLM

| Component | Choice | Reason |
|-----------|--------|--------|
| LLM Judge | Claude claude-sonnet-4-6 | Best reasoning for structured diagnostic output; cost-effective |
| Embedding | OpenAI text-embedding-3-small | Low cost, high quality for FKG similarity searches |
| Framework | Direct API calls (no LangChain) | Avoids abstraction overhead; diagnostic prompts are deterministic |

---

## Part 4: Project Structure

```
wrp/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI application factory
│   │   ├── config.py                  # Pydantic Settings (env vars)
│   │   ├── database.py                # Async SQLAlchemy engine + session factory
│   │   │
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── deps.py                # Dependency injection (auth, db session)
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── router.py          # Mounts all v1 routes
│   │   │   │   ├── workspaces.py      # Workspace CRUD
│   │   │   │   ├── integrations.py    # Platform connection management
│   │   │   │   ├── monitors.py        # Heartbeat monitor config
│   │   │   │   ├── alerts.py          # Alert history + acknowledge
│   │   │   │   ├── executions.py      # Execution trace query
│   │   │   │   ├── credentials.py     # OAuth credential vault
│   │   │   │   ├── diagnostics.py     # Run diagnostic on execution
│   │   │   │   ├── ingest.py          # OTel ingestion endpoint
│   │   │   │   └── hitl.py            # Human-in-the-loop approval
│   │   │
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── workspace.py
│   │   │   ├── integration.py
│   │   │   ├── execution.py
│   │   │   ├── alert.py
│   │   │   ├── credential.py
│   │   │   ├── monitor.py
│   │   │   ├── fkg.py                 # FKG node + edge models
│   │   │   └── hitl.py
│   │   │
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── workspace.py           # Pydantic request/response schemas
│   │   │   ├── integration.py
│   │   │   ├── execution.py
│   │   │   ├── alert.py
│   │   │   ├── credential.py
│   │   │   ├── monitor.py
│   │   │   ├── otel.py                # OTel trace payload schema
│   │   │   └── hitl.py
│   │   │
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── sentinel/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── poller.py          # Platform API polling orchestrator
│   │   │   │   ├── n8n_adapter.py     # n8n API client + normalizer
│   │   │   │   ├── make_adapter.py    # Make.com API client + normalizer
│   │   │   │   ├── zapier_adapter.py  # Zapier API client + normalizer
│   │   │   │   └── base_adapter.py    # Abstract adapter interface
│   │   │   │
│   │   │   ├── heartbeat/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── engine.py          # Heartbeat validation engine
│   │   │   │   └── scheduler.py       # Dead-man's switch scheduler
│   │   │   │
│   │   │   ├── credential_vault/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── vault.py           # OAuth token lifecycle manager
│   │   │   │   ├── google.py          # Google OAuth monitor
│   │   │   │   └── microsoft.py       # Microsoft OAuth monitor
│   │   │   │
│   │   │   ├── diagnostic/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── engine.py          # Multi-tier diagnostic orchestrator
│   │   │   │   ├── rule_engine.py     # Static rule matching
│   │   │   │   ├── schema_checker.py  # JSON schema drift detection
│   │   │   │   ├── fkg_traversal.py   # FKG graph walk for root cause
│   │   │   │   └── llm_judge.py       # Claude diagnostic fallback
│   │   │   │
│   │   │   ├── remediation/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── playbook.py        # Remediation playbook dispatcher
│   │   │   │   ├── retry.py           # Jittered exponential backoff
│   │   │   │   ├── circuit_breaker.py # Circuit breaker state machine
│   │   │   │   ├── oauth_refresh.py   # Automated token refresh
│   │   │   │   └── dead_letter.py     # Dead-letter queue management
│   │   │   │
│   │   │   └── notifications/
│   │   │       ├── __init__.py
│   │   │       ├── dispatcher.py      # Routes alerts to correct channel
│   │   │       ├── slack.py           # Slack webhook + Block Kit
│   │   │       └── email.py           # Resend email delivery
│   │   │
│   │   ├── worker/
│   │   │   ├── __init__.py
│   │   │   ├── celery_app.py          # Celery application factory
│   │   │   └── tasks/
│   │   │       ├── __init__.py
│   │   │       ├── polling.py         # Periodic polling tasks
│   │   │       ├── heartbeat.py       # Heartbeat check tasks
│   │   │       ├── credential.py      # Credential expiry check tasks
│   │   │       └── diagnostic.py      # Async diagnostic dispatch
│   │   │
│   │   └── core/
│   │       ├── __init__.py
│   │       ├── security.py            # API key hashing, JWT validation
│   │       ├── encryption.py          # AES-256 for credential storage
│   │       └── exceptions.py          # Custom exception hierarchy
│   │
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/                  # Migration files
│   │
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── conftest.py
│   │
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── .env.example
│
├── frontend/
│   ├── app/
│   │   ├── (auth)/
│   │   │   ├── sign-in/page.tsx
│   │   │   └── sign-up/page.tsx
│   │   ├── (dashboard)/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx               # Overview: active monitors, recent alerts
│   │   │   ├── monitors/
│   │   │   │   ├── page.tsx           # Monitor list
│   │   │   │   └── [id]/page.tsx      # Monitor detail + heartbeat history
│   │   │   ├── integrations/
│   │   │   │   ├── page.tsx           # Connected platforms
│   │   │   │   └── new/page.tsx       # Integration setup wizard
│   │   │   ├── credentials/
│   │   │   │   └── page.tsx           # OAuth vault status
│   │   │   ├── alerts/
│   │   │   │   ├── page.tsx           # Alert feed
│   │   │   │   └── [id]/page.tsx      # Alert detail + diagnostic output
│   │   │   └── hitl/
│   │   │       └── [id]/page.tsx      # HITL approval interface
│   │   └── api/                       # Next.js API routes (thin proxies to backend)
│   │
│   ├── components/
│   │   ├── ui/                        # shadcn/ui base components
│   │   ├── monitors/
│   │   ├── alerts/
│   │   ├── credentials/
│   │   └── hitl/
│   │
│   ├── lib/
│   │   ├── api.ts                     # TanStack Query hooks
│   │   └── utils.ts
│   │
│   └── package.json
│
├── infrastructure/
│   ├── docker-compose.yml             # Local dev: postgres, redis, backend, worker
│   └── render.yaml                    # Render deployment config
│
└── docs/
    ├── api.md
    └── runbooks/                      # Operational runbooks per failure type
```

---

## Part 5: Database Design

### Schema Design Principles

- All tables use UUID primary keys (v7, time-sortable)
- Soft deletes everywhere (`deleted_at` timestamp)
- Row-level security enforced at application layer via `workspace_id`
- Credentials never stored in plaintext — AES-256-GCM with per-tenant key
- JSONB for flexible payload storage with GIN indexes on hot query paths

### Core Tables

```sql
-- Multi-tenant workspace
CREATE TABLE workspaces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    plan            TEXT NOT NULL DEFAULT 'developer', -- developer | growth | enterprise
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

-- Platform integrations (n8n, Make, Zapier, LangGraph)
CREATE TABLE integrations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id),
    platform        TEXT NOT NULL, -- n8n | make | zapier | langgraph | crewai | custom
    display_name    TEXT NOT NULL,
    base_url        TEXT,          -- For self-hosted n8n instances
    api_key_enc     BYTEA,         -- AES-256-GCM encrypted
    api_key_hint    TEXT,          -- Last 4 chars for UI display
    polling_enabled BOOLEAN NOT NULL DEFAULT true,
    poll_interval_s INT NOT NULL DEFAULT 60,
    last_polled_at  TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'active', -- active | paused | error
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_integrations_workspace ON integrations(workspace_id)
    WHERE deleted_at IS NULL;

-- Normalized execution records from all platforms
CREATE TABLE executions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id),
    integration_id  UUID NOT NULL REFERENCES integrations(id),
    platform        TEXT NOT NULL,
    platform_run_id TEXT NOT NULL,   -- Native ID from the source platform
    workflow_id     TEXT NOT NULL,
    workflow_name   TEXT,
    status          TEXT NOT NULL,   -- success | error | running | timeout | silent_fail
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    duration_ms     INT,
    node_count      INT,
    items_processed INT,
    error_message   TEXT,
    error_node      TEXT,
    raw_payload     JSONB NOT NULL,  -- Full platform response, compressed
    outcome_valid   BOOLEAN,         -- Business outcome validation result
    outcome_reason  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (integration_id, platform_run_id)
);

CREATE INDEX idx_executions_workspace_created ON executions(workspace_id, created_at DESC);
CREATE INDEX idx_executions_integration ON executions(integration_id, created_at DESC);
CREATE INDEX idx_executions_status ON executions(workspace_id, status) WHERE deleted_at IS NULL;
CREATE INDEX idx_executions_payload ON executions USING GIN(raw_payload);

-- Heartbeat / dead-man's switch monitors
CREATE TABLE monitors (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        UUID NOT NULL REFERENCES workspaces(id),
    integration_id      UUID REFERENCES integrations(id),
    name                TEXT NOT NULL,
    description         TEXT,
    monitor_type        TEXT NOT NULL, -- heartbeat | outcome | cron | schema
    ping_url            TEXT UNIQUE,   -- Public URL for heartbeat pings
    expected_cron       TEXT,          -- Cron expression for expected schedule
    grace_period_s      INT NOT NULL DEFAULT 300,
    expected_outcome    JSONB,         -- Outcome assertion (e.g., min_records, field_exists)
    last_ping_at        TIMESTAMPTZ,
    last_status         TEXT NOT NULL DEFAULT 'pending', -- healthy | degraded | failing
    alert_on_miss       BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);

-- Credential OAuth lifecycle tracking
CREATE TABLE credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id),
    integration_id  UUID REFERENCES integrations(id),
    provider        TEXT NOT NULL,    -- google | microsoft | github | slack | etc.
    display_name    TEXT NOT NULL,
    token_enc       BYTEA,            -- Encrypted access token
    refresh_enc     BYTEA,            -- Encrypted refresh token
    expires_at      TIMESTAMPTZ,
    scopes          TEXT[],
    gcp_status      TEXT,             -- testing | production (GCP-specific)
    last_verified_at TIMESTAMPTZ,
    last_error      TEXT,
    status          TEXT NOT NULL DEFAULT 'active', -- active | expiring | expired | error
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_credentials_expiry ON credentials(workspace_id, expires_at)
    WHERE deleted_at IS NULL AND status != 'expired';

-- Alerts generated by diagnostic engine
CREATE TABLE alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id),
    integration_id  UUID REFERENCES integrations(id),
    execution_id    UUID REFERENCES executions(id),
    monitor_id      UUID REFERENCES monitors(id),
    credential_id   UUID REFERENCES credentials(id),
    severity        TEXT NOT NULL,   -- info | warning | critical
    category        TEXT NOT NULL,   -- auth | api | logic | schema | infra | ai | silent
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    root_cause      TEXT,
    suggested_fix   TEXT,
    auto_remediated BOOLEAN NOT NULL DEFAULT false,
    remediation_log JSONB,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_alerts_workspace_created ON alerts(workspace_id, created_at DESC);
CREATE INDEX idx_alerts_unresolved ON alerts(workspace_id, severity)
    WHERE resolved_at IS NULL;

-- Human-in-the-loop approval queue
CREATE TABLE hitl_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id),
    alert_id        UUID REFERENCES alerts(id),
    execution_id    UUID REFERENCES executions(id),
    state_snapshot  JSONB NOT NULL,   -- Execution state at point of pause
    proposed_fix    JSONB,            -- Suggested state modifications
    slack_message_ts TEXT,            -- Slack message timestamp for updates
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected | expired
    reviewer        TEXT,
    review_note     TEXT,
    decision_at     TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Failure Knowledge Graph: nodes
CREATE TABLE fkg_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID,             -- NULL = global knowledge
    node_type       TEXT NOT NULL,    -- FailureInstance | ErrorSignature | RootCause |
                                      -- SystemComponent | RemediationWorkflow | PreventionPolicy
    label           TEXT NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}',
    embedding       vector(1536),     -- For semantic similarity search
    confidence      FLOAT NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_fkg_nodes_type ON fkg_nodes(node_type);
CREATE INDEX idx_fkg_nodes_props ON fkg_nodes USING GIN(properties);
-- Requires pgvector extension:
CREATE INDEX idx_fkg_nodes_embedding ON fkg_nodes USING ivfflat(embedding vector_cosine_ops);

-- Failure Knowledge Graph: edges
CREATE TABLE fkg_edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES fkg_nodes(id),
    target_id       UUID NOT NULL REFERENCES fkg_nodes(id),
    edge_type       TEXT NOT NULL,    -- HAS_SIGNATURE | EXPLAINS | OCCURRED_IN |
                                      -- RESOLVED_BY | PREVENTED_BY
    properties      JSONB NOT NULL DEFAULT '{}',
    weight          FLOAT NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_fkg_edges_source ON fkg_edges(source_id, edge_type);
CREATE INDEX idx_fkg_edges_target ON fkg_edges(target_id, edge_type);

-- Notification delivery log
CREATE TABLE notification_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL REFERENCES workspaces(id),
    alert_id        UUID REFERENCES alerts(id),
    channel         TEXT NOT NULL,   -- slack | email | pagerduty
    recipient       TEXT,
    payload         JSONB,
    status          TEXT NOT NULL,   -- sent | failed | suppressed
    error           TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## Part 6: API Design

### API Conventions

- Base path: `/api/v1/`
- Authentication: Bearer token (Clerk JWT) + API key (for integrations)
- Response envelope: `{ data: T, meta?: {...}, error?: {...} }`
- Errors: RFC 7807 Problem Details format
- Pagination: Cursor-based (`?after=<cursor>&limit=50`)
- Versioning: URI path versioning

### Core Endpoints

```
# Workspace & Auth
POST   /api/v1/workspaces                    # Create workspace
GET    /api/v1/workspaces/me                 # Current workspace

# Integrations
GET    /api/v1/integrations                  # List integrations
POST   /api/v1/integrations                  # Connect new platform
GET    /api/v1/integrations/{id}             # Integration detail
PATCH  /api/v1/integrations/{id}             # Update (pause, rename)
DELETE /api/v1/integrations/{id}             # Disconnect
POST   /api/v1/integrations/{id}/test        # Test connectivity
POST   /api/v1/integrations/{id}/poll        # Trigger manual poll

# Executions
GET    /api/v1/executions                    # List executions (paginated)
GET    /api/v1/executions/{id}               # Execution detail + trace
GET    /api/v1/executions/{id}/diagnostic    # Get or trigger diagnostic

# Monitors (Heartbeat)
GET    /api/v1/monitors                      # List monitors
POST   /api/v1/monitors                      # Create monitor
GET    /api/v1/monitors/{id}                 # Monitor + ping history
PATCH  /api/v1/monitors/{id}                 # Update config
DELETE /api/v1/monitors/{id}
GET    /api/v1/monitors/{id}/status          # Live status

# Public heartbeat ping endpoint (no auth)
POST   /ping/{ping_token}                    # Receive heartbeat ping

# Credential Vault
GET    /api/v1/credentials                   # List tracked credentials
POST   /api/v1/credentials                   # Register credential
GET    /api/v1/credentials/{id}              # Credential status detail
DELETE /api/v1/credentials/{id}
POST   /api/v1/credentials/{id}/refresh      # Trigger manual refresh

# Alerts
GET    /api/v1/alerts                        # Alert feed (filterable)
GET    /api/v1/alerts/{id}                   # Alert detail
POST   /api/v1/alerts/{id}/acknowledge       # Acknowledge alert
POST   /api/v1/alerts/{id}/resolve           # Mark resolved

# HITL
GET    /api/v1/hitl                          # Pending HITL requests
GET    /api/v1/hitl/{id}                     # HITL detail
POST   /api/v1/hitl/{id}/approve             # Approve and resume
POST   /api/v1/hitl/{id}/reject              # Reject and dead-letter
PATCH  /api/v1/hitl/{id}/state               # Modify state then approve

# Ingest (OTel / Custom SDK)
POST   /api/v1/ingest/otel                   # OTel OTLP trace ingestion
POST   /api/v1/ingest/event                  # Custom event ingestion

# Notification Channels
GET    /api/v1/channels                      # List configured channels
POST   /api/v1/channels                      # Add Slack/email/PagerDuty
DELETE /api/v1/channels/{id}
POST   /api/v1/channels/{id}/test            # Send test notification
```

---

## Part 7: Service Boundaries & Key Implementations

### 7.1 Platform Adapter Interface

Every platform integration must conform to this abstract interface:

```python
# backend/app/services/sentinel/base_adapter.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator
from datetime import datetime

@dataclass
class NormalizedExecution:
    platform_run_id: str
    workflow_id: str
    workflow_name: str | None
    status: str           # success | error | running | timeout
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    node_count: int | None
    items_processed: int | None
    error_message: str | None
    error_node: str | None
    raw_payload: dict

class BaseAdapter(ABC):
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    @abstractmethod
    async def test_connectivity(self) -> bool:
        """Verify API key is valid and platform is reachable."""

    @abstractmethod
    async def list_recent_executions(
        self,
        since: datetime,
        limit: int = 100
    ) -> AsyncGenerator[NormalizedExecution, None]:
        """Yield normalized executions since the given timestamp."""

    @abstractmethod
    async def get_execution_detail(
        self,
        platform_run_id: str
    ) -> NormalizedExecution:
        """Fetch full execution detail including node-level data."""

    @abstractmethod
    async def list_workflow_credentials(self) -> list[dict]:
        """List connected OAuth credentials with metadata."""

    def normalize_status(self, platform_status: str) -> str:
        """Subclasses implement platform-specific status mapping."""
        raise NotImplementedError
```

### 7.2 Diagnostic Engine (Multi-Tier)

```python
# backend/app/services/diagnostic/engine.py

from enum import Enum
from dataclasses import dataclass

class DiagnosticTier(Enum):
    RULE_BASED = "rule_based"
    SCHEMA_CHECK = "schema_check"
    FKG_TRAVERSAL = "fkg_traversal"
    HISTORICAL_BASELINE = "historical_baseline"
    LLM_JUDGE = "llm_judge"

@dataclass
class DiagnosticResult:
    root_cause: str
    category: str
    confidence: float
    suggested_fix: str
    tier_used: DiagnosticTier
    remediation_playbook: str | None
    requires_hitl: bool

class DiagnosticEngine:
    """
    Implements the multi-tier diagnostic strategy from the research.
    
    Tier ordering (fast → expensive):
    1. Rule-based: Static error signature matching (target: <50ms)
    2. Schema checker: JSON schema drift detection (target: <100ms)
    3. FKG traversal: Graph walk on failure knowledge graph (target: <200ms)
    4. Historical baseline: Anomaly vs rolling baseline (target: <300ms)
    5. LLM Judge: Claude fallback for novel failures (target: <5s, cost-gated)
    """

    async def diagnose(
        self,
        execution: dict,
        workspace_id: str
    ) -> DiagnosticResult:
        # Tier 1: Fast path for known error signatures
        result = await self._rule_based_diagnosis(execution)
        if result and result.confidence >= 0.9:
            return result

        # Tier 2: Schema validation drift
        result = await self._schema_check(execution)
        if result and result.confidence >= 0.85:
            return result

        # Tier 3: FKG graph traversal
        result = await self._fkg_traversal(execution, workspace_id)
        if result and result.confidence >= 0.80:
            return result

        # Tier 4: Baseline anomaly detection
        result = await self._baseline_check(execution, workspace_id)
        if result and result.confidence >= 0.75:
            return result

        # Tier 5: LLM Judge (gated by plan tier + daily budget)
        if await self._llm_judge_available(workspace_id):
            return await self._llm_judge(execution)

        # Fallback: human review
        return DiagnosticResult(
            root_cause="Unable to determine root cause automatically",
            category="unknown",
            confidence=0.0,
            suggested_fix="Please review execution logs manually.",
            tier_used=DiagnosticTier.RULE_BASED,
            remediation_playbook=None,
            requires_hitl=True
        )
```

### 7.3 Rule Engine Signature Map

```python
# backend/app/services/diagnostic/rule_engine.py

# Directly derived from the research taxonomy
SIGNATURE_RULES = [
    # Category: Authentication
    {
        "id": "AUTH_001",
        "pattern": "invalid_grant",
        "field": "error_message",
        "match": "contains",
        "category": "auth",
        "root_cause": "OAuth refresh token expired or revoked",
        "confidence": 0.97,
        "suggested_fix": (
            "The OAuth refresh token has been invalidated. This typically occurs when "
            "the GCP app is in 'Testing' mode (7-day token expiry), when the user changes "
            "their password, or when tokens are manually revoked. Re-authenticate the "
            "credential and consider migrating to a Google Service Account."
        ),
        "remediation_playbook": "oauth_refresh",
        "requires_hitl": False,
    },
    {
        "id": "AUTH_002",
        "pattern": "401",
        "field": "error_message",
        "match": "contains",
        "category": "auth",
        "root_cause": "API authentication failed — key invalid or revoked",
        "confidence": 0.90,
        "suggested_fix": "Verify the API key is still active and has not been rotated.",
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # Category: Rate Limiting
    {
        "id": "API_001",
        "pattern": "429",
        "field": "error_message",
        "match": "contains",
        "category": "api",
        "root_cause": "API rate limit exceeded — too many concurrent requests",
        "confidence": 0.95,
        "suggested_fix": (
            "Reduce parallel execution concurrency in your workflow. Add delay nodes "
            "between API calls in loops. Consider upgrading your API tier."
        ),
        "remediation_playbook": "retry_backoff",
        "requires_hitl": False,
    },
    {
        "id": "API_002",
        "pattern": "504",
        "field": "error_message",
        "match": "contains",
        "category": "api",
        "root_cause": "Gateway timeout — upstream LLM or API exceeded timeout window",
        "confidence": 0.90,
        "suggested_fix": (
            "The upstream API took too long to respond. For LLMs, reduce context length "
            "or switch to a faster model variant. Increase the node timeout setting "
            "or implement streaming responses."
        ),
        "remediation_playbook": "retry_backoff",
        "requires_hitl": False,
    },
    # Category: Schema/Data
    {
        "id": "SCHEMA_001",
        "pattern": "violates not-null constraint",
        "field": "error_message",
        "match": "contains",
        "category": "schema",
        "root_cause": "Database insert failed — required field is null",
        "confidence": 0.95,
        "suggested_fix": (
            "A field required by your database schema is arriving as null. "
            "Add a defensive null-check node before the database insert node. "
            "Inspect the upstream data source for missing or renamed fields."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # Category: Infrastructure
    {
        "id": "INFRA_001",
        "pattern": "out of memory",
        "field": "error_message",
        "match": "contains_lower",
        "category": "infra",
        "root_cause": "Container OOM kill — memory limit exceeded during execution",
        "confidence": 0.92,
        "suggested_fix": (
            "The execution consumed more RAM than the container limit. "
            "Split this workflow into smaller sub-workflows. Avoid loading "
            "large datasets into memory — use streaming or batched processing. "
            "Consider increasing the container memory allocation."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    {
        "id": "INFRA_002",
        "pattern": "database is locked",
        "field": "error_message",
        "match": "contains_lower",
        "category": "infra",
        "root_cause": "SQLite write lock under concurrent access",
        "confidence": 0.95,
        "suggested_fix": (
            "SQLite does not support concurrent writers. Migrate to Postgres for "
            "production workloads with concurrent executions."
        ),
        "remediation_playbook": None,
        "requires_hitl": True,
    },
    # Category: Silent Failure (items_processed == 0 with status success)
    {
        "id": "SILENT_001",
        "pattern": None,
        "field": "items_processed",
        "match": "equals_zero_on_success",
        "category": "silent",
        "root_cause": "Silent operational failure — execution succeeded but processed zero records",
        "confidence": 0.85,
        "suggested_fix": (
            "The workflow reported success but processed no items. This may indicate: "
            "an empty trigger (IMAP socket silently disconnected, webhook not receiving), "
            "upstream data source returned empty result, or a filter condition blocking all records."
        ),
        "remediation_playbook": "heartbeat_alert",
        "requires_hitl": True,
    },
]
```

### 7.4 Heartbeat Engine

```python
# backend/app/services/heartbeat/engine.py

from datetime import datetime, timedelta
from app.models.monitor import Monitor
from app.models.alert import Alert

class HeartbeatEngine:
    """
    Dead-man's switch implementation.
    
    The engine checks: did the expected event fire within the grace period?
    If not, it generates a critical alert. This catches the IMAP silent drop,
    cron drift, and trigger disconnection failures identified in the research.
    """

    async def check_monitor(self, monitor: Monitor) -> Alert | None:
        if monitor.monitor_type == "heartbeat":
            return await self._check_heartbeat(monitor)
        elif monitor.monitor_type == "cron":
            return await self._check_cron_schedule(monitor)
        elif monitor.monitor_type == "outcome":
            return await self._check_outcome_assertion(monitor)
        elif monitor.monitor_type == "schema":
            return await self._check_schema_stability(monitor)
        return None

    async def _check_heartbeat(self, monitor: Monitor) -> Alert | None:
        deadline = monitor.last_ping_at + timedelta(
            seconds=monitor.grace_period_s
        ) if monitor.last_ping_at else None

        if deadline is None or datetime.utcnow() > deadline:
            return Alert(
                workspace_id=monitor.workspace_id,
                monitor_id=monitor.id,
                severity="critical",
                category="silent",
                title=f"Heartbeat missed: {monitor.name}",
                description=(
                    f"No ping received for monitor '{monitor.name}' within the "
                    f"{monitor.grace_period_s}s grace period. Last ping: "
                    f"{monitor.last_ping_at or 'never'}."
                ),
                root_cause=(
                    "The workflow or process monitoring this trigger has not executed "
                    "within the expected window. Possible causes: IMAP trigger silently "
                    "disconnected, cron schedule drifted, container restarted, or "
                    "workflow was manually paused."
                ),
                suggested_fix=(
                    "Check your workflow is active and the trigger is connected. "
                    "For IMAP triggers, verify the TCP socket is alive. "
                    "Review recent infrastructure events."
                ),
            )
        return None
```

### 7.5 OAuth Credential Vault

```python
# backend/app/services/credential_vault/vault.py

from datetime import datetime, timedelta

EXPIRY_WARNING_HOURS = 48  # Alert 48 hours before expiry (research: weekly rotation risk)
CRITICAL_HOURS = 6          # Critical alert 6 hours before expiry

class CredentialVault:
    """
    Actively monitors OAuth token lifecycle.
    
    Research finding: Google Cloud apps in 'Testing' mode expire tokens every 7 days.
    Gmail password changes immediately invalidate OAuth sessions.
    Firebase tokens expire with invalid_grant.
    
    This vault catches these before they cause production failures.
    """

    async def check_credential(self, credential) -> list[dict]:
        alerts = []
        if not credential.expires_at:
            return alerts

        now = datetime.utcnow()
        hours_until_expiry = (credential.expires_at - now).total_seconds() / 3600

        if hours_until_expiry <= 0:
            alerts.append({
                "severity": "critical",
                "title": f"Credential expired: {credential.display_name}",
                "category": "auth",
                "description": (
                    f"OAuth token for {credential.provider} ({credential.display_name}) "
                    f"expired at {credential.expires_at}. All workflows using this "
                    f"credential are now failing."
                ),
                "suggested_fix": self._get_provider_fix(credential.provider),
            })
        elif hours_until_expiry <= CRITICAL_HOURS:
            alerts.append({
                "severity": "critical",
                "title": f"Credential expiring in {hours_until_expiry:.1f}h: {credential.display_name}",
                "category": "auth",
            })
        elif hours_until_expiry <= EXPIRY_WARNING_HOURS:
            alerts.append({
                "severity": "warning",
                "title": f"Credential expiring soon: {credential.display_name}",
                "category": "auth",
            })

        # GCP-specific check (research: Testing mode causes weekly expiry)
        if credential.provider == "google" and credential.gcp_status == "testing":
            alerts.append({
                "severity": "warning",
                "title": f"GCP app in Testing mode: {credential.display_name}",
                "category": "auth",
                "description": (
                    "Your Google Cloud OAuth app is in 'Testing' mode. "
                    "This causes refresh tokens to expire every 7 days, "
                    "regardless of the token's stated expiry. Publish your "
                    "app or migrate to a Service Account to fix this."
                ),
            })

        return alerts

    def _get_provider_fix(self, provider: str) -> str:
        fixes = {
            "google": (
                "Re-authenticate the Google credential. If this expires weekly, "
                "publish your GCP OAuth app or migrate to a Service Account."
            ),
            "microsoft": "Re-authenticate the Microsoft credential via OAuth flow.",
            "github": "Regenerate the GitHub personal access token or reinstall the OAuth app.",
        }
        return fixes.get(provider, "Re-authenticate the credential in your integration settings.")
```

---

## Part 8: Security Architecture

### Authentication & Authorization

- **User auth:** Clerk (JWT-based, org/team support for future multi-seat)
- **API keys:** For platform integrations calling `/ingest/*` and `/ping/*` endpoints
  - Format: `wrp_live_{32-char-random}` (live) / `wrp_test_{32-char-random}` (test)
  - Stored as SHA-256 hash in DB; never stored plaintext
  - Rate-limited per key: 1,000 req/min (enforced in Redis)
- **Row-level isolation:** Every DB query includes `WHERE workspace_id = :current_workspace_id`
- **Middleware:** FastAPI dependency `get_current_workspace()` enforces this on every authenticated route

### Credential Storage

```python
# backend/app/core/encryption.py

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import secrets, base64

class CredentialEncryption:
    """
    AES-256-GCM encryption for OAuth tokens.
    Key derived from CREDENTIAL_MASTER_KEY env var + workspace_id salt.
    Each encrypt operation generates a unique nonce.
    """

    def __init__(self, master_key: bytes):
        self.master_key = master_key

    def encrypt(self, plaintext: str, workspace_id: str) -> bytes:
        key = self._derive_key(workspace_id)
        aesgcm = AESGCM(key)
        nonce = secrets.token_bytes(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), workspace_id.encode())
        return nonce + ciphertext  # Prepend nonce for storage

    def decrypt(self, ciphertext: bytes, workspace_id: str) -> str:
        key = self._derive_key(workspace_id)
        aesgcm = AESGCM(key)
        nonce, data = ciphertext[:12], ciphertext[12:]
        return aesgcm.decrypt(nonce, data, workspace_id.encode()).decode()

    def _derive_key(self, workspace_id: str) -> bytes:
        import hashlib
        return hashlib.sha256(
            self.master_key + workspace_id.encode()
        ).digest()  # 32-byte key for AES-256
```

### Security Hardening Checklist

- All API responses include `X-Content-Type-Options: nosniff`
- CORS: Allow only `app.wrp.io` and `localhost:3000` in development
- Input validation: Pydantic v2 strict mode on all request bodies
- SQL injection: SQLAlchemy ORM only; no f-string SQL
- Secrets: Never logged; scrubbed from error messages and traces
- Webhook signatures: Verify Slack signature (`X-Slack-Signature`) on all HITL callbacks
- Rate limiting: Redis sliding window on all public endpoints
- Audit log: Every credential access, HITL decision, and remediation action logged

---

## Part 9: Infrastructure & Deployment

### Local Development

```yaml
# infrastructure/docker-compose.yml
version: '3.9'
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: wrp_dev
      POSTGRES_USER: wrp
      POSTGRES_PASSWORD: wrp_dev_password
    ports: ["5432:5432"]
    volumes: ["postgres_data:/var/lib/postgresql/data"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: redis-server --save "" --appendonly no

  backend:
    build: ./backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      DATABASE_URL: postgresql+asyncpg://wrp:wrp_dev_password@postgres:5432/wrp_dev
      REDIS_URL: redis://redis:6379/0
    ports: ["8000:8000"]
    depends_on: [postgres, redis]
    volumes: ["./backend:/app"]

  worker:
    build: ./backend
    command: celery -A app.worker.celery_app worker --loglevel=info -B
    environment:
      DATABASE_URL: postgresql+asyncpg://wrp:wrp_dev_password@postgres:5432/wrp_dev
      REDIS_URL: redis://redis:6379/0
    depends_on: [postgres, redis]
    volumes: ["./backend:/app"]

volumes:
  postgres_data:
```

### Render Production Deployment

```yaml
# infrastructure/render.yaml
services:
  - type: web
    name: wrp-api
    env: docker
    dockerfilePath: ./backend/Dockerfile
    dockerContext: ./backend
    plan: free
    healthCheckPath: /health
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: wrp-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          type: redis
          name: wrp-redis
          property: connectionString
      - key: CREDENTIAL_MASTER_KEY
        generateValue: true
      - key: JWT_SECRET_KEY
        sync: false
      - key: CLERK_SECRET_KEY
        sync: false
      - key: CLERK_PUBLISHABLE_KEY
        sync: false
      - key: RESEND_API_KEY
        sync: false
      - key: SLACK_SIGNING_SECRET
        sync: false
      - key: SLACK_WEBHOOK_URL
        sync: false
      - key: ENABLE_LLM_JUDGE
        value: "false"
      - key: LLM_JUDGE_DAILY_BUDGET_USD
        value: "10.00"
      - key: MAX_POLL_CONCURRENCY
        value: "10"

  - type: worker
    name: wrp-worker
    env: docker
    dockerfilePath: ./backend/Dockerfile
    dockerContext: ./backend
    plan: free
    startCommand: "celery -A app.worker.celery_app worker --loglevel=info -B"
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: wrp-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          type: redis
          name: wrp-redis
          property: connectionString
      - key: CREDENTIAL_MASTER_KEY
        generateValue: true
      - key: JWT_SECRET_KEY
        sync: false
      - key: CLERK_SECRET_KEY
        sync: false
      - key: CLERK_PUBLISHABLE_KEY
        sync: false
      - key: RESEND_API_KEY
        sync: false
      - key: SLACK_SIGNING_SECRET
        sync: false
      - key: SLACK_WEBHOOK_URL
        sync: false

databases:
  - name: wrp-db
    plan: free
    databaseName: wrp
    user: wrp

services:
  - type: redis
    name: wrp-redis
    plan: free
    maxmemoryPolicy: allkeys-lru
```

### Environment Variables

```bash
# backend/.env.example

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/wrp

# Redis
REDIS_URL=redis://host:6379/0

# Security
CREDENTIAL_MASTER_KEY=<64-char-hex>    # openssl rand -hex 32
JWT_SECRET_KEY=<clerk-jwt-secret>

# Clerk Auth
CLERK_SECRET_KEY=sk_live_...
CLERK_PUBLISHABLE_KEY=pk_live_...

# AI Services
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...              # For embeddings

# Notifications
RESEND_API_KEY=re_...
SLACK_SIGNING_SECRET=...

# Object Storage (trace archival)
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=wrp-traces

# Feature flags
ENABLE_LLM_JUDGE=true
LLM_JUDGE_DAILY_BUDGET_USD=10.00
MAX_POLL_CONCURRENCY=10
```

---

## Part 10: Observability

### Logging Strategy

```python
# backend/app/core/logging.py
# Structured JSON logs via structlog

import structlog

logger = structlog.get_logger()

# Every log entry includes: workspace_id, integration_id, execution_id, 
# request_id (for correlation), tier, duration_ms

# Example usage in diagnostic engine:
logger.info(
    "diagnostic.completed",
    workspace_id=workspace_id,
    execution_id=execution_id,
    tier=result.tier_used.value,
    confidence=result.confidence,
    category=result.category,
    duration_ms=elapsed_ms,
)
```

### Key Metrics (Prometheus via FastAPI middleware)

```
# Ingestion
wrp_executions_ingested_total{platform, workspace_id}
wrp_polling_duration_seconds{integration_id}
wrp_polling_errors_total{integration_id, error_type}

# Diagnostic
wrp_diagnostic_duration_seconds{tier}
wrp_diagnostic_tier_used_total{tier}
wrp_llm_judge_cost_usd_total{workspace_id}

# Alerts
wrp_alerts_generated_total{severity, category}
wrp_alerts_acknowledged_total{workspace_id}
wrp_alerts_mean_time_to_acknowledge_seconds{workspace_id}

# Heartbeat
wrp_heartbeat_checks_total{monitor_id}
wrp_heartbeat_misses_total{monitor_id}

# Remediation
wrp_remediation_attempted_total{playbook}
wrp_remediation_succeeded_total{playbook}
wrp_hitl_requests_pending{workspace_id}
```

### Health Check

```python
# GET /health — returns 200 or 503
{
  "status": "healthy",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "worker": "ok"
  },
  "version": "1.0.0"
}
```

---

## Part 11: Testing Strategy

### Testing Pyramid

```
E2E Tests (Playwright)          ← Critical user journeys only (5–10 tests)
    ↑
Integration Tests               ← API endpoint + DB integration (covers all routes)
    ↑
Unit Tests (pytest)             ← Business logic, diagnostic engine, adapters
```

### Critical Test Coverage Requirements

**Unit Tests (must have before any phase exit):**

- Rule engine: All 7 failure taxonomy categories fire correctly
- Heartbeat engine: Miss detection with grace period boundary conditions
- Credential vault: Expiry window alerts (48h warning, 6h critical, expired)
- Circuit breaker: State transitions (closed → open → half-open → closed)
- Retry with backoff: Jitter ranges, max retries, 429 header parsing
- OAuth refresh: Token update, failure handling
- Schema drift detection: Field addition, field removal, type change

**Integration Tests (against test DB):**

- Platform adapters: Mock n8n/Make/Zapier API responses → normalized executions
- Alert deduplication: Same failure doesn't generate duplicate alerts within 1h
- HITL workflow: Approve → execution resumes; Reject → dead-letter
- Notification delivery: Slack webhook delivery + retry on failure

**E2E Tests:**

- Connect n8n integration → poll → alert fires → acknowledge
- Set up heartbeat monitor → miss window → critical alert → Slack message
- View execution detail + diagnostic output

### Test Fixtures

```python
# tests/conftest.py
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.fixture
async def client():
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c

@pytest.fixture
def mock_n8n_execution():
    return {
        "id": "exec_123",
        "finished": True,
        "mode": "trigger",
        "retryOf": None,
        "retrySuccessId": None,
        "startedAt": "2026-01-01T00:00:00.000Z",
        "stoppedAt": "2026-01-01T00:00:01.000Z",
        "workflowId": "wf_456",
        "data": {
            "resultData": {
                "error": {
                    "message": "invalid_grant",
                    "name": "NodeApiError",
                }
            }
        },
        "status": "error",
    }
```

---

## Part 12: Implementation Phases

---

### PHASE 1 — Foundation & Core Sentinel

**Objective:** Ship a working out-of-band sentinel that connects to n8n, polls execution data, detects the top 3 highest-frequency failure modes, and delivers alerts via Slack. This is the minimum thing you can show to a paying customer.

**Timeline:** 3–4 weeks solo

**Scope:**
- Project scaffolding and local dev environment
- Postgres schema (core tables only, no FKG yet)
- FastAPI backend with workspace + integration APIs
- n8n adapter (polling only)
- Rule-based diagnostic engine (AUTH_001, API_001, SILENT_001 rules minimum)
- Heartbeat engine (heartbeat monitor type only)
- Credential Vault (Google OAuth expiry tracking only)
- Slack alert delivery
- Minimal Next.js dashboard: connect integration, view alerts, view execution list
- Clerk auth integration
- Render deployment

**Deliverables:**
1. Working Docker Compose local environment
2. Database migrations for all Phase 1 tables
3. n8n polling adapter ingesting executions every 60 seconds
4. Rule engine firing `invalid_grant`, `429`, and zero-items alerts
5. Heartbeat monitor creating a ping URL and alerting on miss
6. Credential vault checking Google OAuth expiry with 48h/6h thresholds
7. Slack notification with alert title, description, and suggested fix
8. Dashboard: integration setup wizard, alert feed, execution list
9. Production deployment on Render
10. 80%+ unit test coverage on diagnostic engine and heartbeat logic

**Dependencies:**
- Clerk account + app setup
- Render account
- Supabase project (Postgres with pgvector extension enabled)
- Upstash Redis
- Resend account
- Slack app with webhook URL

**Architecture Decisions for Phase 1:**
- Polling interval: 60 seconds per integration (configurable, min 30s)
- Alert deduplication: Suppress duplicate alerts for same (integration_id, category, root_cause) within 1 hour
- No FKG in Phase 1. Diagnostic engine uses rule-based only.
- No LLM Judge in Phase 1. Falls back to "manual review required."
- No Make/Zapier adapters in Phase 1. n8n only.

**Technical Tasks:**

```
[ ] 1. Init monorepo structure (backend/, frontend/, infrastructure/)
[ ] 2. pyproject.toml with all dependencies
[ ] 3. FastAPI app factory + config (Pydantic Settings)
[ ] 4. SQLAlchemy async engine + session factory
[ ] 5. Alembic init + migration for Phase 1 tables
[ ] 6. Workspace CRUD API + Clerk JWT middleware
[ ] 7. Integration model + encrypt/decrypt API key
[ ] 8. n8n API client (httpx async) with timeout handling
[ ] 9. n8n adapter implementing BaseAdapter interface
[ ] 10. Celery app factory + Redis connection
[ ] 11. Polling task: poll_integration_executions (periodic, 60s)
[ ] 12. Execution normalization + upsert to DB
[ ] 13. Rule engine with AUTH_001, API_001, SILENT_001 rules
[ ] 14. Alert creation + deduplication logic
[ ] 15. Heartbeat engine + /ping/{token} endpoint
[ ] 16. Monitor CRUD API
[ ] 17. Credential model + Google expiry check
[ ] 18. Credential CRUD API
[ ] 19. Slack notification service (Block Kit)
[ ] 20. Notification dispatcher
[ ] 21. Celery beat schedule: heartbeat check (1min), credential check (1h)
[ ] 22. /health endpoint
[ ] 23. Structured logging setup (structlog)
[ ] 24. Prometheus metrics middleware
[ ] 25. Next.js project init + Clerk setup
[ ] 26. Dashboard layout + navigation
[ ] 27. Integration setup wizard (connect n8n)
[ ] 28. Alert feed page with severity filtering
[ ] 29. Execution list page (paginated)
[ ] 30. Monitor create/list page
[ ] 31. Credential status page
[ ] 32. Docker Compose local environment
[ ] 33. Render deployment config
[ ] 34. Unit tests: rule engine (all 3 rules), heartbeat, credential vault
[ ] 35. Integration test: polling → alert generation → Slack delivery
```

**Testing Strategy:**
- Unit test every diagnostic rule with both matching and non-matching payloads
- Integration test: mock n8n API → poll → alert generated → notification sent
- Manual validation: connect real n8n instance with a failing credential

**Validation Checklist:**
- [ ] Can connect an n8n instance via API key in under 2 minutes
- [ ] Executions appear in the dashboard within 90 seconds of completion
- [ ] `invalid_grant` error in n8n generates a critical alert within 2 minutes
- [ ] Heartbeat miss generates a critical Slack alert
- [ ] Google credential expiring in <48h generates a warning alert
- [ ] Duplicate alerts are suppressed for 1 hour
- [ ] Dashboard loads in <1s
- [ ] Render deployment works and stays up

**Exit Criteria:**
All 8 validation checklist items pass against a live n8n instance. No P0 bugs in alert generation or notification delivery.

---

### PHASE 2 — Platform Expansion + Schema Drift Detection

**Objective:** Add Make.com and Zapier support, implement schema drift detection (failure taxonomy class 4), expand the rule engine to cover all 7 taxonomy categories, and ship the outcome-assertion monitor type.

**Timeline:** 2–3 weeks

**Scope:**
- Make.com adapter (polling)
- Zapier adapter (polling)
- Custom OTel ingest endpoint for LangGraph/CrewAI
- Schema drift detection service
- Full rule engine expansion (all SIGNATURE_RULES)
- Outcome assertion monitor type
- Email notification channel (Resend)
- Dashboard: execution detail view + raw payload inspector
- Historical baseline tracking (rolling 7-day)

**Deliverables:**
1. Make.com adapter with normalized execution ingestion
2. Zapier adapter with normalized execution ingestion
3. OTel ingest endpoint accepting WorkflowTracePayload schema
4. Schema drift detector: field addition/removal/type-change detection
5. Full rule engine: all 7 taxonomy categories, minimum 15 rules
6. Outcome assertion monitor: `min_records`, `field_exists`, `value_range` checks
7. Email alert delivery via Resend
8. Execution detail page with node-level error display
9. Baseline anomaly detection (z-score on items_processed, duration_ms)

**Dependencies:** Phase 1 complete + passing all exit criteria

**Architecture Decisions for Phase 2:**
- OTel endpoint accepts the WorkflowTracePayload JSON schema defined in the research. Stores in `executions` with `integration_id` pointing to a "custom" integration.
- Schema drift: Store schema fingerprint (sorted field hash) per workflow_id. Alert when fingerprint changes between consecutive successful runs.
- Baseline: Calculate rolling 7-day percentiles for `items_processed` and `duration_ms` per workflow. Alert when current value falls below p10 or exceeds p95.
- No FKG still. Phase 3 brings graph diagnostics.

**Testing Strategy:**
- Mock Make/Zapier API responses; verify normalization
- Schema drift: feed same workflow two executions with different field sets
- Outcome assertion: test each assertion type (min_records, field_exists, value_range)

**Exit Criteria:** All three platforms ingesting; schema drift fires on a real payload mutation; outcome assertion monitors working; email delivery confirmed.

---

### PHASE 3 — Failure Knowledge Graph + LLM Judge

**Objective:** Implement the Failure Knowledge Graph in Postgres, enable semantic similarity search via pgvector, integrate the LLM Judge (Claude) for novel failure diagnosis, and ship the HITL approval portal.

**Timeline:** 3–4 weeks

**Scope:**
- FKG schema migration (fkg_nodes, fkg_edges tables)
- FKG ingestion: Every diagnosed failure creates nodes + edges
- FKG traversal: Recursive CTE queries to find root cause matches
- Embedding generation: Store embeddings on ErrorSignature nodes
- Semantic similarity search for novel failures
- LLM Judge integration (Claude claude-sonnet-4-6, cost-gated)
- HITL approval portal: Slack interactive messages + web UI
- Dead-letter queue implementation
- Remediation playbook dispatcher

**Key FKG CTE Query Pattern:**
```sql
-- Find root causes similar to a given error signature
WITH RECURSIVE fkg_path AS (
    SELECT n.id, n.node_type, n.label, n.properties, e.edge_type, 1 AS depth
    FROM fkg_nodes n
    JOIN fkg_edges e ON e.source_id = n.id
    WHERE n.node_type = 'ErrorSignature'
      AND n.properties->>'signature_hash' = :hash

    UNION ALL

    SELECT n2.id, n2.node_type, n2.label, n2.properties, e2.edge_type, fp.depth + 1
    FROM fkg_nodes n2
    JOIN fkg_edges e2 ON e2.target_id = n2.id
    JOIN fkg_path fp ON fp.id = e2.source_id
    WHERE fp.depth < 5
)
SELECT * FROM fkg_path
WHERE node_type IN ('RootCause', 'RemediationWorkflow')
ORDER BY depth;
```

**LLM Judge Prompt Template:**
```python
LLM_JUDGE_SYSTEM = """
You are a workflow reliability expert diagnosing production automation failures.
You analyze execution traces, error messages, and system context to determine 
root causes and suggest specific, actionable fixes.

Your diagnosis must be:
- Specific (name the exact node, credential, or configuration that failed)
- Actionable (give step-by-step remediation instructions)
- Categorized (one of: auth, api, logic, schema, infra, ai, silent)
- Confidence-scored (0.0 to 1.0)

Respond ONLY with valid JSON matching this schema:
{
  "root_cause": "string",
  "category": "auth|api|logic|schema|infra|ai|silent",
  "confidence": 0.0-1.0,
  "suggested_fix": "string",
  "remediation_playbook": "retry_backoff|oauth_refresh|dead_letter|hitl|null",
  "requires_hitl": boolean
}
"""
```

**HITL Flow:**
1. Diagnostic engine sets `requires_hitl = True`
2. Execution state serialized to `hitl_requests` table
3. Slack message sent with Block Kit buttons: [Approve] [Modify State] [Reject]
4. Slack callback received at `/api/v1/hitl/slack-callback`
5. On approve: resume playbook executed; execution re-queued
6. On reject: dead-letter queue entry created; alert marked resolved

**Exit Criteria:** FKG accumulates knowledge across 50+ diagnosed failures; LLM Judge runs on novel failures not matched by rules; HITL flow completes end-to-end in Slack.

---

### PHASE 4 — Agent Reliability Layer + Agentic Metrics

**Objective:** Implement the Agent Reliability Score (ARS), add agentic-specific failure detection (tool bypass, hallucination, context explosion, structured output failure), and build the agent observability dashboard.

**Timeline:** 3–4 weeks

**Scope:**
- ARS calculation engine (Completion Rate, Tool Accuracy, Constraint Compliance, Recursion Density, Latency Variance)
- Multi-turn agentic conversation payload ingestion (AgenticMultiTurnPayload schema)
- Tool usage drift detection (missing tool calls on tool-required intents)
- Context explosion detection (token count threshold alerts)
- Structured output failure detection (JSON schema validation on agent output)
- Behavioral regression tracking (rolling evaluation baseline)
- Agent observability dashboard: ARS timeline, tool call success rate, latency distribution
- OTel context propagation (W3C traceparent header parsing for distributed traces)

**Architecture Decisions for Phase 4:**
- ARS is computed per workflow_id over a rolling 100-execution window
- Tool usage drift: Parse multi-turn payloads; flag turns where tool_calls is empty but intent classifier indicates data retrieval needed
- Hallucination detection: NLI scoring is computationally expensive; defer to Phase 5. Phase 4 uses structural checks (response contains no source citations when prompt requires them).
- OTel distributed context: Parse `traceparent` header on ingest endpoint; store trace_id + parent_span_id to link distributed executions.

**Exit Criteria:** ARS score visible per workflow; tool bypass alerts fire on a real LangGraph agent trace; context explosion alerts fire when token count exceeds configurable threshold.

---

### PHASE 5 — Self-Healing Automation + Scale

**Objective:** Implement the remaining autonomous remediation playbooks, upgrade telemetry to streaming ingestion (Kafka → ClickHouse), ship SOC 2 compliance readiness, and build the enterprise tier.

**Timeline:** 4–6 weeks

**Scope:**
- Durable checkpointing + state recovery (Postgres-backed)
- Autonomous OAuth token refresh (safe, idempotent)
- Behavioral regression auto-routing (switch to fallback model on quality drop)
- Kafka producer on ingestion gateway (replaces direct DB writes)
- ClickHouse for trace storage (Postgres retained for operational data)
- Audit log for all automated actions
- SOC 2 readiness: access logging, data retention policies, encrypted backups
- Enterprise tier: SSO via Clerk, self-hosted deployment docs, custom SLA config

**Dependencies:** Phase 4 complete + 10+ paying customers justifying infrastructure investment

---

## Part 13: Risk Analysis

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Platform API changes break adapters | High | High | Adapter interface + versioned parsers; integration health check runs daily |
| LLM Judge costs spiral | Medium | Medium | Daily budget cap ($10/day default); cost-per-diagnosis logged; Growth tier only |
| OAuth credential storage breach | Critical | Low | AES-256-GCM per-tenant encryption; master key in Render secrets; never logged |
| Polling creates load on n8n instance | Medium | Low | Poll interval min 30s; exponential backoff on 429; documented in onboarding |
| Solo founder bandwidth | High | High | Phase-based build order; each phase is shippable; no phase mixing |
| Postgres performance at scale | Medium | Low | Partitioned executions table by created_at (Phase 5); ClickHouse for analytical queries |
| False positive alerts erode trust | High | Medium | Alert deduplication + confidence thresholds; user feedback loop on false positives |
| HITL Slack token revoked | Low | Low | Webhook fallback to email; HITL request expires after 24h with email escalation |

---

## Part 14: Milestones

| Milestone | Deliverable | Target |
|-----------|-------------|--------|
| M1 | Phase 1 complete — n8n sentinel live, first paying customer | Week 4 |
| M2 | Phase 2 complete — 3 platforms, schema drift, outcome monitors | Week 7 |
| M3 | First 10 paying customers | Week 8 |
| M4 | Phase 3 complete — FKG + LLM Judge + HITL | Week 12 |
| M5 | Phase 4 complete — Agent reliability layer | Week 16 |
| M6 | $10k MRR | Week 18 |
| M7 | Phase 5 complete — streaming scale + SOC 2 readiness | Week 24 |

---

## Part 15: Future Expansion Points

These are explicitly deferred but must not be blocked by current architecture:

- **Kafka ingestion pipeline:** The `executions` insert path must be async-idempotent so a Kafka consumer can drive it in Phase 5 without schema changes
- **ClickHouse analytics:** The `raw_payload` JSONB column maps directly to ClickHouse's JSON type; migration is a data copy, not a schema redesign
- **Public integration marketplace:** The `BaseAdapter` interface is the plugin API; third-party adapters can be packaged as pip packages
- **Graph ML on FKG:** Embedding field on `fkg_nodes` is pre-provisioned; Neo4j migration or graph ML layer plugs in at Phase 5+
- **Multi-region deployment:** All services are stateless except Postgres + Redis; region expansion is an infra config change, not a code change
- **Embedded SDK:** The OTel ingest endpoint is the SDK's API surface; a thin Python/TypeScript SDK wrapping it can be published without backend changes

---

## Appendix: Agent Reliability Score (ARS) Reference

From the research, formalized for implementation:

```
ARS = (w1 * CR) + (w2 * TA) + (w3 * CC) - (w4 * RD_penalty) - (w5 * LV_penalty)

Where:
  CR  = Completion Rate (% executions reaching terminal state without error)
  TA  = Tool Accuracy (valid tool invocations / total attempts)
  CC  = Constraint Compliance (LLM-as-judge score against system prompt)
  RD  = Recursion Density (state changes per trace, normalized 0-1, higher = worse)
  LV  = Latency Variance (normalized std dev over rolling 100-trace window)

  w1 = 0.35 (Completion Rate — primary outcome metric)
  w2 = 0.25 (Tool Accuracy — critical for grounding)
  w3 = 0.20 (Constraint Compliance)
  w4 = 0.10 (Recursion penalty)
  w5 = 0.10 (Latency variance penalty)

  ARS >= 0.85 → Stable deployment
  ARS 0.70–0.84 → Degraded, monitor closely
  ARS < 0.70 → Unstable, alert required
```

---

*This document is the engineering source of truth. All implementation work begins from Phase 1. No work on Phase 2+ begins until Phase 1 exit criteria are fully validated.*
