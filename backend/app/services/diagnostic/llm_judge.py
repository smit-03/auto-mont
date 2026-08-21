"""
LLM Judge — Blueprint Part 3 tier 5, the fallback for failures no cheaper tier
could explain.

Routed through OpenRouter rather than Claude directly: no Anthropic key exists
on this project. DECISIONS.md, 2026-08-21, is the full amendment — model
choice, the live latency/context table it was chosen from, and the free-tier
operational quirks below. Default model is `z-ai/glm-5.2:free`.

Two things about the free tier are load-bearing here, both confirmed live
against OpenRouter this session, not assumed:

1. These are reasoning models. They spend part of `max_tokens` on reasoning
   before emitting the JSON answer, so a low budget returns an *empty* string,
   not a short one — `_MIN_MAX_TOKENS` exists because of this, observed
   directly.
2. The free tier is capped near 50 requests/day *per key*. "Cost-gated" in the
   Blueprint's scope line is, on this stack, a *rate* gate — every free-tier
   response prices at $0, so LLM_JUDGE_DAILY_BUDGET_USD never binds. The gate
   that actually matters is LLM_JUDGE_DAILY_REQUEST_LIMIT, enforced below.

The gate fails **closed**: if Redis is unreachable, no request is sent. This is
the opposite of `app.core.redis.rate_limit`, which fails open for heartbeat
pings because rejecting a ping falsely reports a live workflow as missed.
Failing open here would mean uncapped calls to a request-limited external API
during a Redis blip — a worse outcome than one execution going undiagnosed by
this tier, which is a safe, already-defined state the cascade handles.
"""

import json
from datetime import UTC, datetime

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.core.redis import get_cache

logger = get_logger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Reasoning models spend part of the budget on reasoning tokens before content.
# Verified live this session: a 20-token budget returned empty content for
# every model tried. 1000 leaves headroom for reasoning plus a JSON answer.
_MIN_MAX_TOKENS = 1000

# Nominal Blueprint budget is <5s; live testing this session put every
# reasoning-tier free model except gpt-oss-20b (not used, 17.7s) between 0.8s
# and 2.5s. 15s leaves room for network jitter without letting one slow call
# stall a diagnosis indefinitely.
_REQUEST_TIMEOUT_S = 15.0

_VALID_CATEGORIES = {"auth", "api", "logic", "schema", "infra", "ai", "silent", "unknown"}
_VALID_PLAYBOOKS = {"retry_backoff", "oauth_refresh", "dead_letter", "hitl", None}

# Blueprint Part 3, near-verbatim.
_SYSTEM_PROMPT = """You are a workflow reliability expert diagnosing production automation failures.
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
}"""


class JudgeVerdict:
    """A parsed, validated response from the judge. Mirrors the JSON schema above."""

    def __init__(
        self,
        root_cause: str,
        category: str,
        confidence: float,
        suggested_fix: str,
        remediation_playbook: str | None,
        requires_hitl: bool,
    ):
        self.root_cause = root_cause
        self.category = category
        self.confidence = confidence
        self.suggested_fix = suggested_fix
        self.remediation_playbook = remediation_playbook
        self.requires_hitl = requires_hitl


def _build_user_prompt(
    *,
    error_message: str | None,
    category_hint: str | None,
    platform: str,
    workflow_name: str | None,
    workflow_id: str,
    status: str,
    items_processed: int | None,
    raw_payload: dict,
) -> str:
    # raw_payload can be arbitrarily large (a full n8n execution trace); this
    # tier only runs on failures nothing cheaper explained, so a truncated
    # excerpt is enough context to diagnose against without spending the
    # token budget the model needs for reasoning.
    payload_excerpt = json.dumps(raw_payload, default=str)[:3000]
    return (
        f"Platform: {platform}\n"
        f"Workflow: {workflow_name or workflow_id}\n"
        f"Status: {status}\n"
        f"Items processed: {items_processed}\n"
        f"Error message: {error_message or '(none — see raw payload)'}\n"
        f"Prior detection hint: {category_hint or 'none'}\n\n"
        f"Raw execution payload (truncated):\n{payload_excerpt}"
    )


