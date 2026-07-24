"""
Persistent event loop for Celery tasks.

Tasks run async code, but ``asyncio.run()`` creates a fresh loop and closes it
on return. Our redis client and DB engine are module-level singletons that bind
their connection pools to whichever loop first touched them. Under
``--pool=solo`` those singletons live for the whole worker process, so the
second task invocation reuses pools attached to the already-closed first loop
and fails with "Event loop is closed".

Running every task on one long-lived loop keeps those singletons valid for the
life of the worker process.
"""

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

_loop: asyncio.AbstractEventLoop | None = None

T = TypeVar("T")


def run_async(coro: Coroutine[object, object, T]) -> T:
    """Run a coroutine on the worker's persistent event loop."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)
