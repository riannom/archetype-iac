"""Extended tests for app/tasks/image_sync.py - covering gaps in existing test file.

Focuses on:
- ensure_images_for_deployment / _ensure_images_for_deployment_impl
- check_and_start_image_sync (image present, missing, provider mismatch, already syncing)
- _run_sync_and_callback (success re-reconcile, failure marks nodes failed)
- _wait_for_sync_and_callback (wait completes callback, wait exception handled)
- _mark_nodes_sync_failed (sets image_sync_status="failed" on matching nodes)
- _broadcast_nodes_sync_cleared (clears sync status and broadcasts)
- get_images_from_db (unique images, empty lab)
- _trigger_re_reconcile (creates job, skips stopped nodes, calls safe_create_task)
- _is_file_reference (path-like, docker refs, qcow2/img extensions)
- _required_provider_for_reference (qcow2->libvirt, img->libvirt, docker->None)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# TestIsFileReference
# ---------------------------------------------------------------------------


class TestIsFileReference:
    """Tests for the _is_file_reference helper function."""

    def test_absolute_path_is_file_reference(self):
        """Absolute path starting with / is a file reference."""
        from app.tasks.image_sync import _is_file_reference

        assert _is_file_reference("/var/lib/archetype/images/disk.qcow2") is True

    def test_relative_qcow2_is_file_reference(self):
        """A bare filename ending in .qcow2 is a file reference."""
        from app.tasks.image_sync import _is_file_reference

        assert _is_file_reference("nexus9300v.qcow2") is True

    def test_img_extension_is_file_reference(self):
        """A filename ending in .img is a file reference."""
        from app.tasks.image_sync import _is_file_reference

        assert _is_file_reference("vios-adventerprisek9-m.img") is True

    def test_docker_tag_is_not_file_reference(self):
        """Docker image tags are not file references."""
        from app.tasks.image_sync import _is_file_reference

        assert _is_file_reference("ceos:4.28.0F") is False

    def test_docker_registry_ref_is_not_file_reference(self):
        """A registry-qualified docker ref is not a file reference."""
        from app.tasks.image_sync import _is_file_reference

        assert _is_file_reference("ghcr.io/acme/ceos:4.28.0F") is False

    def test_empty_string_is_not_file_reference(self):
        """Empty string is not a file reference."""
        from app.tasks.image_sync import _is_file_reference

        assert _is_file_reference("") is False

    def test_path_with_img_in_middle_not_file_reference(self):
        """A docker tag that contains 'img' but does not end with .img is not a file reference."""
        from app.tasks.image_sync import _is_file_reference

        assert _is_file_reference("my-image:latest") is False


# ---------------------------------------------------------------------------
# TestRequiredProviderForReference
# ---------------------------------------------------------------------------


class TestRequiredProviderForReference:
    """Tests for the _required_provider_for_reference helper function."""

    def test_qcow2_requires_libvirt(self):
        """qcow2 images require the libvirt provider."""
        from app.tasks.image_sync import _required_provider_for_reference

        assert _required_provider_for_reference("/images/nexus9300v.qcow2") == "libvirt"

    def test_img_requires_libvirt(self):
        """.img images require the libvirt provider."""
        from app.tasks.image_sync import _required_provider_for_reference

        assert _required_provider_for_reference("vios.img") == "libvirt"

    def test_docker_tag_returns_none(self):
        """Docker tags have no required provider constraint."""
        from app.tasks.image_sync import _required_provider_for_reference

        assert _required_provider_for_reference("ceos:4.28.0F") is None

    def test_absolute_path_without_known_ext_returns_none(self):
        """An absolute path without a qcow2/img suffix returns None (no provider constraint)."""
        from app.tasks.image_sync import _required_provider_for_reference

        assert _required_provider_for_reference("/some/path/to/file.iso") is None


# ---------------------------------------------------------------------------
# TestGetImagesFromDb
# ---------------------------------------------------------------------------


class TestGetImagesFromDb:
    """Tests for get_images_from_db function."""

    def test_returns_unique_images_from_nodes(self, test_db: Session, sample_lab: models.Lab):
        """Should return deduplicated image references from Node records."""
        from app.tasks.image_sync import get_images_from_db

        # Create Node records with images (two share the same image)
        for i, (gui_id, cname, image) in enumerate([
            ("n1", "router1", "ceos:4.28.0F"),
            ("n2", "router2", "ceos:4.28.0F"),
            ("n3", "switch1", "veos:4.27.0F"),
        ]):
            node = models.Node(
                id=f"node-{i}",
                lab_id=sample_lab.id,
                gui_id=gui_id,
                display_name=cname,
                container_name=cname,
                image=image,
            )
            test_db.add(node)
        test_db.commit()

        result = get_images_from_db(sample_lab.id, test_db)

        assert sorted(result) == sorted(["ceos:4.28.0F", "veos:4.27.0F"])

    def test_empty_lab_returns_empty_list(self, test_db: Session, sample_lab: models.Lab):
        """Should return empty list when no nodes exist for the lab."""
        from app.tasks.image_sync import get_images_from_db

        result = get_images_from_db(sample_lab.id, test_db)

        assert result == []

    def test_nodes_without_image_are_excluded(self, test_db: Session, sample_lab: models.Lab):
        """Nodes with image=None should be excluded from results."""
        from app.tasks.image_sync import get_images_from_db

        node = models.Node(
            id="node-no-image",
            lab_id=sample_lab.id,
            gui_id="n1",
            display_name="router1",
            container_name="router1",
            image=None,
        )
        test_db.add(node)
        test_db.commit()

        result = get_images_from_db(sample_lab.id, test_db)

        assert result == []

    def test_does_not_return_images_from_other_labs(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Images from a different lab must not bleed into the result."""
        from app.tasks.image_sync import get_images_from_db

        other_lab = models.Lab(
            name="Other Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/other-lab",
        )
        test_db.add(other_lab)
        test_db.commit()
        test_db.refresh(other_lab)

        other_node = models.Node(
            id="other-node",
            lab_id=other_lab.id,
            gui_id="o1",
            display_name="other-router",
            container_name="other-router",
            image="alpine:latest",
        )
        test_db.add(other_node)
        test_db.commit()

        result = get_images_from_db(sample_lab.id, test_db)

        assert result == []


