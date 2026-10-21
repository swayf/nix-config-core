#!/usr/bin/env python3
"""Native VLESS/REALITY + WARP VPS provisioning and explicit operations.

probe inventories remotely and optionally authorizes a temporary SSH key with consent;
plan probes REALITY candidates when needed; check is local.
secrets creates credentials
and registers WARP on the source VPS via saved SSH (using secrets implies accepting Cloudflare terms). install is staged and
destructive only after a second authenticated inventory in the installer.
"""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import getpass
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import urllib.parse
import warnings

SOURCE = Path(__file__).resolve().parent
ANYWHERE = "github:nix-community/nixos-anywhere/b6be7b277b468d55082584441cbef1fed8530eb3"
PRIVATE_PORTS = {3000, 9090, 9093, 9100, 9115, 9550, 10085}
REALITY_CANDIDATES = ("www.microsoft.com", "www.apple.com", "www.cloudflare.com", "www.amazon.com")
HOST_CONFIG_FILES = ("machine.json", "vless.json", "configuration.nix",
                     "disk-config.nix", "hardware-configuration.nix")
# A boot-disabled stack has no per-interface sysctls. After a live switch,
# require existing/future interfaces disabled and no IPv6 addresses or routes.
IPV6_DISABLED_CHECK = (
    'if test "$(cat /sys/module/ipv6/parameters/disable 2>/dev/null)" = 1; then exit 0; fi; '
    'for flag in /proc/sys/net/ipv6/conf/*/disable_ipv6; do '
    'test "$(cat "$flag")" = 1 || exit 1; done; '
    'addresses=$(ip -6 -o address show) && routes=$(ip -6 route show table all) && '
    'test -z "$addresses" && test -z "$routes"'
)


def has_host_plan(host):
    artifacts = (*HOST_CONFIG_FILES, "plan.json", "secrets.yaml", "monitoring.yaml",
                 "credentials.json", "checked.json", "install-attempt.json", "verified.json")
    return host.is_symlink() or any((host / name).exists() or (host / name).is_symlink()
                                    for name in artifacts)


class Error(Exception):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


class SSHAuthenticationError(Error):
    pass


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_renderer(name="vps_renderer"):
    """Use the tool's pinned renderer, never executable code from --repo."""
    configured = os.environ.get("VPS_XRAY_RENDERER")
    path = Path(configured) if configured else SOURCE.parent / "modules/nixos/xray-render.py"
    if not path.is_absolute():
        raise Error("VPS_XRAY_RENDERER must be an absolute tool-owned path; no fallback")
    try:
        return load(path, name)
    except OSError:
        raise Error("Pinned Xray renderer unavailable; rebuild the CLI; no checkout fallback") from None


def source_policy():
    return load(SOURCE / "vps-policy.py", "vps_policy").policy()


def legacy_admin(machine):
    return admin_user(machine["adminUser"] if "adminUser" in machine else source_policy()["legacyAdminUser"])


def ssh_probe_error(code, stderr):
    # Classify captured output, but never publish any part of it: SSH banners
    # and remote errors are untrusted and may contain credentials.
    prefix = f"SSH probe failed (exit {code}): "
    if code == 5:
        reason = "SSH authentication rejected; check the password and whether this user may log in by password"
    elif code == 6:
        reason = "unknown SSH host key; verify the fingerprint via the provider console and add the verified key to --known-hosts"
    elif code == 7:
        reason = "SSH host key changed; verify the new fingerprint via the provider console before updating --known-hosts"
    elif code == 255:
        if b"REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
            reason = "SSH host key changed; verify the new fingerprint via the provider console before updating --known-hosts"
        elif b"Host key verification failed" in stderr or b"you have requested strict checking" in stderr:
            reason = "SSH host key verification failed; verify the fingerprint via the provider console and check --known-hosts"
        elif b"Permission denied" in stderr:
            reason = "SSH authentication rejected; check the password and whether this user may log in by password"
        elif b"Connection refused" in stderr:
            reason = "SSH connection refused; check the address, --port, SSH service and provider firewall"
        elif b"Connection timed out" in stderr or b"No route to host" in stderr or b"Network is unreachable" in stderr:
            reason = "SSH server unreachable; check the address, --port, network and provider firewall"
        else:
            reason = "SSH connection/authentication failed; check the address, --port, --known-hosts and server SSH policy"
    else:
        # SSH can propagate a remote command's exit status, so small codes do
        # not necessarily indicate sshpass's own argument/runtime errors.
        reason = "check local sshpass/SSH and remote inventory prerequisites: root or sudo -n, Python 3, iproute2, util-linux and systemd"
    return prefix + reason + "; tool output withheld to protect secrets; no retry"


def run(argv, data=None, timeout=60, pass_fds=(), env=None):
    options = {"env": env} if env is not None else {}
    tool = Path(argv[0]).name
    details = {"tool": tool, "timeout_seconds": timeout}
    started = time.monotonic()
    try:
        result = subprocess.run([str(a) for a in argv], input=data, capture_output=True, timeout=timeout,
                                pass_fds=pass_fds, **options)
    except FileNotFoundError:
        details["elapsed_seconds"] = round(time.monotonic() - started, 3)
        raise Error("Required tool not found: " + tool + "; check the vps-vless runtime/PATH; no retry", details=details) from None
    except subprocess.TimeoutExpired as error:
        details["elapsed_seconds"] = round(time.monotonic() - started, 3)
        details["stderr"] = error.stderr
        raise Error("Command timed out: " + tool + f" ({timeout}s); output withheld; no retry", details=details) from None
    except OSError:
        details["elapsed_seconds"] = round(time.monotonic() - started, 3)
        raise Error("Unable to start tool: " + tool + "; check permissions/runtime; no retry", details=details) from None
    details["elapsed_seconds"] = round(time.monotonic() - started, 3)
    details["exit_code"] = result.returncode
    if result.returncode:
        # Keep stderr for the private JSON report, never for terminal diagnostics.
        # It can contain credentials or invalid secret fields; stdout stays omitted.
        details["stderr"] = result.stderr
        if tool == "sshpass":
            error = SSHAuthenticationError if result.returncode in (5, 6, 7, 255) else Error
            raise error(ssh_probe_error(result.returncode, result.stderr), details=details)
        raise Error(f"Command failed: {tool} (exit {result.returncode}); "
                    "output withheld to protect secrets; check tools/configuration; no retry", details=details)
    return result.stdout


def confirm_overwrite(path, force=False):
    # Standalone scripts are also shipped as individual Nix store files.
    parts = Path(os.path.abspath(path)).parts
    if not any(parts[i:i + 2] == ("hosts", "generated") for i in range(len(parts) - 1)):
        return
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise Error("Refusing to overwrite a non-regular generated file: " + str(path))
    if force:
        print(f"File already exists: {path}. Overwrite? y — automatically accepted (--force)")
        return
    if not sys.stdin.isatty():
        raise Error("Overwrite requires an interactive terminal; file unchanged: " + str(path))
    try:
        answer = input(f"File already exists: {path}. Overwrite? [y/N]: ")
    except EOFError:
        raise Error("Overwrite cancelled; file unchanged: " + str(path)) from None
    if answer.strip().lower() not in ("y", "yes"):
        raise Error("Overwrite declined; file unchanged: " + str(path))


def write_json(path, value, *, force=False, overwrite_confirmed=False):
    if not overwrite_confirmed:
        confirm_overwrite(path, force=force)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".vps-", delete=False) as f:
        temp = Path(f.name)
        try:
            f.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
            f.flush()
            os.fsync(f.fileno())
            os.chmod(temp, 0o600)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def read_json(path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise Error("JSON input exceeds 4 MiB")
    return json.loads(path.read_text())


def validate_name(name):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        raise Error("Host name must be a lowercase DNS label")
    return name


def admin_user(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value) or value in ("root", "nobody"):
        raise ValueError("Administrator user must be a lowercase Linux login (up to 32 characters), not root/nobody")
    return value


def domain(value):
    if len(value) > 253 or not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", s)
                                  for s in value.split(".")):
        raise Error("Expected a DNS name, not a URL or routing expression")
    return value.lower()


def public_key_path(value):
    path = Path(value)
    if path.suffix != ".pub":
        raise argparse.ArgumentTypeError("Administrator SSH key must be a PUBLIC .pub file")
    return path


def ensure_admin_key(path, repo):
    path = path.expanduser()
    if path.is_file():
        return path
    if path.exists() or path.is_symlink():
        raise Error("Administrator public key path is not a regular file: " + str(path))
    private = path.with_suffix("")
    if private.exists() or private.is_symlink():
        raise Error("Public key is missing but private key already exists: " + str(private)
                    + "; restore its .pub with ssh-keygen -y, or choose a new pair path. No overwrite.")
    parent = path.parent.resolve()
    repo = repo.resolve()
    if parent == repo or repo in parent.parents or parent == Path("/nix/store") or Path("/nix/store") in parent.parents:
        raise Error("Generate administrator private keys outside the checkout and Nix store")
    if not sys.stdin.isatty():
        raise Error("Public key not found: " + str(path) + "; create it first or use an interactive terminal")
    answer = input(f"Public key not found: {path}. Generate Ed25519 pair ({private}, {path})? [y/N]: ")
    if answer.strip().lower() not in ("y", "yes"):
        raise Error("SSH key generation declined; secrets not provisioned")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Generate privately, then publish without overwriting even if another process
    # creates either destination during the interactive passphrase prompt.
    with tempfile.TemporaryDirectory(prefix=".vps-ssh-", dir=parent) as tmp:
        key = Path(tmp) / "key"
        # Inherit the terminal: ssh-keygen handles hidden passphrase input itself.
        result = subprocess.run(["ssh-keygen", "-t", "ed25519", "-C", "vps-admin", "-f", str(key)])
        if result.returncode:
            raise Error(f"SSH key generation failed or cancelled (exit {result.returncode}); secrets not provisioned",
                        details={"tool": "ssh-keygen", "exit_code": result.returncode})
        os.link(key, private)
        os.link(key.with_suffix(".pub"), path)
    print("Created administrator SSH key pair: " + str(private) + ", " + str(path))
    print("The administrator key was not installed on the current VPS; probe/install keep their separate SSH identity.")
    print("After installation, verify uses the first administrator key pair by default, or an explicit --key PATH.")
    return path


def prepare_admin_keys(machine, repo):
    # Older plans already contain public key contents, without file references.
    if "adminKeyFiles" not in machine:
        if not machine.get("adminKeys"):
            raise Error("No administrator public keys; add adminKeyFiles to machine.json")
        return machine["adminKeys"]
    filenames = machine["adminKeyFiles"]
    if not isinstance(filenames, list) or not filenames:
        raise Error("At least one administrator PUBLIC .pub path is required in adminKeyFiles")
    keys = []
    for filename in filenames:
        if not isinstance(filename, str):
            raise Error("Expected an absolute administrator PUBLIC .pub path")
        path = Path(filename)
        if not path.is_absolute() or path.suffix != ".pub":
            raise Error("Expected an absolute administrator PUBLIC .pub path")
        path = ensure_admin_key(path, repo)
        key = path.read_text().strip()
        parts = key.split()
        if len(parts) < 2 or parts[0] not in ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"):
            raise Error("Expected an administrator PUBLIC SSH key")
        try:
            base64.b64decode(parts[1], validate=True)
        except ValueError:
            raise Error("Invalid SSH public key encoding")
        run(["ssh-keygen", "-lf", path])
        keys.append(key)
    return keys


def choose_target(c):
    if not sys.stdin.isatty():
        raise Error("REALITY target selection requires an interactive terminal; supply --target DOMAIN")
    # Fail before the candidate loop when the remote TLS tool is unavailable.
    password = prompt_ssh_password(c) if not c.get("key") else None
    remote_run(c, "command -v openssl >/dev/null", timeout=20, password=password)
    working = []
    print("Checking REALITY candidates from the VPS (TLS 1.3, trusted certificate, HTTP/2):")
    for candidate in REALITY_CANDIDATES:
        try:
            options = {"password": password} if password is not None else {}
            preflight(c, {"serverName": candidate, "target": candidate + ":443"}, **options)
        except SSHAuthenticationError:
            raise  # Do not retry rejected credentials across candidates.
        except (Error, subprocess.TimeoutExpired):
            print(candidate + ": failed")
        else:
            working.append(candidate)
            print(candidate + ": passed")
    if not working:
        raise Error("No REALITY candidate passed; verify VPS connectivity or supply a verified --target DOMAIN")
    for number, candidate in enumerate(working, 1):
        print(f"{number}. {candidate}")
    answer = input("Select REALITY target number (empty cancels): ").strip()
    if not answer.isascii() or not answer.isdigit() or not 1 <= int(answer) <= len(working):
        raise Error("No valid REALITY target selected; plan not created")
    return working[int(answer) - 1]


