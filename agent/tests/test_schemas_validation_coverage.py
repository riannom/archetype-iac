"""Unit tests for agent schema validation coverage.

Tests cover:
- BaseResponse and HardwareSpecMixin base classes
- Enum types (AgentStatus, NodeStatus, JobStatus, Provider, LinkState)
- Deploy schemas (DeployNode, DeployLink, DeployRequest, DeployTopology)
- Lab schemas (NodeInfo, LabStatusResponse, NodeReconcileTarget)
- Node lifecycle schemas (CreateNodeRequest, StartNodeRequest, StopNodeResponse)
- Console schemas (ConsoleRequest, CliVerifyRequest validation constraints)
- Common schemas (AgentCapabilities, HeartbeatRequest, JobResult)
- Required field validation and optional defaults
- Field constraint enforcement (gt, ge, le)
- Inheritance from BaseResponse and HardwareSpecMixin
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.schemas.base import BaseResponse, HardwareSpecMixin
from agent.schemas.enums import (
    AgentStatus,
    JobStatus,
    LinkState,
    NodeStatus,
    Provider,
)
from agent.schemas.common import (
    AgentCapabilities,
    HeartbeatRequest,
    JobResult,
)
from agent.schemas.deploy import (
    DeployLink,
    DeployNode,
    DeployRequest,
    DeployTopology,
)
from agent.schemas.labs import (
    CleanupLabOrphansRequest,
    ExtractConfigsResponse,
    ExtractedConfig,
    LabStatusResponse,
    NodeInfo,
    NodeReconcileResult,
    NodeReconcileTarget,
)
from agent.schemas.node_lifecycle import (
    CreateNodeRequest,
    CreateNodeResponse,
    DestroyNodeResponse,
    StartNodeRequest,
    StartNodeResponse,
    StopNodeResponse,
)
from agent.schemas.console import (
    CliCommandOutput,
    CliVerifyRequest,
    CliVerifyResponse,
    ConsoleRequest,
)


# ---------------------------------------------------------------------------
# 1. Base classes
# ---------------------------------------------------------------------------


class TestBaseResponse:
    """BaseResponse: success + optional error."""

    def test_success_only(self):
        r = BaseResponse(success=True)
        assert r.success is True
        assert r.error is None

    def test_with_error(self):
        r = BaseResponse(success=False, error="something broke")
        assert r.success is False
        assert r.error == "something broke"

    def test_missing_success_raises(self):
        with pytest.raises(ValidationError):
            BaseResponse()


class TestHardwareSpecMixin:
    """HardwareSpecMixin: all fields optional with constraints."""

    def test_all_defaults_none(self):
        m = HardwareSpecMixin()
        assert m.memory is None
        assert m.cpu is None
        assert m.cpu_limit is None
        assert m.disk_driver is None
        assert m.efi_boot is None
        assert m.data_volume_gb is None

    def test_valid_values(self):
        m = HardwareSpecMixin(memory=4096, cpu=2, cpu_limit=80, readiness_timeout=60)
        assert m.memory == 4096
        assert m.cpu == 2
        assert m.cpu_limit == 80
        assert m.readiness_timeout == 60

    def test_memory_must_be_positive(self):
        with pytest.raises(ValidationError, match="memory"):
            HardwareSpecMixin(memory=0)
        with pytest.raises(ValidationError, match="memory"):
            HardwareSpecMixin(memory=-1)

    def test_cpu_limit_range(self):
        """cpu_limit must be 1..100."""
        with pytest.raises(ValidationError):
            HardwareSpecMixin(cpu_limit=0)
        with pytest.raises(ValidationError):
            HardwareSpecMixin(cpu_limit=101)
        # boundary values
        m = HardwareSpecMixin(cpu_limit=1)
        assert m.cpu_limit == 1
        m = HardwareSpecMixin(cpu_limit=100)
        assert m.cpu_limit == 100

    def test_data_volume_allows_zero(self):
        m = HardwareSpecMixin(data_volume_gb=0)
        assert m.data_volume_gb == 0


# ---------------------------------------------------------------------------
# 2. Enum coverage
# ---------------------------------------------------------------------------


class TestEnums:
    """All enum types have expected members and are str-based."""

    def test_agent_status_values(self):
        assert AgentStatus.ONLINE.value == "online"
        assert AgentStatus.DEGRADED.value == "degraded"
        assert AgentStatus.OFFLINE.value == "offline"

    def test_node_status_values(self):
        expected = {"pending", "starting", "running", "stopping", "stopped", "error", "unknown"}
        assert {s.value for s in NodeStatus} == expected

    def test_provider_values(self):
        assert Provider.DOCKER.value == "docker"
        assert Provider.LIBVIRT.value == "libvirt"

    def test_link_state_values(self):
        expected = {"connected", "disconnected", "pending", "error"}
        assert {s.value for s in LinkState} == expected

    def test_job_status_includes_accepted(self):
        assert JobStatus.ACCEPTED.value == "accepted"
        assert len(JobStatus) == 5


# ---------------------------------------------------------------------------
# 3. Deploy schemas
# ---------------------------------------------------------------------------


class TestDeploySchemas:
    """Deploy-related Pydantic models."""

    def test_deploy_node_minimal(self):
        n = DeployNode(name="r1")
        assert n.name == "r1"
        assert n.kind == "linux"
        assert n.image is None
        assert n.binds == []
        assert n.env == {}
        assert n.startup_config is None

    def test_deploy_node_inherits_hw_mixin(self):
        n = DeployNode(name="r1", memory=2048, cpu=4, efi_boot=True)
        assert n.memory == 2048
        assert n.cpu == 4
        assert n.efi_boot is True

    def test_deploy_link_requires_all_fields(self):
        link = DeployLink(
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
        )
        assert link.source_node == "r1"
        assert link.target_interface == "eth1"

        with pytest.raises(ValidationError):
            DeployLink(source_node="r1")  # missing other required fields

    def test_deploy_request_defaults(self):
        req = DeployRequest(job_id="j1", lab_id="lab1")
        assert req.provider == Provider.DOCKER
        assert req.topology is None
        assert req.callback_url is None

    def test_deploy_topology_empty_links(self):
        topo = DeployTopology(nodes=[DeployNode(name="n1")])
        assert len(topo.nodes) == 1
        assert topo.links == []


# ---------------------------------------------------------------------------
# 4. Lab schemas
# ---------------------------------------------------------------------------


class TestLabSchemas:
    """Lab status, reconciliation, config extraction."""

    def test_node_info_defaults(self):
        ni = NodeInfo(name="sw1", status=NodeStatus.RUNNING)
        assert ni.container_id is None
        assert ni.ip_addresses == []

    def test_lab_status_response_empty(self):
        r = LabStatusResponse(lab_id="lab-1")
        assert r.nodes == []
        assert r.error is None

    def test_reconcile_target_literal_constraint(self):
        t = NodeReconcileTarget(container_name="c1", desired_state="running")
        assert t.desired_state == "running"
        with pytest.raises(ValidationError):
            NodeReconcileTarget(container_name="c1", desired_state="restarting")

    def test_reconcile_result_inherits_base_response(self):
        r = NodeReconcileResult(
            success=True,
            container_name="c1",
            action="started",
        )
        assert r.success is True
        assert r.error is None

    def test_extracted_config(self):
        ec = ExtractedConfig(node_name="r1", content="hostname r1")
        assert ec.content == "hostname r1"

    def test_extract_configs_response_inherits_base(self):
        r = ExtractConfigsResponse(success=True, extracted_count=2)
        assert r.extracted_count == 2
        assert r.configs == []

    def test_cleanup_lab_orphans_request(self):
        req = CleanupLabOrphansRequest(lab_id="lab-x", keep_node_names=["n1", "n2"])
        assert req.lab_id == "lab-x"
        assert len(req.keep_node_names) == 2


# ---------------------------------------------------------------------------
# 5. Node lifecycle schemas
# ---------------------------------------------------------------------------


class TestNodeLifecycleSchemas:
    """Per-node create/start/stop/destroy schemas."""

    def test_create_node_request_inherits_hw_mixin(self):
        req = CreateNodeRequest(node_name="sw1", memory=512, cpu=1)
        assert req.node_name == "sw1"
        assert req.memory == 512
        assert req.kind == "linux"
        assert req.image_sha256 is None

    def test_create_node_response_defaults(self):
        r = CreateNodeResponse(success=True)
        assert r.status == "unknown"
        assert r.container_name is None
        assert r.duration_ms is None

    def test_start_node_request_defaults(self):
        req = StartNodeRequest()
        assert req.repair_endpoints is True
        assert req.fix_interfaces is True

    def test_start_node_response_defaults(self):
        r = StartNodeResponse(success=True)
        assert r.endpoints_repaired == 0
        assert r.interfaces_fixed == 0

    def test_stop_node_response(self):
        r = StopNodeResponse(success=False, error="timeout")
        assert r.success is False
        assert r.error == "timeout"

    def test_destroy_node_response(self):
        r = DestroyNodeResponse(success=True, container_removed=True, duration_ms=150)
        assert r.container_removed is True
        assert r.duration_ms == 150


# ---------------------------------------------------------------------------
# 6. Console schemas
# ---------------------------------------------------------------------------


class TestConsoleSchemas:
    """Console and CLI verification schemas."""

    def test_console_request_defaults(self):
        r = ConsoleRequest(lab_id="lab1", node_name="r1")
        assert r.shell == "/bin/sh"

    def test_cli_verify_request_timeout_constraints(self):
        """timeout: ge=5, le=600."""
        req = CliVerifyRequest(timeout=30)
        assert req.timeout == 30
        with pytest.raises(ValidationError):
            CliVerifyRequest(timeout=4)
        with pytest.raises(ValidationError):
            CliVerifyRequest(timeout=601)

    def test_cli_verify_request_retries_constraints(self):
        """retries: ge=0, le=5, default=2."""
        req = CliVerifyRequest()
        assert req.retries == 2
        with pytest.raises(ValidationError):
            CliVerifyRequest(retries=-1)
        with pytest.raises(ValidationError):
            CliVerifyRequest(retries=6)

    def test_cli_verify_response_inherits_base(self):
        r = CliVerifyResponse(success=True, provider="docker", node_name="r1")
        assert r.commands_run == 0
        assert r.outputs == []
        assert r.domain_name is None

    def test_cli_command_output(self):
        o = CliCommandOutput(success=True, command="show version", output="v1.0")
        assert o.command == "show version"
        assert o.output == "v1.0"


# ---------------------------------------------------------------------------
# 7. Common schemas
# ---------------------------------------------------------------------------


class TestCommonSchemas:
    """AgentCapabilities, HeartbeatRequest, JobResult."""

    def test_agent_capabilities_defaults(self):
        cap = AgentCapabilities()
        assert cap.providers == []
        assert cap.max_concurrent_jobs == 4
        assert cap.features == []

    def test_heartbeat_request_defaults(self):
        hb = HeartbeatRequest(agent_id="a1")
        assert hb.status == AgentStatus.ONLINE
        assert hb.active_jobs == 0
        assert hb.resource_usage == {}

    def test_job_result_required_fields(self):
        with pytest.raises(ValidationError):
            JobResult()  # missing job_id and status
        jr = JobResult(job_id="j1", status=JobStatus.COMPLETED)
        assert jr.exit_code == 0
        assert jr.stderr == ""
        assert jr.error_message is None
