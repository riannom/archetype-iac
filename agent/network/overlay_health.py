"""Periodic VXLAN tunnel health monitor.

Validates that all tracked VXLAN tunnels have functioning Linux netdevs.
Runs independently of the API-driven convergence cycle so the agent can
self-heal even when the controller is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class OverlayHealthMonitor:
    """Periodically checks tracked VXLAN tunnels for broken netdevs.

    When a tracked tunnel's OVS port reports ``ofport == -1`` (underlying
    Linux VXLAN device missing), the monitor deletes the stale OVS port
    and recreates the full VXLAN device from the in-memory tunnel cache.
    """

    def __init__(self, interval: int = 60) -> None:
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            logger.warning("Overlay health monitor already running")
            return
        self._running = True

        # Run one immediate check before entering the periodic loop
        # so broken tunnels are repaired without waiting a full interval.
        try:
            await self.check_and_repair()
        except Exception:
            logger.warning("Overlay health monitor: initial check failed", exc_info=True)

        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Overlay health monitor started (interval: {self._interval}s)"
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Overlay health monitor stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                if self._running:
                    await self.check_and_repair()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("Overlay health check failed", exc_info=True)

    async def check_and_repair(self) -> dict[str, Any]:
        """Run a single health check pass.

        Returns dict with ``checked`` and ``repaired`` counts.
        """
        from agent.agent_state import get_overlay_manager
        from agent.network.overlay_state import batch_read_ovs_ports

        overlay = get_overlay_manager()
        if not overlay or not overlay._link_tunnels:
            return {"checked": 0, "repaired": 0}

        ovs_ports = await batch_read_ovs_ports(overlay._bridge_name)

        if ovs_ports is None:
            logger.warning("Health monitor: OVS query failed, skipping check")
            return {"checked": 0, "repaired": 0, "skipped": "ovs_read_error"}

        checked = 0
        repaired = 0

        for link_id, tunnel in list(overlay._link_tunnels.items()):
            port_name = tunnel.interface_name
            checked += 1

            port_info = ovs_ports.get(port_name)

            if port_info and port_info.get("ofport") != -1:
                # Port exists and underlying device is healthy
                continue

            # Two failure modes:
            # - port_info is None: OVS port missing entirely
            # - ofport == -1: OVS port exists but Linux netdev is gone
            if port_info:
                reason = "ofport=-1 (underlying device missing)"
            else:
                reason = "OVS port missing"

            logger.warning(
                f"Health monitor: {port_name} — {reason} "
                f"(link_id={link_id}, vni={tunnel.vni}), repairing"
            )

            try:
                if port_info:
                    # Delete stale OVS entry before recreating
                    await overlay._ovs_vsctl(
                        "del-port", overlay._bridge_name, port_name
                    )

                # Re-check tracking right before creation: tunnel may have
                # been intentionally removed by a concurrent declare_state
                # between the iteration snapshot and this await point.
                # del-port above is harmless; creation would produce an orphan.
                if link_id not in overlay._link_tunnels:
                    continue

                await overlay._create_vxlan_device(
                    name=port_name,
                    vni=tunnel.vni,
                    local_ip=tunnel.local_ip,
                    remote_ip=tunnel.remote_ip,
                    bridge=overlay._bridge_name,
                    vlan_tag=tunnel.local_vlan if tunnel.local_vlan > 0 else None,
                    tenant_mtu=tunnel.tenant_mtu,
                )
                repaired += 1
                logger.info(
                    f"Health monitor: repaired {port_name} "
                    f"(vni={tunnel.vni}, vlan={tunnel.local_vlan})"
                )
            except Exception as e:
                logger.error(
                    f"Health monitor: failed to repair {port_name}: {e}"
                )

        if repaired > 0:
            logger.info(
                f"Health monitor: checked={checked}, repaired={repaired}"
            )

        return {"checked": checked, "repaired": repaired}
