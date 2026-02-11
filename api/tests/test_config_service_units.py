from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from app import models
from app.config import settings
from app.services.config_service import ConfigService, ActiveConfigGuardError


@pytest.fixture()
def lab_and_node(test_db):
    lab = models.Lab(
        id="lab-1",
        name="Lab",
        owner_id=None,
        provider="docker",
        state="stopped",
        workspace_path="",
    )
    node = models.Node(
        lab_id=lab.id,
        gui_id="gui-1",
        display_name="R1",
        container_name="r1",
        device="ceos",
    )
    test_db.add_all([lab, node])
    test_db.commit()
    test_db.refresh(lab)
    test_db.refresh(node)
    return lab, node


def test_resolve_startup_config_priority(test_db, lab_and_node):
    lab, node = lab_and_node
    svc = ConfigService(test_db)

    # Latest snapshot fallback
    older = models.ConfigSnapshot(
        lab_id=lab.id,
        node_name=node.container_name,
        content="old",
        content_hash="h1",
        snapshot_type="manual",
        device_kind="ceos",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    newer = models.ConfigSnapshot(
        lab_id=lab.id,
        node_name=node.container_name,
        content="new",
        content_hash="h2",
        snapshot_type="manual",
        device_kind="ceos",
        created_at=datetime.now(timezone.utc),
    )
    test_db.add_all([older, newer])
    test_db.commit()

    assert svc.resolve_startup_config(node) == "new"

    # config_json overrides latest snapshot
    node.config_json = json.dumps({"startup-config": "from-json"})
    test_db.commit()
    assert svc.resolve_startup_config(node) == "from-json"

    # active snapshot overrides config_json
    node.active_config_snapshot_id = older.id
    test_db.commit()
    assert svc.resolve_startup_config(node) == "old"


def test_save_extracted_config_and_dedup(test_db, lab_and_node, tmp_path, monkeypatch):
    lab, node = lab_and_node
    monkeypatch.setattr(settings, "workspace", str(tmp_path))

    svc = ConfigService(test_db)
    snap = svc.save_extracted_config(lab.id, node.container_name, "cfg")
    test_db.commit()

    assert snap is not None
    test_db.refresh(node)
    assert node.active_config_snapshot_id == snap.id
    config = json.loads(node.config_json or "{}")
    assert config.get("startup-config") == "cfg"

    # Dedup: same content returns None
    snap2 = svc.save_extracted_config(lab.id, node.container_name, "cfg")
    assert snap2 is None


def test_delete_configs_guard(test_db, lab_and_node, tmp_path, monkeypatch):
    lab, node = lab_and_node
    monkeypatch.setattr(settings, "workspace", str(tmp_path))

    snap = models.ConfigSnapshot(
        lab_id=lab.id,
        node_name=node.container_name,
        content="cfg",
        content_hash="h1",
        snapshot_type="manual",
        device_kind="ceos",
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(snap)
    test_db.commit()

    node.active_config_snapshot_id = snap.id
    test_db.commit()

    svc = ConfigService(test_db)
    with pytest.raises(ActiveConfigGuardError):
        svc.delete_configs(lab.id, snapshot_ids=[snap.id])

    # Force deletion succeeds
    result = svc.delete_configs(lab.id, snapshot_ids=[snap.id], force=True)
    assert result["deleted_count"] == 1

