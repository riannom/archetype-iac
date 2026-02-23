"""Base classes for agent schema models."""

from pydantic import BaseModel, Field


class BaseResponse(BaseModel):
    """Base for all response models with success/error pattern."""
    success: bool
    error: str | None = None


class HardwareSpecMixin(BaseModel):
    """Shared hardware specification fields for deploy and node lifecycle."""
    memory: int | None = Field(None, gt=0, description="RAM in MB")
    cpu: int | None = Field(None, gt=0, description="vCPU count")
    cpu_limit: int | None = Field(None, ge=1, le=100, description="CPU limit percentage")
    disk_driver: str | None = None
    nic_driver: str | None = None
    machine_type: str | None = None
    libvirt_driver: str | None = None
    readiness_probe: str | None = None
    readiness_pattern: str | None = None
    readiness_timeout: int | None = Field(None, gt=0, description="Readiness timeout in seconds")
    efi_boot: bool | None = None
    efi_vars: str | None = None
    data_volume_gb: int | None = Field(None, ge=0, description="Data volume size in GB")
