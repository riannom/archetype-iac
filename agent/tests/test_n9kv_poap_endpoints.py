"""Tests for N9Kv POAP bootstrap endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "controller_secret", "")
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
    return TestClient(app, raise_server_exceptions=False)


def _write_startup_config(tmp_path: Path, lab_id: str, node_name: str, content: str) -> None:
    cfg = tmp_path / lab_id / "configs" / node_name
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "startup-config").write_text(content, encoding="utf-8")


def test_poap_startup_config_endpoint_serves_workspace_config(client: TestClient, tmp_path: Path) -> None:
    _write_startup_config(tmp_path, "lab1", "n9k1", "hostname n9k1\n")

    resp = client.get("/poap/lab1/n9k1/startup-config")
    assert resp.status_code == 200
    assert "hostname n9k1" in resp.text


def test_poap_script_endpoint_includes_startup_config_url(client: TestClient, tmp_path: Path) -> None:
    _write_startup_config(tmp_path, "lab2", "n9k2", "hostname n9k2\n")

    resp = client.get("/poap/lab2/n9k2/script.py")
    assert resp.status_code == 200
    assert "CONFIG_URL = \"http://testserver/poap/lab2/n9k2/startup-config\"" in resp.text
    assert "copy bootflash:startup-config startup-config" in resp.text


def test_poap_script_endpoint_returns_404_when_config_missing(client: TestClient) -> None:
    resp = client.get("/poap/lab3/n9k3/script.py")
    assert resp.status_code == 404
