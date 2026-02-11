from __future__ import annotations

from app.utils.pagination import paginated_query


class EmptyQuery:
    def offset(self, _value):
        return self

    def limit(self, _value):
        return self

    def all(self):
        return []


def test_paginated_query_empty():
    assert list(paginated_query(EmptyQuery(), batch_size=2)) == []
