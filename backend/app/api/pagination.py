"""
Keyset ("cursor") pagination for list endpoints.

Keyset rather than LIMIT/OFFSET because these feeds are append-heavy: rows
arrive at the head while a client is paging, so an offset page shifts under the
reader and silently duplicates or skips rows. A cursor names the last row seen,
so the next page resumes exactly where the previous one ended regardless of what
was inserted in the meantime.

The cursor is opaque on purpose — it is base64 of an internal sort key, not a
documented format, so its contents can change without breaking clients. It is
also not a security boundary: it names a position in an already
workspace-scoped query, and every endpoint re-applies its own scoping.
"""

import base64
import binascii
import uuid
from datetime import datetime

from pydantic import BaseModel

_SEPARATOR = "|"


class CursorPage[T](BaseModel):
    """
    An envelope for a cursor-paginated list.

    The rest of the API returns bare arrays. Pagination is the exception: a
    cursor is response-level metadata with nowhere to live inside an array.
    See DECISIONS.md — the envelope applies to cursor-paginated endpoints only.
    """

    items: list[T]
    # None means this is the last page. Clients must treat it as opaque and
    # pass it back verbatim rather than parsing or constructing one.
    next_cursor: str | None = None


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    """Encode a (created_at, id) sort position as an opaque cursor."""
    raw = f"{created_at.isoformat()}{_SEPARATOR}{row_id}"
    # Padding is stripped so the cursor survives being passed around as a query
    # string without escaping; decode_cursor restores it.
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """
    Decode a cursor back into its (created_at, id) sort position.

    Raises ValueError for anything that is not a cursor this module produced.
    Callers turn that into a 400 — a malformed cursor is a client error, and
    letting it through would silently return page one instead, which reads as a
    pagination loop rather than a bug.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Cursor is not valid base64") from exc

    timestamp, _, row_id = raw.partition(_SEPARATOR)
    if not row_id:
        raise ValueError("Cursor is missing its row id")

    # fromisoformat and UUID raise ValueError already, which is the contract.
    return datetime.fromisoformat(timestamp), uuid.UUID(row_id)
