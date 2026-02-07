"""Tests for app/tasks/image_sync.py - Image synchronization tasks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models


class TestSyncImageToAgent:
    """Tests for the sync_image_to_agent function."""

    @pytest.mark.asyncio
    async def test_returns_error_when_image_not_found(self, test_db: Session, sample_host: models.Host):
        """Should return error when image not in library."""
        from app.tasks.image_sync import sync_image_to_agent

        with patch("app.tasks.image_sync.load_manifest") as mock_manifest:
            mock_manifest.return_value = {"images": []}
            with patch("app.tasks.image_sync.find_image_by_id") as mock_find:
                mock_find.return_value = None

                success, error = await sync_image_to_agent("nonexistent:image", sample_host.id, test_db)

                assert success is False
                assert "not found" in error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_for_non_docker_image(self, test_db: Session, sample_host: models.Host):
        """Should return error for non-Docker images."""
        from app.tasks.image_sync import sync_image_to_agent

        with patch("app.tasks.image_sync.load_manifest") as mock_manifest:
            mock_manifest.return_value = {"images": [{"id": "qcow2:test", "kind": "qcow2"}]}
            with patch("app.tasks.image_sync.find_image_by_id") as mock_find:
                mock_find.return_value = {"id": "qcow2:test", "kind": "qcow2"}

                success, error = await sync_image_to_agent("qcow2:test", sample_host.id, test_db)

                assert success is False
                assert "docker" in error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_host_not_found(self, test_db: Session):
        """Should return error when host doesn't exist."""
        from app.tasks.image_sync import sync_image_to_agent

        with patch("app.tasks.image_sync.load_manifest") as mock_manifest:
            mock_manifest.return_value = {"images": [{"id": "docker:test:1.0", "kind": "docker"}]}
            with patch("app.tasks.image_sync.find_image_by_id") as mock_find:
                mock_find.return_value = {"id": "docker:test:1.0", "kind": "docker"}

                success, error = await sync_image_to_agent("docker:test:1.0", "nonexistent-host", test_db)

                assert success is False
                assert "not found" in error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_host_offline(self, test_db: Session, offline_host: models.Host):
        """Should return error when host is offline."""
        from app.tasks.image_sync import sync_image_to_agent

        with patch("app.tasks.image_sync.load_manifest") as mock_manifest:
            mock_manifest.return_value = {"images": [{"id": "docker:test:1.0", "kind": "docker"}]}
            with patch("app.tasks.image_sync.find_image_by_id") as mock_find:
                mock_find.return_value = {"id": "docker:test:1.0", "kind": "docker"}

                success, error = await sync_image_to_agent("docker:test:1.0", offline_host.id, test_db)

                assert success is False
                assert "not online" in error.lower()

    @pytest.mark.asyncio
    async def test_returns_success_when_already_synced(self, test_db: Session, sample_host: models.Host, sample_image_host: models.ImageHost):
        """Should return success immediately if image already synced."""
        from app.tasks.image_sync import sync_image_to_agent

        with patch("app.tasks.image_sync.load_manifest") as mock_manifest:
            mock_manifest.return_value = {"images": [{"id": sample_image_host.image_id, "kind": "docker"}]}
            with patch("app.tasks.image_sync.find_image_by_id") as mock_find:
                mock_find.return_value = {"id": sample_image_host.image_id, "kind": "docker"}

                success, error = await sync_image_to_agent(sample_image_host.image_id, sample_host.id, test_db)

                assert success is True
                assert error is None


class TestCheckAgentHasImage:
    """Tests for the check_agent_has_image function."""

    @pytest.mark.asyncio
    async def test_returns_true_when_image_exists(self, sample_host: models.Host):
        """Should return True when agent has the image."""
        from app.tasks.image_sync import check_agent_has_image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"exists": True}

        with patch("app.tasks.image_sync.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await check_agent_has_image(sample_host, "test:1.0")

            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_image_missing(self, sample_host: models.Host):
        """Should return False when agent doesn't have the image."""
        from app.tasks.image_sync import check_agent_has_image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"exists": False}

        with patch("app.tasks.image_sync.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await check_agent_has_image(sample_host, "missing:1.0")

            assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self, sample_host: models.Host):
        """Should return False when request fails."""
        from app.tasks.image_sync import check_agent_has_image

        with patch("app.tasks.image_sync.httpx.AsyncClient") as mock_client:
            mock_client.side_effect = Exception("Connection error")

            result = await check_agent_has_image(sample_host, "test:1.0")

            assert result is False


