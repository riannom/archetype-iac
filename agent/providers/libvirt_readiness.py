"""Readiness checking and boot intervention handlers for LibvirtProvider.

Extracted from libvirt.py to reduce file size.  Functions accept explicit
parameters (state dicts, conn, domain_name, etc.) instead of ``self``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import time
from pathlib import Path

from agent.config import settings
from agent.readiness import ReadinessResult, get_libvirt_probe
from agent.vendors import (
    get_console_method,
    get_libvirt_config,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Probe marker helpers
# ---------------------------------------------------------------------------

def extract_probe_markers(details: str | None) -> set[str]:
    """Parse `markers=` payload from readiness details."""
    if not details:
        return set()
    match = re.search(r"(?:^|;\s*)markers=([^;]+)", details)
    if not match:
        return set()
    raw = match.group(1).strip()
    if not raw or raw == "none":
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def classify_console_result(result) -> str:
    """Classify console command result into a status string.

    Used by N9Kv boot intervention handlers (loader recovery, POAP skip,
    admin password setup) to normalize success/failure/timeout outcomes.
    """
    if result.success:
        return "sent"
    first_error = ""
    if result.outputs:
        first_error = (result.outputs[0].error or "").strip()
    overall_error = (result.error or "").strip()
    if "Timeout waiting for command output" in first_error:
        return "sent_handoff_timeout"
    if "Console connection closed unexpectedly" in overall_error:
        return "sent_console_closed"
    return "failed"


# ---------------------------------------------------------------------------
# TCP port check
# ---------------------------------------------------------------------------

def check_tcp_port(host: str, port: int, timeout: float) -> bool:
    """Return True when TCP port is connectable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Post-boot commands
# ---------------------------------------------------------------------------

async def run_post_boot_commands(
    domain_name: str,
    kind: str,
    uri: str,
    *,
    canonical_kind_fn,
    clear_console_state_fn,
) -> bool:
    """Run vendor-specific post-boot commands on a VM.

    This handles workarounds like disabling paging or DNS lookups
    that need to be executed after the VM is ready.

    Args:
        domain_name: Libvirt domain name
        kind: Device kind for looking up vendor config
        uri: Libvirt connection URI
        canonical_kind_fn: Callable to canonicalize kind strings
        clear_console_state_fn: Callable to clear console control state

    Returns:
        True if commands were run (or already completed), False on error
    """
    canonical_kind = canonical_kind_fn(kind)
    if canonical_kind == "cisco_n9kv" and not settings.n9kv_boot_modifications_enabled:
        logger.info(
            "Skipping N9Kv post-boot console automation for %s (boot mutations disabled)",
            domain_name,
        )
        clear_console_state_fn(domain_name)
        return True

    if canonical_kind == "cisco_n9kv" and settings.n9kv_poap_preboot_enabled:
        logger.info(
            "Running N9Kv post-boot console automation for %s (pre-boot POAP enabled)",
            domain_name,
        )

    from agent.console_extractor import run_vm_post_boot_commands, PEXPECT_AVAILABLE

    if not PEXPECT_AVAILABLE:
        logger.debug("pexpect not available, skipping post-boot commands")
        return True  # Not an error, just skip

    try:
        result = await asyncio.to_thread(
            run_vm_post_boot_commands,
            domain_name,
            kind,
            uri,
        )
        return result.success
    except Exception as e:
        logger.warning(f"Post-boot commands failed for {domain_name}: {e}")
        return False


# ---------------------------------------------------------------------------
# N9Kv boot intervention handlers
# ---------------------------------------------------------------------------