def connection(address, user, port, key, known_hosts):
    address = str(ipaddress.IPv4Address(address))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user) or not 1 <= int(port) <= 65535:
        raise Error("Invalid SSH user/port")
    return {"address": address, "user": user, "port": int(port),
            "key": str(Path(key).expanduser().resolve()) if key is not None else None,
            "known_hosts": str(Path(known_hosts).expanduser().resolve())}


def ssh_args(c, password=False):
    # Never bypass host-key verification or enumerate ssh-agent keys.
    if password:
        auth = ["-o", "BatchMode=no", "-o", "PubkeyAuthentication=no",
                "-o", "PreferredAuthentications=password", "-o", "NumberOfPasswordPrompts=1"]
    else:
        if not c.get("key"):
            raise Error("This connection requires an SSH key; use password authentication on the source VPS or the installation key in the installer")
        auth = ["-i", c["key"], "-o", "BatchMode=yes"]
    return ["ssh", "-p", str(c["port"])] + auth + ["-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
            "-o", "UserKnownHostsFile=" + c["known_hosts"], c["user"] + "@" + c["address"]]


def remove_probe_host_key(candidate, target):
    # -R validates the whole file and aborts on unrelated malformed lines.
    # -F locates usable matching entries without rewriting any other records,
    # and delegates hashed-host and port matching to OpenSSH.
    try:
        found = run(["ssh-keygen", "-F", target, "-f", candidate], env={**os.environ, "LC_ALL": "C"})
    except Error as error:
        if error.details.get("exit_code") == 1:
            raise Error("Changed SSH host key was not found in --known-hosts; check global SSH host-key configuration; file unchanged; no password requested",
                        details=error.details) from None
        raise
    pattern = rb"^# Host " + re.escape(target.encode("ascii")) + rb" found: line ([1-9][0-9]*)(?:[ \t].*)?$"
    matched = {int(match.group(1)) for match in re.finditer(pattern, found, re.MULTILINE)}
    if not matched:
        raise Error("Cannot locate changed SSH host key entries; --known-hosts unchanged; no password requested")
    # Split only on LF, as OpenSSH does, even if malformed lines contain CR.
    lines = re.findall(rb"[^\n]*\n|[^\n]+$", candidate.read_bytes())
    if max(matched) > len(lines):
        raise Error("Invalid SSH host key entry location; --known-hosts unchanged; no password requested")
    kept = []
    for number, line in enumerate(lines, 1):
        if number not in matched:
            kept.append(line)
            continue
        field = re.search(rb"\S+", line)
        hosts = field.group()
        if hosts.startswith(b"@"):
            raise Error("Cannot automatically replace revoked or certificate-authority SSH host key entries; update --known-hosts manually; no password requested")
        if hosts.startswith(b"|"):
            continue  # A matched hashed record represents this one host only.
        aliases = hosts.split(b",")
        remaining = [alias for alias in aliases if alias != target.encode("ascii")]
        if remaining == aliases:
            raise Error("Cannot automatically replace a wildcard SSH host key entry; update --known-hosts manually; no password requested")
        if remaining:
            kept.append(line[:field.start()] + b",".join(remaining) + line[field.end():])
    candidate.write_bytes(b"".join(kept))


def replace_probe_host_key(c):
    path = Path(c["known_hosts"])
    if path.is_symlink() or not path.is_file():
        raise Error("Cannot replace SSH host key in a non-regular --known-hosts file; no password requested")
    original_stat = path.stat()
    original = path.read_bytes()
    target = c["address"] if c["port"] == 22 else f'[{c["address"]}]:{c["port"]}'
    # Remove stale entries only in a private copy. OpenSSH shows the new
    # fingerprint and enrolls the exact key accepted by the operator; no keyscan
    # or disabled verification, and no change to the real file on refusal/failure.
    with tempfile.TemporaryDirectory(prefix=".vps-known-hosts-", dir=path.parent) as tmp:
        candidate = Path(tmp) / "known_hosts"
        candidate.write_bytes(original)
        candidate.chmod(0o600)
        remove_probe_host_key(candidate, target)
        without_target = candidate.read_bytes()
        print(f"WARNING: SSH host key changed for {target}.\n"
              "Verify the new SHA256 fingerprint via the provider console before accepting the next OpenSSH prompt.\n"
              f"Accepting confirms replacement of this host's stored keys in {path}; declining leaves the file unchanged.",
              file=sys.stderr)
        confirm_probe_host_key(c | {"known_hosts": str(candidate)}, replace_changed=False)
        if candidate.read_bytes() == without_target:
            raise Error("No replacement SSH host key was saved; --known-hosts unchanged; no password requested")
        current_stat = path.stat()
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (path.is_symlink() or any(getattr(current_stat, field) != getattr(original_stat, field) for field in fields)
                or path.read_bytes() != original):
            raise Error("--known-hosts changed during confirmation; refusing to overwrite it; no password requested")
        candidate.chmod(stat.S_IMODE(original_stat.st_mode))
        with candidate.open("rb") as f:
            os.fsync(f.fileno())
        candidate.replace(path)
    print(f"Replaced confirmed SSH host key for {target} in {path}.")


def confirm_probe_host_key(c, *, replace_changed=True):
    if not sys.stdin.isatty():
        return  # Unattended probes never enroll unknown keys.
    # Let OpenSSH present its native yes/no/[fingerprint] prompt via /dev/tty.
    # No credentials or collector are supplied on this connection. Authentication
    # is deliberately disabled; the actual inventory still uses strict checking.
    argv = ["ssh", "-p", str(c["port"]),
            "-o", "StrictHostKeyChecking=ask", "-o", "BatchMode=no",
            "-o", "PreferredAuthentications=none", "-o", "PubkeyAuthentication=no",
            "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-o", "NumberOfPasswordPrompts=0", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
            "-o", "UserKnownHostsFile=" + c["known_hosts"],
            c["user"] + "@" + c["address"], "true"]
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                            env={**os.environ, "LC_ALL": "C"})
    if b"Failed to add the host to the list of known hosts" in result.stderr:
        raise Error("Cannot save accepted SSH host key; check that the --known-hosts directory exists and is writable; no password requested")
    if result.returncode == 0:
        return
    # A trusted SSH handshake normally ends here because no authentication
    # method is enabled. Host-key failures must never proceed to a password.
    if (result.returncode == 255 and b"Permission denied" in result.stderr
            and b"Host key verification failed" not in result.stderr
            and b"REMOTE HOST IDENTIFICATION HAS CHANGED" not in result.stderr):
        return
    if (replace_changed and result.returncode == 255
            and b"REMOTE HOST IDENTIFICATION HAS CHANGED" in result.stderr):
        replace_probe_host_key(c)
        return
    raise Error(ssh_probe_error(result.returncode, result.stderr),
                details={"tool": "ssh", "stage": "SSH host-key confirmation", "exit_code": result.returncode})


def prompt_ssh_password(c):
    if not shutil.which("sshpass"):
        raise Error("Password SSH requires sshpass on the administrative computer")
    if not sys.stdin.isatty():
        raise Error("Password SSH requires an interactive terminal; use probe --key for unattended authentication")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass(f'SSH password for {c["user"]}@{c["address"]}: ').encode()
        except getpass.GetPassWarning:
            raise Error("Cannot hide SSH password input; use a working terminal or --key")
    if not password or len(password) > 1024 or b"\n" in password or b"\r" in password:
        raise Error("Empty, multiline or oversized SSH password is unsupported")
    return password


def password_inventory(c, command, script=None, *, password=None, timeout=90):
    if password is None:
        password = prompt_ssh_password(c)
    # Separate inherited pipe: no password in argv, environment, files or collector stdin.
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, password + b"\n")
        del password
        return run(["sshpass", "-d", str(read_fd)] + ssh_args(c, password=True) + [command],
                   script, timeout=timeout, pass_fds=(read_fd,))
    finally:
        os.close(read_fd)
        os.close(write_fd)


def remote_run(c, command, data=None, *, timeout=60, password=None):
    if c.get("installer_tools"):
        tools = c["installer_tools"]
        command = ("export PATH=" + shlex.quote(tools + "/bin") + ':"$PATH" SSL_CERT_FILE='
                   + shlex.quote(tools + "/etc/ssl/certs/ca-bundle.crt") + "; " + command)
    if c.get("key"):
        return run(ssh_args(c) + [command], data, timeout=timeout)
    return password_inventory(c, command, data, password=password, timeout=timeout)


def register_warp_on_vps(c):
    # Use the source connection frozen by plan, never future administrator keys.
    c = connection(c["address"], c["user"], c["port"], c.get("key"), c["known_hosts"])
    binary = os.environ.get("VPS_WGCF_BINARY")
    if not binary:
        raise Error("Portable wgcf binary unavailable; use the packaged vps command. No local registration fallback",
                    details={"registration_location": "source-VPS"})
    worker = load(SOURCE / "vps-warp.py", "vps_warp_worker")
    try:
        payload = worker.request(Path(binary), (SOURCE / "vps-wgcf.py").read_text())
        # Only public code/binary travel in stdin. The success response contains
        # credentials and must stay captured, then be encrypted locally by SOPS.
        command = "python3 -c " + shlex.quote((SOURCE / "vps-warp.py").read_text())
        print(f'WARP registration on source VPS via {c["user"]}@{c["address"]}:{c["port"]}; no local fallback.')
        raw = remote_run(c, command, json.dumps(payload).encode(), timeout=300)
        return worker.response(raw)
    except worker.WorkerError as e:
        raise Error(str(e), details=e.details | {"registration_location": "source-VPS"}) from None
    except Error as e:
        e.details = e.details | {"registration_location": "source-VPS"}
        raise


def inventory(c, *, password=None):
    script = (SOURCE / "vps-probe.py").read_bytes()
    command = "python3 -" if c["user"] == "root" else "sudo -n python3 -"
    raw = remote_run(c, command, script, timeout=90, password=password)
    if len(raw) > 4 * 1024 * 1024:
        raise Error("Remote inventory too large")
    return json.loads(raw)


def disks_of(facts):
    disks = [d for d in facts["disks"] if d["type"] == "disk" and not d.get("ro") and not d.get("rm")]
    if len(disks) != 1:
        raise Error("Supported profile requires exactly one writable, non-removable disk")
    disk = disks[0]
    def descendants(node):
        return [node] + [n for child in node.get("children", []) for n in descendants(child)]
    if any(n["type"] not in ("disk", "part") for n in descendants(disk)):
        raise Error("LVM, RAID, encrypted or stacked block devices require a reviewed custom profile")
    if not re.fullmatch(r"/dev/(?:[sv]d[a-z]+|nvme\d+n\d+)", disk["name"]):
        raise Error("Unknown block device naming/driver")
    if int(disk["size"]) < 16 * 1024**3:
        raise Error("At least 16 GiB disk is required for this full monitoring profile")
    return {k: disk.get(k) for k in ("name", "size", "model", "serial", "wwn")}


def platform_check(facts):
    if facts.get("schema") != 2 or not facts.get("link_details") or facts["architecture"] != "x86_64" or facts["virtualization"] != "kvm":
        raise Error("Supported profile: x86_64 KVM only (no containers/bare metal)")
    if facts["firmware"] not in ("bios", "uefi") or facts.get("secure_boot"):
        raise Error("Unknown boot mode or Secure Boot; custom boot provisioning is required")
    if facts["memory_bytes"] < 1800 * 1024**2:
        raise Error("This profile requires approximately 2 GiB RAM; use a separate smaller profile")
    if not facts.get("machine_id") or not facts.get("ssh_host_public_key", "").startswith("ssh-ed25519 "):
        raise Error("Missing machine identity or Ed25519 SSH host key")
    return disks_of(facts)


