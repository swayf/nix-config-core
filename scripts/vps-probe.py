#!/usr/bin/env python3
"""Read-only Linux inventory. Invoked over authenticated SSH; no installation."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def command(*args, required=True):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        if not required:
            return ""
        raise ValueError("inventory command unavailable: " + args[0])
    if result.returncode:
        if required:
            raise ValueError("inventory command unavailable: " + args[0])
        return ""
    return result.stdout.strip()


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def collect():
    if os.geteuid() != 0:
        raise ValueError("inventory requires root or sudo -n")
    links = json.loads(command("ip", "-d", "-j", "link", "show"))
    addresses = json.loads(command("ip", "-j", "address", "show"))
    routes = json.loads(command("ip", "-j", "-4", "route", "show", "table", "main"))
    rules = json.loads(command("ip", "-j", "-4", "rule", "show"))
    extra_routes = json.loads(command("ip", "-j", "-4", "route", "show", "table", "all"))
    disks = json.loads(command("lsblk", "--json", "--bytes", "--paths", "--output",
        "NAME,TYPE,SIZE,MODEL,SERIAL,WWN,RO,RM,FSTYPE,MOUNTPOINTS,PKNAME"))["blockdevices"]
    defaults = [r for r in routes if r.get("dst") == "default"]
    interface = defaults[0].get("dev") if len(defaults) == 1 else None
    dns = []
    if interface:
        for word in command("resolvectl", "dns", interface, required=False).split():
            import ipaddress
            try:
                address = ipaddress.ip_address(word)
                if address.version == 4 and not address.is_loopback:
                    dns.append(str(address))
            except ValueError:
                pass
    if not dns:
        for line in read("/etc/resolv.conf").splitlines():
            words = line.split()
            if len(words) >= 2 and words[0] == "nameserver":
                import ipaddress
                try:
                    address = ipaddress.ip_address(words[1])
                    if address.version == 4 and not address.is_loopback:
                        dns.append(str(address))
                except ValueError:
                    pass
    mem = dict(line.split(":", 1) for line in read("/proc/meminfo").splitlines())
    os_release = {}
    for line in read("/etc/os-release").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip('"')
    hardware = command("nixos-generate-config", "--show-hardware-config", "--no-filesystems", required=False) \
        if Path("/run/current-system").exists() else ""
    return {
        "schema": 2, "link_details": True, "architecture": platform.machine(),
        "virtualization": command("systemd-detect-virt", required=False),
        "firmware": "uefi" if Path("/sys/firmware/efi").exists() else "bios",
        "machine_id": read("/etc/machine-id"),
        "ssh_host_public_key": read("/etc/ssh/ssh_host_ed25519_key.pub"),
        "os": os_release.get("ID", "unknown"),
        "installer": os_release.get("VARIANT_ID") == "installer",
        "memory_bytes": int(mem["MemTotal"].split()[0]) * 1024,
        "cpu_count": os.cpu_count(), "disks": disks, "links": links,
        "addresses": addresses, "routes": routes, "rules": rules,
        "extra_routes": extra_routes, "dns": sorted(set(dns)),
        "kexec_disabled": read("/proc/sys/kernel/kexec_load_disabled"),
        "secure_boot": any(p.read_bytes()[-1:] == b"\x01" for p in
            Path("/sys/firmware/efi/efivars").glob("SecureBoot-*")),
        "hardware_configuration": hardware,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(collect()))
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
        print("Inventory failed: root/sudo, Python 3, iproute2, util-linux and systemd are required.", file=sys.stderr)
        sys.exit(1)
