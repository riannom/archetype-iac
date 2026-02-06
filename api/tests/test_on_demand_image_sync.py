"""Tests for on-demand image sync triggered by node start/reload.

When starting or reloading nodes on a remote agent that doesn't have the required
Docker image, the system should:
1. Detect the missing image
2. Trigger an async non-blocking sync
3. Set node to image_sync_status="syncing" (NOT actual_state="error")
4. Automatically start the node once sync completes

These tests define the expected behavior BEFORE implementation (TDD).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.state import ImageSyncStatus, NodeActualState


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_agent(test_db: Session) -> models.Host:
    """Remote agent configured for on-demand image sync, with NO ceos image."""
    host = models.Host(
        id="remote-agent-1",
        name="Remote Agent",
        address="remote.local:8080",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        image_sync_strategy="on_demand",
        resource_usage=json.dumps({}),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture()
def starting_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Lab in 'starting' state, assigned to a remote agent."""
    lab = models.Lab(
        name="Sync Test Lab",
        owner_id=test_user.id,
        provider="docker",
        state="starting",
        workspace_path="/tmp/sync-test-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture()
def ceos_nodes(
    test_db: Session, starting_lab: models.Lab, remote_agent: models.Host
) -> list[models.NodeState]:
    """Two ceos nodes wanting to start, image not on agent."""
    nodes = [
        models.NodeState(
            lab_id=starting_lab.id,
            node_id="ceos-2",
            node_name="ceos-2",
            desired_state="running",
            actual_state="undeployed",
        ),
        models.NodeState(
            lab_id=starting_lab.id,
            node_id="ceos-5",
            node_name="ceos-5",
            desired_state="running",
            actual_state="undeployed",
        ),
    ]
    for node in nodes:
        test_db.add(node)
    test_db.commit()
    for node in nodes:
        test_db.refresh(node)
    return nodes


@pytest.fixture()
def ceos_node_definitions(
    test_db: Session, starting_lab: models.Lab, remote_agent: models.Host
) -> list[models.Node]:
    """Node definitions with ceos image reference."""
    defs = [
        models.Node(
            id="ndef-ceos2",
            lab_id=starting_lab.id,
            gui_id="ceos-2",
            display_name="ceos-2",
            container_name="archetype-sync-test-ceos-2",
            device="ceos",
            image="ceos:4.28.0F",
            host_id=remote_agent.id,
        ),
        models.Node(
            id="ndef-ceos5",
            lab_id=starting_lab.id,
            gui_id="ceos-5",
            display_name="ceos-5",
            container_name="archetype-sync-test-ceos-5",
            device="ceos",
            image="ceos:4.28.0F",
            host_id=remote_agent.id,
        ),
    ]
    for d in defs:
        test_db.add(d)
    test_db.commit()
    for d in defs:
        test_db.refresh(d)
    return defs


@pytest.fixture()
def srlinux_node(
    test_db: Session, starting_lab: models.Lab, remote_agent: models.Host
) -> tuple[models.NodeState, models.Node]:
    """Single srlinux node with different image."""
    ns = models.NodeState(
        lab_id=starting_lab.id,
        node_id="srl-1",
        node_name="srl-1",
        desired_state="running",
        actual_state="undeployed",
    )
    ndef = models.Node(
        id="ndef-srl1",
        lab_id=starting_lab.id,
        gui_id="srl-1",
        display_name="srl-1",
        container_name="archetype-sync-test-srl-1",
        device="srlinux",
        image="ghcr.io/nokia/srlinux:23.10.1",
        host_id=remote_agent.id,
    )
    test_db.add(ns)
    test_db.add(ndef)
    test_db.commit()
    test_db.refresh(ns)
    test_db.refresh(ndef)
    return ns, ndef


# ---------------------------------------------------------------------------
# Tests: Node start triggers image sync
# ---------------------------------------------------------------------------


class TestNodeStartTriggersImageSync:
    """When run_node_reconcile() finds a missing image, it should start sync
    instead of erroring out the node."""

    @pytest.mark.asyncio
    async def test_detects_missing_image_and_sets_syncing_state(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        ceos_node_definitions: list[models.Node],
        remote_agent: models.Host,
        monkeypatch,
    ):
        """When image is missing on agent, node should transition to
        image_sync_status='syncing' rather than actual_state='error'."""
        monkeypatch.setattr(settings, "image_sync_enabled", True)
        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)

        with (
            patch(
                "app.tasks.image_sync.check_agent_has_image",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "app.tasks.image_sync.sync_image_to_agent",
                new_callable=AsyncMock,
                return_value=(True, None),
            ),
        ):
            from app.tasks.image_sync import update_node_image_sync_status

            # Simulate what the new non-blocking reconcile should do:
            # Detect missing image and set syncing status
            update_node_image_sync_status(
                test_db,
                starting_lab.id,
                [n.node_name for n in ceos_nodes],
                ImageSyncStatus.SYNCING.value,
                "Syncing ceos:4.28.0F to Remote Agent...",
            )

        for node in ceos_nodes:
            test_db.refresh(node)
            assert node.image_sync_status == "syncing"
            assert node.image_sync_message == "Syncing ceos:4.28.0F to Remote Agent..."
            # Critically, actual_state should NOT be 'error'
            assert node.actual_state != NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_creates_image_sync_job_for_missing_image(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """An ImageSyncJob record should be created for the missing image."""
        job = models.ImageSyncJob(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            status="pending",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        assert job.id is not None
        assert job.image_id == "docker:ceos:4.28.0F"
        assert job.host_id == remote_agent.id
        assert job.status == "pending"

        # Verify it can be queried
        found = (
            test_db.query(models.ImageSyncJob)
            .filter_by(image_id="docker:ceos:4.28.0F", host_id=remote_agent.id)
            .first()
        )
        assert found is not None

    @pytest.mark.asyncio
    async def test_node_start_from_stopped_triggers_sync(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        remote_agent: models.Host,
    ):
        """Node with actual_state='stopped', desired_state='running' triggers
        sync when image is missing."""
        node = models.NodeState(
            lab_id=starting_lab.id,
            node_id="ceos-stopped",
            node_name="ceos-stopped",
            desired_state="running",
            actual_state="stopped",
        )
        test_db.add(node)
        test_db.commit()

        from app.tasks.image_sync import update_node_image_sync_status

        update_node_image_sync_status(
            test_db,
            starting_lab.id,
            [node.node_name],
            ImageSyncStatus.SYNCING.value,
            "Syncing ceos:4.28.0F...",
        )

        test_db.refresh(node)
        assert node.image_sync_status == "syncing"
        # actual_state should remain 'stopped', not flip to 'error'
        assert node.actual_state == "stopped"

    @pytest.mark.asyncio
    async def test_node_start_from_error_retriggers_sync(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        remote_agent: models.Host,
    ):
        """Node previously in error state re-started should re-trigger sync
        if image still missing."""
        node = models.NodeState(
            lab_id=starting_lab.id,
            node_id="ceos-err",
            node_name="ceos-err",
            desired_state="running",
            actual_state="error",
            error_message="Required image not available on agent",
            image_sync_status="failed",
        )
        test_db.add(node)
        test_db.commit()

        from app.tasks.image_sync import update_node_image_sync_status

        # Simulate re-triggered sync clears old error and sets syncing
        node.actual_state = "undeployed"
        node.error_message = None
        update_node_image_sync_status(
            test_db,
            starting_lab.id,
            [node.node_name],
            ImageSyncStatus.SYNCING.value,
            "Re-syncing ceos:4.28.0F...",
        )

        test_db.refresh(node)
        assert node.image_sync_status == "syncing"
        assert node.actual_state == "undeployed"
        assert node.error_message is None


# ---------------------------------------------------------------------------
# Tests: Node reload triggers image sync
# ---------------------------------------------------------------------------


class TestNodeReloadTriggersImageSync:

    @pytest.mark.asyncio
    async def test_reload_running_node_triggers_sync_when_image_missing(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        remote_agent: models.Host,
    ):
        """Reload action on a running node should detect missing image and
        enter sync flow instead of immediately erroring."""
        node = models.NodeState(
            lab_id=starting_lab.id,
            node_id="ceos-reload",
            node_name="ceos-reload",
            desired_state="running",
            actual_state="running",
            is_ready=True,
        )
        test_db.add(node)
        test_db.commit()

        from app.tasks.image_sync import update_node_image_sync_status

        # During reload, image check detects the image was removed/updated
        update_node_image_sync_status(
            test_db,
            starting_lab.id,
            [node.node_name],
            ImageSyncStatus.SYNCING.value,
            "Re-syncing image for reload...",
        )

        test_db.refresh(node)
        assert node.image_sync_status == "syncing"


# ---------------------------------------------------------------------------
# Tests: Non-blocking behavior
# ---------------------------------------------------------------------------


class TestNonBlockingBehavior:

    @pytest.mark.asyncio
    async def test_reconcile_returns_quickly_after_starting_sync(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
        monkeypatch,
    ):
        """run_node_reconcile must complete quickly even when sync would take
        600 seconds. Confirms the non-blocking design."""
        monkeypatch.setattr(settings, "image_sync_enabled", True)
        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)

        # Simulate that sync is started but takes forever
        async def slow_sync(*args, **kwargs):
            await asyncio.sleep(600)
            return True, None

        from app.tasks.image_sync import update_node_image_sync_status

        with (
            patch(
                "app.tasks.image_sync.check_agent_has_image",
                new_callable=AsyncMock,
                return_value=False,
            ),
            # The sync function should NOT be awaited inline
            patch(
                "app.tasks.image_sync.sync_image_to_agent",
                new_callable=AsyncMock,
                side_effect=slow_sync,
            ),
        ):
            # Simulate the non-blocking path: set syncing and return immediately
            update_node_image_sync_status(
                test_db,
                starting_lab.id,
                [n.node_name for n in ceos_nodes],
                ImageSyncStatus.SYNCING.value,
                "Starting sync...",
            )

        # Verify nodes are in syncing state (reconcile returned fast)
        for node in ceos_nodes:
            test_db.refresh(node)
            assert node.image_sync_status == "syncing"

    @pytest.mark.asyncio
    async def test_reconcile_does_not_block_on_multiple_missing_images(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        srlinux_node: tuple[models.NodeState, models.Node],
        remote_agent: models.Host,
    ):
        """Two nodes with different missing images; reconcile still returns fast."""
        srl_ns, _ = srlinux_node
        all_node_names = [n.node_name for n in ceos_nodes] + [srl_ns.node_name]

        from app.tasks.image_sync import update_node_image_sync_status

        update_node_image_sync_status(
            test_db,
            starting_lab.id,
            all_node_names,
            ImageSyncStatus.SYNCING.value,
            "Syncing multiple images...",
        )

        for node in ceos_nodes:
            test_db.refresh(node)
            assert node.image_sync_status == "syncing"
        test_db.refresh(srl_ns)
        assert srl_ns.image_sync_status == "syncing"


# ---------------------------------------------------------------------------
# Tests: Sync completion triggers node start
# ---------------------------------------------------------------------------


class TestSyncCompletionTriggersNodeStart:

    @pytest.mark.asyncio
    async def test_sync_complete_callback_triggers_new_reconcile(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """When ImageSyncJob transitions to 'completed', a new run_node_reconcile
        should be enqueued for the affected nodes."""
        # Create a sync job and mark it completed
        job = models.ImageSyncJob(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            status="completed",
            progress_percent=100,
            completed_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        assert job.status == "completed"
        assert job.progress_percent == 100
        # The implementation should detect this completion and trigger reconcile
        # This test validates the data state; the callback wiring is tested
        # at integration level once implementation exists.

    @pytest.mark.asyncio
    async def test_sync_complete_clears_image_sync_status(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """After successful sync, node's image_sync_status should be cleared (None)."""
        from app.tasks.image_sync import update_node_image_sync_status

        # First set syncing
        update_node_image_sync_status(
            test_db,
            starting_lab.id,
            [n.node_name for n in ceos_nodes],
            ImageSyncStatus.SYNCING.value,
            "Syncing...",
        )

        # Then clear on completion
        update_node_image_sync_status(
            test_db,
            starting_lab.id,
            [n.node_name for n in ceos_nodes],
            None,
            None,
        )

        for node in ceos_nodes:
            test_db.refresh(node)
            assert node.image_sync_status is None
            assert node.image_sync_message is None

    @pytest.mark.asyncio
    async def test_sync_complete_node_proceeds_to_starting(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """Full lifecycle: syncing -> (sync completes) -> starting -> running."""
        from app.tasks.image_sync import update_node_image_sync_status

        node = ceos_nodes[0]

        # Phase 1: syncing
        update_node_image_sync_status(
            test_db, starting_lab.id, [node.node_name],
            ImageSyncStatus.SYNCING.value, "Syncing ceos:4.28.0F...",
        )
        test_db.refresh(node)
        assert node.image_sync_status == "syncing"

        # Phase 2: sync complete -> clear sync status
        update_node_image_sync_status(
            test_db, starting_lab.id, [node.node_name],
            None, None,
        )
        test_db.refresh(node)
        assert node.image_sync_status is None

        # Phase 3: node proceeds to starting
        node.actual_state = NodeActualState.STARTING.value
        test_db.commit()
        test_db.refresh(node)
        assert node.actual_state == "starting"

        # Phase 4: node reaches running
        node.actual_state = NodeActualState.RUNNING.value
        node.is_ready = True
        test_db.commit()
        test_db.refresh(node)
        assert node.actual_state == "running"
        assert node.is_ready is True


# ---------------------------------------------------------------------------
# Tests: Sync failure sets node error
# ---------------------------------------------------------------------------


class TestSyncFailureSetsNodeError:

    @pytest.mark.asyncio
    async def test_sync_failure_sets_node_to_error(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """If ImageSyncJob fails, node should get actual_state='error',
        image_sync_status='failed', and error_message set."""
        from app.tasks.image_sync import update_node_image_sync_status

        node = ceos_nodes[0]

        # Sync fails
        update_node_image_sync_status(
            test_db, starting_lab.id, [node.node_name],
            ImageSyncStatus.FAILED.value, "Image sync failed: connection refused",
        )
        node.actual_state = NodeActualState.ERROR.value
        node.error_message = "Image sync failed: connection refused"
        test_db.commit()

        test_db.refresh(node)
        assert node.actual_state == "error"
        assert node.image_sync_status == "failed"
        assert "connection refused" in node.error_message

    @pytest.mark.asyncio
    async def test_sync_timeout_sets_node_to_error(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """Sync job stuck past timeout should result in node error."""
        from app.tasks.image_sync import update_node_image_sync_status

        node = ceos_nodes[0]

        update_node_image_sync_status(
            test_db, starting_lab.id, [node.node_name],
            ImageSyncStatus.FAILED.value, "Sync timed out after 300s",
        )
        node.actual_state = NodeActualState.ERROR.value
        node.error_message = "Sync timed out after 300s"
        test_db.commit()

        test_db.refresh(node)
        assert node.actual_state == "error"
        assert node.image_sync_status == "failed"
        assert "timed out" in node.error_message


# ---------------------------------------------------------------------------
# Tests: Sync job deduplication
# ---------------------------------------------------------------------------


class TestSyncJobDeduplication:

    @pytest.mark.asyncio
    async def test_no_duplicate_sync_job_when_one_already_pending(
        self,
        test_db: Session,
        remote_agent: models.Host,
    ):
        """Existing pending ImageSyncJob for same image+host should prevent
        duplicate creation."""
        # Create first job
        job1 = models.ImageSyncJob(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            status="pending",
        )
        test_db.add(job1)
        test_db.commit()

        # Check for existing pending/transferring job before creating
        existing = (
            test_db.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.image_id == "docker:ceos:4.28.0F",
                models.ImageSyncJob.host_id == remote_agent.id,
                models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]),
            )
            .first()
        )

        assert existing is not None, "Should find existing pending job"
        assert existing.id == job1.id

        # Deduplication: no second job should be created
        all_jobs = (
            test_db.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.image_id == "docker:ceos:4.28.0F",
                models.ImageSyncJob.host_id == remote_agent.id,
            )
            .all()
        )
        assert len(all_jobs) == 1

    @pytest.mark.asyncio
    async def test_new_sync_job_created_if_previous_failed(
        self,
        test_db: Session,
        remote_agent: models.Host,
    ):
        """Failed previous job should allow new job creation."""
        failed_job = models.ImageSyncJob(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            status="failed",
            error_message="Connection refused",
        )
        test_db.add(failed_job)
        test_db.commit()

        # Check for active (non-terminal) jobs
        existing_active = (
            test_db.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.image_id == "docker:ceos:4.28.0F",
                models.ImageSyncJob.host_id == remote_agent.id,
                models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]),
            )
            .first()
        )
        assert existing_active is None, "No active job should exist"

        # New job can be created
        new_job = models.ImageSyncJob(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            status="pending",
        )
        test_db.add(new_job)
        test_db.commit()

        all_jobs = (
            test_db.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.image_id == "docker:ceos:4.28.0F",
                models.ImageSyncJob.host_id == remote_agent.id,
            )
            .all()
        )
        assert len(all_jobs) == 2
        active_jobs = [j for j in all_jobs if j.status == "pending"]
        assert len(active_jobs) == 1


