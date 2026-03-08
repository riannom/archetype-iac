"""Tests for agent.providers.docker_config_extract module.

Covers config extraction via docker exec, SSH, and NVRAM, including
error handling, empty results, container-not-running guards, and
workspace persistence.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.providers.docker_config_extract import (
    LABEL_NODE_DISPLAY_NAME,
    LABEL_NODE_KIND,
    LABEL_NODE_NAME,
    LABEL_PROVIDER,
    _log_name_from_labels,
    _parse_iol_nvram,
    extract_all_container_configs,
    extract_config_via_docker,
    extract_config_via_nvram,
    extract_config_via_ssh,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _container(
    name: str = "archetype-lab1-r1",
    *,
    status: str = "running",
    kind: str = "arista_ceos",
    node_name: str = "r1",
    display_name: str = "",
    provider: str = "docker",
    exec_stdout: bytes | None = b"hostname R1\n",
    exec_stderr: bytes | None = None,
    exec_exit_code: int = 0,
) -> SimpleNamespace:
    """Build a fake Docker container with configurable exec results."""
    labels = {
        LABEL_NODE_NAME: node_name,
        LABEL_NODE_KIND: kind,
        LABEL_PROVIDER: provider,
    }
    if display_name:
        labels[LABEL_NODE_DISPLAY_NAME] = display_name

    exec_result = SimpleNamespace(
        exit_code=exec_exit_code,
        output=(exec_stdout, exec_stderr),
    )

    return SimpleNamespace(
        name=name,
        status=status,
        labels=labels,
        exec_run=MagicMock(return_value=exec_result),
    )


@pytest.fixture
def fast_async(monkeypatch):
    """Make asyncio.to_thread execute synchronously for deterministic tests."""
    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)


# ---------------------------------------------------------------------------
# _log_name_from_labels
# ---------------------------------------------------------------------------

class TestLogNameFromLabels:

    def test_node_name_only(self):
        assert _log_name_from_labels({LABEL_NODE_NAME: "r1"}) == "r1"

    def test_display_name_differs(self):
        labels = {LABEL_NODE_NAME: "r1", LABEL_NODE_DISPLAY_NAME: "Router-1"}
        assert _log_name_from_labels(labels) == "Router-1(r1)"

    def test_display_name_same_as_node(self):
        labels = {LABEL_NODE_NAME: "r1", LABEL_NODE_DISPLAY_NAME: "r1"}
        assert _log_name_from_labels(labels) == "r1"

    def test_empty_labels(self):
        assert _log_name_from_labels({}) == ""


# ---------------------------------------------------------------------------
# _parse_iol_nvram
# ---------------------------------------------------------------------------

class TestParseIolNvram:

    def test_returns_none_for_empty_data(self):
        assert _parse_iol_nvram(b"") is None

    def test_returns_none_for_small_data(self):
        assert _parse_iol_nvram(b"x" * 32) is None

    def test_extracts_config_from_binary_with_version_marker(self):
        header = b"\x00" * 80
        config = b"\nversion 15.9\nhostname R1\n!\ninterface Ethernet0\n ip address 10.0.0.1\nend\n"
        data = header + config + b"\x00" * 16
        result = _parse_iol_nvram(data)
        assert result is not None
        assert "version 15.9" in result
        assert "hostname R1" in result
        assert result.endswith("end")

    def test_extracts_config_from_hostname_marker(self):
        header = b"\x00" * 80
        config = b"\nhostname SW1\n!\ninterface Vlan1\n no ip address\nend\n"
        data = header + config
        result = _parse_iol_nvram(data)
        assert result is not None
        assert "hostname SW1" in result
        assert result.endswith("end")

    def test_returns_none_when_no_markers_found(self):
        # Data with no recognizable config markers
        data = b"\x00" * 128
        assert _parse_iol_nvram(data) is None

    def test_returns_none_for_config_too_short(self):
        header = b"\x00" * 80
        config = b"\nversion\n"  # too short after strip
        data = header + config
        result = _parse_iol_nvram(data)
        assert result is None

    def test_truncates_at_null_byte(self):
        header = b"\x00" * 80
        config = b"\nversion 15.9\nhostname R1\n!\nend\n"
        garbage = b"\x00trailing garbage after config"
        data = header + config + garbage
        result = _parse_iol_nvram(data)
        assert result is not None
        assert "trailing" not in result


# ---------------------------------------------------------------------------
# extract_config_via_docker
# ---------------------------------------------------------------------------

class TestExtractConfigViaDocker:

    async def test_success(self, fast_async):
        ctr = _container(exec_stdout=b"hostname R1\nip routing\nend\n")
        result = await extract_config_via_docker(ctr, "FastCli -p 15 -c 'show running-config'", "R1")
        assert result == "hostname R1\nip routing\nend\n"
        ctr.exec_run.assert_called_once_with(
            ["sh", "-c", "FastCli -p 15 -c 'show running-config'"],
            demux=True,
        )

    async def test_nonzero_exit_returns_none(self, fast_async):
        ctr = _container(exec_exit_code=1, exec_stderr=b"command failed")
        result = await extract_config_via_docker(ctr, "bad-cmd", "r1")
        assert result is None

    async def test_empty_stdout_returns_none(self, fast_async):
        ctr = _container(exec_stdout=None)
        result = await extract_config_via_docker(ctr, "cat /config", "r1")
        assert result is None

    async def test_exception_returns_none(self, fast_async):
        ctr = _container()
        ctr.exec_run = MagicMock(side_effect=RuntimeError("container gone"))
        result = await extract_config_via_docker(ctr, "cmd", "r1")
        assert result is None

    async def test_retries_transient_fastcli_failure(self, fast_async, monkeypatch):
        ctr = _container()
        ctr.exec_run = MagicMock(side_effect=[
            SimpleNamespace(exit_code=1, output=(b"% Authorization denied", b"")),
            SimpleNamespace(exit_code=0, output=(b"hostname R1\nend\n", b"")),
        ])

        sleep_calls = []

        async def _sleep(delay):
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", _sleep)

        with patch(
            "agent.providers.docker_config_extract.settings",
            SimpleNamespace(
                docker_config_extract_retry_attempts=2,
                docker_config_extract_retry_delay_seconds=1.5,
            ),
        ):
            result = await extract_config_via_docker(
                ctr,
                "FastCli -p 15 -c 'show running-config'",
                "R1",
            )

        assert result == "hostname R1\nend\n"
        assert sleep_calls == [1.5]
        assert ctr.exec_run.call_count == 2

    async def test_does_not_retry_non_fastcli_failure(self, fast_async, monkeypatch):
        ctr = _container(exec_exit_code=1, exec_stdout=b"", exec_stderr=b"permission denied")

        async def _sleep(_delay):
            raise AssertionError("sleep should not be called")

        monkeypatch.setattr(asyncio, "sleep", _sleep)

        with patch(
            "agent.providers.docker_config_extract.settings",
            SimpleNamespace(
                docker_config_extract_retry_attempts=3,
                docker_config_extract_retry_delay_seconds=1.0,
            ),
        ):
            result = await extract_config_via_docker(ctr, "cat /config", "r1")

        assert result is None
        assert ctr.exec_run.call_count == 1


# ---------------------------------------------------------------------------
# extract_config_via_ssh
# ---------------------------------------------------------------------------

class TestExtractConfigViaSsh:

    async def test_success(self):
        ctr = _container(kind="juniper_cjunos")
        get_ips = MagicMock(return_value=["172.20.0.5"])
        run_ssh = AsyncMock(return_value="set system host-name r1\n")

        result = await extract_config_via_ssh(
            ctr, "juniper_cjunos",
            "cli -c 'show configuration'", "r1",
            get_ips, run_ssh,
        )

        assert result == "set system host-name r1\n"
        run_ssh.assert_awaited_once()
        args = run_ssh.call_args[0]
        assert args[0] == "172.20.0.5"  # ip
        assert args[3] == "cli -c 'show configuration'"  # cmd

    async def test_no_ips_returns_none(self):
        ctr = _container()
        get_ips = MagicMock(return_value=[])
        run_ssh = AsyncMock()

        result = await extract_config_via_ssh(
            ctr, "juniper_cjunos", "cmd", "r1",
            get_ips, run_ssh,
        )

        assert result is None
        run_ssh.assert_not_awaited()

    async def test_uses_vendor_credentials(self):
        ctr = _container(kind="cisco_xr")
        get_ips = MagicMock(return_value=["10.0.0.1"])
        run_ssh = AsyncMock(return_value="config output")

        with patch(
            "agent.providers.docker_config_extract.get_console_credentials",
            return_value=("admin", "cisco"),
        ):
            await extract_config_via_ssh(
                ctr, "cisco_xr", "show run", "xr1",
                get_ips, run_ssh,
            )

        args = run_ssh.call_args[0]
        assert args[1] == "admin"
        assert args[2] == "cisco"


# ---------------------------------------------------------------------------
# extract_config_via_nvram
# ---------------------------------------------------------------------------

class TestExtractConfigViaNvram:

    async def test_success(self, tmp_path):
        workspace = tmp_path / "workspace"
        nvram_dir = workspace / "configs" / "sw1" / "iol-data"
        nvram_dir.mkdir(parents=True)

        header = b"\x00" * 80
        config = b"\nversion 15.9\nhostname SW1\n!\ninterface Ethernet0\nend\n"
        (nvram_dir / "nvram_00001").write_bytes(header + config)

        result = await extract_config_via_nvram("archetype-lab1-sw1", workspace)
        assert result is not None
        assert "hostname SW1" in result

    async def test_no_nvram_file_returns_none(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        result = await extract_config_via_nvram("archetype-lab1-sw1", workspace)
        assert result is None

    async def test_small_nvram_returns_none(self, tmp_path):
        workspace = tmp_path / "workspace"
        nvram_dir = workspace / "configs" / "sw1" / "iol-data"
        nvram_dir.mkdir(parents=True)
        (nvram_dir / "nvram_00001").write_bytes(b"\x00" * 30)

        result = await extract_config_via_nvram("archetype-lab1-sw1", workspace)
        assert result is None

    async def test_container_name_parsing(self, tmp_path):
        """Node name is derived from container name split on '-', part [2]."""
        workspace = tmp_path / "workspace"
        nvram_dir = workspace / "configs" / "router-core" / "iol-data"
        nvram_dir.mkdir(parents=True)

        header = b"\x00" * 80
        config = b"\nversion 15.9\nhostname Core\n!\ninterface Loopback0\nend\n"
        (nvram_dir / "nvram_00001").write_bytes(header + config)

        # container name "archetype-lab1-router-core" -> split("-", 2) -> "router-core"
        result = await extract_config_via_nvram("archetype-lab1-router-core", workspace)
        assert result is not None
        assert "hostname Core" in result

    async def test_exception_returns_none(self, tmp_path):
        workspace = tmp_path / "workspace"
        nvram_dir = workspace / "configs" / "sw1" / "iol-data"
        nvram_dir.mkdir(parents=True)
        nvram_file = nvram_dir / "nvram_00001"
        nvram_file.write_bytes(b"\x00" * 80)

        # Make read_bytes raise
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            result = await extract_config_via_nvram("archetype-lab1-sw1", workspace)
        assert result is None


# ---------------------------------------------------------------------------
# extract_all_container_configs
# ---------------------------------------------------------------------------

class TestExtractAllContainerConfigs:

    async def test_extracts_docker_method_containers(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr = _container(
            kind="arista_ceos",
            node_name="spine1",
            exec_stdout=b"hostname spine1\nip routing\nend\n",
        )
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr])

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="docker",
                command="FastCli -p 15 -c 'show running-config'",
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=MagicMock(),
                run_ssh_command_func=AsyncMock(),
            )

        assert len(result) == 1
        assert result[0][0] == "spine1"
        assert "hostname spine1" in result[0][1]

        # Config written to workspace
        config_path = workspace / "configs" / "spine1" / "startup-config"
        assert config_path.exists()
        assert "hostname spine1" in config_path.read_text()

    async def test_extracts_ssh_method_containers(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr = _container(kind="juniper_cjunos", node_name="r1")
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr])

        get_ips = MagicMock(return_value=["172.20.0.5"])
        run_ssh = AsyncMock(return_value="set system host-name r1\n")

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="ssh",
                command="cli -c 'show configuration'",
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=get_ips,
                run_ssh_command_func=run_ssh,
            )

        assert len(result) == 1
        assert result[0][0] == "r1"
        run_ssh.assert_awaited_once()

    async def test_skips_not_running_container(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr = _container(status="exited", node_name="r1", kind="arista_ceos")
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr])

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="docker",
                command="FastCli -p 15 -c 'show running-config'",
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=MagicMock(),
                run_ssh_command_func=AsyncMock(),
            )

        assert result == []

    async def test_skips_unsupported_extraction_method(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr = _container(kind="linux", node_name="host1")
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr])

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="none",
                command="",
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=MagicMock(),
                run_ssh_command_func=AsyncMock(),
            )

        assert result == []

    async def test_skips_container_without_labels(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr = SimpleNamespace(
            name="archetype-lab1-mystery",
            status="running",
            labels={},
        )
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr])

        result = await extract_all_container_configs(
            lab_id="lab1",
            workspace=workspace,
            docker_client=docker_client,
            lab_prefix="archetype-lab1",
            provider_name="docker",
            get_container_ips_func=MagicMock(),
            run_ssh_command_func=AsyncMock(),
        )

        assert result == []

    async def test_skips_empty_config_result(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr = _container(
            kind="arista_ceos",
            node_name="r1",
            exec_stdout=b"   \n  ",  # whitespace-only
        )
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr])

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="docker",
                command="cmd",
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=MagicMock(),
                run_ssh_command_func=AsyncMock(),
            )

        assert result == []
        # No config file written for empty result
        assert not (workspace / "configs" / "r1" / "startup-config").exists()

    async def test_continues_after_per_container_error(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr_bad = _container(kind="arista_ceos", node_name="r1")
        ctr_bad.exec_run = MagicMock(side_effect=RuntimeError("boom"))

        ctr_good = _container(
            kind="arista_ceos",
            node_name="r2",
            exec_stdout=b"hostname R2\nend\n",
        )

        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr_bad, ctr_good])

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="docker",
                command="cmd",
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=MagicMock(),
                run_ssh_command_func=AsyncMock(),
            )

        # Error on r1 doesn't prevent r2 extraction
        assert len(result) == 1
        assert result[0][0] == "r2"

    async def test_docker_list_failure_returns_empty(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(
            side_effect=RuntimeError("Docker daemon unreachable")
        )

        result = await extract_all_container_configs(
            lab_id="lab1",
            workspace=workspace,
            docker_client=docker_client,
            lab_prefix="archetype-lab1",
            provider_name="docker",
            get_container_ips_func=MagicMock(),
            run_ssh_command_func=AsyncMock(),
        )

        assert result == []

    async def test_skips_container_with_no_extraction_command(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr = _container(kind="arista_ceos", node_name="r1")
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr])

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="docker",
                command="",  # empty command
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=MagicMock(),
                run_ssh_command_func=AsyncMock(),
            )

        assert result == []

    async def test_multiple_containers_extracted(self, tmp_path, fast_async):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        ctr1 = _container(
            kind="arista_ceos", node_name="spine1",
            exec_stdout=b"hostname spine1\nend\n",
        )
        ctr2 = _container(
            kind="nokia_srlinux", node_name="leaf1",
            exec_stdout=b"system {\n  name leaf1\n}\n",
        )
        docker_client = MagicMock()
        docker_client.containers.list = MagicMock(return_value=[ctr1, ctr2])

        with patch(
            "agent.providers.docker_config_extract.get_config_extraction_settings",
        ) as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                method="docker",
                command="show run",
            )
            result = await extract_all_container_configs(
                lab_id="lab1",
                workspace=workspace,
                docker_client=docker_client,
                lab_prefix="archetype-lab1",
                provider_name="docker",
                get_container_ips_func=MagicMock(),
                run_ssh_command_func=AsyncMock(),
            )

        assert len(result) == 2
        names = {r[0] for r in result}
        assert names == {"spine1", "leaf1"}

        # Both have config files
        assert (workspace / "configs" / "spine1" / "startup-config").exists()
        assert (workspace / "configs" / "leaf1" / "startup-config").exists()