async def run_n9kv_loader_recovery(
    domain_name: str,
    kind: str,
    uri: str,
    *,
    recovery_attempts: dict[str, int],
    recovery_last_at: dict[str, float],
    max_attempts: int,
    cooldown: float,
) -> str:
    """Attempt loader recovery by booting NX-OS image from bootflash.

    Allows up to ``max_attempts`` retries with a cooldown period between
    attempts.  NX-OS can crash during early boot (sysconf checksum on
    fresh NVRAM) and drop back to loader, so a single attempt is
    insufficient.
    """
    attempts = recovery_attempts.get(domain_name, 0)

    if attempts >= max_attempts:
        return "skipped_max_attempts"

    last_at = recovery_last_at.get(domain_name, 0.0)
    elapsed = time.monotonic() - last_at
    if last_at > 0 and elapsed < cooldown:
        return "skipped_cooldown"

    from agent.console_extractor import run_vm_cli_commands, PEXPECT_AVAILABLE

    if not PEXPECT_AVAILABLE:
        logger.warning(
            "Skipping N9Kv loader recovery for %s: pexpect unavailable",
            domain_name,
        )
        return "skipped_pexpect_unavailable"

    try:
        result = await asyncio.to_thread(
            run_vm_cli_commands,
            domain_name=domain_name,
            kind=kind,
            commands=["boot bootflash:nxos64-cs.10.5.3.F.bin"],
            libvirt_uri=uri,
            prompt_pattern=r"loader >\s*$",
            paging_disable="",
            attempt_enable=False,
            timeout=45,
            retries=0,
        )
    except Exception as e:
        recovery_attempts[domain_name] = attempts + 1
        recovery_last_at[domain_name] = time.monotonic()
        logger.warning(
            "N9Kv loader recovery command failed for %s (attempt %d/%d): %s",
            domain_name,
            attempts + 1,
            max_attempts,
            e,
        )
        return "error"

    # Count this attempt regardless of outcome.
    recovery_attempts[domain_name] = attempts + 1
    recovery_last_at[domain_name] = time.monotonic()

    status = classify_console_result(result)

    logger.info(
        "N9Kv loader recovery for %s: status=%s attempt=%d/%d commands_run=%s error=%s",
        domain_name,
        status,
        attempts + 1,
        max_attempts,
        result.commands_run,
        result.error,
    )
    return status


async def run_n9kv_panic_recovery(
    domain_name: str,
    kind: str,
    serial_log_path: str,
    *,
    run_libvirt_fn,
    conn,
    panic_attempts: dict[str, int],
    panic_last_at: dict[str, float],
    panic_last_log_size: dict[str, int],
    max_attempts: int,
    cooldown: float,
) -> str:
    """Force-restart a VM stuck after a guest kernel panic.

    Guest kernel panics (ksm_scan_thread GPF) leave the VM in QEMU
    'running' state with no serial output.  QEMU does not detect guest
    panics as crash events, so ``<on_crash>restart</on_crash>`` is
    ineffective.  Instead we watch for the panic signature in the serial
    log and, if the log stops growing, force-restart the domain.

    Guards:
      - Max attempts (3) to prevent infinite restart loops.
      - Cooldown (60s) between restarts.
      - Staleness check: first detection records log size; restart only
        fires when log size is unchanged on a subsequent probe.
    """
    attempts = panic_attempts.get(domain_name, 0)

    if attempts >= max_attempts:
        return "skipped_max_attempts"

    last_at = panic_last_at.get(domain_name, 0.0)
    elapsed = time.monotonic() - last_at
    if last_at > 0 and elapsed < cooldown:
        return "skipped_cooldown"

    # Staleness check: only restart when serial log stops growing.
    try:
        current_size = os.path.getsize(serial_log_path)
    except OSError:
        current_size = -1

    prev_size = panic_last_log_size.get(domain_name)
    if prev_size is None:
        # First time seeing panic — record size and give VM a grace period.
        panic_last_log_size[domain_name] = current_size
        return "skipped_first_detection"

    if current_size != prev_size:
        # Log is still growing — VM may be recovering on its own.
        panic_last_log_size[domain_name] = current_size
        return "skipped_log_growing"

    # Log size unchanged since last check — VM is stuck.  Force restart.
    try:
        def _restart_domain(c, dname):
            dom = c.lookupByName(dname)
            dom.destroy()
            time.sleep(2)
            dom.create()

        await run_libvirt_fn(_restart_domain, conn, domain_name)
    except Exception as e:
        panic_attempts[domain_name] = attempts + 1
        panic_last_at[domain_name] = time.monotonic()
        logger.warning(
            "N9Kv panic recovery failed for %s (attempt %d/%d): %s",
            domain_name, attempts + 1, max_attempts, e,
        )
        return "error"

    # Clear log size tracking (fresh boot produces new output).
    panic_last_log_size.pop(domain_name, None)

    panic_attempts[domain_name] = attempts + 1
    panic_last_at[domain_name] = time.monotonic()

    logger.info(
        "N9Kv panic recovery for %s: restarted (attempt %d/%d)",
        domain_name, attempts + 1, max_attempts,
    )
    return "restarted"


