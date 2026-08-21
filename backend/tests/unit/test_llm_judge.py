"""
Unit tests for the LLM Judge (tier 5) — the rate gate and response parsing.

No network calls run here: httpx.AsyncClient.post is patched at the class
level, matching the convention in test_polling_to_slack.py. Redis is faked at
the module's own `get_cache` boundary rather than requiring a live instance.

Two things get the most attention, because they are the two ways this tier can
go wrong silently: the rate gate failing *open* under a Redis outage (it must
not — see the module docstring on why that would be worse here than for a
heartbeat ping), and a malformed or empty model response being accepted as a
diagnosis instead of being rejected.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.diagnostic import llm_judge as llm_judge_module
from app.services.diagnostic.llm_judge import (
    _MIN_MAX_TOKENS,
    _parse_verdict,
    _rate_gate,
    judge,
)


class FakeCache:
    """Stands in for RedisCache: an in-memory incrementing counter."""

    def __init__(self, fail: bool = False):
        self._counts: dict[str, int] = {}
        self._fail = fail

    async def incr(self, key: str, amount: int = 1) -> int:
        if self._fail:
            raise ConnectionError("redis unavailable")
        self._counts[key] = self._counts.get(key, 0) + amount
        return self._counts[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True


def enable_judge(monkeypatch, *, limit: int = 45, key: str = "sk-or-v1-test"):
    monkeypatch.setattr(llm_judge_module.settings, "ENABLE_LLM_JUDGE", True)
    monkeypatch.setattr(llm_judge_module.settings, "OPENROUTER_API_KEY", key)
    monkeypatch.setattr(llm_judge_module.settings, "LLM_JUDGE_DAILY_REQUEST_LIMIT", limit)


def patch_cache(monkeypatch, cache: FakeCache):
    async def fake_get_cache():
        return cache

    monkeypatch.setattr(llm_judge_module, "get_cache", fake_get_cache)


class TestRateGate:
    @pytest.mark.asyncio
    async def test_disabled_by_config_blocks_without_touching_redis(self, monkeypatch):
        monkeypatch.setattr(llm_judge_module.settings, "ENABLE_LLM_JUDGE", False)
        monkeypatch.setattr(llm_judge_module.settings, "OPENROUTER_API_KEY", "sk-or-v1-test")
        cache = FakeCache()
        patch_cache(monkeypatch, cache)

        allowed = await _rate_gate()

        assert allowed is False
        assert cache._counts == {}

    @pytest.mark.asyncio
    async def test_no_api_key_blocks(self, monkeypatch):
        monkeypatch.setattr(llm_judge_module.settings, "ENABLE_LLM_JUDGE", True)
        monkeypatch.setattr(llm_judge_module.settings, "OPENROUTER_API_KEY", None)

        assert await _rate_gate() is False

    @pytest.mark.asyncio
    async def test_under_the_limit_is_allowed(self, monkeypatch):
        enable_judge(monkeypatch, limit=45)
        patch_cache(monkeypatch, FakeCache())

        assert await _rate_gate() is True

    @pytest.mark.asyncio
    async def test_exactly_at_the_limit_is_allowed(self, monkeypatch):
        """Mutation guard on `count <= limit`: the limit-th request is not the overflow."""
        enable_judge(monkeypatch, limit=3)
        cache = FakeCache()
        patch_cache(monkeypatch, cache)

        results = [await _rate_gate() for _ in range(3)]

        assert results == [True, True, True]

    @pytest.mark.asyncio
    async def test_one_past_the_limit_is_blocked(self, monkeypatch):
        enable_judge(monkeypatch, limit=3)
        cache = FakeCache()
        patch_cache(monkeypatch, cache)

        for _ in range(3):
            await _rate_gate()
        fourth = await _rate_gate()

        assert fourth is False

    @pytest.mark.asyncio
    async def test_redis_unavailable_fails_closed(self, monkeypatch):
        """
        The one place this gate must NOT copy app.core.redis.rate_limit's
        fail-open behaviour: failing open here means uncapped calls to a
        request-limited external API during a Redis blip, not a missed
        heartbeat ping.
        """
        enable_judge(monkeypatch)
        patch_cache(monkeypatch, FakeCache(fail=True))

        assert await _rate_gate() is False


class TestParseVerdict:
    def valid_json(self, **overrides) -> str:
        import json

        payload = {
            "root_cause": "OAuth token expired",
            "category": "auth",
            "confidence": 0.85,
            "suggested_fix": "Reconnect the Google Sheets account",
            "remediation_playbook": "oauth_refresh",
            "requires_hitl": False,
        }
        payload.update(overrides)
        return json.dumps(payload)

    def test_valid_response_parses(self):
        verdict = _parse_verdict(self.valid_json())

        assert verdict is not None
        assert verdict.root_cause == "OAuth token expired"
        assert verdict.category == "auth"
        assert verdict.confidence == 0.85
        assert verdict.remediation_playbook == "oauth_refresh"
        assert verdict.requires_hitl is False

    def test_empty_content_returns_none(self):
        assert _parse_verdict("") is None
        assert _parse_verdict("   ") is None

    def test_markdown_fenced_json_is_unwrapped(self):
        fenced = f"```json\n{self.valid_json()}\n```"

        verdict = _parse_verdict(fenced)

        assert verdict is not None
        assert verdict.category == "auth"

    def test_invalid_json_returns_none(self):
        assert _parse_verdict("not json at all") is None

    def test_json_that_is_not_an_object_returns_none(self):
        assert _parse_verdict("[1, 2, 3]") is None

    def test_unknown_category_is_rejected(self):
        assert _parse_verdict(self.valid_json(category="not_a_real_category")) is None

    @pytest.mark.parametrize("confidence", [-0.01, 1.01, "high"])
    def test_confidence_out_of_range_or_wrong_type_is_rejected(self, confidence):
        assert _parse_verdict(self.valid_json(confidence=confidence)) is None

    @pytest.mark.parametrize("confidence", [0.0, 1.0])
    def test_confidence_at_the_exact_boundary_is_accepted(self, confidence):
        """Mutation guard on the `0.0 <= confidence <= 1.0` bounds check."""
        verdict = _parse_verdict(self.valid_json(confidence=confidence))

        assert verdict is not None
        assert verdict.confidence == confidence

    def test_missing_root_cause_is_rejected(self):
        assert _parse_verdict(self.valid_json(root_cause="")) is None

    def test_unknown_playbook_is_coerced_to_none_not_rejected(self):
        """
        A model naming a playbook outside the taxonomy is a formatting slip,
        not evidence the whole diagnosis is untrustworthy — root_cause,
        category and confidence are still usable.
        """
        verdict = _parse_verdict(self.valid_json(remediation_playbook="made_up_playbook"))

        assert verdict is not None
        assert verdict.remediation_playbook is None

    def test_non_bool_requires_hitl_is_coerced_to_false(self):
        verdict = _parse_verdict(self.valid_json(requires_hitl="yes"))

        assert verdict is not None
        assert verdict.requires_hitl is False


class TestJudgeEndToEnd:
    """The full judge() call: rate gate -> HTTP -> parse, with httpx mocked."""

    def _response(self, content: str, status_ok: bool = True):
        response = MagicMock()
        if status_ok:
            response.raise_for_status = MagicMock()
        else:
            import httpx

            response.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "rate limited", request=MagicMock(), response=MagicMock(status_code=429)
                )
            )
        response.json = MagicMock(
            return_value={"choices": [{"message": {"content": content}}]}
        )
        return response

    @pytest.mark.asyncio
    async def test_gate_closed_makes_no_http_call(self, monkeypatch):
        monkeypatch.setattr(llm_judge_module.settings, "ENABLE_LLM_JUDGE", False)

        with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
            result = await judge(
                error_message="401 Unauthorized",
                category_hint="auth",
                platform="n8n",
                workflow_name="Nightly sync",
                workflow_id="wf-1",
                status="error",
                items_processed=0,
                raw_payload={},
            )

        assert result is None
        mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_returns_a_verdict(self, monkeypatch):
        enable_judge(monkeypatch)
        patch_cache(monkeypatch, FakeCache())
        payload = TestParseVerdict().valid_json()

        with patch(
            "httpx.AsyncClient.post", new=AsyncMock(return_value=self._response(payload))
        ):
            result = await judge(
                error_message="401 Unauthorized",
                category_hint="auth",
                platform="n8n",
                workflow_name="Nightly sync",
                workflow_id="wf-1",
                status="error",
                items_processed=0,
                raw_payload={"error": "unauthorized"},
            )

        assert result is not None
        assert result.category == "auth"

    @pytest.mark.asyncio
    async def test_request_carries_at_least_the_minimum_max_tokens(self, monkeypatch):
        """
        Mutation/regression guard: a max_tokens below this returns empty
        content on the free tier's reasoning models — verified live, not
        assumed. The request must never ask for less.
        """
        enable_judge(monkeypatch)
        patch_cache(monkeypatch, FakeCache())
        mock_post = AsyncMock(return_value=self._response(TestParseVerdict().valid_json()))

        with patch("httpx.AsyncClient.post", new=mock_post):
            await judge(
                error_message="401 Unauthorized",
                category_hint=None,
                platform="n8n",
                workflow_name=None,
                workflow_id="wf-1",
                status="error",
                items_processed=None,
                raw_payload={},
            )

        sent_payload = mock_post.call_args.kwargs["json"]
        assert sent_payload["max_tokens"] >= _MIN_MAX_TOKENS

    @pytest.mark.asyncio
    async def test_http_429_returns_none_without_raising(self, monkeypatch):
        enable_judge(monkeypatch)
        patch_cache(monkeypatch, FakeCache())

        with patch(
            "httpx.AsyncClient.post",
            new=AsyncMock(return_value=self._response("", status_ok=False)),
        ):
            result = await judge(
                error_message="x",
                category_hint=None,
                platform="n8n",
                workflow_name=None,
                workflow_id="wf-1",
                status="error",
                items_processed=None,
                raw_payload={},
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_response_shape_returns_none(self, monkeypatch):
        enable_judge(monkeypatch)
        patch_cache(monkeypatch, FakeCache())
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"unexpected": "shape"})

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
            result = await judge(
                error_message="x",
                category_hint=None,
                platform="n8n",
                workflow_name=None,
                workflow_id="wf-1",
                status="error",
                items_processed=None,
                raw_payload={},
            )

        assert result is None
