"""Shared Docker container label constants.

These label keys are used across multiple Docker provider modules to tag
containers with Archetype metadata.  Centralised here to avoid duplication.
"""

LABEL_LAB_ID = "archetype.lab_id"
LABEL_NODE_DEFINITION_ID = "archetype.node_definition_id"
LABEL_NODE_NAME = "archetype.node_name"
LABEL_NODE_DISPLAY_NAME = "archetype.node_display_name"
LABEL_NODE_KIND = "archetype.node_kind"
LABEL_NODE_INTERFACE_COUNT = "archetype.node_interface_count"
LABEL_NODE_READINESS_PROBE = "archetype.readiness_probe"
LABEL_NODE_READINESS_PATTERN = "archetype.readiness_pattern"
LABEL_NODE_READINESS_TIMEOUT = "archetype.readiness_timeout"
LABEL_PROVIDER = "archetype.provider"
