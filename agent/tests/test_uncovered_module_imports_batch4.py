from __future__ import annotations

import agent.n9kv_poap as n9kv_poap
import agent.network.cmd as network_cmd
import agent.routers as routers_pkg
import agent.routers.admin as routers_admin
import agent.routers.console as routers_console
import agent.routers.health as routers_health
import agent.routers.interfaces as routers_interfaces
import agent.routers.nodes as routers_nodes
import agent.routers.overlay as routers_overlay
import agent.routers.ovs_plugin as routers_ovs_plugin
import agent.schemas.admin as schemas_admin
import agent.schemas.base as schemas_base
import agent.schemas.console as schemas_console
import agent.schemas.deploy as schemas_deploy
import agent.schemas.enums as schemas_enums
import agent.schemas.images as schemas_images
import agent.schemas.labs as schemas_labs
import agent.schemas.network as schemas_network
import agent.schemas.node_lifecycle as schemas_node_lifecycle
import agent.schemas.overlay as schemas_overlay
import agent.schemas.plugin as schemas_plugin
import agent.schemas.provisioning as schemas_provisioning


def test_import_only_modules_load() -> None:
    assert routers_pkg is not None
    assert n9kv_poap is not None
    assert network_cmd is not None


def test_router_modules_expose_router() -> None:
    modules = [
        routers_admin,
        routers_console,
        routers_health,
        routers_interfaces,
        routers_nodes,
        routers_overlay,
        routers_ovs_plugin,
    ]
    for module in modules:
        assert hasattr(module, "router")
        assert module.router is not None


def test_schema_modules_load() -> None:
    modules = [
        schemas_admin,
        schemas_base,
        schemas_console,
        schemas_deploy,
        schemas_enums,
        schemas_images,
        schemas_labs,
        schemas_network,
        schemas_node_lifecycle,
        schemas_overlay,
        schemas_plugin,
        schemas_provisioning,
    ]
    for module in modules:
        assert module is not None