async def run_n9kv_poap_skip(
    domain_name: str,
    kind: str,
    uri: str,
    *,
    poap_skip_attempted: set[str],
) -> str:
    """Send 'yes' to POAP abort prompt to skip POAP and continue normal setup."""
    if domain_name in poap_skip_attempted:
        return "skipped_already_attempted"

    poap_skip_attempted.add(domain_name)

    from agent.console_extractor import run_vm_cli_commands, PEXPECT_AVAILABLE

    if not PEXPECT_AVAILABLE:
        logger.warning(
            "Skipping N9Kv POAP skip for %s: pexpect unavailable",
            domain_name,
        )
        return "skipped_pexpect_unavailable"

    try:
        result = await asyncio.to_thread(
            run_vm_cli_commands,
            domain_name=domain_name,
            kind=kind,
            commands=["yes"],
            libvirt_uri=uri,
            prompt_pattern=r"\(yes/no\)\[n(?:o)?\]:\s*$",
            paging_disable="",
            attempt_enable=False,
            timeout=30,
            retries=0,
        )
    except Exception as e:
        logger.warning(
            "N9Kv POAP skip failed for %s: %s",
            domain_name,
            e,
        )
        return "error"

    status = classify_console_result(result)

    logger.info(
        "N9Kv POAP skip for %s: status=%s commands_run=%s error=%s",
        domain_name,
        status,
        result.commands_run,
        result.error,
    )
    return status


async def run_n9kv_admin_password_setup(
    domain_name: str,
    kind: str,
    uri: str,
    *,
    admin_password_completed: set[str],
) -> str:
    """Navigate the first-boot admin password wizard via console interaction.

    Uses run_vm_cli_commands which triggers _handle_login() in the console
    extractor — that method already handles the password prompts, sending
    a bootstrap password that meets NX-OS complexity requirements.
    """
    if domain_name in admin_password_completed:
        return "skipped_already_completed"

    from agent.console_extractor import run_vm_cli_commands, PEXPECT_AVAILABLE

    if not PEXPECT_AVAILABLE:
        logger.warning(
            "Skipping N9Kv admin password setup for %s: pexpect unavailable",
            domain_name,
        )
        return "skipped_pexpect_unavailable"

    try:
        result = await asyncio.to_thread(
            run_vm_cli_commands,
            domain_name=domain_name,
            kind=kind,
            commands=["show clock"],
            libvirt_uri=uri,
            timeout=60,
            retries=1,
        )
    except Exception as e:
        logger.warning(
            "N9Kv admin password setup failed for %s: %s",
            domain_name,
            e,
        )
        return "error"

    status = classify_console_result(result)

    logger.info(
        "N9Kv admin password setup for %s: status=%s commands_run=%s error=%s",
        domain_name,
        status,
        result.commands_run,
        result.error,
    )

    if status.startswith("sent"):
        admin_password_completed.add(domain_name)

    return status


# ---------------------------------------------------------------------------
# Main readiness check orchestrator
# ---------------------------------------------------------------------------

