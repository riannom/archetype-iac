from __future__ import annotations


import app.utils.locks as locks
from app import models


def test_link_ops_lock_acquire_release(monkeypatch) -> None:
    calls = []
    lock_value = {"value": None}

    class FakeRedis:
        def set(self, key, value, nx, ex):
            calls.append(("set", key, nx, ex))
            lock_value["value"] = value
            return True

        def get(self, key):
            calls.append(("get", key))
            return lock_value["value"]

        def eval(self, script, nkeys, key, token, *args):
            calls.append(("eval", key, token))
            if token != lock_value["value"]:
                return 0
            if "DEL" in script:
                lock_value["value"] = None
                return 1
            if "EXPIRE" in script:
                return 1
            return 0

        def delete(self, key):
            calls.append(("delete", key))
            return 1

        def expire(self, key, ttl):
            calls.append(("expire", key, ttl))
            return True

    monkeypatch.setattr(locks, "get_redis", lambda: FakeRedis())

    token = locks.acquire_link_ops_lock("lab1")
    assert token is not None
    assert locks.release_link_ops_lock("lab1", token)
    lock_value["value"] = token
    assert locks.extend_link_ops_lock("lab1", token, additional_seconds=10)

    assert calls[0][0] == "set"
    assert calls[1][0] == "eval"
    assert calls[2][0] == "eval"


def test_link_ops_lock_context(monkeypatch) -> None:
    lock_value = {"value": None}

    class FakeRedis:
        def set(self, *args, **kwargs):
            lock_value["value"] = args[1]
            return True

        def eval(self, script, _nkeys, _key, token, *args):
            if token != lock_value["value"]:
                return 0
            if "DEL" in script:
                lock_value["value"] = None
                return 1
            return 1

    monkeypatch.setattr(locks, "get_redis", lambda: FakeRedis())

    with locks.link_ops_lock("lab2") as acquired:
        assert acquired


def test_link_ops_lock_release_requires_owner_token(monkeypatch) -> None:
    lock_value = {"value": None}

    class FakeRedis:
        def set(self, key, value, nx, ex):
            lock_value["value"] = value
            return True

        def eval(self, script, _nkeys, _key, token, *args):
            if token != lock_value["value"]:
                return 0
            if "DEL" in script:
                lock_value["value"] = None
                return 1
            return 1

        def get(self, _key):
            return lock_value["value"]

        def delete(self, _key):
            lock_value["value"] = None
            return 1

    monkeypatch.setattr(locks, "get_redis", lambda: FakeRedis())

    token = locks.acquire_link_ops_lock("lab1")
    assert token is not None
    assert not locks.release_link_ops_lock("lab1", "wrong-token")
    assert locks.release_link_ops_lock("lab1", token)


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
