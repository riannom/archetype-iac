"""Centralized naming conventions for container and VM resources.

All agent components that construct container/domain names MUST use these
functions to ensure naming consistency across providers and network modules.
"""

import re

# Container name prefix for Docker
DOCKER_PREFIX = "archetype"

# Domain name prefix for libvirt VMs
LIBVIRT_PREFIX = "arch"


def sanitize_id(value: str, max_len: int = 0) -> str:
    """Sanitize a string for use in container/domain names.

    Strips all characters except alphanumeric, underscore, and dash.
    Optionally truncates to max_len if > 0.
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", value)
    if max_len > 0:
        safe = safe[:max_len]
    return safe


def docker_container_name(lab_id: str, node_name: str) -> str:
    """Generate Docker container name for a node.

    Format: archetype-{safe_lab_id[:20]}-{safe_node_name}
    """
    safe_lab_id = sanitize_id(lab_id, max_len=20)
    safe_node = sanitize_id(node_name)
    return f"{DOCKER_PREFIX}-{safe_lab_id}-{safe_node}"


def libvirt_domain_name(lab_id: str, node_name: str) -> str:
    """Generate libvirt domain name for a node.

    Format: arch-{safe_lab_id[:20]}-{safe_node_name[:30]}
    """
    safe_lab_id = sanitize_id(lab_id, max_len=20)
    safe_node = sanitize_id(node_name, max_len=30)
    return f"{LIBVIRT_PREFIX}-{safe_lab_id}-{safe_node}"
