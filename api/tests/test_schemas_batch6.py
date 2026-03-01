"""Batch 6: Pydantic schema validation tests.

Covers untested validation logic across schemas.py:
- NodeStateUpdate regex pattern
- LinkStateUpdate regex pattern
- CarrierStateChangeRequest Literal field
- InfraSettingsUpdate Field constraints (ge/le)
- AgentNetworkConfigUpdate pattern + constraints
- SetMtuRequestIn bounds
- AgentManagedInterfaceCreate pattern + bounds
- NodeStateOut @model_validator
- ThemeSettings defaults
- CanvasSettings nested defaults
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# NodeStateUpdate — regex pattern "^(stopped|running)$"
# ---------------------------------------------------------------------------

class TestNodeStateUpdate:
    def test_running_accepted(self):
        from app.schemas import NodeStateUpdate
        obj = NodeStateUpdate(state="running")
        assert obj.state == "running"

    def test_stopped_accepted(self):
        from app.schemas import NodeStateUpdate
        obj = NodeStateUpdate(state="stopped")
        assert obj.state == "stopped"

    def test_invalid_state_rejected(self):
        from app.schemas import NodeStateUpdate
        with pytest.raises(ValidationError):
            NodeStateUpdate(state="pending")

    def test_empty_string_rejected(self):
        from app.schemas import NodeStateUpdate
        with pytest.raises(ValidationError):
            NodeStateUpdate(state="")

    def test_partial_match_rejected(self):
        from app.schemas import NodeStateUpdate
        with pytest.raises(ValidationError):
            NodeStateUpdate(state="running_extra")

    def test_case_sensitive(self):
        from app.schemas import NodeStateUpdate
        with pytest.raises(ValidationError):
            NodeStateUpdate(state="Running")


# ---------------------------------------------------------------------------
# LinkStateUpdate — regex pattern "^(up|down)$"
# ---------------------------------------------------------------------------

class TestLinkStateUpdate:
    def test_up_accepted(self):
        from app.schemas import LinkStateUpdate
        obj = LinkStateUpdate(state="up")
        assert obj.state == "up"

    def test_down_accepted(self):
        from app.schemas import LinkStateUpdate
        obj = LinkStateUpdate(state="down")
        assert obj.state == "down"

    def test_invalid_rejected(self):
        from app.schemas import LinkStateUpdate
        with pytest.raises(ValidationError):
            LinkStateUpdate(state="unknown")

    def test_empty_rejected(self):
        from app.schemas import LinkStateUpdate
        with pytest.raises(ValidationError):
            LinkStateUpdate(state="")

    def test_case_sensitive(self):
        from app.schemas import LinkStateUpdate
        with pytest.raises(ValidationError):
            LinkStateUpdate(state="Up")


# ---------------------------------------------------------------------------
# CarrierStateChangeRequest — Literal["on", "off"]
# ---------------------------------------------------------------------------

class TestCarrierStateChangeRequest:
    def test_on_accepted(self):
        from app.schemas import CarrierStateChangeRequest
        obj = CarrierStateChangeRequest(
            lab_id="lab1", node="r1", interface="eth1", carrier_state="on"
        )
        assert obj.carrier_state == "on"

    def test_off_accepted(self):
        from app.schemas import CarrierStateChangeRequest
        obj = CarrierStateChangeRequest(
            lab_id="lab1", node="r1", interface="eth1", carrier_state="off"
        )
        assert obj.carrier_state == "off"

    def test_invalid_carrier_rejected(self):
        from app.schemas import CarrierStateChangeRequest
        with pytest.raises(ValidationError):
            CarrierStateChangeRequest(
                lab_id="lab1", node="r1", interface="eth1", carrier_state="yes"
            )


# ---------------------------------------------------------------------------
# InfraSettingsUpdate — ge/le bounds
# ---------------------------------------------------------------------------

class TestInfraSettingsUpdate:
    def test_valid_mtu(self):
        from app.schemas import InfraSettingsUpdate
        obj = InfraSettingsUpdate(overlay_mtu=1500)
        assert obj.overlay_mtu == 1500

    def test_mtu_min_boundary(self):
        from app.schemas import InfraSettingsUpdate
        obj = InfraSettingsUpdate(overlay_mtu=68)
        assert obj.overlay_mtu == 68

    def test_mtu_max_boundary(self):
        from app.schemas import InfraSettingsUpdate
        obj = InfraSettingsUpdate(overlay_mtu=9000)
        assert obj.overlay_mtu == 9000

    def test_mtu_below_min_rejected(self):
        from app.schemas import InfraSettingsUpdate
        with pytest.raises(ValidationError):
            InfraSettingsUpdate(overlay_mtu=67)

    def test_mtu_above_max_rejected(self):
        from app.schemas import InfraSettingsUpdate
        with pytest.raises(ValidationError):
            InfraSettingsUpdate(overlay_mtu=9001)

    def test_opacity_valid(self):
        from app.schemas import InfraSettingsUpdate
        obj = InfraSettingsUpdate(login_dark_background_opacity=75)
        assert obj.login_dark_background_opacity == 75

    def test_opacity_min(self):
        from app.schemas import InfraSettingsUpdate
        obj = InfraSettingsUpdate(login_dark_background_opacity=0)
        assert obj.login_dark_background_opacity == 0

    def test_opacity_max(self):
        from app.schemas import InfraSettingsUpdate
        obj = InfraSettingsUpdate(login_light_background_opacity=100)
        assert obj.login_light_background_opacity == 100

    def test_opacity_below_min_rejected(self):
        from app.schemas import InfraSettingsUpdate
        with pytest.raises(ValidationError):
            InfraSettingsUpdate(login_dark_background_opacity=-1)

    def test_opacity_above_max_rejected(self):
        from app.schemas import InfraSettingsUpdate
        with pytest.raises(ValidationError):
            InfraSettingsUpdate(login_light_background_opacity=101)

    def test_all_none_valid(self):
        from app.schemas import InfraSettingsUpdate
        obj = InfraSettingsUpdate()
        assert obj.overlay_mtu is None


# ---------------------------------------------------------------------------
# AgentNetworkConfigUpdate — pattern + ge/le
# ---------------------------------------------------------------------------

class TestAgentNetworkConfigUpdate:
    def test_valid_transport_mode_management(self):
        from app.schemas import AgentNetworkConfigUpdate
        obj = AgentNetworkConfigUpdate(transport_mode="management")
        assert obj.transport_mode == "management"

    def test_valid_transport_mode_subinterface(self):
        from app.schemas import AgentNetworkConfigUpdate
        obj = AgentNetworkConfigUpdate(transport_mode="subinterface")
        assert obj.transport_mode == "subinterface"

    def test_valid_transport_mode_dedicated(self):
        from app.schemas import AgentNetworkConfigUpdate
        obj = AgentNetworkConfigUpdate(transport_mode="dedicated")
        assert obj.transport_mode == "dedicated"

    def test_invalid_transport_mode_rejected(self):
        from app.schemas import AgentNetworkConfigUpdate
        with pytest.raises(ValidationError):
            AgentNetworkConfigUpdate(transport_mode="auto")

    def test_desired_mtu_valid(self):
        from app.schemas import AgentNetworkConfigUpdate
        obj = AgentNetworkConfigUpdate(desired_mtu=9000)
        assert obj.desired_mtu == 9000

    def test_desired_mtu_below_min_rejected(self):
        from app.schemas import AgentNetworkConfigUpdate
        with pytest.raises(ValidationError):
            AgentNetworkConfigUpdate(desired_mtu=67)

    def test_desired_mtu_above_max_rejected(self):
        from app.schemas import AgentNetworkConfigUpdate
        with pytest.raises(ValidationError):
            AgentNetworkConfigUpdate(desired_mtu=9217)

    def test_vlan_id_valid(self):
        from app.schemas import AgentNetworkConfigUpdate
        obj = AgentNetworkConfigUpdate(vlan_id=100)
        assert obj.vlan_id == 100

    def test_vlan_id_below_min_rejected(self):
        from app.schemas import AgentNetworkConfigUpdate
        with pytest.raises(ValidationError):
            AgentNetworkConfigUpdate(vlan_id=0)

    def test_vlan_id_above_max_rejected(self):
        from app.schemas import AgentNetworkConfigUpdate
        with pytest.raises(ValidationError):
            AgentNetworkConfigUpdate(vlan_id=4095)


# ---------------------------------------------------------------------------
# SetMtuRequestIn — ge/le bounds
# ---------------------------------------------------------------------------

class TestSetMtuRequestIn:
    def test_valid_mtu(self):
        from app.schemas import SetMtuRequestIn
        obj = SetMtuRequestIn(mtu=9000)
        assert obj.mtu == 9000

    def test_mtu_min_boundary(self):
        from app.schemas import SetMtuRequestIn
        obj = SetMtuRequestIn(mtu=68)
        assert obj.mtu == 68

    def test_mtu_max_boundary(self):
        from app.schemas import SetMtuRequestIn
        obj = SetMtuRequestIn(mtu=9216)
        assert obj.mtu == 9216

    def test_mtu_below_min_rejected(self):
        from app.schemas import SetMtuRequestIn
        with pytest.raises(ValidationError):
            SetMtuRequestIn(mtu=67)

    def test_mtu_above_max_rejected(self):
        from app.schemas import SetMtuRequestIn
        with pytest.raises(ValidationError):
            SetMtuRequestIn(mtu=9217)

    def test_persist_default_true(self):
        from app.schemas import SetMtuRequestIn
        obj = SetMtuRequestIn(mtu=1500)
        assert obj.persist is True


# ---------------------------------------------------------------------------
# AgentManagedInterfaceCreate — pattern + bounds
# ---------------------------------------------------------------------------

class TestAgentManagedInterfaceCreate:
    def test_valid_transport(self):
        from app.schemas import AgentManagedInterfaceCreate
        obj = AgentManagedInterfaceCreate(interface_type="transport")
        assert obj.interface_type == "transport"

    def test_valid_external(self):
        from app.schemas import AgentManagedInterfaceCreate
        obj = AgentManagedInterfaceCreate(interface_type="external")
        assert obj.interface_type == "external"

    def test_valid_custom(self):
        from app.schemas import AgentManagedInterfaceCreate
        obj = AgentManagedInterfaceCreate(interface_type="custom")
        assert obj.interface_type == "custom"

    def test_invalid_type_rejected(self):
        from app.schemas import AgentManagedInterfaceCreate
        with pytest.raises(ValidationError):
            AgentManagedInterfaceCreate(interface_type="bridge")

    def test_vlan_id_valid_range(self):
        from app.schemas import AgentManagedInterfaceCreate
        obj = AgentManagedInterfaceCreate(interface_type="transport", vlan_id=4094)
        assert obj.vlan_id == 4094

    def test_vlan_id_below_min_rejected(self):
        from app.schemas import AgentManagedInterfaceCreate
        with pytest.raises(ValidationError):
            AgentManagedInterfaceCreate(interface_type="transport", vlan_id=0)

    def test_desired_mtu_default(self):
        from app.schemas import AgentManagedInterfaceCreate
        obj = AgentManagedInterfaceCreate(interface_type="transport")
        assert obj.desired_mtu == 9000

    def test_desired_mtu_below_min_rejected(self):
        from app.schemas import AgentManagedInterfaceCreate
        with pytest.raises(ValidationError):
            AgentManagedInterfaceCreate(interface_type="transport", desired_mtu=50)


# ---------------------------------------------------------------------------
# NodeStateOut — @model_validator display_state computation
# ---------------------------------------------------------------------------

class TestNodeStateOutModelValidator:
    def _make(self, actual_state: str, desired_state: str):
        from app.schemas import NodeStateOut
        now = datetime.now(timezone.utc)
        return NodeStateOut(
            id="ns-1", lab_id="lab-1", node_id="n-1", node_name="r1",
            desired_state=desired_state, actual_state=actual_state,
            created_at=now, updated_at=now,
        )

    def test_running_display_state(self):
        obj = self._make("running", "running")
        assert obj.display_state == "running"

    def test_stopped_display_state(self):
        obj = self._make("stopped", "stopped")
        assert obj.display_state == "stopped"

    def test_starting_display_state(self):
        obj = self._make("starting", "running")
        assert obj.display_state == "starting"

    def test_stopping_display_state(self):
        obj = self._make("stopping", "stopped")
        assert obj.display_state == "stopping"

    def test_error_display_state(self):
        obj = self._make("error", "running")
        assert obj.display_state == "error"

    def test_undeployed_display_state(self):
        obj = self._make("undeployed", "stopped")
        assert obj.display_state == "stopped"


# ---------------------------------------------------------------------------
# ThemeSettings & CanvasSettings — defaults
# ---------------------------------------------------------------------------

class TestSettingsDefaults:
    def test_theme_settings_defaults(self):
        from app.schemas import ThemeSettings
        obj = ThemeSettings()
        assert obj.themeId == "sage-stone"
        assert obj.mode == "system"
        assert obj.backgroundId == "breath"
        assert obj.backgroundOpacity == 50
        assert obj.favoriteBackgrounds == []
        assert obj.customThemes == []

    def test_canvas_settings_defaults(self):
        from app.schemas import CanvasSettings
        obj = CanvasSettings()
        assert obj.showAgentIndicators is True
        assert obj.consoleInBottomPanel is False
        assert obj.errorIndicator.showIcon is True
        assert obj.sidebarFilters.searchQuery == ""

    def test_notification_settings_defaults(self):
        from app.schemas import NotificationSettings
        obj = NotificationSettings()
        assert obj.toasts.enabled is True
        assert obj.toasts.duration == 5000
        assert obj.bell.enabled is True
        assert obj.bell.soundEnabled is False