# ---------------------------------------------------------------------------
# TestMarkNodesSyncFailed
# ---------------------------------------------------------------------------


class TestMarkNodesSyncFailed:
    """Tests for the _mark_nodes_sync_failed function."""

    def test_sets_failed_status_on_matching_nodes(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """Should update image_sync_status and actual_state to error."""
        from app.tasks.image_sync import _mark_nodes_sync_failed

        lab, nodes = sample_lab_with_nodes
        node_names = [n.node_name for n in nodes]

        with patch("app.utils.async_tasks.safe_create_task"):
            _mark_nodes_sync_failed(test_db, lab.id, node_names, "Transfer failed")

        for node in nodes:
            test_db.refresh(node)
            assert node.image_sync_status == "failed"
            assert node.image_sync_message == "Transfer failed"
            assert node.actual_state == "error"
            assert "Transfer failed" in node.error_message

    def test_broadcasts_failure_for_each_node(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """Should call safe_create_task once per matching node state."""
        from app.tasks.image_sync import _mark_nodes_sync_failed

        lab, nodes = sample_lab_with_nodes
        node_names = [n.node_name for n in nodes]

        with patch("app.utils.async_tasks.safe_create_task") as mock_task:
            _mark_nodes_sync_failed(test_db, lab.id, node_names, "Timeout")

        # One broadcast call per node
        assert mock_task.call_count == len(nodes)

    def test_no_op_for_empty_node_list(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Should not raise or commit anything for an empty list."""
        from app.tasks.image_sync import _mark_nodes_sync_failed

        with patch("app.utils.async_tasks.safe_create_task") as mock_task:
            _mark_nodes_sync_failed(test_db, sample_lab.id, [], "irrelevant")

        mock_task.assert_not_called()

    def test_ignores_nodes_not_in_list(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """Only the specified node names should be updated."""
        from app.tasks.image_sync import _mark_nodes_sync_failed

        lab, nodes = sample_lab_with_nodes
        # Only pass the first node
        with patch("app.utils.async_tasks.safe_create_task"):
            _mark_nodes_sync_failed(test_db, lab.id, [nodes[0].node_name], "err")

        test_db.refresh(nodes[0])
        test_db.refresh(nodes[1])
        assert nodes[0].image_sync_status == "failed"
        assert nodes[1].image_sync_status is None  # untouched


# ---------------------------------------------------------------------------
# TestBroadcastNodesSyncCleared
# ---------------------------------------------------------------------------


class TestBroadcastNodesSyncCleared:
    """Tests for _broadcast_nodes_sync_cleared function."""

    def test_clears_sync_status_in_db(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """Should clear image_sync_status and message for all listed nodes."""
        from app.tasks.image_sync import _broadcast_nodes_sync_cleared

        lab, nodes = sample_lab_with_nodes
        # Pre-set a status
        for node in nodes:
            node.image_sync_status = "syncing"
            node.image_sync_message = "in progress"
        test_db.commit()

        with patch("app.utils.async_tasks.safe_create_task"):
            _broadcast_nodes_sync_cleared(test_db, lab.id, [n.node_name for n in nodes])

        # bulk update uses synchronize_session=False, so we need to expire objects
        test_db.expire_all()
        for node in nodes:
            test_db.refresh(node)
            assert node.image_sync_status is None
            assert node.image_sync_message is None

    def test_broadcasts_cleared_state_for_each_node(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """Should fire a broadcast task for every node in the list."""
        from app.tasks.image_sync import _broadcast_nodes_sync_cleared

        lab, nodes = sample_lab_with_nodes

        with patch("app.utils.async_tasks.safe_create_task") as mock_task:
            _broadcast_nodes_sync_cleared(test_db, lab.id, [n.node_name for n in nodes])

        assert mock_task.call_count == len(nodes)

    def test_no_op_for_empty_node_list(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Empty node list should not call safe_create_task."""
        from app.tasks.image_sync import _broadcast_nodes_sync_cleared

        with patch("app.utils.async_tasks.safe_create_task") as mock_task:
            _broadcast_nodes_sync_cleared(test_db, sample_lab.id, [])

        mock_task.assert_not_called()


# ---------------------------------------------------------------------------
# TestTriggerReReconcile
# ---------------------------------------------------------------------------


class TestTriggerReReconcile:
    """Tests for the _trigger_re_reconcile function."""

    def test_creates_job_for_running_nodes(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """Should create an image-callback job when nodes desire running state."""
        from app.tasks.image_sync import _trigger_re_reconcile

        lab, nodes = sample_lab_with_nodes
        for node in nodes:
            node.desired_state = "running"
        test_db.commit()

        job_count_before = test_db.query(models.Job).count()

        with patch("app.utils.async_tasks.safe_create_task") as mock_task:
            _trigger_re_reconcile(
                session=test_db,
                lab_id=lab.id,
                node_ids=[n.node_id for n in nodes],
                provider="docker",
            )

        assert test_db.query(models.Job).count() == job_count_before + 1
        mock_task.assert_called_once()

    def test_skips_all_stopped_nodes(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """Should not create a job when all target nodes have desired_state=stopped."""
        from app.tasks.image_sync import _trigger_re_reconcile

        lab, nodes = sample_lab_with_nodes
        # Both nodes remain stopped (default from fixture)
        job_count_before = test_db.query(models.Job).count()

        def _consume(coro, name=None):
            coro.close()
            return None

        with patch("app.utils.async_tasks.safe_create_task", side_effect=_consume):
            _trigger_re_reconcile(
                session=test_db,
                lab_id=lab.id,
                node_ids=[n.node_id for n in nodes],
                provider="docker",
            )

        # All nodes are stopped — no job should be created
        assert test_db.query(models.Job).count() == job_count_before

    def test_handles_missing_lab_gracefully(
        self, test_db: Session
    ):
        """Should log a warning and return without raising when lab does not exist."""
        from app.tasks.image_sync import _trigger_re_reconcile

        with patch("app.utils.async_tasks.safe_create_task") as mock_task:
            _trigger_re_reconcile(
                session=test_db,
                lab_id="nonexistent-lab-id",
                node_ids=["n1"],
                provider="docker",
            )

        mock_task.assert_not_called()

    def test_only_runnable_nodes_are_included(
        self, test_db: Session, sample_lab_with_nodes
    ):
        """When only one of two nodes wants running, job should only include that node."""
        from app.tasks.image_sync import _trigger_re_reconcile

        lab, nodes = sample_lab_with_nodes
        nodes[0].desired_state = "running"
        nodes[1].desired_state = "stopped"
        test_db.commit()

        captured_coros = []

        def _capture(coro, name=None):
            captured_coros.append(coro)
            coro.close()
            return None

        with patch("app.utils.async_tasks.safe_create_task", side_effect=_capture):
            with patch("app.tasks.jobs.run_node_reconcile", return_value=AsyncMock()) as mock_reconcile:
                _trigger_re_reconcile(
                    session=test_db,
                    lab_id=lab.id,
                    node_ids=[n.node_id for n in nodes],
                    provider="docker",
                )
                # run_node_reconcile should only receive the running-desired node
                args, kwargs = mock_reconcile.call_args
                passed_node_ids = args[2] if len(args) > 2 else kwargs.get("node_ids", [])
                assert nodes[0].node_id in passed_node_ids
                assert nodes[1].node_id not in passed_node_ids


# ---------------------------------------------------------------------------
# TestEnsureImagesForDeployment
# ---------------------------------------------------------------------------


class TestEnsureImagesForDeployment:
    """Tests for ensure_images_for_deployment / _ensure_images_for_deployment_impl."""

    @pytest.mark.asyncio
    async def test_returns_true_when_all_images_present(
        self, test_db: Session, sample_host: models.Host, monkeypatch
    ):
        """Should return (True, [], logs) when all images are already on the agent."""
        from app.tasks.image_sync import ensure_images_for_deployment
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)
        monkeypatch.setattr(settings, "image_sync_timeout", 60)

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=True)), \
             patch("app.tasks.image_sync.load_manifest", return_value={"images": []}):
            ok, missing, logs = await ensure_images_for_deployment(
                host_id=sample_host.id,
                image_references=["ceos:4.28.0F"],
                database=test_db,
            )

        assert ok is True
        assert missing == []
        assert any("already present" in line for line in logs)

    @pytest.mark.asyncio
    async def test_skips_check_when_pre_deploy_check_disabled(
        self, test_db: Session, sample_host: models.Host, monkeypatch
    ):
        """Should return (True, [], []) immediately when image_sync_pre_deploy_check is False."""
        from app.tasks.image_sync import ensure_images_for_deployment
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", False)

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock()) as mock_check:
            ok, missing, logs = await ensure_images_for_deployment(
                host_id=sample_host.id,
                image_references=["ceos:4.28.0F"],
                database=test_db,
            )

        assert ok is True
        assert missing == []
        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_disabled_strategy_returns_failure(
        self, test_db: Session, sample_host: models.Host, monkeypatch
    ):
        """Should return (False, missing, logs) when the host strategy is 'disabled'."""
        from app.tasks.image_sync import ensure_images_for_deployment
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)
        monkeypatch.setattr(settings, "image_sync_timeout", 60)
        monkeypatch.setattr(settings, "image_sync_fallback_strategy", "disabled")

        sample_host.image_sync_strategy = "disabled"
        test_db.commit()

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value={"images": []}):
            ok, missing, logs = await ensure_images_for_deployment(
                host_id=sample_host.id,
                image_references=["ceos:4.28.0F"],
                database=test_db,
            )

        assert ok is False
        assert "ceos:4.28.0F" in missing

    @pytest.mark.asyncio
    async def test_sync_failure_included_in_results(
        self, test_db: Session, sample_host: models.Host, monkeypatch
    ):
        """When sync fails, the image should appear in still_missing and logs."""
        from app.tasks.image_sync import ensure_images_for_deployment
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)
        monkeypatch.setattr(settings, "image_sync_timeout", 60)
        monkeypatch.setattr(settings, "image_sync_fallback_strategy", "on_demand")

        sample_host.image_sync_strategy = "on_demand"
        test_db.commit()

        manifest = {
            "images": [{"id": "docker:ceos:4.28.0F", "kind": "docker", "reference": "ceos:4.28.0F"}]
        }

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest), \
             patch("app.tasks.image_sync.sync_image_to_agent", new=AsyncMock(return_value=(False, "timeout"))):
            ok, missing, logs = await ensure_images_for_deployment(
                host_id=sample_host.id,
                image_references=["ceos:4.28.0F"],
                database=test_db,
            )

        assert ok is False
        assert "ceos:4.28.0F" in missing
        assert any("FAILED" in line or "failure" in line for line in logs)

    @pytest.mark.asyncio
    async def test_offline_host_returns_failure(
        self, test_db: Session, offline_host: models.Host, monkeypatch
    ):
        """Offline host should cause immediate failure without making HTTP calls."""
        from app.tasks.image_sync import ensure_images_for_deployment
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)
        monkeypatch.setattr(settings, "image_sync_timeout", 60)

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock()) as mock_check:
            ok, missing, logs = await ensure_images_for_deployment(
                host_id=offline_host.id,
                image_references=["ceos:4.28.0F"],
                database=test_db,
            )

        assert ok is False
        assert "ceos:4.28.0F" in missing
        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_node_states_updated_on_sync_success(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes, monkeypatch
    ):
        """Node image_sync_status should be cleared after successful sync."""
        from app.tasks.image_sync import ensure_images_for_deployment
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)
        monkeypatch.setattr(settings, "image_sync_timeout", 60)
        monkeypatch.setattr(settings, "image_sync_fallback_strategy", "on_demand")

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"
        image_to_nodes = {ref: [nodes[0].node_name]}
        manifest = {
            "images": [{"id": "docker:ceos:4.28.0F", "kind": "docker", "reference": ref}]
        }

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest), \
             patch("app.tasks.image_sync.sync_image_to_agent", new=AsyncMock(return_value=(True, None))):
            ok, missing, logs = await ensure_images_for_deployment(
                host_id=sample_host.id,
                image_references=[ref],
                database=test_db,
                lab_id=lab.id,
                image_to_nodes=image_to_nodes,
            )

        assert ok is True
        assert missing == []