async def check_readiness(
    lab_id: str,
    node_name: str,
    kind: str,
    *,
    domain_name: str,
    uri: str,
    run_libvirt_fn,
    check_readiness_domain_sync_fn,
    get_vm_management_ip_fn,
    canonical_kind_fn,
    clear_console_state_fn,
    # N9Kv state dicts (mutable, modified in place)
    loader_recovery_attempts: dict[str, int],
    loader_recovery_last_at: dict[str, float],
    loader_max_attempts: int,
    loader_cooldown: float,
    panic_recovery_attempts: dict[str, int],
    panic_recovery_last_at: dict[str, float],
    panic_last_log_size: dict[str, int],
    panic_max_attempts: int,
    panic_cooldown: float,
    poap_skip_attempted: set[str],
    admin_password_completed: set[str],
    conn,
) -> ReadinessResult:
    """Check if a VM has finished booting and is ready.

    This uses the serial console output to detect boot completion
    patterns defined in the vendor config. When the VM becomes ready,
    post-boot commands are automatically executed (once).

    Args:
        lab_id: Lab identifier
        node_name: Node name within the lab
        kind: Device kind for vendor config lookup
        domain_name: Full libvirt domain name
        uri: Libvirt connection URI
        run_libvirt_fn: Async callable to run blocking libvirt ops
        check_readiness_domain_sync_fn: Sync callable for domain state lookup
        get_vm_management_ip_fn: Async callable for VM management IP lookup
        canonical_kind_fn: Callable to canonicalize kind strings
        clear_console_state_fn: Callable to clear console control state
        ...: N9Kv state dicts and recovery constants
        conn: Active libvirt connection

    Returns:
        ReadinessResult with ready status and progress
    """
    # Import libvirt for state constants
    try:
        import libvirt as libvirt_mod
    except ImportError:
        return ReadinessResult(
            is_ready=False,
            message="libvirt not available",
            progress_percent=0,
        )

    result_tuple = await run_libvirt_fn(check_readiness_domain_sync_fn, domain_name)
    if result_tuple is None:
        return ReadinessResult(
            is_ready=False,
            message="VM domain not found",
            progress_percent=0,
        )
    state, overrides = result_tuple

    if state != libvirt_mod.VIR_DOMAIN_RUNNING:
        return ReadinessResult(
            is_ready=False,
            message=f"VM not running (state={state})",
            progress_percent=0,
        )
    effective_probe = overrides.get("readiness_probe") or get_libvirt_config(kind).readiness_probe

    # Only use management-IP/SSH gating when readiness probe explicitly asks
    # for SSH readiness. Console method alone is not sufficient.
    if get_console_method(kind) == "ssh" and effective_probe in {"ssh", "tcp_ssh", "management_ssh"}:
        ip = await get_vm_management_ip_fn(domain_name)
        if not ip:
            return ReadinessResult(
                is_ready=False,
                message="Waiting for management IP",
                progress_percent=30,
            )

        ssh_ready = await asyncio.to_thread(check_tcp_port, ip, 22, 2.0)
        if not ssh_ready:
            return ReadinessResult(
                is_ready=False,
                message=f"Management IP {ip} reachable, waiting for SSH",
                progress_percent=70,
            )

        return ReadinessResult(
            is_ready=True,
            message=f"Management SSH ready on {ip}",
            progress_percent=100,
        )

    # Compute serial log path for lock-free observation
    serial_log_path = (
        Path(settings.workspace_path) / lab_id / "serial-logs" / f"{domain_name}.log"
    )

    probe = get_libvirt_probe(
        kind,
        domain_name,
        uri,
        readiness_probe=overrides.get("readiness_probe"),
        readiness_pattern=overrides.get("readiness_pattern"),
        serial_log_path=str(serial_log_path),
    )

    # Run the probe
    result = await probe.check(node_name)

    canonical_kind = canonical_kind_fn(kind)
    if canonical_kind == "cisco_n9kv" and not result.is_ready:
        markers = extract_probe_markers(result.details)
        # Loader recovery is invasive (boots a specific image) — gate behind flag
        if "loader_prompt" in markers and settings.n9kv_boot_modifications_enabled:
            recovery_status = await run_n9kv_loader_recovery(
                domain_name,
                kind,
                uri,
                recovery_attempts=loader_recovery_attempts,
                recovery_last_at=loader_recovery_last_at,
                max_attempts=loader_max_attempts,
                cooldown=loader_cooldown,
            )
            recovery_note = f"loader_recovery={recovery_status}"
            result.details = (
                f"{result.details}; {recovery_note}"
                if result.details
                else recovery_note
            )
            attempts = loader_recovery_attempts.get(domain_name, 0)
            if recovery_status.startswith("sent"):
                result.message = f"Boot recovery in progress (attempt {attempts}/{loader_max_attempts})"
            elif recovery_status == "skipped_max_attempts":
                result.message = f"Boot recovery exhausted ({loader_max_attempts} attempts)"
            elif recovery_status == "skipped_cooldown":
                result.message = f"Boot recovery cooling down (attempt {attempts}/{loader_max_attempts})"
        # POAP skip is safe — just answers "yes" at the standard abort prompt.
        # When POAP preboot is enabled we WANT POAP to run (download script
        # from TFTP, apply startup config), so only skip on explicit failure.
        elif "poap_abort_prompt" in markers or "poap_failure" in markers:
            if settings.n9kv_poap_preboot_enabled and "poap_failure" not in markers:
                # Let POAP proceed — the DHCP/TFTP/HTTP pipeline will deliver
                # the startup config via the staged script.py.
                result.message = "POAP provisioning in progress"
            else:
                skip_status = await run_n9kv_poap_skip(
                    domain_name,
                    kind,
                    uri,
                    poap_skip_attempted=poap_skip_attempted,
                )
                skip_note = f"poap_skip={skip_status}"
                result.details = (
                    f"{result.details}; {skip_note}"
                    if result.details
                    else skip_note
                )
                if skip_status.startswith("sent"):
                    result.message = "POAP skip in progress (skipping to normal setup)"
        # Admin password wizard blocks before login prompt on first boot.
        # The console extractor's _handle_login() already handles the
        # password prompts — we just need to open a console session.
        elif "admin_password_prompt" in markers and settings.n9kv_boot_modifications_enabled:
            pw_status = await run_n9kv_admin_password_setup(
                domain_name,
                kind,
                uri,
                admin_password_completed=admin_password_completed,
            )
            pw_note = f"admin_password_setup={pw_status}"
            result.details = (
                f"{result.details}; {pw_note}"
                if result.details
                else pw_note
            )
            if pw_status.startswith("sent"):
                result.is_ready = True
                result.progress_percent = 100
                result.message = "Boot complete (admin password configured)"
        # Guest kernel panic (ksm_scan_thread GPF) leaves QEMU running
        # but guest OS dead — no serial output.  Force-restart if stuck.
        elif "kernel_panic" in markers and settings.n9kv_boot_modifications_enabled:
            panic_status = await run_n9kv_panic_recovery(
                domain_name, kind, str(serial_log_path),
                run_libvirt_fn=run_libvirt_fn,
                conn=conn,
                panic_attempts=panic_recovery_attempts,
                panic_last_at=panic_recovery_last_at,
                panic_last_log_size=panic_last_log_size,
                max_attempts=panic_max_attempts,
                cooldown=panic_cooldown,
            )
            panic_note = f"panic_recovery={panic_status}"
            result.details = (
                f"{result.details}; {panic_note}"
                if result.details
                else panic_note
            )
            attempts = panic_recovery_attempts.get(domain_name, 0)
            if panic_status == "restarted":
                result.message = f"Kernel panic detected — restarting VM (attempt {attempts}/{panic_max_attempts})"
            elif panic_status == "skipped_first_detection":
                result.message = "Kernel panic detected — monitoring for recovery"
            elif panic_status == "skipped_log_growing":
                result.message = "Kernel panic detected — VM recovering (output still growing)"
            elif panic_status == "skipped_max_attempts":
                result.message = f"Kernel panic recovery exhausted ({panic_max_attempts} attempts)"
            elif panic_status == "skipped_cooldown":
                result.message = f"Kernel panic recovery cooling down (attempt {attempts}/{panic_max_attempts})"

    # If ready, run post-boot commands (idempotent - only runs once)
    if result.is_ready:
        await run_post_boot_commands(
            domain_name,
            kind,
            uri,
            canonical_kind_fn=canonical_kind_fn,
            clear_console_state_fn=clear_console_state_fn,
        )

    return result
