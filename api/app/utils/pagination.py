"""Pagination helper for unbounded database queries.

Prevents loading entire tables into memory by yielding results in batches.
Use for system-wide queries (all hosts, all labs) where row count is unbounded.
Per-lab queries are naturally bounded and don't need this.
"""
from __future__ import annotations

from typing import Generator, TypeVar

from sqlalchemy.orm import Query

T = TypeVar("T")


def paginated_query(query: Query, batch_size: int = 100) -> Generator[T, None, None]:
    """Yield results in batches to avoid loading entire table into memory.

    Args:
        query: SQLAlchemy query to paginate
        batch_size: Number of rows per batch

    Yields:
        Individual model instances
    """
    offset = 0
    while True:
        batch = query.offset(offset).limit(batch_size).all()
        if not batch:
            break
        yield from batch
        offset += batch_size
