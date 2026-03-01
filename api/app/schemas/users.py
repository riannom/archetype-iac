from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, ConfigDict, model_validator


class UserOut(BaseModel):
    id: str
    username: str
    email: EmailStr
    is_active: bool
    global_role: str = "operator"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PermissionCreate(BaseModel):
    user_identifier: str  # username or email
    role: str = "viewer"


class PermissionOut(BaseModel):
    id: str
    lab_id: str
    user_id: str
    role: str
    created_at: datetime
    user_email: EmailStr | None = None
    user_username: str | None = None

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# User Preferences Schemas (Notification System)
# =============================================================================


class NotificationToastSettings(BaseModel):
    """Settings for toast notifications."""
    enabled: bool = True
    position: str = "bottom-right"  # bottom-right, bottom-left, top-right, top-left
    duration: int = 5000  # milliseconds
    showJobStart: bool = True
    showJobComplete: bool = True
    showJobFailed: bool = True
    showJobRetry: bool = True
    showImageSync: bool = True


class NotificationBellSettings(BaseModel):
    """Settings for notification center (bell icon)."""
    enabled: bool = True
    maxHistory: int = 50
    soundEnabled: bool = False


class NotificationSettings(BaseModel):
    """Combined notification settings."""
    toasts: NotificationToastSettings = NotificationToastSettings()
    bell: NotificationBellSettings = NotificationBellSettings()


class CanvasErrorIndicatorSettings(BaseModel):
    """Settings for canvas error indicators on nodes."""
    showIcon: bool = True
    showBorder: bool = True
    pulseAnimation: bool = True


class SidebarFilterSettings(BaseModel):
    """Sidebar device library filter preferences."""
    searchQuery: str = ""
    selectedVendors: list[str] = []
    selectedTypes: list[str] = []
    imageStatus: str = "all"  # 'all' | 'has_image' | 'has_default' | 'no_image'


class CanvasSettings(BaseModel):
    """Canvas display preferences."""
    errorIndicator: CanvasErrorIndicatorSettings = CanvasErrorIndicatorSettings()
    showAgentIndicators: bool = True
    sidebarFilters: SidebarFilterSettings = SidebarFilterSettings()
    consoleInBottomPanel: bool = False
    metricsBarExpanded: bool = False


class ThemeSettings(BaseModel):
    """Theme customization preferences."""
    themeId: str = "sage-stone"
    mode: str = "system"  # 'light' | 'dark' | 'system'
    backgroundId: str = "breath"
    backgroundOpacity: int = 50
    taskLogOpacity: int = 92
    favoriteBackgrounds: list[str] = Field(default_factory=list)
    favoriteThemeIds: list[str] = Field(default_factory=list)
    customThemes: list[dict[str, Any]] = Field(default_factory=list)


class UserPreferencesOut(BaseModel):
    """Output schema for user preferences."""
    notification_settings: NotificationSettings = NotificationSettings()
    canvas_settings: CanvasSettings = CanvasSettings()
    theme_settings: ThemeSettings = ThemeSettings()


class UserPreferencesUpdate(BaseModel):
    """Input schema for updating user preferences."""
    notification_settings: NotificationSettings | None = None
    canvas_settings: CanvasSettings | None = None
    theme_settings: ThemeSettings | None = None


# =============================================================================
# User Management Schemas (RBAC)
# =============================================================================


class UserCreateAdmin(BaseModel):
    """Admin-created user."""

    username: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9._-]{2,31}$")
    password: str = Field(min_length=8, max_length=72)
    email: EmailStr | None = None
    global_role: str = "operator"


class UserUpdateAdmin(BaseModel):
    """Admin update of user profile."""

    email: EmailStr | None = None
    global_role: str | None = None


class PasswordChange(BaseModel):
    """Password change request."""

    current_password: str | None = None  # Required for self-change, optional for admin
    new_password: str = Field(min_length=8, max_length=72)


class UserListResponse(BaseModel):
    """Response for listing users."""

    users: list[UserOut]
    total: int


# =============================================================================
# Audit Log Schemas
# =============================================================================


class AuditLogOut(BaseModel):
    """Output schema for an audit log entry."""

    id: str
    event_type: str
    user_id: str | None = None
    target_user_id: str | None = None
    ip_address: str | None = None
    details: dict | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogsResponse(BaseModel):
    """Response for listing audit logs."""

    entries: list[AuditLogOut]
    total: int
    has_more: bool = False


# =============================================================================
# Support Bundle Schemas
# =============================================================================


class SupportBundleCreate(BaseModel):
    """Create request for an offline support bundle."""

    summary: str = Field(min_length=5, max_length=4000)
    repro_steps: str = Field(min_length=5, max_length=10000)
    expected_behavior: str = Field(min_length=2, max_length=4000)
    actual_behavior: str = Field(min_length=2, max_length=4000)
    incident_started_at: datetime | None = None
    incident_ended_at: datetime | None = None
    time_window_hours: int = Field(default=24, ge=1, le=168)
    impacted_lab_ids: list[str] = Field(default_factory=list)
    impacted_agent_ids: list[str] = Field(default_factory=list)
    include_configs: bool = False
    pii_safe: bool = True

    @model_validator(mode="after")
    def validate_incident_window(self):
        if self.incident_started_at and self.incident_ended_at:
            if self.incident_ended_at < self.incident_started_at:
                raise ValueError("incident_ended_at must be >= incident_started_at")
        return self


class SupportBundleOut(BaseModel):
    """Metadata for a generated support bundle."""

    id: str
    user_id: str
    status: str
    include_configs: bool
    pii_safe: bool
    time_window_hours: int
    size_bytes: int | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    completeness_status: Literal["pending", "ok", "warning", "unknown"] = "pending"
    completeness_warning_count: int = 0
    completeness_warnings: list[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
