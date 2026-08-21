"""
Local embedding generation for the Failure Knowledge Graph.

Runs `intfloat/e5-large-v2` on CPU rather than a hosted API — no OpenAI key
exists on this project, and the model's 1024-dimensional output already
matches `fkg_nodes.embedding` with no projection or padding. Budget-driven
substitution, same pattern as the Resend/Groq/OpenRouter swaps already in
DECISIONS.md; see the 2026-08-21 entry there for the alternatives considered
and what would justify revisiting it.

e5 models are trained for asymmetric retrieval and require the caller to say
which side of the pair a text is on: a `"query: "` prefix for text being
searched *for*, `"passage: "` for text being searched *over*. Dropping the
prefix does not error — it silently degrades retrieval quality, which is a far
harder bug to notice than a crash. `embed_query` and `embed_passage` exist so
the call site cannot get this wrong by picking the wrong function.
"""

import asyncio
from functools import lru_cache
from typing import TYPE_CHECKING, cast

from app.core.logging import get_logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = get_logger(__name__)

EMBEDDING_MODEL_NAME = "intfloat/e5-large-v2"
EMBEDDING_DIMENSIONS = 1024


@lru_cache(maxsize=1)
def _model() -> "SentenceTransformer":
    """
    Load the model once per process, on first use.

    Not at import time: this module is imported at app/worker startup
    regardless of whether the FKG is ever touched, and loading ~1.3GB of
    weights on every process boot — including every test collection, since
    nothing here is mocked at the import level — would tax every code path
    with a cost only the embedding path needs to pay.
    """
    from sentence_transformers import SentenceTransformer

    logger.info("embeddings.loading_model", model=EMBEDDING_MODEL_NAME)
    return SentenceTransformer(EMBEDDING_MODEL_NAME)


def _encode(text: str) -> list[float]:
    """
    Blocking model call. Normalized so pgvector's `<=>` cosine-distance
    operator and a plain dot product agree — callers should not have to know
    which one the traversal SQL uses.

    torch and sentence_transformers are mypy's follow_imports="skip"
    (pyproject.toml) — real code, but too large to type-check on every run —
    so `.encode()`'s return is statically Any. The cast documents what it
    actually is: a numpy array whose .tolist() is a list[float] at runtime.
    """
    vector = _model().encode(text, normalize_embeddings=True)
    return cast(list[float], vector.tolist())


async def embed_passage(text: str) -> list[float]:
    """
    Embed text being stored for later retrieval — an ErrorSignature's label.

    Runs the blocking encode in a thread so ingestion's event loop is not
    stalled for its duration. Ingestion runs inside a DB transaction on the
    polling path, which serves many integrations concurrently; a synchronous
    tens-of-milliseconds CPU call there would serialize all of them behind it.
    """
    return await asyncio.to_thread(_encode, f"passage: {text}")


async def embed_query(text: str) -> list[float]:
    """Embed text being searched for — a new failure's normalized signature."""
    return await asyncio.to_thread(_encode, f"query: {text}")