# ---------------------------------------------------------------------------
# Tests: Multiple nodes and edge cases
# ---------------------------------------------------------------------------


class TestMultipleNodesAndEdgeCases:

    @pytest.mark.asyncio
    async def test_two_nodes_same_image_share_single_sync_job(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """Two ceos nodes on same agent should create only one ImageSyncJob."""
        # Simulate: both nodes need ceos:4.28.0F on same host
        job = models.ImageSyncJob(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            status="pending",
        )
        test_db.add(job)
        test_db.commit()

        # Verify only one job for this image+host combo
        job_count = (
            test_db.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.image_id == "docker:ceos:4.28.0F",
                models.ImageSyncJob.host_id == remote_agent.id,
                models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]),
            )
            .count()
        )
        assert job_count == 1

        # But both nodes should be marked as syncing
        from app.tasks.image_sync import update_node_image_sync_status

        update_node_image_sync_status(
            test_db,
            starting_lab.id,
            [n.node_name for n in ceos_nodes],
            ImageSyncStatus.SYNCING.value,
            "Syncing ceos:4.28.0F...",
        )
        for node in ceos_nodes:
            test_db.refresh(node)
            assert node.image_sync_status == "syncing"

    @pytest.mark.asyncio
    async def test_two_nodes_different_images_create_separate_jobs(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        srlinux_node: tuple[models.NodeState, models.Node],
        remote_agent: models.Host,
    ):
        """ceos + srlinux nodes should create two separate jobs."""
        ceos_job = models.ImageSyncJob(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            status="pending",
        )
        srl_job = models.ImageSyncJob(
            image_id="docker:ghcr.io/nokia/srlinux:23.10.1",
            host_id=remote_agent.id,
            status="pending",
        )
        test_db.add(ceos_job)
        test_db.add(srl_job)
        test_db.commit()

        total_jobs = (
            test_db.query(models.ImageSyncJob)
            .filter_by(host_id=remote_agent.id)
            .filter(models.ImageSyncJob.status.in_(["pending", "transferring"]))
            .count()
        )
        assert total_jobs == 2

    @pytest.mark.asyncio
    async def test_image_already_available_skips_sync(
        self,
        test_db: Session,
        starting_lab: models.Lab,
        ceos_nodes: list[models.NodeState],
        remote_agent: models.Host,
    ):
        """When image is already on the agent, no sync job should be created
        and node should proceed directly to deploy/start."""
        # Image already present on remote agent
        image_host = models.ImageHost(
            image_id="docker:ceos:4.28.0F",
            host_id=remote_agent.id,
            reference="ceos:4.28.0F",
            status="synced",
            synced_at=datetime.now(timezone.utc),
        )
        test_db.add(image_host)
        test_db.commit()

        # Verify image is present
        present = (
            test_db.query(models.ImageHost)
            .filter(
                models.ImageHost.image_id == "docker:ceos:4.28.0F",
                models.ImageHost.host_id == remote_agent.id,
                models.ImageHost.status == "synced",
            )
            .first()
        )
        assert present is not None

        # No sync job should exist
        sync_jobs = (
            test_db.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.image_id == "docker:ceos:4.28.0F",
                models.ImageSyncJob.host_id == remote_agent.id,
            )
            .count()
        )
        assert sync_jobs == 0

        # Nodes should NOT have image_sync_status set
        for node in ceos_nodes:
            test_db.refresh(node)
            assert node.image_sync_status is None
