from __future__ import annotations

from app.utils.cache import cache_set


def test_cache_set_handles_unserializable(monkeypatch):
    class FakeRedis:
        def setex(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("app.utils.cache.get_redis", lambda: FakeRedis())

    class Unserializable:
        pass

    # json.dumps will fall back to str via default=str
    cache_set("key", Unserializable())
