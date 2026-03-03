"""Tests for app.utils.locks — Redis locking, row-level locking, TTL, contention."""
from __future__ import annotations


import pytest
import redis as redis_mod

import app.utils.locks as locks
from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRedis:
    """Minimal Redis mock that supports SET NX EX, GET, DEL, EVAL, EXPIRE."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self._store:
            return False
        self._store[key] = value
        if ex:
            self._ttls[key] = ex
        return True

    def get(self, key):
        return self._store.get(key)

    def delete(self, key):
        if key in self._store:
            del self._store[key]
            self._ttls.pop(key, None)
            return 1
        return 0

    def expire(self, key, ttl):
        if key in self._store:
            self._ttls[key] = ttl
            return True
        return False

    def eval(self, script, nkeys, key, token, *args):
        # Safe Redis server-side Lua script execution (not Python eval)
        current = self._store.get(key)
        if current != token:
            return 0
        if "DEL" in script:
            del self._store[key]
            self._ttls.pop(key, None)
            return 1
        if "EXPIRE" in script:
            additional = int(args[0]) if args else 300
            self._ttls[key] = additional
            return 1
        return 0


class FailingEvalRedis(FakeRedis):
    """Redis mock that raises on server-side script execution (to test fallback)."""

    def eval(self, *args, **kwargs):
        raise redis_mod.RedisError("scripting not supported")


# ---------------------------------------------------------------------------
# Tests: _normalize_redis_value
# ---------------------------------------------------------------------------

class TestNormalizeRedisValue:
    def test_none_returns_none(self) -> None:
        assert locks._normalize_redis_value(None) is None

    def test_bytes_decoded(self) -> None:
        assert locks._normalize_redis_value(b"hello") == "hello"

    def test_string_passthrough(self) -> None:
        assert locks._normalize_redis_value("token") == "token"

    def test_int_converted_to_str(self) -> None:
        assert locks._normalize_redis_value(42) == "42"


# ---------------------------------------------------------------------------
# Tests: acquire_link_ops_lock
# ---------------------------------------------------------------------------

class TestAcquireLinkOpsLock:
    def test_acquire_returns_token(self, monkeypatch) -> None:
        monkeypatch.setattr(locks, "get_redis", lambda: FakeRedis())
        token = locks.acquire_link_ops_lock("lab-1")
        assert token is not None
        assert isinstance(token, str)

    def test_acquire_fails_when_already_held(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        token1 = locks.acquire_link_ops_lock("lab-1")
        assert token1 is not None
        token2 = locks.acquire_link_ops_lock("lab-1")
        assert token2 is None

    def test_acquire_fails_on_redis_error(self, monkeypatch) -> None:
        def bad_redis():
            raise redis_mod.RedisError("connection refused")

        monkeypatch.setattr(locks, "get_redis", bad_redis)
        token = locks.acquire_link_ops_lock("lab-1")
        assert token is None

    def test_different_labs_independent(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        t1 = locks.acquire_link_ops_lock("lab-1")
        t2 = locks.acquire_link_ops_lock("lab-2")
        assert t1 is not None
        assert t2 is not None


# ---------------------------------------------------------------------------
# Tests: release_link_ops_lock
# ---------------------------------------------------------------------------

class TestReleaseLinkOpsLock:
    def test_release_with_correct_token(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        token = locks.acquire_link_ops_lock("lab-1")
        assert locks.release_link_ops_lock("lab-1", token) is True

    def test_release_with_wrong_token(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        locks.acquire_link_ops_lock("lab-1")
        assert locks.release_link_ops_lock("lab-1", "wrong-token") is False

    def test_release_with_none_token(self, monkeypatch) -> None:
        assert locks.release_link_ops_lock("lab-1", None) is False

    def test_release_on_redis_error(self, monkeypatch) -> None:
        def bad_redis():
            raise redis_mod.RedisError("connection lost")

        monkeypatch.setattr(locks, "get_redis", bad_redis)
        result = locks.release_link_ops_lock("lab-1", "some-token")
        assert result is False


# ---------------------------------------------------------------------------
# Tests: extend_link_ops_lock
# ---------------------------------------------------------------------------

class TestExtendLinkOpsLock:
    def test_extend_with_owner_token(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        token = locks.acquire_link_ops_lock("lab-1")
        assert locks.extend_link_ops_lock("lab-1", token, additional_seconds=600) is True

    def test_extend_with_wrong_token(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        locks.acquire_link_ops_lock("lab-1")
        assert locks.extend_link_ops_lock("lab-1", "wrong", additional_seconds=600) is False

    def test_extend_with_none_token(self) -> None:
        assert locks.extend_link_ops_lock("lab-1", None) is False

    def test_extend_on_redis_error(self, monkeypatch) -> None:
        def bad_redis():
            raise redis_mod.RedisError("timeout")

        monkeypatch.setattr(locks, "get_redis", bad_redis)
        assert locks.extend_link_ops_lock("lab-1", "token") is False


# ---------------------------------------------------------------------------
# Tests: Fallback path for _release_if_owner / _extend_if_owner
# ---------------------------------------------------------------------------

class TestFallbackPath:
    def test_release_fallback_on_script_failure(self, monkeypatch) -> None:
        fake = FailingEvalRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        token = locks.acquire_link_ops_lock("lab-1")
        # Fallback path through GET + DELETE
        assert locks.release_link_ops_lock("lab-1", token) is True

    def test_extend_fallback_on_script_failure(self, monkeypatch) -> None:
        fake = FailingEvalRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        token = locks.acquire_link_ops_lock("lab-1")
        # Fallback path through GET + EXPIRE
        assert locks.extend_link_ops_lock("lab-1", token, additional_seconds=100) is True


# ---------------------------------------------------------------------------
# Tests: link_ops_lock context manager
# ---------------------------------------------------------------------------

class TestLinkOpsLockContext:
    def test_context_acquires_and_releases(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        with locks.link_ops_lock("lab-ctx") as acquired:
            assert acquired is True
            # Lock should be held
            assert fake.get("link_ops:lab-ctx") is not None
        # Lock should be released after context
        assert fake.get("link_ops:lab-ctx") is None

    def test_context_yields_false_when_cannot_acquire(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        # Pre-hold the lock
        locks.acquire_link_ops_lock("lab-ctx")
        with locks.link_ops_lock("lab-ctx") as acquired:
            assert acquired is False

    def test_context_releases_on_exception(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(locks, "get_redis", lambda: fake)
        with pytest.raises(RuntimeError):
            with locks.link_ops_lock("lab-exc") as acquired:
                assert acquired is True
                raise RuntimeError("boom")
        # Lock should still be released
        assert fake.get("link_ops:lab-exc") is None


# ---------------------------------------------------------------------------
# Tests: Row-level locking helpers
# ---------------------------------------------------------------------------

class TestRowLevelLocking:
    def test_get_link_state_for_update_returns_row(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        fetched = locks.get_link_state_for_update(
            test_db, sample_lab.id, "r1:eth1-r2:eth1"
        )
        assert fetched is not None
        assert fetched.link_name == "r1:eth1-r2:eth1"

    def test_get_link_state_for_update_returns_none(self, test_db, sample_lab) -> None:
        fetched = locks.get_link_state_for_update(
            test_db, sample_lab.id, "nonexistent"
        )
        assert fetched is None

    def test_get_link_state_by_id_for_update(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="x:eth1-y:eth1",
            source_node="x",
            source_interface="eth1",
            target_node="y",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        fetched = locks.get_link_state_by_id_for_update(test_db, link.id)
        assert fetched is not None
        assert fetched.id == link.id

    def test_get_link_state_by_id_nonexistent(self, test_db) -> None:
        fetched = locks.get_link_state_by_id_for_update(test_db, "fake-id")
        assert fetched is None

    def test_get_vxlan_tunnel_for_update(
        self, test_db, sample_vxlan_tunnel
    ) -> None:
        fetched = locks.get_vxlan_tunnel_for_update(
            test_db, sample_vxlan_tunnel.link_state_id
        )
        assert fetched is not None
        assert fetched.id == sample_vxlan_tunnel.id

    def test_get_vxlan_tunnel_nonexistent(self, test_db) -> None:
        fetched = locks.get_vxlan_tunnel_for_update(test_db, "fake-link-state-id")
        assert fetched is None