# ---------------------------------------------------------------------------
# TestCheckAndStartImageSync
# ---------------------------------------------------------------------------


class TestCheckAndStartImageSync:
    """Tests for check_and_start_image_sync function."""

    @pytest.mark.asyncio
    async def test_all_images_present_returns_empty_sets(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """When all images are present, syncing and failed sets should be empty."""
        from app.tasks.image_sync import check_and_start_image_sync

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=True)):
            syncing, failed, logs = await check_and_start_image_sync(
                host_id=sample_host.id,
                image_references=[ref],
                database=test_db,
                lab_id=lab.id,
                job_id="job-abc",
                node_ids=[n.node_id for n in nodes],
                image_to_nodes={ref: [nodes[0].node_name]},
            )

        assert syncing == set()
        assert failed == set()
        assert any("already present" in line for line in logs)

    @pytest.mark.asyncio
    async def test_missing_image_fires_sync_task(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """Missing image should add nodes to syncing set and fire a background task."""
        from app.tasks.image_sync import check_and_start_image_sync

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"
        manifest = {
            "images": [{"id": "docker:ceos:4.28.0F", "kind": "docker", "reference": ref}]
        }

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest), \
             patch("app.utils.async_tasks.safe_create_task") as mock_task:
            syncing, failed, logs = await check_and_start_image_sync(
                host_id=sample_host.id,
                image_references=[ref],
                database=test_db,
                lab_id=lab.id,
                job_id="job-abc",
                node_ids=[n.node_id for n in nodes],
                image_to_nodes={ref: [nodes[0].node_name]},
            )

        assert nodes[0].node_name in syncing
        assert failed == set()
        # At least one safe_create_task call for the sync
        assert mock_task.called

    @pytest.mark.asyncio
    async def test_provider_mismatch_fails_nodes(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """qcow2 image on a docker-only host should add node to failed set."""
        from app.tasks.image_sync import check_and_start_image_sync

        lab, nodes = sample_lab_with_nodes
        # sample_host only has docker capability (set in fixture)
        ref = "/var/lib/archetype/images/nexus9300v64.qcow2"
        manifest = {
            "images": [{"id": "qcow2:nexus9300v64.qcow2", "kind": "qcow2", "reference": ref}]
        }

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest):
            syncing, failed, logs = await check_and_start_image_sync(
                host_id=sample_host.id,
                image_references=[ref],
                database=test_db,
                lab_id=lab.id,
                job_id="job-abc",
                node_ids=[n.node_id for n in nodes],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
            )

        assert nodes[0].node_name in failed
        assert nodes[0].node_name not in syncing
        assert any("required provider" in line.lower() or "capability" in line.lower() for line in logs)

    @pytest.mark.asyncio
    async def test_already_syncing_waits_instead_of_starting(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes,
        sample_image_sync_job: models.ImageSyncJob
    ):
        """When a sync job is already in progress, it should call _wait_for_sync_and_callback."""
        from app.tasks.image_sync import check_and_start_image_sync

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        # Put the existing sync job into "transferring" status to trigger the dedup branch
        sample_image_sync_job.status = "transferring"
        test_db.commit()

        manifest = {
            "images": [{"id": "docker:ceos:4.28.0F", "kind": "docker", "reference": ref}]
        }

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest), \
             patch("app.utils.async_tasks.safe_create_task") as mock_task:
            syncing, failed, logs = await check_and_start_image_sync(
                host_id=sample_host.id,
                image_references=[ref],
                database=test_db,
                lab_id=lab.id,
                job_id="job-abc",
                node_ids=[n.node_id for n in nodes],
                image_to_nodes={ref: [nodes[0].node_name]},
            )

        # One safe_create_task call for _wait_for_sync_and_callback
        assert mock_task.called
        # Node should be in syncing set (waiting for existing job)
        assert nodes[0].node_name in syncing
        assert any("already in progress" in line for line in logs)

    @pytest.mark.asyncio
    async def test_image_not_in_library_fails_node(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """An image reference not in the manifest should mark the node as failed."""
        from app.tasks.image_sync import check_and_start_image_sync

        lab, nodes = sample_lab_with_nodes
        ref = "unknown-image:9.9"

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value={"images": []}):
            syncing, failed, logs = await check_and_start_image_sync(
                host_id=sample_host.id,
                image_references=[ref],
                database=test_db,
                lab_id=lab.id,
                job_id="job-abc",
                node_ids=[n.node_id for n in nodes],
                image_to_nodes={ref: [nodes[0].node_name]},
            )

        assert nodes[0].node_name in failed
        assert nodes[0].node_name not in syncing

    @pytest.mark.asyncio
    async def test_offline_host_fails_all_nodes(
        self, test_db: Session, offline_host: models.Host, sample_lab_with_nodes
    ):
        """Offline host should immediately fail all nodes without checking images."""
        from app.tasks.image_sync import check_and_start_image_sync

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock()) as mock_check:
            syncing, failed, logs = await check_and_start_image_sync(
                host_id=offline_host.id,
                image_references=[ref],
                database=test_db,
                lab_id=lab.id,
                job_id="job-abc",
                node_ids=[n.node_id for n in nodes],
                image_to_nodes={ref: [nodes[0].node_name]},
            )

        assert nodes[0].node_name in failed
        mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunSyncAndCallback
