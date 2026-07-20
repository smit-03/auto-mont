# WRP API Documentation

## Overview

The Workflow Reliability Platform API provides endpoints for:
- Integration management (connect workflow platforms)
- Execution monitoring (view execution history)
- Alert management (view and acknowledge alerts)
- Heartbeat monitoring (dead-man's switch)
- Credential vault (OAuth token lifecycle)

Base URL: `https://api.wrp.io/api/v1` (or `http://localhost:8000/api/v1` for local)

## Authentication

All authenticated endpoints require a Bearer token from Clerk:

```
Authorization: Bearer <clerk-jwt-token>
```

Public endpoints (ping):
```
POST /ping/{ping_token}
```

## Endpoints

### Workspaces

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/workspaces` | Create a new workspace |
| GET | `/workspaces/me` | Get current workspace details |
| PATCH | `/workspaces/{id}` | Update workspace |

### Integrations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/integrations` | List connected integrations |
| POST | `/integrations` | Connect new platform (required: platform, display_name, api_key) |
| GET | `/integrations/{id}` | Get integration details |
| PATCH | `/integrations/{id}` | Update integration config |
| DELETE | `/integrations/{id}` | Disconnect integration |
| POST | `/integrations/{id}/test` | Test connectivity |
| POST | `/integrations/{id}/poll` | Trigger manual poll |

### Executions

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/executions` | List executions (paginated, filterable) |
| GET | `/executions/{id}` | Get execution detail |

### Monitors

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/monitors` | List monitors |
| POST | `/monitors` | Create monitor (heartbeat, cron, outcome, schema) |
| GET | `/monitors/{id}` | Get monitor detail |

### Alerts

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/alerts` | List alerts (filterable by severity/category) |
| POST | `/alerts/{id}/acknowledge` | Acknowledge alert |

### Credentials

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/credentials` | List OAuth credentials |
| POST | `/credentials` | Register credential for expiry tracking |

### Heartbeat Ping

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ping/{ping_token}` | Public endpoint for workflow heartbeat pings |

## Response Format

Successful responses:
```json
{
  "data": {...},
  "meta": {...}
}
```

Error responses:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input",
    "details": {...}
  }
}
```

## Rate Limits

Public endpoints: 1,000 requests/minute per API key
Authenticated endpoints: 5,000 requests/minute per workspace