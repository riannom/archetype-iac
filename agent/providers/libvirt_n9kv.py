"""N9Kv POAP bootstrapping and management network helpers for LibvirtProvider.

Extracted from libvirt.py to reduce file size.  Functions accept explicit
parameters (``conn``, ``lab_id``, ``node_name``, etc.) instead of ``self``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from agent.config import settings
from agent.n9kv_poap import render_poap_script
from agent.vendors import get_vendor_config

logger = logging.getLogger(__name__)


# CML reference: preamble prepended to N9Kv ISO config (nxos_config.txt).
# Echo commands create set_boot.py on bootflash at boot time — no qemu-nbd needed.
# EEM applet fires on first login, auto-sets boot variable, self-deletes.
# Uses `python` (not python3) per CML reference.
_N9KV_CONFIG_PREAMBLE = """\
hostname {hostname}
echo 'from cli import cli' > set_boot.py
echo 'import json' >> set_boot.py
echo 'import os' >> set_boot.py
echo 'import time' >> set_boot.py
echo 'bootimage = json.loads(cli("show version | json"))["nxos_file_name"]' >> set_boot.py
echo 'set_boot = cli("conf t ; boot nxos {{}} ; no event manager applet BOOTCONFIG".format(bootimage))' >> set_boot.py
echo 'i = 0' >> set_boot.py
echo 'while i < 10:' >> set_boot.py
echo '    try:' >> set_boot.py
echo '        save_config = cli("copy running-config startup-config")' >> set_boot.py
echo '        break' >> set_boot.py
echo '    except Exception:' >> set_boot.py
echo '        i += 1' >> set_boot.py
echo '        time.sleep(1)' >> set_boot.py
echo 'os.remove("/bootflash/set_boot.py")' >> set_boot.py
event manager applet BOOTCONFIG
 event syslog pattern "Configured from vty"
 action 1.0 cli python bootflash:set_boot.py
no password strength-check
username admin role network-admin
username admin password cisco"""


# ---------------------------------------------------------------------------
# Deterministic naming / addressing helpers
# ---------------------------------------------------------------------------

def n9kv_poap_network_name(lab_id: str, node_name: str) -> str:
    """Build a deterministic libvirt network name for N9Kv POAP bootstrapping."""
    digest = hashlib.sha1(f"{lab_id}:{node_name}".encode("utf-8")).hexdigest()
    return f"ap-poap-{digest[:10]}"


def n9kv_poap_bridge_name(lab_id: str, node_name: str) -> str:
    """Build a deterministic Linux bridge name (<=15 chars)."""
    digest = hashlib.sha1(f"{lab_id}:{node_name}".encode("utf-8")).hexdigest()
    return f"vpoap{digest[:8]}"


def n9kv_poap_subnet(lab_id: str, node_name: str) -> tuple[str, str, str]:
    """Derive a deterministic /24 subnet (gateway, dhcp_start, dhcp_end)."""
    digest = hashlib.sha1(f"{lab_id}:{node_name}".encode("utf-8")).digest()
    octet_2 = 64 + (digest[0] % 64)  # 10.64.0.0/10 private slice
    octet_3 = digest[1]
    base = f"10.{octet_2}.{octet_3}"
    return f"{base}.1", f"{base}.10", f"{base}.250"


def n9kv_poap_config_url(lab_id: str, node_name: str, gateway_ip: str) -> str:
    """Build startup-config URL consumed by the staged POAP script."""
    lab_q = quote(lab_id, safe="")
    node_q = quote(node_name, safe="")
    return f"http://{gateway_ip}:{settings.agent_port}/poap/{lab_q}/{node_q}/startup-config"


def n9kv_poap_tftp_root(lab_id: str, node_name: str) -> Path:
    """Build deterministic per-node TFTP root for POAP script staging."""
    return Path(settings.workspace_path) / ".poap-tftp" / n9kv_poap_network_name(lab_id, node_name)


def n9kv_poap_bootfile_name() -> str:
    """Return the staged POAP script filename served via TFTP."""
    return "script.py"


def stage_n9kv_poap_tftp_script(lab_id: str, node_name: str, gateway_ip: str) -> tuple[Path, str] | None:
    """Write per-node POAP script to the deterministic TFTP root."""
    tftp_root = n9kv_poap_tftp_root(lab_id, node_name)
    script_name = n9kv_poap_bootfile_name()
    script_path = tftp_root / script_name
    config_url = n9kv_poap_config_url(lab_id, node_name, gateway_ip)
    script_content = render_poap_script(config_url)

    try:
        tftp_root.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_content, encoding="utf-8")
        return tftp_root, script_name
    except Exception as e:
        logger.warning(
            "Failed to stage N9Kv POAP script for %s/%s under %s: %s",
            lab_id,
            node_name,
            script_path,
            e,
        )
        return None


# ---------------------------------------------------------------------------
# Libvirt network management
# ---------------------------------------------------------------------------

def ensure_n9kv_poap_network(conn, lab_id: str, node_name: str) -> str | None:
    """Ensure per-node libvirt network with DHCP bootp options for N9Kv POAP.

    Args:
        conn: Active libvirt connection (virConnect)
        lab_id: Lab identifier
        node_name: Node name
    """
    network_name = n9kv_poap_network_name(lab_id, node_name)
    bridge_name = n9kv_poap_bridge_name(lab_id, node_name)
    gateway_ip, dhcp_start, dhcp_end = n9kv_poap_subnet(lab_id, node_name)
    staged = stage_n9kv_poap_tftp_script(lab_id, node_name, gateway_ip)
    if staged is None:
        return None
    tftp_root, script_name = staged
    script_server_opt = f"dhcp-option-force=66,{gateway_ip}"
    script_name_opt = f"dhcp-option-force=67,{script_name}"

    try:
        network = conn.networkLookupByName(network_name)
        if network is not None:
            needs_recreate = False
            try:
                existing_xml = network.XMLDesc(0)
                if (
                    script_server_opt not in existing_xml
                    or script_name_opt not in existing_xml
                    or "<tftp root=" not in existing_xml
                    or f"<bootp file='{script_name}'" not in existing_xml
                ):
                    needs_recreate = True
            except Exception:
                needs_recreate = True

            if needs_recreate:
                logger.info(
                    "Recreating N9Kv POAP network %s for %s/%s to apply DHCP script options",
                    network_name,
                    lab_id,
                    node_name,
                )
                try:
                    if network.isActive() == 1:
                        network.destroy()
                except Exception:
                    pass
                try:
                    network.undefine()
                except Exception as e:
                    logger.warning(
                        "Failed to recreate N9Kv POAP network %s for %s/%s: %s",
                        network_name,
                        lab_id,
                        node_name,
                        e,
                    )
                    if network.isActive() != 1:
                        network.create()
                    try:
                        network.setAutostart(True)
                    except Exception:
                        pass
                    return network_name
            else:
                if network.isActive() != 1:
                    network.create()
                try:
                    network.setAutostart(True)
                except Exception:
                    pass
                return network_name
    except Exception:
        # Define the network if it does not already exist.
        pass

    network_xml = f"""
