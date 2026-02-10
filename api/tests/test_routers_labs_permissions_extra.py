from __future__ import annotations


import app.routers.labs as labs_router  # noqa: F401
import app.routers.permissions as permissions_router  # noqa: F401

from app import models


def test_list_and_create_lab(test_client, test_db, test_user, auth_headers, tmp_path, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "workspace", str(tmp_path))

    resp = test_client.get("/labs", headers=auth_headers)
    assert resp.status_code == 200

    payload = {"name": "Lab One"}
    create = test_client.post("/labs", json=payload, headers=auth_headers)
    assert create.status_code == 200
    data = create.json()
    assert data["name"] == "Lab One"

    list_again = test_client.get("/labs", headers=auth_headers)
    assert list_again.status_code == 200
    assert len(list_again.json()["labs"]) == 1


def test_get_update_delete_lab(test_client, test_db, test_user, auth_headers) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    resp = test_client.get(f"/labs/{lab.id}", headers=auth_headers)
    assert resp.status_code == 200

    update = test_client.put(
        f"/labs/{lab.id}",
        json={"name": "Lab Updated"},
        headers=auth_headers,
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Lab Updated"

    delete = test_client.delete(f"/labs/{lab.id}", headers=auth_headers)
    assert delete.status_code == 200


def test_lab_layout_roundtrip(test_client, test_db, test_user, auth_headers, tmp_path, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "workspace", str(tmp_path))

    lab = models.Lab(
        name="Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    # LabLayout expects nodes as dict[str, NodeLayout]
    layout = {"nodes": {"n1": {"x": 1, "y": 2}}}
    put_resp = test_client.put(
        f"/labs/{lab.id}/layout",
        json=layout,
        headers=auth_headers,
    )
    assert put_resp.status_code == 200

    get_resp = test_client.get(f"/labs/{lab.id}/layout", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["nodes"]["n1"]["x"] == 1

    del_resp = test_client.delete(f"/labs/{lab.id}/layout", headers=auth_headers)
    assert del_resp.status_code == 200


def test_permissions_flow(test_client, test_db, test_user, admin_user, auth_headers, admin_auth_headers) -> None:
    # Create lab owned by admin_user (not test_user)
    lab = models.Lab(
        name="Lab",
        owner_id=admin_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    # Give test_user viewer access so they can see the lab but not modify permissions
    perm = models.Permission(lab_id=lab.id, user_id=test_user.id, role="viewer")
    test_db.add(perm)
    test_db.commit()

    # Non-owner/non-admin (viewer) cannot add permissions
    denied = test_client.post(
        f"/labs/{lab.id}/permissions",
        json={"user_identifier": test_user.email, "role": "editor"},
        headers=auth_headers,
    )
    assert denied.status_code == 403

    # Remove existing viewer perm to avoid UNIQUE constraint on (lab_id, user_id)
    test_db.delete(perm)
    test_db.commit()

    # Owner can add
    added = test_client.post(
        f"/labs/{lab.id}/permissions",
        json={"user_identifier": test_user.email, "role": "editor"},
        headers=admin_auth_headers,
    )
    assert added.status_code == 200

    listed = test_client.get(f"/labs/{lab.id}/permissions", headers=admin_auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()["permissions"]) >= 1

    # Find the editor permission we just added
    editor_perms = [p for p in listed.json()["permissions"] if p["role"] == "editor"]
    assert len(editor_perms) == 1
    perm_id = editor_perms[0]["id"]
    deleted = test_client.delete(
        f"/labs/{lab.id}/permissions/{perm_id}",
        headers=admin_auth_headers,
    )
    assert deleted.status_code == 200