class TestGetAgentImageInventory:
    """Tests for the get_agent_image_inventory function."""

    @pytest.mark.asyncio
    async def test_returns_image_list(self, sample_host: models.Host):
        """Should return list of images from agent."""
        from app.tasks.image_sync import get_agent_image_inventory

        mock_images = [
            {"id": "sha256:abc", "tags": ["ceos:4.28.0F"], "size_bytes": 1000000},
            {"id": "sha256:def", "tags": ["alpine:latest"], "size_bytes": 5000000},
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"images": mock_images}

        with patch("app.tasks.image_sync.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await get_agent_image_inventory(sample_host)

            assert len(result) == 2
            assert result[0]["tags"] == ["ceos:4.28.0F"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_error(self, sample_host: models.Host):
        """Should return empty list when request fails."""
        from app.tasks.image_sync import get_agent_image_inventory

        with patch("app.tasks.image_sync.httpx.AsyncClient") as mock_client:
            mock_client.side_effect = Exception("Connection error")

            result = await get_agent_image_inventory(sample_host)

            assert result == []


class TestReconcileAgentImages:
    """Tests for the reconcile_agent_images function."""

    @pytest.mark.asyncio
    async def test_handles_offline_host(self, test_db: Session, offline_host: models.Host):
        """Should return early for offline hosts."""
        from app.tasks.image_sync import reconcile_agent_images

        await reconcile_agent_images(offline_host.id, test_db)

    @pytest.mark.asyncio
    async def test_handles_nonexistent_host(self, test_db: Session):
        """Should handle nonexistent host gracefully."""
        from app.tasks.image_sync import reconcile_agent_images

        await reconcile_agent_images("nonexistent-host-id", test_db)

    @pytest.mark.asyncio
    async def test_marks_present_images_as_synced(self, test_db: Session, sample_host: models.Host):
        """Should mark images present on agent as synced."""
        from app.tasks.image_sync import reconcile_agent_images

        mock_images = [{"id": "sha256:abc", "tags": ["ceos:4.28.0F"], "size_bytes": 1000000}]

        with patch("app.tasks.image_sync.get_agent_image_inventory", new_callable=AsyncMock) as mock_inventory:
            mock_inventory.return_value = mock_images
            with patch("app.tasks.image_sync.load_manifest") as mock_manifest:
                mock_manifest.return_value = {
                    "images": [
                        {"id": "docker:ceos:4.28.0F", "kind": "docker", "reference": "ceos:4.28.0F"}
                    ]
                }

                await reconcile_agent_images(sample_host.id, test_db)

                # Check that ImageHost was created/updated
                image_host = test_db.query(models.ImageHost).filter(
                    models.ImageHost.host_id == sample_host.id,
                    models.ImageHost.image_id == "docker:ceos:4.28.0F"
                ).first()
                assert image_host is not None
                assert image_host.status == "synced"


class TestPushImageOnUpload:
    """Tests for the push_image_on_upload function."""

    @pytest.mark.asyncio
    async def test_skips_when_sync_disabled(self, test_db: Session, monkeypatch):
        """Should skip when image sync is disabled."""
        from app.tasks.image_sync import push_image_on_upload
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_enabled", False)

        with patch("app.tasks.image_sync.sync_image_to_agent", new_callable=AsyncMock) as mock_sync:
            await push_image_on_upload("docker:test:1.0", test_db)
            mock_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_push_hosts(self, test_db: Session, sample_host: models.Host, monkeypatch):
        """Should skip when no hosts have push strategy."""
        from app.tasks.image_sync import push_image_on_upload
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_enabled", True)

        # sample_host has default strategy (on_demand), not push
        with patch("app.tasks.image_sync.sync_image_to_agent", new_callable=AsyncMock) as mock_sync:
            await push_image_on_upload("docker:test:1.0", test_db)
            mock_sync.assert_not_called()


class TestPullImagesOnRegistration:
    """Tests for the pull_images_on_registration function."""

    @pytest.mark.asyncio
    async def test_skips_when_sync_disabled(self, test_db: Session, sample_host: models.Host, monkeypatch):
        """Should skip when image sync is disabled."""
        from app.tasks.image_sync import pull_images_on_registration
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_enabled", False)

        with patch("app.tasks.image_sync.sync_image_to_agent", new_callable=AsyncMock) as mock_sync:
            await pull_images_on_registration(sample_host.id, test_db)
            mock_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_nonexistent_host(self, test_db: Session, monkeypatch):
        """Should skip when host doesn't exist."""
        from app.tasks.image_sync import pull_images_on_registration
        from app.config import settings

        monkeypatch.setattr(settings, "image_sync_enabled", True)

        await pull_images_on_registration("nonexistent-host", test_db)


class TestGetImagesFromTopology:
    """Tests for the get_images_from_topology function."""

    def test_extracts_images_from_valid_topology(self):
        """Should extract image references from valid topology YAML."""
        from app.tasks.image_sync import get_images_from_topology

        topology = """
name: test-lab
topology:
  nodes:
    router1:
      kind: ceos
      image: ceos:4.28.0F
    router2:
      kind: ceos
      image: ceos:4.28.0F
    linux1:
      kind: linux
      image: alpine:latest
"""
        images = get_images_from_topology(topology)

        assert len(images) == 2  # Unique images
        assert "ceos:4.28.0F" in images
        assert "alpine:latest" in images

    def test_returns_empty_for_invalid_yaml(self):
        """Should return empty list for invalid YAML."""
        from app.tasks.image_sync import get_images_from_topology

        images = get_images_from_topology("invalid: yaml: {{")

        assert images == []

    def test_returns_empty_for_topology_without_images(self):
        """Should return empty list for topology without images."""
        from app.tasks.image_sync import get_images_from_topology

        topology = """
name: test-lab
topology:
  nodes:
    router1:
      kind: linux
"""
        images = get_images_from_topology(topology)

        assert images == []


class TestGetImageToNodesMap:
    """Tests for the get_image_to_nodes_map function."""

    def test_maps_images_to_nodes(self):
        """Should create mapping from images to node names."""
        from app.tasks.image_sync import get_image_to_nodes_map

        topology = """
name: test-lab
topology:
  nodes:
    router1:
      image: ceos:4.28.0F
    router2:
      image: ceos:4.28.0F
    switch1:
      image: veos:4.27.0F
"""
        mapping = get_image_to_nodes_map(topology)

        assert "ceos:4.28.0F" in mapping
        assert len(mapping["ceos:4.28.0F"]) == 2
        assert "router1" in mapping["ceos:4.28.0F"]
        assert "router2" in mapping["ceos:4.28.0F"]
        assert "veos:4.27.0F" in mapping
        assert "switch1" in mapping["veos:4.27.0F"]

    def test_returns_empty_for_invalid_yaml(self):
        """Should return empty dict for invalid YAML."""
        from app.tasks.image_sync import get_image_to_nodes_map

        mapping = get_image_to_nodes_map("invalid: yaml: {{")

        assert mapping == {}


class TestUpdateNodeImageSyncStatus:
    """Tests for the update_node_image_sync_status function."""

    def test_updates_node_status(self, test_db: Session, sample_lab_with_nodes):
        """Should update image sync status for specified nodes."""
        from app.tasks.image_sync import update_node_image_sync_status

        lab, nodes = sample_lab_with_nodes
        node_names = [n.node_name for n in nodes]

        update_node_image_sync_status(
            test_db,
            lab.id,
            node_names,
            "syncing",
            "Syncing image to agent..."
        )

        for node in nodes:
            test_db.refresh(node)
            assert node.image_sync_status == "syncing"
            assert node.image_sync_message == "Syncing image to agent..."

    def test_handles_empty_node_list(self, test_db: Session, sample_lab: models.Lab):
        """Should handle empty node list gracefully."""
        from app.tasks.image_sync import update_node_image_sync_status

        # Should not raise
        update_node_image_sync_status(test_db, sample_lab.id, [], "syncing", "Test")

    def test_clears_status_when_none(self, test_db: Session, sample_lab_with_nodes):
        """Should clear status when set to None."""
        from app.tasks.image_sync import update_node_image_sync_status

        lab, nodes = sample_lab_with_nodes

        # First set a status
        node_names = [n.node_name for n in nodes]
        update_node_image_sync_status(test_db, lab.id, node_names, "syncing", "Test")

        # Then clear it
        update_node_image_sync_status(test_db, lab.id, node_names, None, None)

        for node in nodes:
            test_db.refresh(node)
            assert node.image_sync_status is None
            assert node.image_sync_message is None
