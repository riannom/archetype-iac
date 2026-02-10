from __future__ import annotations


import app.utils.locks as locks
from app import models


def test_link_ops_lock_acquire_release(monkeypatch) -> None:
    calls = []

    class FakeRedis:
        def set(self, key, value, nx, ex):
            calls.append(("set", key, nx, ex))
            return True

        def delete(self, key):
            calls.append(("delete", key))
            return 1

        def expire(self, key, ttl):
            calls.append(("expire", key, ttl))
            return True

    monkeypatch.setattr(locks, "get_redis", lambda: FakeRedis())

    assert locks.acquire_link_ops_lock("lab1")
    locks.release_link_ops_lock("lab1")
    assert locks.extend_link_ops_lock("lab1", additional_seconds=10)

    assert calls[0][0] == "set"
    assert calls[1][0] == "delete"
    assert calls[2][0] == "expire"


def test_link_ops_lock_context(monkeypatch) -> None:
    class FakeRedis:
        def set(self, *args, **kwargs):
            return True

        def delete(self, *args, **kwargs):
            return 1

    monkeypatch.setattr(locks, "get_redis", lambda: FakeRedis())

    with locks.link_ops_lock("lab2") as acquired:
        assert acquired


def test_row_level_lock_helpers(test_db, sample_lab) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
    )
    test_db.add(link_state)
    test_db.commit()

    fetched = locks.get_link_state_for_update(test_db, sample_lab.id, link_state.link_name)
    assert fetched is not None

    fetched_by_id = locks.get_link_state_by_id_for_update(test_db, link_state.id)
    assert fetched_by_id is not None