def network_plan(facts, mode, dns):
    routes = facts["routes"]
    defaults = [r for r in routes if r.get("dst") == "default"]
    if len(defaults) != 1 or "gateway" not in defaults[0] or "dev" not in defaults[0]:
        raise Error("Exactly one IPv4 gateway/default uplink is required")
    route = defaults[0]
    interface = route["dev"]
    ipaddress.IPv4Address(route["gateway"])
    if any(r.get("table", "main") not in ("main", "local", 254, 255) for r in facts.get("extra_routes", [])):
        raise Error("Custom routing tables are unsupported")
    if any(r.get("priority") not in (0, 32766, 32767) or r.get("src", "all") != "all"
           for r in facts["rules"]):
        raise Error("Policy routing is unsupported")
    for r in routes:
        if r.get("dev") != interface or any(k in r for k in ("nexthops", "nhid", "encap")):
            raise Error("Multiple uplinks or advanced routes are unsupported")
        if r.get("dst") != "default" and not (r.get("protocol") == "kernel" and r.get("scope") == "link"):
            # iproute2 may report a host route as either an address or address/32.
            try:
                destination = ipaddress.IPv4Network(r.get("dst", ""))
            except (ValueError, TypeError):
                raise Error("Non-connected routes require a custom network profile") from None
            if destination != ipaddress.IPv4Network(route["gateway"] + "/32") or r.get("scope") != "link":
                raise Error("Non-connected routes require a custom network profile")
    link = next((x for x in facts["links"] if x["ifname"] == interface), None)
    if not facts.get("link_details") or link is None or link.get("link_type") != "ether" or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", link.get("address", "")):
        raise Error("Expected detailed inventory of a simple Ethernet uplink with a stable MAC address")
    if link.get("linkinfo", {}).get("info_kind") or link.get("master") or link.get("link_index"):
        raise Error("VLAN, bridge, bond, tunnel and stacked network links are unsupported")
    active = next((x for x in facts["addresses"] if x["ifname"] == interface), None)
    if active is None:
        raise Error("Uplink has no address inventory")
    for item in facts["addresses"]:
        for a in item["addr_info"]:
            # IPv6 is never transferred; vps-base disables it on every interface.
            if a["family"] == "inet6":
                continue
            if a.get("scope") == "global" and (item["ifname"] != interface or a["family"] != "inet"):
                raise Error("Additional global IPv4 uplinks require a custom profile")
    v4 = [a for a in active["addr_info"] if a["family"] == "inet" and a.get("scope") == "global"]
    if not v4:
        raise Error("No usable IPv4 uplink addresses")
    dynamic = [a for a in v4 if a.get("dynamic") or a.get("valid_life_time", 4294967295) != 4294967295]
    if mode == "auto":
        # Infer from the saved inventory, not from a provider policy guarantee.
        if not dynamic:
            mode = "static"
        elif len(v4) == 1 and len(dynamic) == 1:
            mode = "dhcp"
        else:
            raise Error("Mixed or multiple dynamic IPv4 addresses require a custom network profile")
    if mode == "dhcp" and (len(v4) != 1 or len(dynamic) != 1):
        raise Error("DHCP profile requires one observed dynamic IPv4 address")
    if mode == "static" and dynamic:
        raise Error("Refusing to turn an observed DHCP lease into a static address")
    addresses = [str(ipaddress.IPv4Interface(f'{a["local"]}/{a["prefixlen"]}')) for a in v4]
    dns = dns or facts["dns"]
    dns = [str(ipaddress.IPv4Address(x)) for x in dns]
    if any(ipaddress.ip_address(x).is_loopback or ipaddress.ip_address(x).is_unspecified for x in dns):
        raise Error("Loopback/unspecified DNS cannot be persisted")
    if mode == "static" and not dns:
        raise Error("Static network requires real upstream DNS; supply --dns")
    config = {"matchConfig": {"MACAddress": link["address"]},
              "networkConfig": {"DHCP": "ipv4" if mode == "dhcp" else "no",
                                "IPv6AcceptRA": False, "LinkLocalAddressing": "no"},
              "linkConfig": {"RequiredForOnline": "routable"}}
    if dns:
        config["networkConfig"]["DNS"] = dns
    if mode == "static":
        config["address"] = addresses
        config["routes"] = [{"Destination": route["gateway"] + "/32", "Scope": "link"},
                            {"Destination": "0.0.0.0/0", "Gateway": route["gateway"], "GatewayOnLink": True}]
    return {"mode": mode, "interface": interface, "mac": link["address"], "config": config}


def identity(facts):
    return {"architecture": facts["architecture"], "virtualization": facts["virtualization"],
            "firmware": facts["firmware"], "disk": disks_of(facts)}


def selected_digest(repo, name, directory, secret, monitoring):
    try:
        return load(SOURCE / "vps-inputs.py", "vps_inputs").digest(repo, name, directory, secret, monitoring)
    except (ValueError, KeyError, OSError) as error:
        raise Error("Selected VPS inputs unavailable or tool/core pin mismatch; review/rebuild CLI and repeat local check") from error


def source_digest(repo, name):
    host = repo / "hosts/generated" / name
    return selected_digest(repo, name, host, host / "secrets.yaml", host / "monitoring.yaml")


def state_path(repo, name, override=None, create=False):
    if override is not None:
        # source_ref knows only legacy and XDG state. Refuse other in-checkout
        # roots before reading/creating state or reaching any Nix consumer.
        # Normalize lexically, without traversing a private state directory.
        checkout = Path(os.path.abspath(repo.expanduser()))
        root = Path(os.path.abspath(override.expanduser()))
        known = {checkout / "hosts/generated"}
        xdg = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "vps"
        if xdg.is_absolute():
            known.add(Path(os.path.abspath(xdg)))
        if (root / name).is_relative_to(checkout) and root not in known:
            raise Error("In-checkout --state-dir is unsupported outside legacy/XDG layouts; "
                        "use a workflow artifact root outside the checkout")
    helper = load(SOURCE / "vps-state.py", "vps_state")
    try:
        state = helper.select(repo, name, override)
        if create:
            helper.create(state, repo)
        return state
    except (ValueError, OSError) as error:
        raise Error("Unsafe/ambiguous VPS state; inspect legacy/XDG copies and use explicit --state-dir after review") from error


def host_paths(args):
    name = validate_name(args.name)
    repo = args.repo.expanduser().resolve()
    if not (repo / "flake.nix").is_file():
        raise Error("--repo must be the editable configuration checkout")
    host = repo / "hosts/generated" / name
    if host.parent.is_symlink() or host.is_symlink():
        raise Error("Generated host directory must not be a symlink")
    state = state_path(repo, name, args.state_dir, create=True)
    return repo, state, host


