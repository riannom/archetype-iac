"""Shared helpers for N9Kv POAP bootstrap content."""

from __future__ import annotations


def render_poap_script(config_url: str) -> str:
    """Render a minimal NX-OS POAP Python script that applies startup config."""
    return f"""#!/usr/bin/env python
import sys
import urllib.request
from cli import cli

CONFIG_URL = "{config_url}"


def _log(message):
    print("POAP-ARCTYPE: %s" % message)


def _run(command):
    try:
        cli(command)
    except Exception as exc:
        _log("command failed '%s': %s" % (command, exc))


def main():
    _log("fetching startup-config")
    payload = urllib.request.urlopen(CONFIG_URL, timeout=60).read()
    content = payload.decode("utf-8", "ignore")
    if not content.strip():
        raise RuntimeError("empty startup-config payload")

    with open("/bootflash/startup-config", "w") as handle:
        handle.write(content)

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
        sys.exit(1)
"""
