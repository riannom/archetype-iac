from __future__ import annotations

import importlib


def test_import_api_modules() -> None:
    importlib.import_module("app.__init__")
    importlib.import_module("app.errors")
    importlib.import_module("app.iso.__init__")
    importlib.import_module("app.utils.__init__")
    importlib.import_module("app.routers.v1")


def test_v1_version_endpoint(test_client) -> None:
    resp = test_client.get("/api/v1/version")
    assert resp.status_code == 200
    assert resp.json()["version"] == "1"
