"""Tests for deploy lock, run_multihost_deploy, and run_multihost_destroy.

Covers:
- acquire_deploy_lock: success, already held, Redis error, partial failure
- run_multihost_deploy: job not found, lab not found, successful deploy
- run_multihost_destroy: marks lab stopped, offline agent completes with warnings
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.tasks.jobs import (
    acquire_deploy_lock,
    release_deploy_lock,
    run_multihost_deploy,
    run_multihost_destroy,
)


def _mock_get_session(test_db: Session):
    """Create a mock get_session context manager that yields the test database session."""
    @contextmanager
    def mock_session():
        yield test_db
    return mock_session


class TestAcquireDeployLock:
    """Tests for acquire_deploy_lock function."""

    def _make_fake_redis(self, held_keys: set[str] | None = None):
        """Build a fake Redis that tracks SET NX calls.

        Args:
            held_keys: Keys that are already "held" (SET NX returns False).
        """
        held = set(held_keys or [])
        store: dict[str, str] = {}

        fake = MagicMock()

        def _set(key, value, nx=False, ex=None):
            if nx and key in held:
                return False
            store[key] = value
            held.add(key)
            return True

        def _get(key):
            val = store.get(key)
            if val is not None:
                return val.encode()
            return None

        def _delete(key):
            held.discard(key)
            store.pop(key, None)

        fake.set = MagicMock(side_effect=_set)
        fake.get = MagicMock(side_effect=_get)
        fake.delete = MagicMock(side_effect=_delete)

        return fake, store

    def test_acquire_successfully(self):
        """All requested locks are acquired when none are held."""
        fake_redis, _ = self._make_fake_redis()
        with patch("app.tasks.jobs.get_redis", return_value=fake_redis):
            success, locked = acquire_deploy_lock("lab-1", ["r1", "r2"], "agent-a")

        assert success is True
        assert set(locked) == {"r1", "r2"}

    def test_already_held_returns_false(self):
        """If another agent holds a lock, acquire returns False with failed nodes."""
        fake_redis, _ = self._make_fake_redis(
            held_keys={"deploy_lock:lab-1:r2"}
        )
        # Pre-populate so .get() returns a holder value
        fake_redis.get = MagicMock(return_value=b"agent:agent-b:time:2026-01-01T00:00:00")

        with patch("app.tasks.jobs.get_redis", return_value=fake_redis):
            success, failed = acquire_deploy_lock("lab-1", ["r1", "r2"], "agent-a")

        assert success is False
        assert "r2" in failed

    def test_redis_error_degrades_gracefully(self):
        """Redis connection failure still returns success (degrade gracefully)."""
        import redis as redis_lib

        fake_redis = MagicMock()
        fake_redis.set.side_effect = redis_lib.RedisError("connection refused")

        with patch("app.tasks.jobs.get_redis", return_value=fake_redis):
            success, nodes = acquire_deploy_lock("lab-1", ["r1", "r2"], "agent-a")

        # On Redis error, the function proceeds without lock
        assert success is True
        assert set(nodes) == {"r1", "r2"}

    def test_partial_failure_releases_acquired(self):
        """If some nodes fail to lock, already-acquired locks are released."""
        fake_redis, _ = self._make_fake_redis(
            held_keys={"deploy_lock:lab-1:r3"}
        )
        fake_redis.get = MagicMock(return_value=b"agent:other:time:2026-01-01")

        with patch("app.tasks.jobs.get_redis", return_value=fake_redis):
            success, failed = acquire_deploy_lock("lab-1", ["r1", "r2", "r3"], "agent-a")

        assert success is False
        assert "r3" in failed
        # Verify that r1 and r2 locks were released (delete called for them)
        delete_calls = [c.args[0] for c in fake_redis.delete.call_args_list]
        assert "deploy_lock:lab-1:r1" in delete_calls
        assert "deploy_lock:lab-1:r2" in delete_calls

    def test_release_deploy_lock(self):
        """release_deploy_lock deletes keys from Redis."""
        fake_redis, _ = self._make_fake_redis()

        with patch("app.tasks.jobs.get_redis", return_value=fake_redis):
            release_deploy_lock("lab-1", ["r1", "r2"])

        assert fake_redis.delete.call_count == 2


class TestRunMultihostDeploy:
    """Tests for run_multihost_deploy function."""

    @pytest.mark.asyncio
    async def test_job_not_found_returns_early(self, test_db: Session):
        """Job that doesn't exist should log error and return without crashing."""
        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            # Should not raise
            await run_multihost_deploy("nonexistent-job", "lab-id")

    @pytest.mark.asyncio
    async def test_lab_not_found_marks_failed(
        self, test_db: Session, test_user: models.User
    ):
        """Job with missing lab should be marked as failed."""
        job = models.Job(
            lab_id="nonexistent-lab",
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            await run_multihost_deploy(job.id, "nonexistent-lab")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "not found" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_successful_deploy_marks_completed(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Successful multi-host deploy marks job completed and lab running."""
        host1 = multiple_hosts[0]
        host2 = multiple_hosts[1]

        lab = models.Lab(
            name="Multi-host Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        # Create nodes with explicit host assignments
        node1 = models.Node(
            lab_id=lab.id,
            gui_id="r1",
            display_name="r1",
            container_name="r1",
            node_type="device",
            device="linux",
            host_id=host1.id,
        )
        node2 = models.Node(
            lab_id=lab.id,
            gui_id="r2",
            display_name="r2",
            container_name="r2",
            node_type="device",
            device="linux",
            host_id=host2.id,
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            with patch(
                "app.tasks.jobs_multihost.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch(
                    "app.tasks.jobs_multihost.agent_client.get_lab_status_from_agent",
                    new_callable=AsyncMock,
                    return_value={"nodes": []},
                ):
                    with patch(
                        "app.tasks.jobs_multihost.agent_client.deploy_to_agent",
                        new_callable=AsyncMock,
                    ) as mock_deploy:
                        mock_deploy.return_value = {"status": "completed", "stdout": "OK"}
                        with patch(
                            "app.tasks.link_orchestration.create_deployment_links",
                            new_callable=AsyncMock,
                            return_value=(0, 0),
                        ):
                            with patch(
                                "app.tasks.jobs_multihost._dispatch_webhook",
                                new_callable=AsyncMock,
                            ):
                                with patch(
                                    "app.tasks.jobs_multihost.emit_deploy_finished",
                                    new_callable=AsyncMock,
                                ):
                                    with patch(
                                        "app.tasks.jobs_multihost._capture_node_ips",
                                        new_callable=AsyncMock,
                                    ):
                                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed"
        assert lab.state == "running"
        # Both hosts should have been deployed to
        assert mock_deploy.await_count == 2


class TestRunMultihostDestroy:
    """Tests for run_multihost_destroy function."""

    @pytest.mark.asyncio
    async def test_job_not_found_returns_early(self, test_db: Session):
        """Non-existent job should return early without crashing."""
        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            await run_multihost_destroy("nonexistent-job", "lab-id")

    @pytest.mark.asyncio
    async def test_lab_not_found_marks_failed(
        self, test_db: Session, test_user: models.User
    ):
        """Job with missing lab should be marked as failed."""
        job = models.Job(
            lab_id="nonexistent-lab",
            user_id=test_user.id,
            action="down",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            await run_multihost_destroy(job.id, "nonexistent-lab")

        test_db.refresh(job)
        assert job.status == "failed"
        assert "not found" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_marks_lab_stopped_on_full_success(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Full success clears links and marks lab stopped."""
        host1 = multiple_hosts[0]
        host2 = multiple_hosts[1]

        lab = models.Lab(
            name="Multi-host Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="down",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        node1 = models.Node(
            lab_id=lab.id,
            gui_id="r1",
            display_name="r1",
            container_name="r1",
            node_type="device",
            device="linux",
            host_id=host1.id,
        )
        node2 = models.Node(
            lab_id=lab.id,
            gui_id="r2",
            display_name="r2",
            container_name="r2",
            node_type="device",
            device="linux",
            host_id=host2.id,
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            with patch(
                "app.tasks.jobs_multihost.agent_client.is_agent_online",
                side_effect=lambda host: host.id in {host1.id, host2.id},
            ):
                with patch(
                    "app.tasks.link_orchestration.teardown_deployment_links",
                    new_callable=AsyncMock,
                    return_value=(0, 0),
                ):
                    with patch(
                        "app.tasks.jobs_multihost.agent_client.destroy_on_agent",
                        new_callable=AsyncMock,
                    ) as mock_destroy:
                        mock_destroy.side_effect = [
                            {"status": "completed"},
                            {"status": "completed"},
                        ]
                        with patch(
                            "app.tasks.jobs_multihost._dispatch_webhook",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "app.tasks.jobs_multihost.emit_destroy_finished",
                                new_callable=AsyncMock,
                            ):
                                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed"
        assert lab.state == "stopped"
        assert mock_destroy.await_count == 2

    @pytest.mark.asyncio
    async def test_offline_agent_completes_with_warnings(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """Offline agent during destroy should result in completed_with_warnings."""
        online_host = multiple_hosts[0]
        offline_host = multiple_hosts[2]  # status="offline" from fixture

        lab = models.Lab(
            name="Multi-host Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="down",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        node1 = models.Node(
            lab_id=lab.id,
            gui_id="r1",
            display_name="r1",
            container_name="r1",
            node_type="device",
            device="linux",
            host_id=online_host.id,
        )
        node2 = models.Node(
            lab_id=lab.id,
            gui_id="r2",
            display_name="r2",
            container_name="r2",
            node_type="device",
            device="linux",
            host_id=offline_host.id,
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            with patch(
                "app.tasks.jobs_multihost.agent_client.is_agent_online",
                side_effect=lambda host: host.id == online_host.id,
            ):
                with patch(
                    "app.tasks.link_orchestration.teardown_deployment_links",
                    new_callable=AsyncMock,
                    return_value=(0, 0),
                ):
                    with patch(
                        "app.tasks.jobs_multihost.agent_client.destroy_on_agent",
                        new_callable=AsyncMock,
                    ) as mock_destroy:
                        mock_destroy.return_value = {"status": "completed"}
                        with patch(
                            "app.tasks.jobs_multihost._dispatch_webhook",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "app.tasks.jobs_multihost.emit_job_failed",
                                new_callable=AsyncMock,
                            ):
                                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed_with_warnings"
        assert lab.state == "error"
        # Only the online host should have been called
        assert mock_destroy.await_count == 1

    @pytest.mark.asyncio
    async def test_no_online_agents_fails(
        self,
        test_db: Session,
        test_user: models.User,
        multiple_hosts: list[models.Host],
    ):
        """If all agents are offline, destroy should fail."""
        offline_host = multiple_hosts[2]

        lab = models.Lab(
            name="Multi-host Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="down",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        node1 = models.Node(
            lab_id=lab.id,
            gui_id="r1",
            display_name="r1",
            container_name="r1",
            node_type="device",
            device="linux",
            host_id=offline_host.id,
        )
        test_db.add(node1)
        test_db.commit()

        with patch("app.tasks.jobs_multihost.get_session", _mock_get_session(test_db)):
            with patch(
                "app.tasks.jobs_multihost.agent_client.is_agent_online",
                return_value=False,
            ):
                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "no online agents found" in job.log_path.lower()
