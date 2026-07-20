"""
Redis connection utility for Celery broker and caching.

Provides async Redis client with connection pooling.
"""

from typing import cast

import redis.asyncio as redis
from redis.asyncio import Redis

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: Redis | None = None


async def get_redis_client() -> Redis:
    """Get or create the global async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
        logger.info("redis.client_created", url=settings.REDIS_URL)
    return _redis_client


async def close_redis_client() -> None:
    """Close the global Redis client."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("redis.client_closed")


async def check_redis_connection() -> bool:
    """Check if Redis connection is healthy."""
    try:
        client = await get_redis_client()
        await client.ping()
        return True
    except Exception as e:
        logger.error("redis.health_check_failed", error=str(e))
        return False


class RedisCache:
    """Simple async cache wrapper with TTL support."""

    def __init__(self, client: Redis | None = None):
        self._client = client

    async def _get_client(self) -> Redis:
        if self._client is None:
            self._client = await get_redis_client()
        return self._client

    async def get(self, key: str) -> str | None:
        """Get value by key."""
        client = await self._get_client()
        return cast("str | None", await client.get(key))

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set key-value pair with optional TTL in seconds."""
        client = await self._get_client()
        if ttl:
            return cast(bool, await client.setex(key, ttl, value))
        return cast(bool, await client.set(key, value))

    async def delete(self, key: str) -> int:
        """Delete key."""
        client = await self._get_client()
        return cast(int, await client.delete(key))

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        client = await self._get_client()
        return cast(int, await client.exists(key)) > 0

    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment key by amount."""
        client = await self._get_client()
        return cast(int, await client.incrby(key, amount))

    async def expire(self, key: str, ttl: int) -> bool:
        """Set expiry on key."""
        client = await self._get_client()
        return cast(bool, await client.expire(key, ttl))

    async def ttl(self, key: str) -> int:
        """Get remaining TTL for key."""
        client = await self._get_client()
        return cast(int, await client.ttl(key))


async def get_cache() -> RedisCache:
    """Get a RedisCache instance."""
    return RedisCache()