def probe(args, repo, state, host):
    c = connection(args.address, args.user, args.port, args.key, args.known_hosts)
    confirm_probe_host_key(c)
    password = prompt_ssh_password(c) if not c.get("key") else None
    auth = {"password": password} if password is not None else {}
    facts = inventory(c, **auth)
    disk = platform_check(facts)
    report = state / "probe.json"
    # Declining replacement must not leave a newly authorized key on the VPS.
    confirm_overwrite(report, force=getattr(args, "force", False))
    if not c.get("key") and sys.stdin.isatty():
        try:
            answer = input("Generate and authorize a temporary SSH key for " + args.name
                           + " (changes VPS authorized_keys; removed locally after successful verify)? [y/N]: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() in ("y", "yes"):
            c = authorize_installation_key(c, state, repo, password)
    del password, auth
    write_json(report, {"connection": c, "facts": facts}, overwrite_confirmed=True)
    print(json.dumps({"name": args.name, "firmware": facts["firmware"], "disk": disk,
                      "ram_mib": facts["memory_bytes"] // 1024**2,
                      "report": str(state / "probe.json"), "network_policy": "plan defaults to auto: one dynamic IPv4 uses DHCP; only permanent IPv4 addresses use static; review provider policy"}, indent=2))


HARDWARE = '''# Standard supported KVM drivers; no UUIDs/filesystems inherited from Ubuntu.
{ modulesPath, ... }: {
  imports = [ "${modulesPath}/profiles/qemu-guest.nix" ];
  boot.initrd.availableKernelModules = [ "virtio_pci" "virtio_scsi" "virtio_blk" "ahci" "sd_mod" "nvme" ];
}
'''
DISKO = '''{ ... }:
let m = builtins.fromJSON (builtins.readFile ./machine.json); in {
  disko.devices.disk.main = {
    type = "disk"; device = m.disk.name;
    content = { type = "gpt"; partitions = {
      boot = if m.firmware == "uefi" then {
        size = "512M"; type = "EF00";
        content = { type = "filesystem"; format = "vfat"; mountpoint = "/boot"; };
      } else { size = "1M"; type = "EF02"; };
      root = { size = "100%"; content = { type = "filesystem"; format = "ext4"; mountpoint = "/"; }; };
    }; };
  };
}
'''
CONFIGURATION = '''{ inputs, ... }:
let
  m = builtins.fromJSON (builtins.readFile ./machine.json);
  # Keep ciphertext as paths so SOPS manifests retain their build dependencies.
  monitoring = ./monitoring.yaml;
  telegram = (m.chatId or 0) != 0 && builtins.pathExists monitoring;
in {
  imports = [ inputs.disko.nixosModules.disko ./disk-config.nix ./hardware-configuration.nix
    inputs.core.nixosModules.vps-base inputs.core.nixosModules.xray-sops ];
  system.stateVersion = m.stateVersion;
  boot.loader.grub = { enable = true; efiSupport = m.firmware == "uefi";
    efiInstallAsRemovable = m.firmware == "uefi"; };
  swapDevices = [ { device = "/var/lib/swapfile"; size = 1024; } ];
  systemd.network.networks."10-uplink" = m.network.config;
  services.vps = {
    enable = true; adminUser = m.adminUser;
    adminKeys = m.adminKeys; sshPort = m.sshPort;
    settings = builtins.fromJSON (builtins.readFile ./vless.json);
    warpDomains = m.warpDomains;
  };
  services.xray.declarative.secretFile = ./secrets.yaml;
  services.xrayMonitoring.telegram = { enable = telegram; chatId = m.chatId or 0;
    secretFile = if telegram then monitoring else null; };
}
'''


def plan(args, repo, state, host):
    if has_host_plan(host):
        raise Error("Generated host plan already exists; review/edit it explicitly rather than replacing it")
    observed = read_json(state / "probe.json")
    facts = observed["facts"]
    disk = platform_check(facts)
    if not args.admin_key or any(p.suffix != ".pub" for p in args.admin_key):
        raise Error("At least one administrator PUBLIC .pub key is required")
    # Freeze relative paths against the planning cwd, without reading files or
    # resolving symlinks: secrets must still detect unsafe/dangling destinations.
    key_paths = [os.path.abspath(p.expanduser()) for p in args.admin_key]
    if not 1 <= args.ssh_port <= 65535 or args.ssh_port == 443:
        raise Error("Valid non-443 SSH port is required")
    machine = {"name": args.name, "disk": disk, "firmware": facts["firmware"], "stateVersion": "26.05",
               "network": network_plan(facts, args.network, args.dns),
               "adminUser": admin_user(args.admin_user),
               "adminKeyFiles": key_paths, "adminKeys": [], "sshPort": args.ssh_port, "chatId": 0,
               "warpDomains": [domain(d) for d in args.warp_domain]}
    registry_path = repo / "hosts/generated/inventory.json"
    if not (repo / "hosts/generated.nix").is_file():
        raise Error("Missing hosts/generated.nix; restore the tracked generated-host loader from the checkout")
    if registry_path.exists() or registry_path.is_symlink():
        registry = read_json(registry_path)
    else:
        if registry_path.parent.exists() and any(
                has_host_plan(path) for path in registry_path.parent.iterdir()):
            raise Error("Missing hosts/generated/inventory.json while generated host plans exist; "
                        "restore the registry rather than resetting it")
        registry = {}
    if not isinstance(registry, dict):
        raise Error("hosts/generated/inventory.json must be a JSON object")
    if args.name in registry:
        raise Error("Generated host is already registered")
    wsl_names = read_json(repo / "hosts/wsl-inventory.json")
    if not isinstance(wsl_names, dict):
        raise Error("Unsupported WSL inventory shape")
    if args.name in wsl_names:
        raise Error("Name collides with WSL inventory; choose another VPS name")
    target = domain(args.target) if args.target else choose_target(observed["connection"])
    # Ask before publishing any host files, so declining the registry update
    # does not leave an unregistered partial plan behind.
    confirm_overwrite(registry_path, force=getattr(args, "force", False))
    host.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(host / "machine.json", machine, force=getattr(args, "force", False))
    write_json(host / "vless.json", {"serverName": target, "target": target + ":443", "clients": ["default"]},
               force=getattr(args, "force", False))
    for filename, text in (("configuration.nix", CONFIGURATION), ("disk-config.nix", DISKO),
                           ("hardware-configuration.nix", HARDWARE)):
        path = host / filename
        confirm_overwrite(path, force=getattr(args, "force", False))
        path.write_text(text)
    registry[args.name] = {"system": "x86_64-linux"}
    write_json(registry_path, registry, overwrite_confirmed=True)
    write_json(state / "plan.json", {"identity": identity(facts), "machine_id": facts["machine_id"],
               "source_host_key": facts["ssh_host_public_key"], "connection": observed["connection"],
               "network": machine["network"]}, force=getattr(args, "force", False))
    print("Generated native host: " + str(host) + ". Review machine.json; run secrets, then check. No deploy.")


def encrypt(payload, path, recipients):
    # Initial provisioning uses only its explicit recipients, even when no
    # matching creation rule has been added to the checkout yet.
    data = run(["sops", "--config", "/dev/null", "encrypt", "--age", ",".join(recipients), "--input-type", "json", "--output-type", "yaml",
                "--filename-override", str(path), "/dev/stdin"], json.dumps(payload).encode())
    actual = set(re.findall(r"recipient:\s*(age1[0-9a-z]+)", data.decode()))
    if actual != set(recipients):
        raise Error("SOPS recipients mismatch")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(data)


def private_age_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise Error("Administrator age identity must be a private regular file: " + str(path))


def prepare_admin_age(args, repo, bootstrap):
    if args.admin_recipient is not None:
        if not re.fullmatch(r"age1[0-9a-z]{58}", args.admin_recipient):
            raise Error("Expected administrator age PUBLIC recipient")
        return args.admin_recipient, None
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    identity = Path(os.path.abspath(config / "sops/age" / (args.name + ".txt")))
    parent = identity.parent.resolve()
    if (parent == repo or repo in parent.parents or Path("/nix/store") == parent
            or Path("/nix/store") in parent.parents):
        raise Error("Administrator age identity must be outside the checkout and Nix store")
    if identity == bootstrap or bootstrap in identity.parents or bootstrap in parent.parents or parent == bootstrap:
        raise Error("Administrator age identity must be outside the bootstrap directory; it must not reach the VPS")
    existing = identity.exists() or identity.is_symlink()
    if existing:
        private_age_file(identity)
        recipient = run(["age-keygen", "-y", identity]).decode().strip()
    else:
        if not sys.stdin.isatty():
            raise Error("No --admin-recipient or existing identity; creating a host-specific age identity requires an interactive terminal")
        answer = input(f"Create administrator age identity for {args.name} at {identity}? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            raise Error("Administrator age identity declined; secrets not provisioned")
        # Keep plaintext in memory until exclusive publication with private permissions.
        material = run(["age-keygen"])
        recipient = run(["age-keygen", "-y", "/dev/stdin"], material).decode().strip()
        if not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
            raise Error("Invalid generated administrator age recipient")
        identity.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(material)
            f.flush()
            os.fsync(f.fileno())
    if not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
        raise Error("Invalid administrator age recipient")
    print("Administrator age identity (keep locally and back up): " + str(identity))
    print("Administrator recipient: " + recipient)
    return recipient, str(identity)


@contextlib.contextmanager
def secrets_step(stage):
    # Never expose exception text from parsers/tools: it can include credentials.
    try:
        yield
    except Exception as e:
        if isinstance(e, Error):
            reason = str(e)
        elif isinstance(e, subprocess.TimeoutExpired):
            reason = "tool timed out; output withheld"
        elif isinstance(e, FileNotFoundError):
            reason = "required tool or file not found; details withheld"
        elif isinstance(e, OSError):
            reason = "local filesystem/tool error; details withheld"
        else:
            reason = "invalid/missing input or tool response; details withheld"
        details = (e.details if isinstance(e, Error) else {}) | {"stage": stage}
        suffix = "" if reason.endswith(("no retry", "no implicit retry")) else "; no implicit retry"
        raise Error("secrets / " + stage + ": " + reason + suffix, details=details) from None


def offer_bootstrap_backup(bootstrap, force=False):
    # A fresh provision is not a resume: preserve all previous files.
    question = (f"Bootstrap directory already exists: {bootstrap}. Move it to a private sibling backup "
                "and create fresh host/VLESS/WARP credentials?")
    if force:
        print(question + " y — automatically accepted (--force)")
    else:
        if not sys.stdin.isatty():
            return False
        try:
            answer = input(question + " [y/N]: ").strip().lower()
        except EOFError:
            return False
        if answer not in ("y", "yes"):
            return False
    backup_root = Path(tempfile.mkdtemp(prefix=bootstrap.name + ".backup-", dir=bootstrap.parent))
    backup = backup_root / bootstrap.name
    try:
        bootstrap.rename(backup)
    except OSError:
        backup_root.rmdir()
        raise
    print("Previous bootstrap directory preserved at: " + str(backup))
    print("Keep this backup for recovery; administrator SSH/age keys are not regenerated by this backup step.")
    return True


def provision(args, repo, state, host):
    machine_path = host / "machine.json"
    if machine_path.is_symlink() or not machine_path.is_file():
        raise Error("Refusing to update a non-regular generated file: " + str(machine_path))
    machine = read_json(machine_path)
    secret = host / "secrets.yaml"
    monitoring = host / "monitoring.yaml"
    bootstrap_input = (args.bootstrap_dir or Path.home() / ".ssh" / args.name).expanduser()
    bootstrap = bootstrap_input.resolve()
    if bootstrap == repo or repo in bootstrap.parents or str(bootstrap).startswith("/nix/store/"):
        raise Error("Bootstrap private keys must be outside the checkout and Nix store")
    inputs = [("host_ciphertext", secret), ("monitoring_ciphertext", monitoring), ("bootstrap_dir", bootstrap_input),
              ("host_ciphertext", repo / "secrets/hosts" / (args.name + ".yaml")),
              ("monitoring_ciphertext", repo / "secrets/hosts" / (args.name + "-monitoring.yaml"))]
    existing = [(kind, path) for kind, path in inputs if path.exists() or path.is_symlink()]
    # Only a standalone, real bootstrap directory can be backed up. Existing
    # ciphertext or a file/symlink still blocks provisioning without a prompt.
    if existing == [("bootstrap_dir", bootstrap_input)] and not bootstrap_input.is_symlink() and bootstrap_input.is_dir():
        with secrets_step("bootstrap backup"):
            if offer_bootstrap_backup(bootstrap, force=getattr(args, "force", False)):
                existing = []
    if existing:
        paths = "; ".join(kind + ": " + str(path) for kind, path in existing)
        raise Error("secrets / credential preflight: refusing to replace existing inputs — " + paths
                    + "; nothing generated or registered by this invocation. Inspect/back up the partial attempt; "
                    "keep administrator SSH/age keys; no implicit retry",
                    details={"stage": "credential preflight", "existing_inputs": [kind for kind, _ in existing]})
    if args.admin_recipient is not None and not re.fullmatch(r"age1[0-9a-z]{58}", args.admin_recipient):
        raise Error("Expected administrator age PUBLIC recipient")
    token = getpass.getpass("Dedicated Telegram bot token (empty disables Telegram): ").strip()
    chat_id = 0
    if token:
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise Error("Invalid Telegram bot token")
        answer = input("Telegram Chat ID (nonzero integer): ").strip()
        if not re.fullmatch(r"-?[0-9]+", answer) or not -(2**63) <= int(answer) < 2**63 or int(answer) == 0:
            raise Error("Telegram Chat ID must be a nonzero signed 64-bit integer")
        chat_id = int(answer)
    with secrets_step("administrator SSH keys"):
        machine["adminKeys"] = prepare_admin_keys(machine, repo)
    with secrets_step("administrator age identity"):
        admin_recipient, admin_identity = prepare_admin_age(args, repo, bootstrap)
    with secrets_step("host SSH key and age recipient"):
        # Persist public keys before registering WARP. A generated SSH pair is kept
        # even if a later provisioning operation fails. This updates the loaded
        # configuration, so it does not need a whole-file replacement prompt.
        write_json(host / "machine.json", machine, overwrite_confirmed=True)
        bootstrap.mkdir(parents=True, mode=0o700)
        keydir = bootstrap / "etc/ssh"
        keydir.mkdir(parents=True, mode=0o700)
        bootstrap_target_permissions(bootstrap)
        key = keydir / "ssh_host_ed25519_key"
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", args.name, "-f", key])
        public = key.with_suffix(".pub").read_text().strip()
        recipient = run(["ssh-to-age"], (public + "\n").encode()).decode().strip()
        if recipient == admin_recipient:
            raise Error("Host and administrator recipients must differ")
    with secrets_step("REALITY credentials"):
        generator = load(SOURCE / "vps-credentials.py", "vps_generator")
        private, public_reality = generator.keypair()
        import secrets
        material = {"private_key": private, "public_key": public_reality, "short_id": secrets.token_hex(8),
                    "clients": {"default": generator.new_client()}}
    with secrets_step("WARP registration/profile"):
        source_connection = read_json(state / "plan.json")["connection"]
        settings, account = register_warp_on_vps(source_connection)
        load_renderer().validate_warp(settings)
    with secrets_step("SOPS encryption and credential metadata"):
        recipients = [admin_recipient, recipient]
        encrypt({"material": json.dumps(material), "warp": json.dumps(settings), "warp_account": account}, secret, recipients)
        if token:
            encrypt({"bot_token": token}, monitoring, recipients)
        machine["chatId"] = chat_id
        write_json(host / "machine.json", machine, overwrite_confirmed=True)
        credentials = {"bootstrap_dir": str(bootstrap), "host_public_key": public,
                       "host_recipient": recipient, "admin_recipient": admin_recipient,
                       "warp_registration_location": "source-VPS"}
        if admin_identity:
            credentials["admin_identity"] = admin_identity
        write_json(state / "credentials.json", credentials, force=getattr(args, "force", False))
    print("Encrypted credentials created; private host key remains outside the checkout.")
    print("Host recipient: " + recipient)
    print(run(["ssh-keygen", "-lf", key.with_suffix(".pub")]).decode().strip())
    print("For later edits, add exactly administrator + host to dedicated .sops.yaml rules.")
    if not token:
        print("Telegram disabled; to enable later, add a SOPS bot_token and set machine.json chatId.")


def copy_source_file(repo, relative, target):
    # Open every component without following links, including during a concurrent
    # replacement. Never dereference a checkout symlink into external state.
    with contextlib.ExitStack() as stack:
        fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        for part in relative.parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        source = stack.enter_context(os.fdopen(fd, "rb"))
        mode = os.fstat(source.fileno()).st_mode
        if not stat.S_ISREG(mode):
            raise Error("Non-regular VPS source input refused")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as output:
            shutil.copyfileobj(source, output)
        destination.chmod(0o755 if mode & 0o111 else 0o644)


@contextlib.contextmanager
def source_ref(repo, output):
    """Stage source before Nix sees any path; never ingest the live checkout.

    Git-selected working-tree files are trusted source (not arbitrary untracked
    files). Generated declarations/ciphertext are the only untracked exception.
    Non-Git exports, symlinks, submodules and conflicted index entries fail closed.
    """
    repo = Path(repo).resolve()
    root = os.fsdecode(run(["git", "-C", repo, "rev-parse", "--show-toplevel"])).strip()
    if Path(root) != repo:
        raise Error("VPS source requires the Git checkout root; no path-flake fallback")
    entries = run(["git", "-C", repo, "ls-files", "--stage", "-z"]).split(b"\0")
    selected = set()
    generated = Path("hosts/generated")
    xdg = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "vps"
    if not xdg.is_absolute():
        raise Error("XDG_STATE_HOME must be absolute")
    # Match vps-state.py:safe_path's lexical normalization without traversing
    # state directories. Equivalent paths containing '..' must exclude state too.
    xdg = Path(os.path.abspath(xdg))
    for entry in filter(None, entries):
        metadata, filename = entry.split(b"\t", 1)
        mode, _oid, stage = metadata.split()
        relative = Path(os.fsdecode(filename))
        if relative.is_absolute() or any(p in ("..", ".git", ".vps.lock", "__pycache__") for p in relative.parts):
            raise Error("Unsafe VPS source path refused")
        # Exclude even force-added legacy/XDG state. Generated inputs are added
        # below by an exact allowlist, independent of Git ignore/staging status.
        if stage != b"0":
            raise Error("Conflicted VPS source input refused")
        if generated in relative.parents or (repo / relative).is_relative_to(xdg):
            continue
        if mode not in (b"100644", b"100755"):
            raise Error("Symlink or submodule VPS source input refused")
        if (repo / relative).exists() or (repo / relative).is_symlink():
            selected.add(relative)
    directory = repo / generated
    try:
        if directory.is_symlink() or (repo / "hosts").is_symlink():
            raise Error("Symlinked generated VPS source refused")
        if directory.exists():
            registry = directory / "inventory.json"
            if registry.exists() or registry.is_symlink():
                selected.add(generated / registry.name)
            plan_files = (*HOST_CONFIG_FILES, "secrets.yaml", "monitoring.yaml",
                          "plan.json", "credentials.json", "checked.json", "install-attempt.json", "verified.json")
            for host in directory.iterdir():
                if host.is_symlink():
                    raise Error("Symlinked generated VPS source refused")
                if not host.is_dir():
                    continue
                names = {p.name for p in host.iterdir()}
                if names.intersection(plan_files) and not registry.is_file():
                    raise Error("Missing generated VPS inventory; restore the registry")
                for name in (*HOST_CONFIG_FILES, "secrets.yaml", "monitoring.yaml"):
                    if name in names:
                        selected.add(generated / host.name / name)
        with tempfile.TemporaryDirectory(prefix="vps-source-") as temporary:
            target = Path(temporary)
            for relative in sorted(selected):
                copy_source_file(repo, relative, target)
            if not (target / "flake.nix").is_file() or not (target / "flake.lock").is_file():
                raise Error("VPS source requires Git-selected flake.nix and flake.lock")
            yield f"path:{target}#{output}"
    except OSError:
        raise Error("Missing/unsafe VPS source input; no live checkout fallback") from None


@contextlib.contextmanager
def ref(repo, name):
    with source_ref(repo, f"nixosConfigurations.{validate_name(name)}.config") as value:
        yield value


def evaluate(repo, name, attribute, raw=False):
    with ref(repo, name) as configuration:
        value = run(["nix", "eval", "--raw" if raw else "--json", configuration + "." + attribute], timeout=300).decode().strip()
    return value if raw else json.loads(value)


def payloads(repo, name, credentials):
    telegram = evaluate(repo, name, "services.xrayMonitoring.telegram.enable")
    host = repo / "hosts/generated" / name
    files = [host / "secrets.yaml"]
    if telegram:
        files.append(host / "monitoring.yaml")
    for path in files:
        recipients = set(re.findall(r"recipient:\s*(age1[0-9a-z]+)", path.read_text()))
        if recipients != {credentials["host_recipient"], credentials["admin_recipient"]}:
            raise Error("Ciphertext recipients do not match this host and administrator")
    decrypt_options = {}
    if credentials.get("admin_identity"):
        private_age_file(Path(credentials["admin_identity"]))
        decrypt_options["env"] = os.environ | {"SOPS_AGE_KEY_FILE": credentials["admin_identity"]}
    values = [json.loads(run(["sops", "decrypt", "--output-type", "json", p], **decrypt_options)) for p in files]
    if telegram and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", values[1].get("bot_token", "")):
        raise Error("Missing or invalid bot token")
    return values


def check(args, repo, state, host):
    credentials = read_json(state / "credentials.json")
    bootstrap_check(credentials, repo)
    before = source_digest(repo, args.name)
    with ref(repo, args.name) as configuration:
        built = run(["nix", "build", configuration + ".system.build.toplevel",
                     configuration + ".system.build.diskoScript", "--no-link", "--print-out-paths"], timeout=3600).decode().splitlines()
    system = evaluate(repo, args.name, "system.build.toplevel.outPath", raw=True)
    disko = evaluate(repo, args.name, "system.build.diskoScript.outPath", raw=True)
    if len(built) != 2 or set(built) != {system, disko} or any(not p.startswith("/nix/store/") for p in built):
        raise Error("Unexpected Nix build results")
    plan_data = read_json(state / "plan.json")
    machine = read_json(host / "machine.json")
    if machine["disk"] != plan_data["identity"]["disk"] or machine["firmware"] != plan_data["identity"]["firmware"]:
        raise Error("Generated boot/disk parameters differ from the reviewed plan; create a new inventory/plan")
    actual_disk = evaluate(repo, args.name, "disko.devices.disk.main.device", raw=True)
    with ref(repo, args.name) as configuration:
        disk_names = json.loads(run(["nix", "eval", "--json", "--apply", "builtins.attrNames",
                                   configuration + ".disko.devices.disk"]).decode())
    if actual_disk != machine["disk"]["name"] or disk_names != ["main"]:
        raise Error("Disko targets differ from the single confirmed disk")
    user = legacy_admin(machine)
    if (not machine["adminKeys"]
            or evaluate(repo, args.name, f"users.users.{user}.openssh.authorizedKeys.keys") != machine["adminKeys"]):
        raise Error("Compiled administrator SSH keys differ from the planned public keys")
    ssh_settings = evaluate(repo, args.name, "services.openssh.settings")
    if (ssh_settings.get("PermitRootLogin") != "no" or ssh_settings.get("PasswordAuthentication") is not False
            or ssh_settings.get("KbdInteractiveAuthentication") is not False
            or ssh_settings.get("AllowUsers") != [user]
            or evaluate(repo, args.name, "services.openssh.ports") != [machine["sshPort"]]):
        raise Error("Compiled SSH policy differs from the planned key-only administrator")
    resolved = evaluate(repo, args.name, "services.resolved.settings.Resolve")
    if (evaluate(repo, args.name, "services.resolved.enable") is not True
            or resolved.get("LLMNR") is not False or resolved.get("MulticastDNS") is not False):
        raise Error("Compiled DNS policy must disable LLMNR and multicast DNS listeners")
    if (evaluate(repo, args.name, "networking.firewall.enable") is not True
            or set(evaluate(repo, args.name, "networking.firewall.allowedTCPPorts")) != {443, machine["sshPort"]}
            or evaluate(repo, args.name, "networking.firewall.allowedUDPPorts") != []):
        raise Error("Compiled firewall policy differs from the two-port VPS profile")
    settings = evaluate(repo, args.name, "services.xray.declarative.settings")
    if settings.get("port") != 443 or "default" not in settings.get("clients", []):
        raise Error("The acceptance profile requires VLESS/443 and the default test client")
    settings["statsPort"] = evaluate(repo, args.name, "services.xray.declarative.statsPort")
    values = payloads(repo, args.name, credentials)
    renderer = load_renderer()
    runtime = renderer.render(settings, json.loads(values[0]["material"]), json.loads(values[0]["warp"]))
    package = evaluate(repo, args.name, "services.xray.package.outPath", raw=True)
    with tempfile.TemporaryDirectory(prefix="vps-check-") as tmp:
        path = Path(tmp) / "config.json"
        write_json(path, runtime)
        run([package + "/bin/xray", "run", "-test", "-config", path], timeout=20)
    if args.vm_test:
        with source_ref(repo, args.name) as flake:
            run(["nix", "run", ANYWHERE, "--", "--flake", flake, "--vm-test"], timeout=3600)
    if source_digest(repo, args.name) != before:
        raise Error("Source changed during check; repeat after reviewing the changes")
    write_json(state / "checked.json", {"source_digest": before, "system": system, "disko": disko, "vm_test": args.vm_test,
               "plan_digest": hashlib.sha256(json.dumps(plan_data, sort_keys=True).encode()).hexdigest(),
               "credentials_digest": hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()},
               force=getattr(args, "force", False))
    print("Local build and secret-backed Xray validation passed. VPS untouched; network tests are still pending.")


def confirm(phrase, force=False):
    if force:
        print("Confirmation: " + phrase + " — automatically accepted (--force)")
        return
    if input("Type exactly " + phrase + ": ") != phrase:
        raise Error("Cancelled; no installation command executed")


def preflight(c, public, *, password=None):
    sni = domain(public["serverName"])
    target = public["target"]
    if not re.fullmatch(r"[A-Za-z0-9.-]+:443", target):
        raise Error("Supported REALITY target must be a DNS name on port 443")
    cmd = shlex.join(["openssl", "s_client", "-connect", target, "-servername", sni, "-tls1_3",
                      "-alpn", "h2", "-verify_hostname", sni, "-verify_return_error"])
    text = remote_run(c, cmd + " 2>&1", b"", timeout=30, password=password).decode(errors="replace")
    if "TLSv1.3" not in text or "ALPN protocol: h2" not in text or "Verify return code: 0 (ok)" not in text:
        raise Error("REALITY target TLS1.3/certificate/ALPN check failed from the target VPS")


def installation_key_directory(state, repo):
    # Never put an unencrypted private key in a checkout or the Nix store.
    private_state = state.resolve()
    if private_state == repo.resolve() / "hosts/generated" / state.name:
        private_root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        private_state = private_root.expanduser().resolve() / "vps" / state.name
    return private_state / "install-ssh"


def installation_key(state, repo, *, create=False):
    # Retain the installation-only identity for automatic resume after interruption.
    directory = installation_key_directory(state, repo)
    for forbidden in (repo.resolve(), Path("/nix/store")):
        if directory == forbidden or forbidden in directory.parents:
            raise Error("Installation SSH key must be stored outside the checkout and Nix store")
    if create and not directory.is_symlink():
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if (directory.is_symlink() or not directory.is_dir()
            or directory.stat().st_mode & 0o077 or directory.stat().st_uid != os.getuid()):
        raise Error("Missing/unsafe installation SSH directory; resume requires the key saved by the initial install")
    # Existing workflows may still use the old filename.
    legacy = directory / "key"
    key = legacy if legacy.exists() or legacy.is_symlink() else directory / (validate_name(state.name) + "-install")
    if create and not key.exists() and not key.is_symlink():
        public = key.with_suffix(".pub")
        marker = directory / "managed.json"
        if public.exists() or public.is_symlink() or marker.exists() or marker.is_symlink():
            raise Error("Incomplete installation SSH key pair; inspect " + str(directory))
        with tempfile.TemporaryDirectory(prefix=".key-", dir=directory) as tmp:
            generated = Path(tmp) / "key"
            run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "vps-install:" + state.name, "-f", generated])
            os.link(generated, key)
            os.link(generated.with_suffix(".pub"), public)
            # Only pairs created by this tool are eligible for automatic cleanup.
            write_json(directory / "managed.json", {"host": state.name,
                       "public_key": " ".join(public.read_text().split()[:2])})
    if (key.is_symlink() or not key.is_file() or not stat.S_ISREG(key.stat().st_mode)
            or key.stat().st_mode & 0o077 or key.stat().st_uid != os.getuid()):
        raise Error("Missing/unsafe installation private SSH key: " + str(key))
    return key


