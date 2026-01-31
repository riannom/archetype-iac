from __future__ import annotations

from typing import Callable


class ProviderActionError(ValueError):
    pass


def _unsupported_node_command(lab_id: str, action: str, node: str) -> list[list[str]]:
    raise ProviderActionError("Node actions are not implemented for this provider")


def _docker_node_command(lab_id: str, action: str, node: str) -> list[list[str]]:
    """Docker provider node actions.

    Note: DockerProvider handles node actions via the agent API, not CLI commands.
    This is kept for backward compatibility with the legacy netlab-based job execution.
    For agent-based deployments, node actions are handled by DockerProvider.start_node()
    and DockerProvider.stop_node() methods.
    """
    raise ProviderActionError(
        "Docker provider node actions are handled by the agent API, not CLI commands"
    )


_NODE_ACTIONS: dict[str, Callable[[str, str, str], list[str]]] = {
    "docker": _docker_node_command,
    "libvirt": _unsupported_node_command,
}

# To add a provider:
# - Implement a <provider>_node_command(lab_id, action, node) builder.
# - Register it in _NODE_ACTIONS with the provider key (matching PROVIDER setting).
# - Update supports_node_actions if the provider supports per-node actions.


def supports_node_actions(provider: str) -> bool:
    """Check if a provider supports per-node start/stop actions.

    Note: DockerProvider supports node actions but handles them via the agent API,
    not through CLI commands built by this module.
    """
    return provider in ("docker",)


def supported_node_actions(provider: str) -> set[str]:
    if provider == "docker":
        return {"start", "stop"}
    return set()


def node_action_command(provider: str, lab_id: str, action: str, node: str) -> list[list[str]]:
    builder = _NODE_ACTIONS.get(provider)
    if not builder:
        raise ProviderActionError("Node actions are not supported for this provider")
    return builder(lab_id, action, node)