async def _rate_gate() -> bool:
    """
    True if this call is allowed to spend one of today's request budget.

    Consumes the slot as a side effect of checking — checking and not calling
    would leave the counter meaningless, and a check-then-call race between
    concurrent diagnoses is exactly what the counter exists to prevent.
    """
    if not settings.ENABLE_LLM_JUDGE or not settings.OPENROUTER_API_KEY:
        return False

    try:
        cache = await get_cache()
        key = f"llm_judge:requests:{datetime.now(UTC).date().isoformat()}"
        count = await cache.incr(key)
        if count == 1:
            # A little over 24h: the key outlives the UTC day it was opened in
            # even if the first increment lands just before midnight, so a
            # burst spanning the rollover is still capped by one counter, not
            # reset mid-burst by its own expiry.
            await cache.expire(key, 90_000)
        return count <= settings.LLM_JUDGE_DAILY_REQUEST_LIMIT
    except Exception as exc:
        logger.warning("llm_judge.rate_gate_unavailable", error=str(exc))
        return False


def _parse_verdict(content: str) -> JudgeVerdict | None:
    """
    Parse and validate the model's JSON response.

    Returns None rather than raising on any failure — malformed JSON, a
    category outside the taxonomy, a confidence out of range. The judge is the
    last tier; a bad response from it should fall through to "no diagnosis",
    the same safe outcome an empty graph or a below-floor match produces
    elsewhere in the cascade, not an exception that breaks the caller.
    """
    text = content.strip()
    if not text:
        logger.warning("llm_judge.empty_content")
        return None

    # Despite the system prompt's "Respond ONLY with valid JSON," models
    # routinely wrap answers in a markdown fence anyway.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("llm_judge.invalid_json", error=str(exc), content=content[:500])
        return None

    if not isinstance(data, dict):
        return None

    root_cause = data.get("root_cause")
    category = data.get("category")
    confidence = data.get("confidence")
    suggested_fix = data.get("suggested_fix")
    remediation_playbook = data.get("remediation_playbook")
    requires_hitl = data.get("requires_hitl")

    if not isinstance(root_cause, str) or not root_cause.strip():
        return None
    if category not in _VALID_CATEGORIES:
        return None
    if not isinstance(confidence, int | float) or not (0.0 <= float(confidence) <= 1.0):
        return None
    if not isinstance(suggested_fix, str) or not suggested_fix.strip():
        return None
    if remediation_playbook not in _VALID_PLAYBOOKS:
        remediation_playbook = None
    if not isinstance(requires_hitl, bool):
        requires_hitl = False

    return JudgeVerdict(
        root_cause=root_cause,
        category=category,
        confidence=float(confidence),
        suggested_fix=suggested_fix,
        remediation_playbook=remediation_playbook,
        requires_hitl=requires_hitl,
    )


async def judge(
    *,
    error_message: str | None,
    category_hint: str | None,
    platform: str,
    workflow_name: str | None,
    workflow_id: str,
    status: str,
    items_processed: int | None,
    raw_payload: dict,
) -> JudgeVerdict | None:
    """
    Ask the LLM Judge to diagnose an execution nothing cheaper explained.

    None covers every "this call did not produce a usable diagnosis" case —
    the tier is disabled, the daily request cap is spent, the HTTP call
    failed, or the response could not be parsed into the expected shape.
    None distinct causes are logged at the point they occur; the caller only
    needs to know whether it got an answer.
    """
    if not await _rate_gate():
        return None

    payload = {
        "model": settings.LLM_JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_prompt(
                    error_message=error_message,
                    category_hint=category_hint,
                    platform=platform,
                    workflow_name=workflow_name,
                    workflow_id=workflow_id,
                    status=status,
                    items_processed=items_processed,
                    raw_payload=raw_payload,
                ),
            },
        ],
        "max_tokens": _MIN_MAX_TOKENS,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _OPENROUTER_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
                timeout=_REQUEST_TIMEOUT_S,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        # Free-tier 429s are routine, not exceptional — observed live this
        # session on two of nine models tried in one run. Logged, not raised:
        # the judge being unavailable is not a reason to fail the diagnosis
        # that was about to fall through to "unknown" anyway.
        logger.warning(
            "llm_judge.http_error", status=exc.response.status_code, error=str(exc)
        )
        return None
    except httpx.HTTPError as exc:
        logger.warning("llm_judge.request_failed", error=str(exc))
        return None

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("llm_judge.unexpected_response_shape", body=str(body)[:500])
        return None

    return _parse_verdict(content)
