"""Docker network plugin protocol handlers mixin for DockerOVSPlugin.

Contains the 12 handle_* methods for the Docker network plugin protocol.
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


class PluginHandlersMixin:
    """Docker network plugin protocol handlers mixin for DockerOVSPlugin.

    Provides all handle_* methods that implement the Docker network
    plugin API protocol.
    """

    # =========================================================================
    # Docker Plugin API Handlers
    # =========================================================================

    async def handle_activate(self, request: web.Request) -> web.Response:
        """Handle /Plugin.Activate - Return plugin capabilities."""
        return web.json_response({"Implements": ["NetworkDriver"]})

    async def handle_get_capabilities(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.GetCapabilities."""
        return web.json_response({
            "Scope": "local",
            "ConnectivityScope": "local",
        })

    async def handle_create_network(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.CreateNetwork - Register interface network."""
        data = await request.json()
        network_id = data.get("NetworkID", "")
        options = data.get("Options", {})

        # Get options
        generic_opts = options.get("com.docker.network.generic", {})
        lab_id = generic_opts.get("lab_id", "")
        interface_name = generic_opts.get("interface_name", "eth1")

        if not lab_id:
            return web.json_response({"Err": "lab_id option is required"})

        logger.info(f"Creating network {network_id[:12]} for lab={lab_id}, interface={interface_name}")

        async with self._locked():
            try:
                from agent.network.docker_plugin import NetworkState

                # Ensure lab bridge exists
                lab_bridge = await self._ensure_bridge(lab_id)

                # Register network
                network = NetworkState(
                    network_id=network_id,
                    lab_id=lab_id,
                    interface_name=interface_name,
                    bridge_name=lab_bridge.bridge_name,
                )
                self.networks[network_id] = network
                lab_bridge.network_ids.add(network_id)

                # Persist state after network creation
                await self._mark_dirty_and_save()

                logger.info(f"Network {network_id[:12]} created on bridge {lab_bridge.bridge_name}")

            except Exception as e:
                logger.error(f"Failed to create network: {e}")
                return web.json_response({"Err": str(e)})

        return web.json_response({})

    async def handle_delete_network(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DeleteNetwork."""
        data = await request.json()
        network_id = data.get("NetworkID", "")

        async with self._locked():
            network = self.networks.pop(network_id, None)
            if network:
                lab_bridge = self.lab_bridges.get(network.lab_id)
                if lab_bridge:
                    lab_bridge.network_ids.discard(network_id)
                    await self._maybe_delete_bridge(network.lab_id)
                logger.info(f"Deleted network {network_id[:12]}")

                # Persist state after network deletion
                await self._mark_dirty_and_save()

        return web.json_response({})

    async def handle_create_endpoint(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.CreateEndpoint - Create veth pair for interface.

        This is called BEFORE the container starts. The veth pair is created
        and attached to OVS. Docker will move the container-side into the
        container's namespace during Join.
        """
        data = await request.json()
        network_id = data.get("NetworkID", "")
        endpoint_id = data.get("EndpointID", "")

        async with self._locked():
            from agent.network.docker_plugin import EndpointState

            network = self.networks.get(network_id)
            if not network:
                return web.json_response({"Err": f"Network {network_id[:12]} not found"})

            lab_bridge = self.lab_bridges.get(network.lab_id)
            if not lab_bridge:
                return web.json_response({"Err": f"Lab bridge for {network.lab_id} not found"})

            # Generate veth names
            host_veth, cont_veth = self._generate_veth_names(endpoint_id)

            # Allocate VLAN (isolated until hot_connect)
            vlan_tag = await self._allocate_vlan(lab_bridge)

            # Create veth pair
            if not await self._create_veth_pair(host_veth, cont_veth):
                return web.json_response({"Err": "Failed to create veth pair"})

            # Attach to OVS
            if not await self._attach_to_ovs(
                network.bridge_name,
                host_veth,
                vlan_tag,
                external_ids={
                    "archetype.endpoint_id": endpoint_id,
                    "archetype.interface_name": network.interface_name,
                    "archetype.lab_id": network.lab_id,
                    "archetype.network_id": network_id,
                },
            ):
                await self._run_cmd(["ip", "link", "delete", host_veth])
                return web.json_response({"Err": "Failed to attach to OVS"})

            # Track endpoint
            endpoint = EndpointState(
                endpoint_id=endpoint_id,
                network_id=network_id,
                interface_name=network.interface_name,
                host_veth=host_veth,
                cont_veth=cont_veth,
                vlan_tag=vlan_tag,
            )
            self.endpoints[endpoint_id] = endpoint

            # Update activity timestamp
            self._touch_lab(network.lab_id)

            # Persist state after endpoint creation
            await self._mark_dirty_and_save()

            logger.info(
                f"Created endpoint {endpoint_id[:12]}: {host_veth} <-> {cont_veth} "
                f"({network.interface_name}, VLAN {vlan_tag})"
            )

        return web.json_response({"Interface": {}})

    async def handle_delete_endpoint(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DeleteEndpoint - Clean up veth pair."""
        data = await request.json()
        network_id = data.get("NetworkID", "")
        endpoint_id = data.get("EndpointID", "")

        async with self._locked():
            endpoint = self.endpoints.pop(endpoint_id, None)
            if endpoint:
                self._release_vlan(endpoint.vlan_tag)
                network = self.networks.get(network_id)
                if network:
                    await self._delete_port(network.bridge_name, endpoint.host_veth)
                logger.info(f"Deleted endpoint {endpoint_id[:12]}")

                # Persist state after endpoint deletion
                await self._mark_dirty_and_save()

        return web.json_response({})

    async def handle_join(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.Join - Provide interface config to Docker.

        Docker will move the veth into the container namespace and rename it.
        This happens BEFORE the container's init process runs.
        """
        data = await request.json()
        endpoint_id = data.get("EndpointID", "")
        sandbox_key = data.get("SandboxKey", "")

        async with self._locked():
            endpoint = self.endpoints.get(endpoint_id)
            if not endpoint:
                return web.json_response({"Err": f"Endpoint {endpoint_id[:12]} not found"})

            logger.info(
                f"Join endpoint {endpoint_id[:12]} -> {endpoint.interface_name} "
                f"(sandbox: {sandbox_key})"
            )

            # Use DstName to specify exact interface name, not DstPrefix which
            # lets Docker auto-number based on attachment order (causing mismatches)
            return web.json_response({
                "InterfaceName": {
                    "SrcName": endpoint.cont_veth,
                    "DstName": endpoint.interface_name,
                },
            })

    async def handle_leave(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.Leave - Container disconnecting."""
        data = await request.json()
        endpoint_id = data.get("EndpointID", "")
        logger.debug(f"Leave endpoint {endpoint_id[:12]}")
        return web.json_response({})

    async def handle_endpoint_oper_info(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.EndpointOperInfo - Return endpoint operational info."""
        data = await request.json()
        endpoint_id = data.get("EndpointID", "")
        logger.debug(f"EndpointOperInfo for {endpoint_id[:12]}")
        # Return empty Value - Docker just wants to know the endpoint exists
        return web.json_response({"Value": {}})

    async def handle_discover_new(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DiscoverNew."""
        return web.json_response({})

    async def handle_discover_delete(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.DiscoverDelete."""
        return web.json_response({})

    async def handle_program_external_connectivity(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.ProgramExternalConnectivity."""
        return web.json_response({})

    async def handle_revoke_external_connectivity(self, request: web.Request) -> web.Response:
        """Handle /NetworkDriver.RevokeExternalConnectivity."""
        return web.json_response({})