<network xmlns:dnsmasq='http://libvirt.org/schemas/network/dnsmasq/1.0'>
  <name>{xml_escape(network_name)}</name>
  <bridge name='{xml_escape(bridge_name)}' stp='on' delay='0'/>
  <forward mode='nat'/>
  <ip address='{gateway_ip}' netmask='255.255.255.0'>
    <tftp root='{xml_escape(str(tftp_root))}'/>
    <dhcp>
      <range start='{dhcp_start}' end='{dhcp_end}'/>
      <bootp file='{xml_escape(script_name)}' server='{gateway_ip}'/>
    </dhcp>
  </ip>
  <dnsmasq:options>
    <dnsmasq:option value='{xml_escape(script_server_opt)}'/>
    <dnsmasq:option value='{xml_escape(script_name_opt)}'/>
  </dnsmasq:options>
</network>""".strip()

    try:
        network = conn.networkDefineXML(network_xml)
        if network is None:
            return None
        if network.isActive() != 1:
            network.create()
        try:
            network.setAutostart(True)
        except Exception:
            pass
        logger.info(
            "Created N9Kv POAP network %s for %s/%s (bootfile=%s)",
            network_name,
            lab_id,
            node_name,
            script_name,
        )
        return network_name
    except Exception as e:
        logger.warning(
            "Failed to create N9Kv POAP network %s for %s/%s: %s",
            network_name,
            lab_id,
            node_name,
            e,
        )
        return None


def teardown_n9kv_poap_network(conn, lab_id: str, node_name: str) -> None:
    """Remove per-node N9Kv POAP network if it exists.

    Args:
        conn: Active libvirt connection (virConnect)
        lab_id: Lab identifier
        node_name: Node name
    """
    network_name = n9kv_poap_network_name(lab_id, node_name)
    try:
        network = conn.networkLookupByName(network_name)
    except Exception:
        return
    try:
        if network.isActive() == 1:
            network.destroy()
    except Exception:
        pass
    try:
        network.undefine()
    except Exception:
        pass


def ensure_libvirt_network(conn, network_name: str) -> bool:
    """Ensure a libvirt network exists, is active, and autostarted.

    Args:
        conn: Active libvirt connection (virConnect)
        network_name: Name of the libvirt network
    """
    try:
        network = conn.networkLookupByName(network_name)
        if network is None:
            return False
        if network.isActive() != 1:
            network.create()
        try:
            network.setAutostart(True)
        except Exception:
            # Autostart failure is non-fatal if the network is active now.
            pass
        return True
    except Exception:
        return False


def node_uses_dedicated_mgmt_interface(kind: str | None) -> bool:
    """Return True when VM has a dedicated management interface.

    Only devices with an explicit management_interface in their vendor config
    get a virbr0 management NIC. Devices without one use all NICs for data.
    """
    if not kind:
        return False
    try:
        config = get_vendor_config(kind)
        return config is not None and config.management_interface is not None
    except Exception:
        return False


def resolve_management_network(
    conn,
    lab_id: str,
    node_name: str,
    kind: str | None,
    *,
    canonical_kind_fn,
) -> tuple[bool, str]:
    """Resolve management network behavior for a VM node.

    Args:
        conn: Active libvirt connection (virConnect)
        lab_id: Lab identifier
        node_name: Node name
        kind: Device kind
        canonical_kind_fn: Callable to canonicalize kind strings
    """
    if not node_uses_dedicated_mgmt_interface(kind):
        return False, "default"

    normalized_kind = canonical_kind_fn(kind)
    if (
        normalized_kind == "cisco_n9kv"
        and settings.n9kv_boot_modifications_enabled
        and settings.n9kv_poap_preboot_enabled
    ):
        poap_network = ensure_n9kv_poap_network(conn, lab_id, node_name)
        if poap_network:
            return True, poap_network
        logger.warning(
            "Falling back to libvirt default management network for %s/%s after POAP network failure",
            lab_id,
            node_name,
        )

    include_management_interface = ensure_libvirt_network(conn, "default")
    if not include_management_interface:
        logger.warning(
            "Unable to enable libvirt 'default' network for %s; management NIC omitted, SSH console may be unavailable",
            node_name,
        )
        return False, "default"
    return True, "default"
