"""SQLAlchemy models package -- re-exports all models for backward compatibility."""

from .base import Base  # noqa: F401
from .auth import User, UserPreferences, AuditLog, Permission, SupportBundle  # noqa: F401
from .lab import Lab, LabFile  # noqa: F401
from .job import Job, ImageSyncJob, AgentUpdateJob, ISOImportJob  # noqa: F401
from .topology import Node, Link  # noqa: F401
from .state import (  # noqa: F401
    NodeState,
    LinkState,
    LinkEndpointReservation,
    NodePlacement,
    NodeMigrationCleanup,
    VxlanTunnel,
    ConfigSnapshot,
)
from .infra import (  # noqa: F401
    Host,
    InfraSettings,
    InterfaceMapping,
    AgentLink,
    AgentNetworkConfig,
    AgentManagedInterface,
    HostNicGroup,
    HostNicGroupMember,
    ExternalNetworkAffinity,
)
from .catalog import (  # noqa: F401
    ImageHost,
    CatalogVendor,
    CatalogDeviceType,
    CatalogDeviceRevision,
    CatalogDeviceAlias,
    CatalogImage,
    CatalogImageCompatibility,
    CatalogImageDefault,
    CatalogIngestEvent,
)
from .webhook import Webhook, WebhookDelivery  # noqa: F401

__all__ = [
    "Base",
    # auth
    "User",
    "UserPreferences",
    "AuditLog",
    "Permission",
    "SupportBundle",
    # lab
    "Lab",
    "LabFile",
    # job
    "Job",
    "ImageSyncJob",
    "AgentUpdateJob",
    "ISOImportJob",
    # topology
    "Node",
    "Link",
    # state
    "NodeState",
    "LinkState",
    "LinkEndpointReservation",
    "NodePlacement",
    "NodeMigrationCleanup",
    "VxlanTunnel",
    "ConfigSnapshot",
    # infra
    "Host",
    "InfraSettings",
    "InterfaceMapping",
    "AgentLink",
    "AgentNetworkConfig",
    "AgentManagedInterface",
    "HostNicGroup",
    "HostNicGroupMember",
    "ExternalNetworkAffinity",
    # catalog
    "ImageHost",
    "CatalogVendor",
    "CatalogDeviceType",
    "CatalogDeviceRevision",
    "CatalogDeviceAlias",
    "CatalogImage",
    "CatalogImageCompatibility",
    "CatalogImageDefault",
    "CatalogIngestEvent",
    # webhook
    "Webhook",
    "WebhookDelivery",
]