# ---------------------------------------------------------------------------


class TestRunSyncAndCallback:
    """Tests for _run_sync_and_callback function."""

    @pytest.mark.asyncio
    async def test_success_triggers_re_reconcile(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """On sync success, _trigger_re_reconcile and _broadcast_nodes_sync_cleared should be called."""
        from app.tasks.image_sync import _run_sync_and_callback

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        # Create a completed ImageSyncJob
        sync_job = models.ImageSyncJob(
            id="sync-job-success",
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            status="completed",
        )
        test_db.add(sync_job)
        test_db.commit()

        # Mark node as running-desired so re-reconcile fires
        nodes[0].desired_state = "running"
        test_db.commit()

        image = {"id": "docker:ceos:4.28.0F", "reference": ref}

        with patch("app.tasks.image_sync.get_session") as mock_get_session, \
             patch("app.routers.images._execute_sync_job", new=AsyncMock()), \
             patch("app.tasks.image_sync._broadcast_nodes_sync_cleared") as mock_broadcast, \
             patch("app.tasks.image_sync._trigger_re_reconcile") as mock_reconcile:
            # get_session returns the test_db session
            from contextlib import contextmanager

            @contextmanager
            def fake_get_session():
                yield test_db

            mock_get_session.side_effect = fake_get_session

            await _run_sync_and_callback(
                sync_job_id=sync_job.id,
                image_id="docker:ceos:4.28.0F",
                image=image,
                host_id=sample_host.id,
                lab_id=lab.id,
                node_ids=[nodes[0].node_id],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
            )

        mock_broadcast.assert_called_once()
        mock_reconcile.assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_marks_nodes_failed(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """On sync failure, _mark_nodes_sync_failed should be called."""
        from app.tasks.image_sync import _run_sync_and_callback

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        sync_job = models.ImageSyncJob(
            id="sync-job-fail",
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            status="failed",
            error_message="disk full",
        )
        test_db.add(sync_job)
        test_db.commit()

        image = {"id": "docker:ceos:4.28.0F", "reference": ref}

        with patch("app.tasks.image_sync.get_session") as mock_get_session, \
             patch("app.routers.images._execute_sync_job", new=AsyncMock()), \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail:
            from contextlib import contextmanager

            @contextmanager
            def fake_get_session():
                yield test_db

            mock_get_session.side_effect = fake_get_session

            await _run_sync_and_callback(
                sync_job_id=sync_job.id,
                image_id="docker:ceos:4.28.0F",
                image=image,
                host_id=sample_host.id,
                lab_id=lab.id,
                node_ids=[nodes[0].node_id],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
            )

        mock_fail.assert_called_once()
        # The error message should be passed through
        call_args = mock_fail.call_args
        assert "disk full" in str(call_args)

    @pytest.mark.asyncio
    async def test_execute_exception_handled_gracefully(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """If _execute_sync_job raises, the function should not propagate the exception."""
        from app.tasks.image_sync import _run_sync_and_callback

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        sync_job = models.ImageSyncJob(
            id="sync-job-exc",
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            status="pending",
        )
        test_db.add(sync_job)
        test_db.commit()

        image = {"id": "docker:ceos:4.28.0F", "reference": ref}

        with patch("app.tasks.image_sync.get_session") as mock_get_session, \
             patch("app.routers.images._execute_sync_job", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("app.tasks.image_sync._mark_nodes_sync_failed"):
            from contextlib import contextmanager

            @contextmanager
            def fake_get_session():
                yield test_db

            mock_get_session.side_effect = fake_get_session

            # Must NOT raise
            await _run_sync_and_callback(
                sync_job_id=sync_job.id,
                image_id="docker:ceos:4.28.0F",
                image=image,
                host_id=sample_host.id,
                lab_id=lab.id,
                node_ids=[nodes[0].node_id],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
            )


# ---------------------------------------------------------------------------
# TestWaitForSyncAndCallback
# ---------------------------------------------------------------------------


class TestWaitForSyncAndCallback:
    """Tests for _wait_for_sync_and_callback function."""

    @pytest.mark.asyncio
    async def test_completed_job_triggers_callback(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """When the watched job completes, re-reconcile should be triggered."""
        from app.tasks.image_sync import _wait_for_sync_and_callback

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        sync_job = models.ImageSyncJob(
            id="wait-job-done",
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            status="completed",
        )
        test_db.add(sync_job)
        test_db.commit()

        image = {"id": "docker:ceos:4.28.0F", "reference": ref}

        with patch("app.tasks.image_sync.get_session") as mock_get_session, \
             patch("asyncio.sleep", new=AsyncMock()), \
             patch("app.tasks.image_sync._broadcast_nodes_sync_cleared") as mock_broadcast, \
             patch("app.tasks.image_sync._trigger_re_reconcile") as mock_reconcile:
            from contextlib import contextmanager

            @contextmanager
            def fake_get_session():
                yield test_db

            mock_get_session.side_effect = fake_get_session

            await _wait_for_sync_and_callback(
                sync_job_id=sync_job.id,
                image=image,
                host_id=sample_host.id,
                lab_id=lab.id,
                node_ids=[nodes[0].node_id],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
                poll_interval=0.01,
                max_wait=10.0,
            )

        mock_broadcast.assert_called_once()
        mock_reconcile.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_job_marks_nodes_failed(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """When the watched job fails, nodes should be marked as failed."""
        from app.tasks.image_sync import _wait_for_sync_and_callback

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        sync_job = models.ImageSyncJob(
            id="wait-job-failed",
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            status="failed",
            error_message="connection reset",
        )
        test_db.add(sync_job)
        test_db.commit()

        image = {"id": "docker:ceos:4.28.0F", "reference": ref}

        with patch("app.tasks.image_sync.get_session") as mock_get_session, \
             patch("asyncio.sleep", new=AsyncMock()), \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail:
            from contextlib import contextmanager

            @contextmanager
            def fake_get_session():
                yield test_db

            mock_get_session.side_effect = fake_get_session

            await _wait_for_sync_and_callback(
                sync_job_id=sync_job.id,
                image=image,
                host_id=sample_host.id,
                lab_id=lab.id,
                node_ids=[nodes[0].node_id],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
                poll_interval=0.01,
                max_wait=10.0,
            )

        mock_fail.assert_called_once()

    @pytest.mark.asyncio
    async def test_disappeared_job_marks_nodes_failed(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """When the sync job disappears from DB, nodes should be marked failed."""
        from app.tasks.image_sync import _wait_for_sync_and_callback

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"
        image = {"id": "docker:ceos:4.28.0F", "reference": ref}

        with patch("app.tasks.image_sync.get_session") as mock_get_session, \
             patch("asyncio.sleep", new=AsyncMock()), \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail:
            from contextlib import contextmanager

            @contextmanager
            def fake_get_session():
                yield test_db

            mock_get_session.side_effect = fake_get_session

            await _wait_for_sync_and_callback(
                sync_job_id="nonexistent-job-id",
                image=image,
                host_id=sample_host.id,
                lab_id=lab.id,
                node_ids=[nodes[0].node_id],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
                poll_interval=0.01,
                max_wait=10.0,
            )

        mock_fail.assert_called_once()
        call_args = mock_fail.call_args
        assert "not found" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_timeout_marks_nodes_failed(
        self, test_db: Session, sample_host: models.Host, sample_lab_with_nodes
    ):
        """Exceeding max_wait without job completion should mark nodes failed."""
        from app.tasks.image_sync import _wait_for_sync_and_callback

        lab, nodes = sample_lab_with_nodes
        ref = "ceos:4.28.0F"

        sync_job = models.ImageSyncJob(
            id="wait-job-pending",
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            status="transferring",
        )
        test_db.add(sync_job)
        test_db.commit()

        image = {"id": "docker:ceos:4.28.0F", "reference": ref}

        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1

        with patch("app.tasks.image_sync.get_session") as mock_get_session, \
             patch("asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)), \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail:
            from contextlib import contextmanager

            @contextmanager
            def fake_get_session():
                yield test_db

            mock_get_session.side_effect = fake_get_session

            await _wait_for_sync_and_callback(
                sync_job_id=sync_job.id,
                image=image,
                host_id=sample_host.id,
                lab_id=lab.id,
                node_ids=[nodes[0].node_id],
                image_to_nodes={ref: [nodes[0].node_name]},
                provider="docker",
                poll_interval=5.0,
                max_wait=5.0,  # Only 1 iteration allowed before timeout
            )

        # Should eventually call _mark_nodes_sync_failed with a timeout message
        mock_fail.assert_called_once()
        call_args = mock_fail.call_args
        assert "timed out" in str(call_args).lower()
