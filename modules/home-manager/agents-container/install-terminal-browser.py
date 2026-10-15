#!/usr/bin/env python3
"""Link the packaged Herdr action offline, preserving explicit user disables."""
import json
import subprocess
import sys


PLUGIN = "zenbu-labs.terminal-browser"


def herdr(*args):
    return subprocess.check_output(["herdr", *args], text=True)


def install(source):
    plugins = json.loads(herdr("plugin", "list", "--json"))["result"]["plugins"]
    current = next((p for p in plugins if p["plugin_id"] == PLUGIN), None)
    if current is not None and current["plugin_root"] == source:
        return
    args = ["plugin", "link", source]
    if current is not None and not current["enabled"]:
        args.append("--disabled")
    herdr(*args)
    # No server stop/restart: Herdr discovers linked actions itself.


if __name__ == "__main__":
    install(sys.argv[1])
