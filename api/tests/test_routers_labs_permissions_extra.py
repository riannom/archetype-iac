from __future__ import annotations

import json

import app.routers.labs as labs_router  # noqa: F401
import app.routers.permissions as permissions_router  # noqa: F401
import pytest

from app import models


def test_list_and_create_lab(test_client, test_db, test_user, auth_headers) -> None:
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


def test_lab_layout_roundtrip(test_client, test_db, test_user, auth_headers) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    layout = {"nodes": [{"id": "n1", "x": 1, "y": 2}]}
    put_resp = test_client.put(
        f"/labs/{lab.id}/layout",
        json=layout,
        headers=auth_headers,
    )
    assert put_resp.status_code == 200

    get_resp = test_client.get(f"/labs/{lab.id}/layout", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["layout"]["nodes"][0]["id"] == "n1"

    del_resp = test_client.delete(f"/labs/{lab.id}/layout", headers=auth_headers)
    assert del_resp.status_code == 200


def test_permissions_flow(test_client, test_db, test_user, admin_user, auth_headers, admin_auth_headers) -> None:
    lab = models.Lab(
        name="Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/lab",
    )
    test_db.add(lab)
    test_db.commit()

    # Non-owner/non-admin cannot add
    denied = test_client.post(
        f"/labs/{lab.id}/permissions",
        json={"user_email": admin_user.email, "role": "viewer"},
        headers=auth_headers,
    )
    assert denied.status_code == 403

    # Owner can add
    added = test_client.post(
        f"/labs/{lab.id}/permissions",
        json={"user_email": admin_user.email, "role": "viewer"},
        headers=auth_headers,
    )
    assert added.status_code == 200

    listed = test_client.get(f"/labs/{lab.id}/permissions", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()["permissions"]) == 1

    perm_id = listed.json()["permissions"][0]["id"]
    deleted = test_client.delete(
        f"/labs/{lab.id}/permissions/{perm_id}",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