def cleanup_installation_key(state, repo, protected_keys=()):
    directory = installation_key_directory(state, repo)
    # Keyed workflows may keep reports inside the checkout. No managed private
    # key can legitimately have been generated there; do not touch those files.
    if any(directory == p or p in directory.parents for p in (repo.resolve(), Path("/nix/store"))):
        return
    if not directory.exists() and not directory.is_symlink():
        return
    if (directory.is_symlink() or not directory.is_dir()
            or directory.stat().st_mode & 0o077 or directory.stat().st_uid != os.getuid()):
        raise Error("Unsafe installation SSH directory; temporary key not removed")
    marker = directory / "managed.json"
    if not marker.exists() and not marker.is_symlink():
        return  # Never delete an unmarked or user-supplied pair.
    if (marker.is_symlink() or not marker.is_file() or marker.stat().st_mode & 0o077
            or marker.stat().st_uid != os.getuid()):
        raise Error("Unsafe installation SSH ownership marker; temporary key not removed")
    metadata = read_json(marker)
    if metadata.get("host") != state.name or not metadata.get("public_key"):
        raise Error("Installation SSH ownership marker mismatch; temporary key not removed")
    key = directory / (validate_name(state.name) + "-install")
    public = key.with_suffix(".pub")
    if any(key.resolve() == Path(p).expanduser().resolve() for p in protected_keys):
        raise Error("Temporary SSH key is also an administrator key; refusing to remove it")
    for path in (key, public):
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid())):
            raise Error("Unsafe installation SSH pair; temporary key not removed")
    if key.exists():
        if key.stat().st_mode & 0o077:
            raise Error("Unsafe installation private SSH key; temporary key not removed")
        actual = " ".join(run(["ssh-keygen", "-y", "-f", key]).decode().split()[:2])
        if actual != metadata["public_key"]:
            raise Error("Installation private SSH key changed; temporary key not removed")
    if public.exists() and " ".join(public.read_text().split()[:2]) != metadata["public_key"]:
        raise Error("Installation public SSH key changed; temporary key not removed")
    public.unlink(missing_ok=True)
    key.unlink(missing_ok=True)
    marker.unlink()
    if not any(directory.iterdir()):
        directory.rmdir()
    print("Removed temporary installation SSH pair: " + str(key))


