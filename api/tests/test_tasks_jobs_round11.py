"""Tests for api/app/tasks/jobs.py — preflight, auto-extract, webhook (round 11).

Covers _run_job_preflight_checks (image sync path), _auto_extract_configs_before_destroy,
and _dispatch_webhook error suppression.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.orm import Session

from app import models
from app.tasks.jobs import (
    _auto_extract_configs_before_destroy,
    _dispatch_webhook,
    _run_job_preflight_checks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lab(db, owner_id, state="running"):
    lab = models.Lab(name="Test", owner_id=owner_id, provider="docker", state=state)
    db.add(lab)
    db.commit()
    db.refresh(lab)
    return lab


def _make_host(db, host_id="h1"):
    from datetime import datetime, timezone
    h = models.Host(
        id=host_id, name="Agent", address="localhost:8080",
        status="online", capabilities="{}", last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _run_job_preflight_checks — image sync path
# ---------------------------------------------------------------------------


class TestPreflightImageSync:

    def test_non_up_action_skips_checks(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
            ok, msg = _run(_run_job_preflight_checks(test_db, lab, host, "restart"))
        assert ok is True
        assert msg is None

    def test_down_action_skips_image_sync(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.settings") as mock_settings:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
            mock_settings.image_sync_enabled = True
            mock_settings.image_sync_pre_deploy_check = True
            ok, msg = _run(_run_job_preflight_checks(test_db, lab, host, "down"))
        assert ok is True

    def test_all_images_ready(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.TopologyService") as mock_topo_cls, \
             patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
            mock_settings.image_sync_enabled = True
            mock_settings.image_sync_pre_deploy_check = True
            mock_settings.image_sync_timeout = 60
            topo = MagicMock()
            topo.get_required_images.return_value = ["ceos:4.28"]
            topo.get_image_to_nodes_map.return_value = {"ceos:4.28": ["R1"]}
            mock_topo_cls.return_value = topo
            mock_ensure.return_value = (True, [], {})

            ok, msg = _run(_run_job_preflight_checks(test_db, lab, host, "up"))
        assert ok is True
        assert msg is None

    def test_missing_images_fails(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.TopologyService") as mock_topo_cls, \
             patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
            mock_settings.image_sync_enabled = True
            mock_settings.image_sync_pre_deploy_check = True
            mock_settings.image_sync_timeout = 60
            topo = MagicMock()
            topo.get_required_images.return_value = ["ceos:4.28"]
            topo.get_image_to_nodes_map.return_value = {"ceos:4.28": ["R1"]}
            mock_topo_cls.return_value = topo
            mock_ensure.return_value = (False, ["ceos:4.28"], {})

            ok, msg = _run(_run_job_preflight_checks(test_db, lab, host, "up"))
        assert ok is False
        assert "Missing images" in msg

    def test_many_missing_truncated(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        missing = [f"img{i}" for i in range(10)]
        with patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.TopologyService") as mock_topo_cls, \
             patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
            mock_settings.image_sync_enabled = True
            mock_settings.image_sync_pre_deploy_check = True
            mock_settings.image_sync_timeout = 60
            topo = MagicMock()
            topo.get_required_images.return_value = missing
            topo.get_image_to_nodes_map.return_value = {}
            mock_topo_cls.return_value = topo
            mock_ensure.return_value = (False, missing, {})

            ok, msg = _run(_run_job_preflight_checks(test_db, lab, host, "up"))
        assert ok is False
        assert "+5 more" in msg

    def test_image_sync_exception(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.TopologyService") as mock_topo_cls, \
             patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
            mock_settings.image_sync_enabled = True
            mock_settings.image_sync_pre_deploy_check = True
            mock_settings.image_sync_timeout = 60
            topo = MagicMock()
            topo.get_required_images.return_value = ["img"]
            topo.get_image_to_nodes_map.return_value = {}
            mock_topo_cls.return_value = topo
            mock_ensure.side_effect = RuntimeError("boom")

            ok, msg = _run(_run_job_preflight_checks(test_db, lab, host, "up"))
        assert ok is False
        assert "unexpectedly" in msg

    def test_no_images_skips_sync(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.TopologyService") as mock_topo_cls:
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
            mock_settings.image_sync_enabled = True
            mock_settings.image_sync_pre_deploy_check = True
            topo = MagicMock()
            topo.get_required_images.return_value = []
            mock_topo_cls.return_value = topo

            ok, msg = _run(_run_job_preflight_checks(test_db, lab, host, "up"))
        assert ok is True


# ---------------------------------------------------------------------------
# _auto_extract_configs_before_destroy
# ---------------------------------------------------------------------------


class TestAutoExtract:

    def test_disabled_feature_returns_early(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = False
            _run(_auto_extract_configs_before_destroy(test_db, lab, host))
        # Should not raise

    def test_saves_configs_via_config_service(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        n = models.Node(
            lab_id=lab.id, gui_id="n1", display_name="R1",
            container_name="archetype-test-r1", device="linux",
        )
        test_db.add(n)
        test_db.commit()

        with patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.services.config_service.ConfigService") as mock_cs_cls:
            mock_settings.feature_auto_extract_on_destroy = True
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [{"node_name": "archetype-test-r1", "content": "hostname R1"}],
            })
            mock_svc = MagicMock()
            mock_svc.save_extracted_config.return_value = MagicMock()
            mock_cs_cls.return_value = mock_svc

            _run(_auto_extract_configs_before_destroy(test_db, lab, host))
        mock_svc.save_extracted_config.assert_called_once()

    def test_skips_missing_node_name(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        host = _make_host(test_db)
        with patch("app.tasks.jobs.settings") as mock_settings, \
             patch("app.tasks.jobs.agent_client") as mock_ac, \
             patch("app.services.config_service.ConfigService") as mock_cs_cls:
            mock_settings.feature_auto_extract_on_destroy = True
            mock_ac.is_agent_online.return_value = True
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [{"node_name": None, "content": "stuff"}],
            })
            mock_svc = MagicMock()
            mock_cs_cls.return_value = mock_svc

            _run(_auto_extract_configs_before_destroy(test_db, lab, host))
        mock_svc.save_extracted_config.assert_not_called()


# ---------------------------------------------------------------------------
# _dispatch_webhook
# ---------------------------------------------------------------------------


class TestDispatchWebhook:

    def test_db_error_suppressed(self, test_db: Session, test_user: models.User):
        lab = _make_lab(test_db, test_user.id)
        job = models.Job(
            lab_id=lab.id, user_id=test_user.id,
            action="up", status="completed",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.webhooks") as mock_wh:
            mock_wh.dispatch_webhook_event = AsyncMock(side_effect=Exception("DB error"))
            # Should not raise
            _run(_dispatch_webhook("lab.deployed", lab, job, test_db))
