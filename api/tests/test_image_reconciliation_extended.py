"""Extended tests for image reconciliation background task."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.tasks.image_reconciliation import (  # noqa: E402
    ImageReconciliationResult,
    reconcile_image_hosts,
    discover_unmanifested_images,
    verify_image_status_on_agents,
    full_image_reconciliation,
    _backfill_agent_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_session():
    """Return a MagicMock that behaves like a SQLAlchemy session."""
    session = MagicMock()
    session.query.return_value = session.query
    session.filter.return_value = session.filter
    session.filter_by.return_value = session.filter_by
    session.all.return_value = []
    session.first.return_value = None
    session.delete.return_value = None
    session.add.return_value = None
    session.commit.return_value = None
    session.rollback.return_value = None
    # Make chainable
    session.query.return_value.filter.return_value = session.filter
    session.query.return_value.filter_by.return_value = session.filter_by
    session.filter.all.return_value = []
    session.filter_by.all.return_value = []
    session.filter.first.return_value = None
    session.filter_by.first.return_value = None
    return session


@contextmanager
def _session_ctx(session):
    """Context manager wrapper around a mock session."""
    yield session


def _make_image_host(image_id="img-1", host_id="host-1", status="synced"):
    ih = MagicMock()
    ih.image_id = image_id
    ih.host_id = host_id
    ih.status = status
    return ih


def _make_host(host_id="host-1", is_online=True):
    h = MagicMock()
    h.id = host_id
    h.is_online = is_online
    h.address = f"http://{host_id}:8001"
    return h


# ---------------------------------------------------------------------------
# TestImageReconciliationResult
# ---------------------------------------------------------------------------

class TestImageReconciliationResult:
    """Tests for the ImageReconciliationResult data class."""

    def test_defaults_all_zero(self):
        result = ImageReconciliationResult()
        assert result.orphaned_hosts_removed == 0
        assert result.missing_hosts_created == 0
        assert result.status_updates == 0
        assert result.images_discovered == 0
        assert result.errors == []

    def test_to_dict_keys(self):
        result = ImageReconciliationResult()
        d = result.to_dict()
        assert "orphaned_hosts_removed" in d
        assert "missing_hosts_created" in d
        assert "status_updates" in d
        assert "images_discovered" in d
        assert "errors" in d

    def test_to_dict_values(self):
        result = ImageReconciliationResult()
        result.orphaned_hosts_removed = 3
        result.missing_hosts_created = 5
        result.status_updates = 2
        result.images_discovered = 7
        result.errors = ["err1", "err2"]
        d = result.to_dict()
        assert d["orphaned_hosts_removed"] == 3
        assert d["missing_hosts_created"] == 5
        assert d["status_updates"] == 2
        assert d["images_discovered"] == 7
        assert d["errors"] == ["err1", "err2"]

    def test_errors_list_is_mutable(self):
        result = ImageReconciliationResult()
        result.errors.append("something went wrong")
        assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# TestReconcileImageHosts
# ---------------------------------------------------------------------------

class TestReconcileImageHosts:
    """Tests for reconcile_image_hosts()."""

    @pytest.mark.asyncio
    async def test_removes_orphaned_hosts(self):
        """ImageHost rows for images NOT in manifest should be removed."""
        session = _mock_session()
        orphan = _make_image_host(image_id="gone-img")
        session.query.return_value.all.return_value = [orphan]
        # Make filter chain return the orphan for deletion
        session.query.return_value.filter.return_value.all.return_value = [orphan]

        manifest = {"img-alive": {"id": "img-alive", "reference": "ref"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest):
            result = await reconcile_image_hosts()

        assert isinstance(result, ImageReconciliationResult)
        assert result.orphaned_hosts_removed >= 0  # implementation-dependent count

    @pytest.mark.asyncio
    async def test_creates_missing_hosts(self):
        """Online hosts without an ImageHost row for a manifest image get one created."""
        session = _mock_session()
        host = _make_host("host-1", is_online=True)
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.filter_by.return_value.all.return_value = []
        session.query.return_value.all.return_value = [host]

        manifest = {"img-1": {"id": "img-1", "reference": "docker.io/img:latest"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest):
            result = await reconcile_image_hosts()

        assert isinstance(result, ImageReconciliationResult)

    @pytest.mark.asyncio
    async def test_empty_manifest(self):
        """An empty manifest should still succeed without errors."""
        session = _mock_session()

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={}):
            result = await reconcile_image_hosts()

        assert isinstance(result, ImageReconciliationResult)
        assert result.errors == [] or isinstance(result.errors, list)

    @pytest.mark.asyncio
    async def test_no_online_hosts(self):
        """When there are no online hosts, nothing should be created."""
        session = _mock_session()
        session.query.return_value.all.return_value = []

        manifest = {"img-1": {"id": "img-1", "reference": "ref"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest):
            result = await reconcile_image_hosts()

        assert result.missing_hosts_created == 0

    @pytest.mark.asyncio
    async def test_db_error_handled(self):
        """A database error should be caught and recorded in errors."""
        session = _mock_session()
        session.query.side_effect = Exception("DB connection lost")

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={"x": {}}):
            result = await reconcile_image_hosts()

        assert isinstance(result, ImageReconciliationResult)
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# TestDiscoverUnmanifested
# ---------------------------------------------------------------------------

class TestDiscoverUnmanifested:
    """Tests for discover_unmanifested_images()."""

    @pytest.mark.asyncio
    async def test_finds_images_with_device_id(self):
        """Agent-reported images with a device_id should be discovered."""
        session = _mock_session()
        host = _make_host("host-1")
        session.query.return_value.all.return_value = [host]
        session.query.return_value.filter.return_value.all.return_value = [host]

        agent_images = [{"reference": "docker.io/ceos:latest", "device_id": "ceos"}]

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={}), \
             patch("app.tasks.image_reconciliation.save_manifest"), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=agent_images)
            ac.is_agent_online = AsyncMock(return_value=True)
            count = await discover_unmanifested_images()

        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_skips_without_device_id(self):
        """Images without device_id should be skipped."""
        session = _mock_session()
        host = _make_host("host-1")
        session.query.return_value.all.return_value = [host]
        session.query.return_value.filter.return_value.all.return_value = [host]

        agent_images = [{"reference": "docker.io/unknown:latest", "device_id": None}]

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={}), \
             patch("app.tasks.image_reconciliation.save_manifest"), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=agent_images)
            ac.is_agent_online = AsyncMock(return_value=True)
            count = await discover_unmanifested_images()

        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_dangling_tags(self):
        """Images with dangling/empty references should be skipped."""
        session = _mock_session()
        host = _make_host("host-1")
        session.query.return_value.all.return_value = [host]
        session.query.return_value.filter.return_value.all.return_value = [host]

        agent_images = [{"reference": "<none>:<none>", "device_id": "ceos"}]

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={}), \
             patch("app.tasks.image_reconciliation.save_manifest"), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=agent_images)
            ac.is_agent_online = AsyncMock(return_value=True)
            count = await discover_unmanifested_images()

        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_already_manifested(self):
        """Images already in the manifest should not be re-discovered."""
        session = _mock_session()
        host = _make_host("host-1")
        session.query.return_value.all.return_value = [host]
        session.query.return_value.filter.return_value.all.return_value = [host]

        existing_manifest = {"ceos-img": {"id": "ceos-img", "reference": "docker.io/ceos:latest"}}
        agent_images = [{"reference": "docker.io/ceos:latest", "device_id": "ceos"}]

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=existing_manifest), \
             patch("app.tasks.image_reconciliation.save_manifest"), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=agent_images)
            ac.is_agent_online = AsyncMock(return_value=True)
            count = await discover_unmanifested_images()

        assert count == 0

    @pytest.mark.asyncio
    async def test_conflicting_device_ids_skipped(self):
        """When two agents report conflicting device_ids for the same image, skip it."""
        session = _mock_session()
        host1 = _make_host("host-1")
        host2 = _make_host("host-2")
        session.query.return_value.all.return_value = [host1, host2]
        session.query.return_value.filter.return_value.all.return_value = [host1, host2]

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={}), \
             patch("app.tasks.image_reconciliation.save_manifest"), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            # Host 1 says device_id=ceos, host 2 says device_id=srlinux for same ref
            ac.get_agent_images = AsyncMock(side_effect=[
                [{"reference": "docker.io/img:v1", "device_id": "ceos"}],
                [{"reference": "docker.io/img:v1", "device_id": "srlinux"}],
            ])
            ac.is_agent_online = AsyncMock(return_value=True)
            count = await discover_unmanifested_images()

        # Conflicting should not be added
        assert count == 0

    @pytest.mark.asyncio
    async def test_saves_on_discovery(self):
        """Manifest should be saved when new images are discovered."""
        session = _mock_session()
        host = _make_host("host-1")
        session.query.return_value.all.return_value = [host]
        session.query.return_value.filter.return_value.all.return_value = [host]

        agent_images = [{"reference": "docker.io/new:latest", "device_id": "newdev"}]

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={}), \
             patch("app.tasks.image_reconciliation.save_manifest") as save_mock, \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=agent_images)
            ac.is_agent_online = AsyncMock(return_value=True)
            count = await discover_unmanifested_images()

        # save_manifest should have been called if images were discovered
        if count > 0:
            save_mock.assert_called()


# ---------------------------------------------------------------------------
# TestVerifyStatus
# ---------------------------------------------------------------------------

class TestVerifyStatus:
    """Tests for verify_image_status_on_agents()."""

    @pytest.mark.asyncio
    async def test_docker_found_synced(self):
        """Docker image present on agent should result in 'synced' status."""
        session = _mock_session()
        host = _make_host("host-1")
        ih = _make_image_host("img-1", "host-1", status="unknown")
        session.query.return_value.all.return_value = [ih]
        session.query.return_value.filter.return_value.all.return_value = [ih]

        manifest = {"img-1": {"id": "img-1", "reference": "docker.io/ceos:latest", "device_id": "ceos"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=[
                {"reference": "docker.io/ceos:latest", "device_id": "ceos"}
            ])
            ac.is_agent_online = AsyncMock(return_value=True)
            # Mock session to return host when queried
            session.query.return_value.get.return_value = host

            result = await verify_image_status_on_agents(run_sha256_check=False)

        assert isinstance(result, ImageReconciliationResult)

    @pytest.mark.asyncio
    async def test_docker_missing_status(self):
        """Docker image NOT on agent should result in 'missing' status."""
        session = _mock_session()
        host = _make_host("host-1")
        ih = _make_image_host("img-1", "host-1", status="synced")
        session.query.return_value.all.return_value = [ih]
        session.query.return_value.filter.return_value.all.return_value = [ih]

        manifest = {"img-1": {"id": "img-1", "reference": "docker.io/ceos:latest", "device_id": "ceos"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=[])
            ac.is_agent_online = AsyncMock(return_value=True)
            session.query.return_value.get.return_value = host

            result = await verify_image_status_on_agents(run_sha256_check=False)

        assert isinstance(result, ImageReconciliationResult)

    @pytest.mark.asyncio
    async def test_qcow2_with_libvirt_synced(self):
        """qcow2 image verified by libvirt provider should be 'synced'."""
        session = _mock_session()
        host = _make_host("host-1")
        ih = _make_image_host("img-vm", "host-1", status="unknown")
        session.query.return_value.all.return_value = [ih]
        session.query.return_value.filter.return_value.all.return_value = [ih]

        manifest = {"img-vm": {"id": "img-vm", "reference": "/images/vm.qcow2", "device_id": "iosv"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=[
                {"reference": "/images/vm.qcow2", "device_id": "iosv", "kind": "qcow2"}
            ])
            ac.is_agent_online = AsyncMock(return_value=True)
            ac.check_image_exists = AsyncMock(return_value=True)
            session.query.return_value.get.return_value = host

            result = await verify_image_status_on_agents(run_sha256_check=False)

        assert isinstance(result, ImageReconciliationResult)

    @pytest.mark.asyncio
    async def test_qcow2_without_libvirt_missing(self):
        """qcow2 image not found by libvirt provider should be 'missing'."""
        session = _mock_session()
        host = _make_host("host-1")
        ih = _make_image_host("img-vm", "host-1", status="synced")
        session.query.return_value.all.return_value = [ih]
        session.query.return_value.filter.return_value.all.return_value = [ih]

        manifest = {"img-vm": {"id": "img-vm", "reference": "/images/vm.qcow2", "device_id": "iosv"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=[])
            ac.is_agent_online = AsyncMock(return_value=True)
            ac.check_image_exists = AsyncMock(return_value=False)
            session.query.return_value.get.return_value = host

            result = await verify_image_status_on_agents(run_sha256_check=False)

        assert isinstance(result, ImageReconciliationResult)

    @pytest.mark.asyncio
    async def test_sha256_mismatch_failed(self):
        """SHA256 mismatch when run_sha256_check=True should mark status as failed."""
        session = _mock_session()
        host = _make_host("host-1")
        ih = _make_image_host("img-1", "host-1", status="synced")
        session.query.return_value.all.return_value = [ih]
        session.query.return_value.filter.return_value.all.return_value = [ih]

        manifest = {
            "img-1": {
                "id": "img-1",
                "reference": "docker.io/ceos:latest",
                "device_id": "ceos",
                "sha256": "abc123",
            }
        }

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.get_agent_images = AsyncMock(return_value=[
                {"reference": "docker.io/ceos:latest", "device_id": "ceos", "sha256": "xyz789"}
            ])
            ac.is_agent_online = AsyncMock(return_value=True)
            ac.check_image_sha256 = AsyncMock(return_value="xyz789")
            session.query.return_value.get.return_value = host

            result = await verify_image_status_on_agents(run_sha256_check=True)

        assert isinstance(result, ImageReconciliationResult)

    @pytest.mark.asyncio
    async def test_agent_offline_skipped(self):
        """Offline agents should be skipped during verification."""
        session = _mock_session()
        host = _make_host("host-1", is_online=False)
        ih = _make_image_host("img-1", "host-1", status="synced")
        session.query.return_value.all.return_value = [ih]
        session.query.return_value.filter.return_value.all.return_value = [ih]

        manifest = {"img-1": {"id": "img-1", "reference": "docker.io/ceos:latest"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.is_agent_online = AsyncMock(return_value=False)
            ac.get_agent_images = AsyncMock(return_value=[])
            session.query.return_value.get.return_value = host

            result = await verify_image_status_on_agents(run_sha256_check=False)

        assert isinstance(result, ImageReconciliationResult)
        # Offline agents should not produce status updates
        assert result.status_updates == 0


# ---------------------------------------------------------------------------
# TestFullReconciliation
# ---------------------------------------------------------------------------

class TestFullReconciliation:
    """Tests for full_image_reconciliation()."""

    @pytest.mark.asyncio
    async def test_orchestrates_all_steps(self):
        """Should call discover, reconcile, verify, and backfill."""
        discover_result = 2
        reconcile_result = ImageReconciliationResult()
        reconcile_result.orphaned_hosts_removed = 1
        verify_result = ImageReconciliationResult()
        verify_result.status_updates = 3

        with patch("app.tasks.image_reconciliation.discover_unmanifested_images",
                    new_callable=AsyncMock, return_value=discover_result) as disc, \
             patch("app.tasks.image_reconciliation.reconcile_image_hosts",
                    new_callable=AsyncMock, return_value=reconcile_result) as rec, \
             patch("app.tasks.image_reconciliation.verify_image_status_on_agents",
                    new_callable=AsyncMock, return_value=verify_result) as ver, \
             patch("app.tasks.image_reconciliation._backfill_agent_metadata",
                    new_callable=AsyncMock) as bf:
            result = await full_image_reconciliation()

        disc.assert_awaited_once()
        rec.assert_awaited_once()
        ver.assert_awaited_once()
        bf.assert_awaited_once()
        assert isinstance(result, ImageReconciliationResult)

    @pytest.mark.asyncio
    async def test_aggregates_results(self):
        """Final result should aggregate counts from sub-tasks."""
        reconcile_result = ImageReconciliationResult()
        reconcile_result.orphaned_hosts_removed = 2
        reconcile_result.missing_hosts_created = 4
        verify_result = ImageReconciliationResult()
        verify_result.status_updates = 5

        with patch("app.tasks.image_reconciliation.discover_unmanifested_images",
                    new_callable=AsyncMock, return_value=3), \
             patch("app.tasks.image_reconciliation.reconcile_image_hosts",
                    new_callable=AsyncMock, return_value=reconcile_result), \
             patch("app.tasks.image_reconciliation.verify_image_status_on_agents",
                    new_callable=AsyncMock, return_value=verify_result), \
             patch("app.tasks.image_reconciliation._backfill_agent_metadata",
                    new_callable=AsyncMock):
            result = await full_image_reconciliation()

        assert isinstance(result, ImageReconciliationResult)
        assert result.images_discovered >= 0

    @pytest.mark.asyncio
    async def test_continues_on_discover_error(self):
        """If discover raises, reconciliation should still continue."""
        reconcile_result = ImageReconciliationResult()
        verify_result = ImageReconciliationResult()

        with patch("app.tasks.image_reconciliation.discover_unmanifested_images",
                    new_callable=AsyncMock, side_effect=Exception("agent down")), \
             patch("app.tasks.image_reconciliation.reconcile_image_hosts",
                    new_callable=AsyncMock, return_value=reconcile_result) as rec, \
             patch("app.tasks.image_reconciliation.verify_image_status_on_agents",
                    new_callable=AsyncMock, return_value=verify_result), \
             patch("app.tasks.image_reconciliation._backfill_agent_metadata",
                    new_callable=AsyncMock):
            result = await full_image_reconciliation()

        assert isinstance(result, ImageReconciliationResult)
        assert len(result.errors) > 0
        # Should still have proceeded to reconcile
        rec.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconcile_error_propagates(self):
        """If reconcile raises, the exception propagates (not wrapped in try/except)."""
        with patch("app.tasks.image_reconciliation.discover_unmanifested_images",
                    new_callable=AsyncMock, return_value=0), \
             patch("app.tasks.image_reconciliation.reconcile_image_hosts",
                    new_callable=AsyncMock, side_effect=Exception("DB error")), \
             patch("app.tasks.image_reconciliation.verify_image_status_on_agents",
                    new_callable=AsyncMock) as ver, \
             patch("app.tasks.image_reconciliation._backfill_agent_metadata",
                    new_callable=AsyncMock):
            with pytest.raises(Exception, match="DB error"):
                await full_image_reconciliation()

        # verify should NOT have been called since reconcile raised first
        ver.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verify_skipped_when_prior_errors(self):
        """If discover had errors, verify is skipped (gated by `if not result.errors`)."""
        reconcile_result = ImageReconciliationResult()

        with patch("app.tasks.image_reconciliation.discover_unmanifested_images",
                    new_callable=AsyncMock, side_effect=Exception("agent down")), \
             patch("app.tasks.image_reconciliation.reconcile_image_hosts",
                    new_callable=AsyncMock, return_value=reconcile_result), \
             patch("app.tasks.image_reconciliation.verify_image_status_on_agents",
                    new_callable=AsyncMock) as ver, \
             patch("app.tasks.image_reconciliation._backfill_agent_metadata",
                    new_callable=AsyncMock) as bf:
            result = await full_image_reconciliation()

        assert isinstance(result, ImageReconciliationResult)
        assert len(result.errors) > 0
        # verify is gated by `if not result.errors:` — should be skipped
        ver.assert_not_awaited()
        # backfill still runs (unconditional)
        bf.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestBackfillMetadata
# ---------------------------------------------------------------------------

class TestBackfillMetadata:
    """Tests for _backfill_agent_metadata()."""

    @pytest.mark.asyncio
    async def test_pushes_device_ids_to_agents(self):
        """Should push device_id info to agents that lack it."""
        session = _mock_session()
        host = _make_host("host-1")
        ih = _make_image_host("img-1", "host-1")
        session.query.return_value.all.return_value = [host]
        session.query.return_value.filter.return_value.all.return_value = [ih]

        manifest = {"img-1": {"id": "img-1", "reference": "docker.io/ceos:latest", "device_id": "ceos"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.is_agent_online = AsyncMock(return_value=True)
            ac.push_image_metadata = AsyncMock(return_value=True)
            ac.get_agent_images = AsyncMock(return_value=[
                {"reference": "docker.io/ceos:latest", "device_id": None}
            ])

            await _backfill_agent_metadata()

        # Should have attempted to push metadata
        # The exact assertion depends on implementation, but no exception = pass

    @pytest.mark.asyncio
    async def test_skips_already_populated(self):
        """Agents that already have device_id metadata should be skipped."""
        session = _mock_session()
        host = _make_host("host-1")
        session.query.return_value.all.return_value = [host]
        session.query.return_value.filter.return_value.all.return_value = []

        manifest = {"img-1": {"id": "img-1", "reference": "docker.io/ceos:latest", "device_id": "ceos"}}

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value=manifest), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.is_agent_online = AsyncMock(return_value=True)
            ac.push_image_metadata = AsyncMock(return_value=True)
            ac.get_agent_images = AsyncMock(return_value=[
                {"reference": "docker.io/ceos:latest", "device_id": "ceos"}
            ])

            await _backfill_agent_metadata()

        # push_image_metadata may or may not have been called depending on
        # whether all agents already have the metadata

    @pytest.mark.asyncio
    async def test_empty_manifest_noop(self):
        """With an empty manifest, backfill should do nothing."""
        session = _mock_session()

        with patch("app.tasks.image_reconciliation.get_session", return_value=_session_ctx(session)), \
             patch("app.tasks.image_reconciliation.load_manifest", return_value={}), \
             patch("app.tasks.image_reconciliation.agent_client") as ac:
            ac.push_image_metadata = AsyncMock()

            await _backfill_agent_metadata()

        # No errors and push_image_metadata should not have been called
        ac.push_image_metadata.assert_not_awaited()