def authorize_installation_key(c, state, repo, password):
    key = installation_key(state, repo, create=True)
    public = run(["ssh-keygen", "-y", "-f", key]).strip()
    # Only the public key goes to the current SSH user's authorized_keys.
    # Refuse symlink/non-regular destinations and preserve existing entries.
    script = ("key = " + repr(public) + "\n" + '''import os, stat
home = os.path.expanduser("~")
ssh = os.path.join(home, ".ssh")
os.makedirs(ssh, mode=0o700, exist_ok=True)
directory = os.open(ssh, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    if os.fstat(directory).st_uid != os.getuid():
        raise ValueError("SSH directory owner mismatch")
    os.fchmod(directory, 0o700)
    fd = os.open("authorized_keys", os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                 0o600, dir_fd=directory)
    with os.fdopen(fd, "a+b") as f:
        metadata = os.fstat(f.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_size > 1024**2:
            raise ValueError("Unsafe authorized_keys")
        os.fchmod(f.fileno(), 0o600)
        f.seek(0)
        existing = f.read()
        if key not in existing.splitlines():
            f.write((b"\\n" if existing and not existing.endswith(b"\\n") else b"") + key + b"\\n")
            f.flush()
            os.fsync(f.fileno())
finally:
    os.close(directory)
''').encode()
    print("Authorizing installation-only SSH key on the current VPS; retained for recovery at " + str(key))
    password_inventory(c, "python3 -", script, password=password)
    keyed = c | {"key": str(key)}
    remote_run(keyed, "true", timeout=20)  # Prove key authentication before kexec.
    return keyed


def installer_command(c, phases, extra=None, stores=None):
    # Upstream hardcodes insecure SSH defaults. Repeated SSH options do NOT fix
    # them (OpenSSH uses the first value). Require the authenticated wrapper
    # preflight and a trusted network; document this upstream limitation.
    cmd = ["nix", "run", ANYWHERE, "--", "--target-host", c["user"] + "@" + c["address"],
           "--ssh-port", str(c["port"]), "--post-kexec-ssh-port", "22", "-i", c["key"],
           "--build-on", "local", "--phases", phases]
    if extra:
        cmd += ["--extra-files", str(extra)]
    if stores:
        cmd += ["--store-paths", stores["disko"], stores["system"]]
    return cmd


def bootstrap_target_permissions(directory):
    # nixos-anywhere preserves these modes when extracting --extra-files.
    # sshd reads authorized_keys as the administrator, not as root. Keep the
    # local staging root private, but make target /etc and /etc/ssh traversable.
    for relative in ("etc", "etc/ssh"):
        (directory / relative).chmod(0o755)


def bootstrap_check(credentials, repo):
    directory = Path(credentials["bootstrap_dir"])
    if repo == directory or repo in directory.parents or directory.is_symlink():
        raise Error("Unsafe bootstrap directory")
    expected = {"etc", "etc/ssh", "etc/ssh/ssh_host_ed25519_key", "etc/ssh/ssh_host_ed25519_key.pub"}
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*")}
    if actual != expected or any(p.is_symlink() for p in directory.rglob("*")):
        raise Error("Bootstrap directory contains unexpected files/symlinks")
    key = directory / "etc/ssh/ssh_host_ed25519_key"
    if key.stat().st_mode & 0o077 or directory.stat().st_mode & 0o077:
        raise Error("Private bootstrap key/directory permissions are too broad")
    pub = run(["ssh-keygen", "-y", "-f", key]).decode().strip().split()[:2]
    if pub != credentials["host_public_key"].split()[:2]:
        raise Error("Bootstrap private/public host keys do not match")
    # Normalize legacy staging trees too; do not regenerate any credentials.
    bootstrap_target_permissions(directory)
    return directory


def confirm_install_warnings(force, name, address, disk, previous=None):
    print(f"Install NixOS on {name} ({address}); erase {disk['name']} ({disk['size']} bytes).")
    if previous is not None:
        print("Previous installation attempt: " + str(previous.get("time", "unknown time")))
        print("Previous target: " + json.dumps(previous.get("identity"), sort_keys=True))
        print("WARNING: This repeats installation with disk formatting, not a resume from the point of failure.\n"
              "The marker does not prove installation failed or completed. Inspect the current state through recovery before continuing.")
    print("Installation warnings:\n"
          "- Verify recovery/VNC access before continuing.\n"
          "- Installer TCP/22 must be reachable through the external firewall.\n"
          "- Upstream nixos-anywhere disables SSH host-key checking: authenticated preflight\n"
          "  does not remove its MITM risk. Use a trusted network and accept this limitation.\n"
          "- The disk installation stage destroys all data on the selected disk;\n"
          "  NixOS rollback cannot restore it. The initial kexec stage does not format it.", file=sys.stderr)
    if force:
        print("Installation automatically accepted (--force).", file=sys.stderr)
        return
    if not sys.stdin.isatty():
        raise Error("Installation warnings require an interactive terminal or explicit --force")
    try:
        answer = input(f"Install {name} ({address}) and erase {disk['name']}? [y/N]: ")
    except EOFError:
        raise Error("Cancelled; installation warnings not accepted") from None
    if answer.strip().lower() not in ("y", "yes"):
        raise Error("Cancelled; installation warnings not accepted")


def installer_connection(c, state, public_key):
    # nixos-anywhere's standard kexec image carries /etc/ssh/ssh_host_* from
    # the authenticated source into the installer. Reuse that known identity,
    # not the final NixOS bootstrap key or an unauthenticated ssh-keyscan.
    parts = public_key.split()
    if len(public_key) > 4096 or "\n" in public_key or "\r" in public_key or len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise Error("Expected a single Ed25519 source host key in plan.json; repeat probe/plan")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise Error("Invalid saved source Ed25519 public host key") from None
    prefix = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
    if len(blob) != len(prefix) + 32 or not blob.startswith(prefix):
        raise Error("Invalid saved source Ed25519 public host key")
    # A unique, private file pins this connection; older files are never reused.
    with tempfile.NamedTemporaryFile(mode="w", dir=state, prefix="installer-known-hosts-", delete=False) as f:
        known_hosts = Path(f.name)
        f.write(c["address"] + " " + " ".join(parts[:2]) + "\n")
    print("Installer SSH identity pinned automatically from plan.json: " + str(known_hosts))
    return connection(c["address"], "root", 22, c["key"], known_hosts)


