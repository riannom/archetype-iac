from __future__ import annotations

from agent.n9kv_poap import render_poap_script


def test_render_poap_script_embeds_config_url_and_core_sections() -> None:
    script = render_poap_script("http://controller/poap/startup.cfg")

    assert script.startswith("#!/usr/bin/env python")
    assert 'CONFIG_URL = "http://controller/poap/startup.cfg"' in script
    assert "DEBUG_LOG = \"/bootflash/poap_archetype_debug.log\"" in script
    assert "def _apply_startup_config():" in script
    assert "def _disable_poap():" in script
    assert "def main():" in script


def test_render_poap_script_avoids_running_config_persistence_command() -> None:
    script = render_poap_script("http://controller/config")

    # The script intentionally avoids the command that can corrupt boot vars.
    assert "_run(\"copy running-config startup-config\")" not in script
    assert "_run(\"copy bootflash:startup-config running-config\")" in script


def test_render_poap_script_contains_disable_poap_sequence() -> None:
    script = render_poap_script("http://controller/config")

    assert "_try_run(\"configure terminal\")" in script
    assert "_try_run(\"system no poap\")" in script
    assert "_try_run(\"end\")" in script
