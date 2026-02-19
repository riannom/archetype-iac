"""Shared helpers for N9Kv POAP bootstrap content."""

from __future__ import annotations


def render_poap_script(config_url: str) -> str:
    """Render a minimal NX-OS POAP Python script that applies startup config."""
    return f"""#!/usr/bin/env python
import sys
import time
import traceback
try:
    import urllib2 as _urlreq
except ImportError:
    import urllib.request as _urlreq
from cli import cli

CONFIG_URL = "{config_url}"
DEBUG_LOG = "/bootflash/poap_archetype_debug.log"


def _append_debug(message):
    try:
        with open(DEBUG_LOG, "a") as handle:
            handle.write("[%s] %s\\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass


def _log(message):
    line = "POAP-ARCTYPE: %s" % message
    print(line)
    _append_debug(line)


def _run(command):
    _log("running command: %s" % command)
    try:
        cli(command)
        _log("command completed: %s" % command)
    except Exception as exc:
        _log("command failed '%s': %s" % (command, exc))


def main():
    _log("fetching startup-config")
    _append_debug("CONFIG_URL=%s" % CONFIG_URL)
    payload = _urlreq.urlopen(CONFIG_URL, timeout=60).read()
    try:
        payload_len = len(payload)
    except Exception:
        payload_len = -1
    _append_debug("payload_bytes=%s" % payload_len)
    content = payload.decode("utf-8", "ignore")
    if not content.strip():
        raise RuntimeError("empty startup-config payload")
    _append_debug("decoded_chars=%s" % len(content))

    with open("/bootflash/startup-config", "w") as handle:
        handle.write(content)
    _append_debug("wrote /bootflash/startup-config")

    _run("configure terminal ; system no poap ; end")
    _run("copy bootflash:startup-config startup-config")
    _run("copy startup-config running-config")
    _run("copy running-config startup-config")
    _log("startup-config applied")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        _log("fatal: %s" % exc)
        _append_debug(traceback.format_exc())
        sys.exit(1)
"""