def wait_for_installer(c, timeout=300):
    print("Waiting up to " + str(timeout) + " seconds for authenticated installer SSH...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(ssh_args(c) + ["true"], capture_output=True,
                                    timeout=min(15, max(0.1, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        else:
            if result.returncode == 0:
                return
            if result.returncode != 255:
                raise Error("Authenticated installer SSH readiness command failed; inspect recovery")
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise Error("Installer SSH did not become ready with the saved source host key; inspect recovery and rerun vps-vless install HOST. "
                "The standard kexec installer must preserve the source SSH host key; an unknown key is never accepted automatically")


def validate_install_inventory(fresh, plan_data):
    platform_check(fresh)
    if identity(fresh) != plan_data["identity"]:
        raise Error("Disk/firmware/architecture changed since plan; installation refused")
    if not any(l.get("address") == plan_data["network"]["mac"] for l in fresh["links"]):
        raise Error("Planned network MAC is absent")


def prepare_installer_tools(c, repo, *, password=None, installer=None):
    # Identify the environment without Python first. Do not install packages on
    # the Ubuntu source or an already installed NixOS system.
    environment = remote_run(c, 'set -eu; . /etc/os-release; '
                             'if [ "$ID" = nixos ] && [ "${VARIANT_ID-}" = installer ]; '
                             'then printf "installer\\n"; else printf "source\\n"; fi',
                             timeout=20, password=password)
    if environment == b"source\n":
        return c
    if environment != b"installer\n":
        raise Error("Unable to identify the installation environment; no tools copied")
    if installer is not None and (c["user"], c["port"]) != ("root", 22):
        # A custom source login may still work after kexec; use the independently
        # pinned root:22 connection before copying tools or collecting inventory.
        wait_for_installer(installer)
        return prepare_installer_tools(installer, repo)
    if c["user"] != "root" or not c.get("key"):
        raise Error("Installer tools require root SSH with the saved installation key; inspect recovery")
    tools = os.environ.get("VPS_INSTALLER_TOOLS")
    if not tools:
        with source_ref(repo, "vps-installer-tools") as flake:
            tools = run(["nix", "build", flake, "--no-link", "--print-out-paths"],
                        timeout=600).decode().strip()
    if not re.fullmatch(r"/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+", tools) or not Path(tools).is_dir():
        raise Error("Installer tools are not an available local Nix store path; check the vps runtime")
    # Unlike nixos-anywhere, this transfer uses the same strict, pinned SSH
    # identity as inventory. Shell quoting preserves spaces in key/known_hosts paths.
    env = os.environ | {"NIX_SSHOPTS": shlex.join(ssh_args(c)[1:-1])}
    print("Preparing Python/OpenSSL in the authenticated NixOS installer; no disk formatting.")
    run(["nix", "copy", "--to", "ssh://" + c["user"] + "@" + c["address"], tools], timeout=300, env=env)
    return c | {"installer_tools": tools}


def detect_install_environment(plan_data, state, repo):
    c = plan_data["connection"]
    if not c.get("key"):
        directory = installation_key_directory(state, repo)
        if directory.exists() or directory.is_symlink():
            c = c | {"key": str(installation_key(state, repo))}
    installer = installer_connection(c, state, plan_data["source_host_key"]) if c.get("key") else None
    # With the usual root:22 source, one authenticated inventory distinguishes
    # Ubuntu from the installer. Custom source users/ports need their own probe.
    probe = installer if installer and (c["user"], c["port"]) == ("root", 22) else c
    password = prompt_ssh_password(probe) if not probe.get("key") else None
    auth = {"password": password} if password is not None else {}
    try:
        probe = prepare_installer_tools(probe, repo, installer=installer, **auth)
        fresh = inventory(probe, **auth)
    except Error as error:
        details = error.details
        stderr = details.get("stderr", b"")
        # Only connection/authentication failures on a keyed source allow the
        # root:22 installer fallback. Never retry a password, an untrusted host
        # key, a failed collector or malformed inventory through another login.
        unavailable = (isinstance(stderr, bytes) and any(message in stderr for message in
                       (b"Connection refused", b"Connection timed out", b"No route to host",
                        b"Network is unreachable", b"Permission denied")))
        host_key_failed = isinstance(stderr, bytes) and any(message in stderr for message in
                         (b"Host key verification failed", b"REMOTE HOST IDENTIFICATION HAS CHANGED",
                          b"you have requested strict checking"))
        if (not installer or details.get("tool") != "ssh" or details.get("exit_code") != 255
                or not unavailable or host_key_failed):
            raise
        print("Saved source SSH is unavailable; checking the authenticated installer on TCP/22.")
        wait_for_installer(installer)
        installer = prepare_installer_tools(installer, repo)
        fresh = inventory(installer)
        if fresh["os"] != "nixos" or not fresh["installer"]:
            raise Error("Source SSH is unavailable and TCP/22 is not a NixOS installer; no installation command executed")
        probe, password = installer, None
    if fresh["os"] == "nixos" and fresh["installer"]:
        if installer is None:
            raise Error("NixOS installer detected but the saved installation SSH key is missing; inspect local private state/recovery")
        if (probe["user"], probe["port"]) != ("root", 22):
            wait_for_installer(installer)
            probe = prepare_installer_tools(installer, repo)
            fresh = inventory(probe)
            if fresh["os"] != "nixos" or not fresh["installer"]:
                raise Error("Authenticated root SSH on TCP/22 is not a NixOS installer; no installation command executed")
        print("NixOS installer is already running; skipping kexec.")
        return probe, fresh, None
    return probe, fresh, password


def install(args, repo, state, host):
    plan_data = read_json(state / "plan.json")
    checked = read_json(state / "checked.json")
    credentials = read_json(state / "credentials.json")
    bootstrap = bootstrap_check(credentials, repo)
    machine = read_json(host / "machine.json")
    if machine["disk"] != plan_data["identity"]["disk"] or machine["firmware"] != plan_data["identity"]["firmware"]:
        raise Error("Planned disk/firmware no longer matches generated configuration")
    for filename, value in (("plan", plan_data), ("credentials", credentials)):
        if hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() != checked[filename + "_digest"]:
            raise Error("Plan/credential metadata changed after check; repeat the local check")
    if source_digest(repo, args.name) != checked["source_digest"] or any(not Path(checked[k]).exists() for k in ("system", "disko")):
        raise Error("Check receipt is stale or build was garbage-collected; repeat check")
    attempt = state / "install-attempt.json"
    previous = read_json(attempt) if attempt.exists() else None
    if attempt.exists() and not isinstance(previous, dict):
        raise Error("Invalid installation attempt marker; inspect recovery")
    c = plan_data["connection"]
    disk = plan_data["identity"]["disk"]
    confirm_install_warnings(args.force, args.name, c["address"], disk, previous)
    c, fresh, password = detect_install_environment(plan_data, state, repo)
    validate_install_inventory(fresh, plan_data)
    if fresh["os"] != "ubuntu" and (fresh["os"] != "nixos" or not fresh["installer"]):
        raise Error("Target is neither the Ubuntu source nor a NixOS installer; an installed OS is not reformatted. Use verify for installed NixOS")
    auth = {"password": password} if password is not None else {}
    public = read_json(host / "vless.json")
    preflight(c, public, **auth)
    if fresh["os"] == "ubuntu":
        if fresh["installer"] or fresh.get("kexec_disabled") != "0":
            raise Error("Initial kexec stage supports only Ubuntu with kexec enabled; boot installer using recovery instead")
        if fresh["machine_id"] != plan_data["machine_id"] or fresh["ssh_host_public_key"] != plan_data["source_host_key"]:
            raise Error("Source machine identity changed")
        # Legacy plans may request IPv6 link-local addresses. Compare IPv4
        # inventory with the current, unconditionally enforced IPv6 policy.
        planned_config = machine["network"]["config"]
        planned_network = machine["network"] | {"config": planned_config | {
            "networkConfig": planned_config["networkConfig"] | {
                "IPv6AcceptRA": False, "LinkLocalAddressing": "no"}}}
        if network_plan(fresh, planned_network["mode"], planned_config["networkConfig"].get("DNS", [])) != planned_network:
            raise Error("Source networking changed since plan")
        if not c.get("key"):
            c = authorize_installation_key(c, state, repo, password)
        del password, auth
        installer = installer_connection(c, state, plan_data["source_host_key"])
        print("Booting the NixOS installer; installation will continue automatically.")
        cmd = installer_command(c, "kexec", stores=checked)
        result = subprocess.run(cmd, timeout=1200)
        if result.returncode:
            raise Error(f"Kexec stage failed (exit {result.returncode}); inspect recovery, do not retry blindly",
                        details={"tool": Path(cmd[0]).name, "stage": "kexec", "exit_code": result.returncode,
                                 "timeout_seconds": 1200})
        print("Installer booted; no disk formatting requested by this stage.")
        c = installer
        wait_for_installer(c)
        c = prepare_installer_tools(c, repo)
        fresh = inventory(c)
        validate_install_inventory(fresh, plan_data)
        preflight(c, public)
    if fresh["os"] != "nixos" or not fresh["installer"]:
        raise Error("Destructive stage requires a confirmed NixOS installer, not an installed OS")
    print("DESTROYS ALL DATA on " + disk["name"] + " (" + str(disk["size"]) + " bytes), including Ubuntu.")
    if (source_digest(repo, args.name) != checked["source_digest"]
            or any(not Path(checked[k]).exists() for k in ("system", "disko"))
            or any(read_json(state / (name + ".json")) != value
                   for name, value in (("plan", plan_data), ("credentials", credentials), ("checked", checked)))):
        raise Error("Checked inputs changed or build was garbage-collected during installation; repeat check")
    bootstrap_check(credentials, repo)
    write_json(attempt, {"identity": identity(fresh), "time": dt.datetime.now(dt.timezone.utc).isoformat()}, overwrite_confirmed=True)
    result = subprocess.run(installer_command(c, "disko,install,reboot", bootstrap, checked), timeout=3600)
    if result.returncode:
        raise Error(f"Installation failed (exit {result.returncode}); use recovery, no automatic destructive retry",
                    details={"tool": "nix", "stage": "disko/install/reboot", "exit_code": result.returncode,
                             "timeout_seconds": 3600})
    print("Installation command completed. Run verify; completion is not proof of a working VLESS/WARP service.")
    return 0


def production_connection(plan_data, machine, credentials, state, key=None, *, force=False):
    user = legacy_admin(machine)
    # The source/installer key is never a fallback for the final administrator.
    if key is None:
        files = machine.get("adminKeyFiles")
        if not isinstance(files, list) or not files:
            raise Error("No administrator key file references; use verify HOST --key PATH-TO-PRIVATE-KEY")
        if not isinstance(files[0], str) or not Path(files[0]).is_absolute() or Path(files[0]).suffix != ".pub":
            raise Error("Expected an absolute administrator PUBLIC .pub path in adminKeyFiles")
        key = Path(files[0]).with_suffix("")
    key = Path(key).expanduser()
    if key.suffix == ".pub":
        raise Error("verify --key requires a PRIVATE SSH key, not a .pub file")
    if not key.is_file():
        raise Error("Administrator private SSH key not found: " + str(key)
                    + "; use verify HOST --key PATH-TO-PRIVATE-KEY")
    # Pin the pre-generated final host key, never blindly replace user known_hosts.
    path = state / "installed-known-hosts"
    address, port = plan_data["connection"]["address"], machine["sshPort"]
    public = " ".join(credentials["host_public_key"].split()[:2])
    host_field = address if port == 22 else f"[{address}]:{port}"
    confirm_overwrite(path, force=force)
    path.write_text(f"{host_field} {public}\n")
    path.chmod(0o600)
    return connection(address, user, port, key, path)


def client_config(public, material, address, port):
    return {"log": {"access": "none", "loglevel": "none"},
      "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"auth": "noauth", "udp": False}}],
      "outbounds": [{"protocol": "vless", "settings": {"vnext": [{"address": address, "port": 443,
          "users": [{"id": material["clients"]["default"]["uuid"], "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
          "serverName": public["serverName"], "fingerprint": "chrome", "password": material["public_key"],
          "shortId": material["short_id"], "spiderX": "/"}}}]}


def curl_proxy(port, url):
    return run(["curl", "--disable", "--silent", "--show-error", "--fail", "--noproxy", "",
                "--proxy", f"socks5h://127.0.0.1:{port}", "--connect-timeout", "10", "--max-time", "30", url], timeout=35)


def direct_destination(machine, explicit=None):
    def routed(host):
        return any(host == d or host.endswith("." + d) for d in machine["warpDomains"])
    if explicit:
        parsed = urllib.parse.urlsplit(explicit)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
            raise Error("Direct diagnostic URL must be HTTPS without credentials/query/fragment and return a plain IPv4 address")
        host = domain(parsed.hostname)
        if routed(host):
            raise Error("Direct diagnostic hostname is selected for WARP")
        return explicit, "ip"
    for url, mode in (("https://www.cloudflare.com/cdn-cgi/trace", "trace"), ("https://api.ipify.org", "ip")):
        if not routed(urllib.parse.urlsplit(url).hostname):
            return url, mode
    raise Error("Default direct diagnostics are routed through WARP; supply an unrouted --direct-test-url returning plain IPv4")


def tunnel_test(repo, name, machine, credentials, c, direct_url=None):
    values = payloads(repo, name, credentials)
    material = json.loads(values[0]["material"])
    public = read_json(repo / "hosts/generated" / name / "vless.json")
    if "ipinfo.io" not in machine["warpDomains"]:
        raise Error("Acceptance test requires diagnostic ipinfo.io in WARP domains")
    direct_url, direct_mode = direct_destination(machine, direct_url)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    package = evaluate(repo, name, "services.xray.package.outPath", raw=True)
    with tempfile.TemporaryDirectory(prefix="vps-client-") as tmp:
        path = Path(tmp) / "client.json"
        write_json(path, client_config(public, material, c["address"], port))
        run([package + "/bin/xray", "run", "-test", "-config", path], timeout=20)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "SSL_CERT_FILE", "XRAY_LOCATION_ASSET")}
        process = subprocess.Popen([package + "/bin/xray", "run", "-config", str(path)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise Error("Test VLESS client exited")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise Error("Test client startup timeout")
            direct = curl_proxy(port, direct_url).decode().strip()
            if direct_mode == "trace":
                trace = dict(line.split("=", 1) for line in direct.splitlines() if "=" in line)
                if trace.get("warp") != "off" or trace.get("ip") != c["address"]:
                    raise Error("Direct route did not report the VPS IPv4 address with WARP off")
            elif str(ipaddress.IPv4Address(direct)) != c["address"]:
                raise Error("Direct route did not report the VPS IPv4 address")
            # Isolated WARP probe gives an independent Cloudflare-confirmed exit.
            isolated = run(ssh_args(c) + ["sudo -n /run/current-system/sw/bin/xray-warp-probe"],
                           json.dumps(json.loads(values[0]["warp"])).encode(), timeout=60)
            isolated = json.loads(isolated)
            if isolated.get("warp") not in ("on", "plus"):
                raise Error("Cloudflare did not confirm isolated WARP")
            routed = curl_proxy(port, "https://ipinfo.io/ip").decode().strip()
            ipaddress.ip_address(routed)
            if routed == c["address"] or routed != isolated["ip"]:
                raise Error("Domain route did not match the confirmed WARP exit (or exit changed); investigate, not accepted")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def listener_check(text, ssh_port):
    seen = set()
    for line in text.splitlines():
        words = line.split()
        if len(words) < 4:
            raise Error("Unexpected ss output")
        try:
            addr, port = words[3].rsplit(":", 1)
            port = int(port)
            # ss can show IPv4%iface, [IPv6%iface] or [IPv6]%iface.
            addr = addr.split("%", 1)[0].strip("[]")
            loopback = addr != "*" and ipaddress.ip_address(addr).is_loopback
        except ValueError:
            raise Error("Unexpected ss listener endpoint; expected a numeric IP or wildcard and port") from None
        if not 1 <= port <= 65535:
            raise Error("Unexpected ss listener port")
        if port in PRIVATE_PORTS:
            if addr not in ("127.0.0.1", "::1"):
                raise Error("Monitoring/API listener is exposed outside loopback")
            seen.add(port)
        elif not loopback and port not in (ssh_port, 443):
            raise Error("Unexpected non-loopback TCP listener: " + str(port))
    if not PRIVATE_PORTS <= seen:
        raise Error("Expected loopback listeners are missing")


def send_test(c, resolved=False):
    now = dt.datetime.now(dt.timezone.utc)
    alert = [{"labels": {"alertname": "VPSAcceptanceTest", "node": "acceptance", "observer": "manual-test"},
              "startsAt": (now - dt.timedelta(seconds=10)).isoformat(),
              "endsAt": (now - dt.timedelta(seconds=1) if resolved else now + dt.timedelta(minutes=30)).isoformat()}]
    run(ssh_args(c) + ["curl --disable --fail --silent --show-error --noproxy '*' -H 'Content-Type: application/json' "
                      "--data-binary @- http://127.0.0.1:9093/api/v2/alerts"], json.dumps(alert).encode())


def verify(args, repo, state, host):
    machine = read_json(host / "machine.json")
    plan_data = read_json(state / "plan.json")
    credentials = read_json(state / "credentials.json")
    telegram = evaluate(repo, args.name, "services.xrayMonitoring.telegram.enable")
    if args.send_test_alert and not telegram:
        raise Error("Telegram is disabled; add its SOPS token and Chat ID, rebuild, then test alerts")
    c = production_connection(plan_data, machine, credentials, state, getattr(args, "key", None),
                              force=getattr(args, "force", False))
    run(ssh_args(c) + ["sudo -n true && systemctl is-active xray prometheus grafana alertmanager xray-exporter"], timeout=30)
    listener_check(run(ssh_args(c) + ["ss -H -lnt"]).decode(), machine["sshPort"])
    run(ssh_args(c) + [IPV6_DISABLED_CHECK])
    run(ssh_args(c) + ["sudo -n fail2ban-client status sshd && sudo -n sshd -T -f /etc/ssh/sshd_config | grep -iqx 'permitrootlogin no'"])
    for url in ("http://127.0.0.1:3000/api/health", "http://127.0.0.1:9090/-/ready", "http://127.0.0.1:9093/-/ready"):
        run(ssh_args(c) + ["curl --disable --fail --silent --show-error --noproxy '*' " + shlex.quote(url)])
    query = "http://127.0.0.1:9090/api/v1/query?query=xray_up"
    metrics = json.loads(run(ssh_args(c) + ["curl --disable --fail --silent --noproxy '*' " + shlex.quote(query)]))
    if metrics.get("status") != "success" or not metrics["data"]["result"] or any(r["value"][1] != "1" for r in metrics["data"]["result"]):
        raise Error("Prometheus has no healthy Xray sample; wait for a scrape or investigate")
    for port in PRIVATE_PORTS:
        try:
            with socket.create_connection((c["address"], port), timeout=1):
                raise Error("Private port is reachable externally: " + str(port))
        except (TimeoutError, ConnectionRefusedError, OSError):
            pass
    tunnel_test(repo, args.name, machine, credentials, c, getattr(args, "direct_test_url", None))
    report = {"ssh_host_key": "passed", "services": "passed", "loopback": "passed", "ipv6": "disabled",
              "prometheus": "passed", "vless_direct": "passed", "vless_warp_domain": "passed",
              "telegram": "not_tested" if telegram else "disabled"}
    if args.send_test_alert:
        send_test(c)
        try:
            confirm("TELEGRAM FIRING RECEIVED")
            send_test(c, resolved=True)
            confirm("TELEGRAM RESOLVED RECEIVED")
            report["telegram"] = "operator_confirmed"
        finally:
            send_test(c, resolved=True)
    write_json(state / "verified.json", report, force=getattr(args, "force", False))
    successful = report["telegram"] in ("operator_confirmed", "disabled")
    if successful:
        protected = [c["key"], *(Path(p).with_suffix("") for p in machine.get("adminKeyFiles", []))]
        cleanup_installation_key(state, repo, protected_keys=protected)
    print(json.dumps(report, indent=2))
    return 0 if successful else 2


class DefaultsHelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    def _get_help_string(self, action):
        # Runtime fallbacks are described explicitly instead of displaying None.
        if action.default is None:
            return action.help
        return super()._get_help_string(action)


def parser():
    p = argparse.ArgumentParser(prog="vps-vless", description=__doc__, formatter_class=DefaultsHelpFormatter)
    p.add_argument("--repo", type=Path, default=Path(os.environ.get("HM_FLAKE", os.getcwd())),
                   help="flake checkout; HM_FLAKE if set, otherwise current directory")
    p.add_argument("--state-dir", type=Path, default=None,
                   help="override workflow artifact root (default: XDG_STATE_HOME/vps; existing legacy state stays in REPO/hosts/generated until explicit copy; HOST is appended); custom roots must be outside the checkout")
    p.add_argument("--debug", action="store_true",
                   help="show redacted exception locations; never print tool output, arguments or local variables")
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("probe", help="authenticated SSH inventory; optionally offer a temporary key for passwordless provisioning",
                       formatter_class=DefaultsHelpFormatter)
    q.add_argument("name"); q.add_argument("address", help="current SSH IPv4 address")
    q.add_argument("--user", default="root", help="current SSH login")
    q.add_argument("--port", type=int, default=22, help="current SSH port")
    q.add_argument("--key", type=Path, help="local client private SSH key authorized on the current VPS, independent of future administrator keys (default: prompt for an SSH password)")
    q.add_argument("--known-hosts", type=Path, default=Path.home() / ".ssh/known_hosts", help="verified SSH host keys file")
    q = sub.add_parser("plan", help="generate local host configuration, never overwrite",
                       formatter_class=DefaultsHelpFormatter)
    q.add_argument("name")
    q.add_argument("admin_key", type=public_key_path, nargs="+", metavar="KEY.pub", help="future administrator PUBLIC SSH key path(s), independent of probe --key; checked/created by secrets")
    q.add_argument("--admin-user", type=admin_user, default="admin", help="administrator login in the future NixOS system; independent of probe --user")
    q.add_argument("--network", choices=("auto", "dhcp", "static"), default="auto",
                   help="network configuration mode; auto uses saved inventory: one dynamic IPv4 selects DHCP, only permanent IPv4 addresses select static")
    q.add_argument("--target", help="REALITY TLS domain (default: probe candidates and select interactively)")
    q.add_argument("--ssh-port", type=int, default=22, help="SSH port in the future NixOS system")
    q.add_argument("--dns", action="append", default=[], help="DNS server; repeat to add servers; empty list uses probed DNS")
    q.add_argument("--warp-domain", action="append", default=["ipinfo.io"], help="domain routed through WARP; repeat to append to the default list")
    q = sub.add_parser("secrets", help="new host/VLESS/WARP/Telegram credentials; registers WARP on the source VPS via saved SSH (Cloudflare terms apply)",
                       formatter_class=DefaultsHelpFormatter)
    q.add_argument("name")
    q.add_argument("--bootstrap-dir", type=Path, help="private host key staging directory (default: ~/.ssh/HOST/)")
    q.add_argument("--admin-recipient", help="administrator PUBLIC age recipient (default: reuse an existing host-specific age identity or offer creation)")
    q = sub.add_parser("check", help="local build and secret-backed config validation; no SSH",
                       formatter_class=DefaultsHelpFormatter)
    q.add_argument("name"); q.add_argument("--vm-test", action="store_true", help="also run the NixOS VM test")
    q = sub.add_parser("install", help="detect Ubuntu or an already running NixOS installer and install in one run; reuse saved SSH identity or prompt for source password",
                       formatter_class=DefaultsHelpFormatter)
    q.add_argument("name")
    q.add_argument("--force", action="store_true",
                   help="display and automatically accept the single installation/erasure confirmation, including repeat installs; no installer key input")
    q = sub.add_parser("verify", help="production SSH, services, ports, REALITY and WARP acceptance",
                       formatter_class=DefaultsHelpFormatter)
    q.add_argument("name")
    q.add_argument("--key", type=Path, help="installed NixOS administrator private SSH key (default: first adminKeyFiles path without .pub; legacy plans require --key)")
    q.add_argument("--send-test-alert", action="store_true", help="send Telegram test alerts and confirm receipt interactively")
    q.add_argument("--direct-test-url", help="unrouted HTTPS endpoint returning plain IPv4 (default: https://www.cloudflare.com/cdn-cgi/trace, then https://api.ipify.org; first endpoint not routed through WARP)")
    load(SOURCE / "vps-vless-ops.py", "vps_vless_ops").add_parsers(sub, DefaultsHelpFormatter)
    for name, command in sub.choices.items():
        if name != "install":
            command.add_argument("--force", action="store_true",
                                 help="display and automatically accept overwriting existing generated files")
    return p


def error_report(error, command, host):
    # Do not use format_exc/format_exception: messages, source lines and locals
    # can contain secret parser input or subprocess arguments. Captured tool stderr
    # is deliberately retained only for the private JSON report.
    chain, seen = [], set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 10:
        seen.add(id(current))
        entry = {"type": type(current).__name__, "frames": []}
        if isinstance(current, OSError):
            if isinstance(current.errno, int):
                entry["errno"] = current.errno
                entry["os_reason"] = os.strerror(current.errno)
            # Only known workflow filenames are safe, not arbitrary user input.
            if isinstance(current.filename, str):
                filename = Path(current.filename).name
                if filename in {"machine.json", "vless.json", "probe.json", "plan.json", "credentials.json",
                                "checked.json", "inventory.json", "wgcf-profile.conf", "wgcf-account.toml"}:
                    entry["input_file"] = filename
        if isinstance(current, json.JSONDecodeError):
            entry["input_line"] = current.lineno
            entry["input_column"] = current.colno
        tb = current.__traceback__
        while tb is not None:
            entry["frames"].append({"file": Path(tb.tb_frame.f_code.co_filename).name,
                                    "function": tb.tb_frame.f_code.co_name, "line": tb.tb_lineno})
            tb = tb.tb_next
        entry["frames"] = entry["frames"][-16:]
        if isinstance(current, (Error, subprocess.TimeoutExpired)):
            details = current.details if isinstance(current, Error) else {
                "timeout_seconds": current.timeout, "stderr": current.stderr}
            stderr = details.get("stderr")
            if isinstance(stderr, bytes):
                entry["stderr"] = stderr.decode("utf-8", errors="replace")
            elif isinstance(stderr, str):
                entry["stderr"] = stderr
            # Explicit allowlist: never serialize arbitrary exception attributes.
            for key in ("stage", "tool", "exit_code", "timeout_seconds", "elapsed_seconds",
                        "reason_code", "http_status", "account_file_created", "timed_out", "registration_location"):
                value = details.get(key)
                if isinstance(value, (str, int, float)):
                    entry[key] = value
            existing = details.get("existing_inputs")
            if isinstance(existing, list):
                entry["existing_inputs"] = [kind for kind in existing if kind in
                                            ("host_ciphertext", "monitoring_ciphertext", "bootstrap_dir")]
        chain.append(entry)
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return {"schema": 1, "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "command": command, "host": host if re.fullmatch(r"[a-z][a-z0-9-]{0,62}", host) else "<invalid>",
            "exceptions": chain,
            "redacted": not any("stderr" in entry for entry in chain), "implicit_retry": False}


def report_failure(error, args, state):
    report = error_report(error, args.command, args.name or "")
    if isinstance(error, Error):
        message = str(error)
    else:
        hints = {"ValueError": "invalid input or tool response", "KeyError": "required field missing",
                 "TypeError": "unexpected input type", "EOFError": "interactive input ended",
                 "TimeoutExpired": "tool timed out", "FileNotFoundError": "required file/tool not found",
                 "PermissionError": "permission denied"}
        fallback = "local filesystem/tool error" if isinstance(error, OSError) else "unexpected failure; inspect diagnostic report"
        message = hints.get(type(error).__name__, fallback) + "; details withheld; no implicit retry"
    print("vps-vless: " + message, file=sys.stderr)
    origin = report["exceptions"][-1]
    application_files = {"vps.py", "vps-vless-ops.py", "vps-probe.py", "vps-wgcf.py", "vps-credentials.py", "vps-inputs.py", "vps-state.py", "xray-render.py"}
    location = next((frame for frame in reversed(origin["frames"]) if frame["file"] in application_files),
                    origin["frames"][-1] if origin["frames"] else None)
    diagnostic = origin["type"]
    if "errno" in origin:
        diagnostic += f"; errno {origin['errno']} ({origin['os_reason']})"
    if "input_file" in origin:
        diagnostic += "; input file " + origin["input_file"]
    if "input_line" in origin:
        diagnostic += f"; JSON line {origin['input_line']}, column {origin['input_column']}"
    if location:
        diagnostic += f" at {location['file']}:{location['line']} ({location['function']})"
    print("vps-vless: failure category: " + diagnostic, file=sys.stderr)
    if args.debug:
        print("vps-vless: redacted debug report (no exception messages, source lines, arguments or locals):", file=sys.stderr)
        debug_report = report | {
            "exceptions": [{key: value for key, value in entry.items() if key != "stderr"}
                           for entry in report["exceptions"]],
            "redacted": True}
        print(json.dumps(debug_report, indent=2), file=sys.stderr)
    if state is not None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = state / "errors" / f"{stamp}-{os.getpid()}.json"
        try:
            write_json(path, report, force=getattr(args, "force", False))
        except (OSError, ValueError, TypeError):
            print("vps-vless: could not save diagnostic report; check state directory permissions/free space", file=sys.stderr)
        else:
            warning = "; raw stderr may contain secrets" if not report["redacted"] else ""
            print("vps-vless: diagnostic report (0600" + warning + "): " + str(path), file=sys.stderr)


def main():
    args = parser().parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    state = None
    try:
        ops = load(SOURCE / "vps-vless-ops.py", "vps_vless_ops")
        if args.command in ops.COMMANDS:
            repo = args.repo.expanduser().resolve()
            if not (repo / "flake.nix").is_file():
                raise Error("--repo must be the editable configuration checkout")
            state_root = args.state_dir.expanduser().absolute() if args.state_dir is not None else None
            if args.name:
                validate_name(args.name)
                state = state_path(repo, args.name, state_root)
                state_root = state.parent
            return ops.dispatch(SimpleNamespace(**globals()), args, repo, state_root) or 0
        repo, state, host = host_paths(args)
        # Serialise this checkout even when callers choose different state roots.
        # Remote operations from other computers/checkouts still require one operator.
        lock = repo / ".vps.lock"
        with lock.open("a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Error("Another vps-vless command is running in this checkout")
            action = {"probe": probe, "plan": plan, "secrets": provision, "check": check,
                      "install": install, "verify": verify}[args.command]
            return action(args, repo, state, host) or 0
    except Exception as e:
        report_failure(e, args, state)
        return 1
    except KeyboardInterrupt:
        print("vps-vless: interrupted; check recovery if installer or switch had started", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
