#!/usr/bin/env python3
"""VLESS-profile operations: local configuration first, explicit remote apply.

Loaded by vps.py so provisioning and operations share tool/error handling.
List reads desired public configuration, not deployed state, and never decrypts.
"""
import contextlib
import fcntl
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.parse
import warnings

COMMANDS = {"list", "client", "telegram", "ssh", "tunnel", "status", "logs", "restart", "apply"}
SERVICES = ("xray", "prometheus", "grafana", "alertmanager", "xray-exporter",
            "prometheus-node-exporter", "prometheus-blackbox-exporter")
UIS = {"grafana": 3000, "prometheus": 9090, "alertmanager": 9093}


def port(value):
    value = int(value)
    if not 1 <= value <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return value


def add_parsers(sub, formatter):
    for name in sorted(COMMANDS):
        q = sub.add_parser(name, formatter_class=formatter, help={
            "list": "list local public VLESS hosts/clients; no secrets or SSH",
            "client": "edit local clients or explicitly export one secret link; no deploy",
            "telegram": "set/disable local Telegram binding; hidden token prompt, no deploy",
            "ssh": "open an administrative SSH session",
            "tunnel": "foreground loopback-only SSH tunnel; Ctrl-C closes it",
            "status": "show profile service states over SSH",
            "logs": "read a service journal over SSH (may contain secrets)",
            "restart": "explicitly restart one allowlisted service after confirmation",
            "apply": "build/validate locally, then explicitly switch NixOS without formatting",
        }[name])
        q.add_argument("name", nargs="?" if name == "list" else None, metavar="HOST")
        if name == "client":
            q.add_argument("action", choices=("add", "remove", "link", "rotate", "sync"))
            q.add_argument("client", nargs="?", help="client name; omit only for sync")
            q.add_argument("--address", help="public IPv4 endpoint override for link (default: saved plan or caller-owned native address source)")
        if name == "telegram":
            q.add_argument("action", choices=("set", "disable"))
            q.add_argument("--chat-id", type=int, help="nonzero signed 64-bit Telegram chat ID (default: prompt)")
        if name == "tunnel":
            q.add_argument("service", choices=tuple(UIS), default="grafana", nargs="?")
            q.add_argument("--local-port", type=port, help="local port (default: service's remote port)")
        if name in {"logs", "restart"}:
            q.add_argument("service", choices=SERVICES, default="xray", nargs="?" if name == "logs" else None)
        if name == "logs":
            q.add_argument("--lines", type=int, default=100, help="last 1..10000 journal lines")
            q.add_argument("--follow", action="store_true", help="follow until Ctrl-C")
        if name == "apply":
            q.add_argument("--check", action="store_true", help="build and secret-backed validation only; no SSH")
        if name in {"ssh", "tunnel", "status", "logs", "restart", "apply"}:
            q.add_argument("--key", type=Path, help="administrator PRIVATE SSH key (default: VPS_VLESS_SSH_KEY or generated adminKeyFiles; required for native hosts)")
            q.add_argument("--known-hosts", type=Path, help="verified known_hosts override (default: generated host key pin or ~/.ssh/known_hosts)")
            q.add_argument("--address", help="current administrative IPv4 endpoint override (default: saved plan or caller-owned native address source)")
            q.add_argument("--user", help="current administrator override for SSH migrations (default: profile adminUser)")
            q.add_argument("--ssh-port", type=port, help="current SSH port override for SSH migrations (default: profile sshPort)")


def regular(v, path, missing=False):
    if path.is_symlink() or (path.exists() and not path.is_file()) or (not missing and not path.is_file()):
        raise v.Error("Missing/unsafe regular configuration file: " + str(path))


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def publish(v, changes, force):
    # Get all replacement approvals before publishing any part of a local edit.
    for path, _ in changes:
        regular(v, path, missing=True)
        v.confirm_overwrite(path, force=force)
    for path, data in changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".vps-vless-", delete=False) as output:
            temp = Path(output.name)
            try:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
                temp.chmod(0o600)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)


class Host:
    def __init__(self, v, repo, name, state_root=None):
        self.v, self.repo, self.name = v, repo, v.validate_name(name)
        generated = repo / "hosts/generated" / name
        native = repo / "hosts" / name
        for directory in (repo / "hosts", generated.parent, generated, native, repo / "secrets", repo / "secrets/hosts"):
            if directory.is_symlink():
                raise v.Error("Host/secret directories must not be symlinks")
        self.generated = (generated / "vless.json").exists() or (generated / "vless.json").is_symlink()
        self.directory = generated if self.generated else native
        self.config = self.directory / "vless.json"
        regular(v, self.config)
        self.public = v.read_json(self.config)
        self.clients = self.public["clients"]
        if (not isinstance(self.clients, list) or not self.clients or
                any(not isinstance(n, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", n) for n in self.clients)
                or len(self.clients) != len(set(self.clients))):
            raise v.Error("Invalid or duplicate VLESS client names")
        v.domain(self.public["serverName"])
        self.secret = (self.directory / "secrets.yaml" if self.generated else
                       repo / "secrets/hosts" / (name + ".yaml"))
        self.monitoring = (self.directory / "monitoring.yaml" if self.generated else
                           repo / "secrets/hosts" / (name + "-monitoring.yaml"))
        self.settings_file = self.directory / ("machine.json" if self.generated else "operations.json")
        self.state = v.state_path(repo, name, state_root)
        self.credentials = {}
        credential_file = self.state / "credentials.json"
        if self.generated and credential_file.exists():
            regular(v, credential_file)
            self.credentials = v.read_json(credential_file)

    def settings(self):
        regular(self.v, self.settings_file)
        return self.v.read_json(self.settings_file)

    def environment(self):
        env = os.environ.copy()
        identity = self.credentials.get("admin_identity")
        if identity and not env.get("SOPS_AGE_KEY_FILE"):
            self.v.private_age_file(Path(identity))
            env["SOPS_AGE_KEY_FILE"] = identity
        return env

    def recipients(self):
        regular(self.v, self.secret)
        recipients = set(re.findall(r"recipient:\s*(age1[0-9a-z]+)", self.secret.read_text()))
        if len(recipients) != 2:
            raise self.v.Error("Host ciphertext must have exactly administrator + host age recipients")
        if self.generated:
            expected = {self.credentials.get("admin_recipient"), self.credentials.get("host_recipient")}
            if recipients != expected:
                raise self.v.Error("Ciphertext recipients do not match this host's credentials.json")
        return sorted(recipients)

    def decrypt(self, path):
        regular(self.v, path)
        return json.loads(self.v.run(["sops", "decrypt", "--output-type", "json", path], env=self.environment()))

    def encrypt(self, payload):
        # Preserve this host's explicit recipients, not an ambient creation rule.
        recipients = self.recipients()
        ciphertext = self.v.run(["sops", "--config", "/dev/null", "encrypt", "--age", ",".join(recipients),
                                "--input-type", "json", "--output-type", "yaml", "/dev/stdin"],
                               json.dumps(payload).encode(), env=self.environment())
        actual = set(re.findall(r"recipient:\s*(age1[0-9a-z]+)", ciphertext.decode()))
        if actual != set(recipients):
            raise self.v.Error("SOPS recipients mismatch; no files written")
        return ciphertext

    def address(self):
        if self.generated:
            regular(self.v, self.state / "plan.json")
            return self.v.read_json(self.state / "plan.json")["connection"]["address"]
        # Caller selects the public Nix data source; no Python helpers come from --repo.
        path = self.repo / self.v.source_policy()["nativeAddressFile"]
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise self.v.Error("Symlinked native address source refused")
        regular(self.v, path)
        expression = f'(import (builtins.toPath {json.dumps(str(path))})).{self.name}.address'
        return json.loads(self.v.run(["nix", "eval", "--json", "--expr", expression]).decode())

    def summary(self):
        return {"host": self.name, "layout": "generated" if self.generated else "native",
                "serverName": self.public["serverName"], "target": self.public["target"],
                "port": self.public.get("port", 443), "clients": self.clients}


def list_hosts(v, args, repo, state_root):
    if args.name:
        value = Host(v, repo, args.name, state_root).summary()
    else:
        for path in (repo / "hosts", repo / "hosts/generated"):
            if path.is_symlink():
                raise v.Error("Host directories must not be symlinks")
        names = {p.parent.name for pattern in ("hosts/*/vless.json", "hosts/generated/*/vless.json")
                 for p in repo.glob(pattern)}
        value = [Host(v, repo, name, state_root).summary() for name in sorted(names)]
    print(json.dumps(value, indent=2))


def confirm(v, phrase, force):
    if force:
        print("Confirmation: " + phrase + " — automatically accepted (--force)")
    elif not os.isatty(0) or input("Type exactly " + phrase + ": ") != phrase:
        raise v.Error("Cancelled; no change made")


def client(v, args, host):
    name = args.client
    if args.action == "sync":
        if name is not None:
            raise v.Error("client sync does not take a client name")
    elif not name or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise v.Error("Expected a lowercase client name (up to 64 characters)")
    if args.action == "remove":
        if name == "default":
            raise v.Error("Keep the default diagnostic client required by profile check/verify")
        if name not in host.clients:
            raise v.Error("Client is not enabled")
        host.clients.remove(name)
        publish(v, [(host.config, json_bytes(host.public))], args.force)
    else:
        host.recipients()
        payload = host.decrypt(host.secret)
        material = json.loads(payload["material"])
        if args.action == "link":
            if name not in host.clients:
                raise v.Error("Client is not enabled")
            query = urllib.parse.urlencode({"encryption": "none", "security": "reality", "type": "tcp",
                "flow": "xtls-rprx-vision", "sni": host.public["serverName"], "fp": "chrome",
                "pbk": material["public_key"], "sid": material["short_id"], "spx": "/"})
            address = str(ipaddress.IPv4Address(args.address or host.address()))
            print(f"vless://{material['clients'][name]['uuid']}@{address}:{port(host.public.get('port', 443))}?{query}"
                  f"#{urllib.parse.quote(host.name + '-' + name)}")
            return
        generator = v.load(v.SOURCE / "vps-credentials.py", "vps_vless_generator")
        config_changed = args.action == "add" and name not in host.clients
        if config_changed:
            host.clients.append(name)
        if args.action == "rotate":
            if name not in host.clients or name not in material["clients"]:
                raise v.Error("Client is not enabled or credentials are missing; run sync first")
            print("Old client link stops working after apply; other clients and REALITY keys stay unchanged.")
            confirm(v, "ROTATE " + host.name + " " + name, args.force)
            material["clients"][name] = generator.new_client()
        else:
            missing = [n for n in host.clients if n not in material["clients"]]
            if not missing and not config_changed:
                print("No missing clients; files unchanged")
                return
            material["clients"].update({n: generator.new_client() for n in missing})
        payload["material"] = json.dumps(material)
        changes = [(host.secret, host.encrypt(payload))]
        if config_changed:
            changes.append((host.config, json_bytes(host.public)))
        publish(v, changes, args.force)
    print("Local client configuration updated. VPS unchanged; run vps-vless apply " + host.name)


def telegram(v, args, host):
    settings = host.settings()
    if args.action == "disable":
        if args.chat_id is not None:
            raise v.Error("telegram disable does not take --chat-id")
        settings["chatId"] = 0
        publish(v, [(host.settings_file, json_bytes(settings))], args.force)
    else:
        chat = args.chat_id if args.chat_id is not None else int(input("Telegram chat ID: "))
        if chat == 0 or not -(2**63) <= chat < 2**63:
            raise v.Error("Telegram chat ID must be a nonzero signed 64-bit integer")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = getpass.getpass("Telegram bot token (empty keeps existing token): ").strip()
        except getpass.GetPassWarning:
            raise v.Error("A terminal with hidden input is required for the Telegram token") from None
        payload = host.decrypt(host.monitoring) if host.monitoring.exists() else {}
        if token:
            payload["bot_token"] = token
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", payload.get("bot_token", "")):
            raise v.Error("Missing/invalid Telegram bot token; nothing written")
        settings["chatId"] = chat
        publish(v, [(host.monitoring, host.encrypt(payload)),
                    (host.settings_file, json_bytes(settings))], args.force)
    print("Local Telegram binding updated. VPS unchanged; run vps-vless apply " + host.name)


@contextlib.contextmanager
def administrative_connection(v, args, host):
    if host.generated:
        settings = host.settings()
        user, ssh_port = v.legacy_admin(settings), settings["sshPort"]
    else:
        user = v.evaluate(host.repo, host.name, "services.vps.adminUser")
        ssh_port = v.evaluate(host.repo, host.name, "services.vps.sshPort")
        settings = {}
    key = args.key or os.environ.get("VPS_VLESS_SSH_KEY")
    if not key and settings.get("adminKeyFiles"):
        public = Path(settings["adminKeyFiles"][0])
        if not public.is_absolute() or public.suffix != ".pub":
            raise v.Error("Expected an absolute administrator PUBLIC .pub reference")
        key = public.with_suffix("")
    if not key:
        raise v.Error("Provide --key PRIVATE-KEY or VPS_VLESS_SSH_KEY for this native/legacy host")
    key = Path(key).expanduser()
    if key.suffix == ".pub" or not key.is_file():
        raise v.Error("Administrator private SSH key is missing or is a .pub file")
    address = str(ipaddress.IPv4Address(args.address or host.address()))
    user, ssh_port = v.admin_user(args.user or user), port(args.ssh_port or ssh_port)
    with tempfile.TemporaryDirectory(prefix="vps-vless-ssh-") as directory:
        known = args.known_hosts
        if known is None and host.credentials.get("host_public_key"):
            fields = host.credentials["host_public_key"].split()
            if len(fields) < 2 or fields[0] != "ssh-ed25519" or not re.fullmatch(r"[A-Za-z0-9+/=]+", fields[1]):
                raise v.Error("Invalid pinned production host key")
            known = Path(directory) / "known_hosts"
            target = address if ssh_port == 22 else f"[{address}]:{ssh_port}"
            known.write_text(target + " " + " ".join(fields[:2]) + "\n")
            known.chmod(0o600)
        known = known or Path.home() / ".ssh/known_hosts"
        yield v.connection(address, user, ssh_port, key, known)


def attached(command):
    # Foreground SSH must not survive interruption/termination of the CLI.
    process = subprocess.Popen(command)
    try:
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def remote_operation(v, args, host):
    if args.command == "logs" and not 1 <= args.lines <= 10000:
        raise v.Error("--lines must be between 1 and 10000")
    if args.command == "restart":
        print("Restart may interrupt proxy/monitoring; it does not apply local configuration.")
        confirm(v, "RESTART " + host.name + " " + args.service, args.force)
    with administrative_connection(v, args, host) as c:
        ssh = v.ssh_args(c)
        if args.command == "ssh":
            return attached(ssh[:-1] + ["-t", ssh[-1]])
        if args.command == "tunnel":
            remote = UIS[args.service]
            local = args.local_port or remote
            print(f"{args.service}: http://127.0.0.1:{local}/\n"
                  "Foreground SSH tunnel; open the URL after SSH connects. Ctrl-C closes it.", flush=True)
            return attached(ssh[:-1] + ["-N", "-T", "-o", "ExitOnForwardFailure=yes",
                            "-o", "ForkAfterAuthentication=no", "-o", "ControlMaster=no",
                            "-o", "ControlPath=none", "-o", "GatewayPorts=no",
                            "-L", f"127.0.0.1:{local}:127.0.0.1:{remote}", ssh[-1]])
        if args.command == "status":
            command = ["systemctl", "--no-pager", "--plain", "list-units", "--all", "--type=service",
                       *[s + ".service" for s in SERVICES]]
        elif args.command == "logs":
            command = ["sudo", "-n", "journalctl", "--no-pager", "-u", args.service + ".service", "-n", str(args.lines)]
            if args.follow:
                command.append("--follow")
        else:
            command = ["sudo", "-n", "systemctl", "restart", args.service + ".service"]
        return attached(ssh + [shlex.join(command)])


def fingerprint(v, host):
    return v.selected_digest(host.repo, host.name, host.directory, host.secret, host.monitoring)


def checked_ciphertext(v, host, attribute, path):
    compiled = v.evaluate(host.repo, host.name, attribute, raw=True)
    if not compiled or Path(compiled).read_bytes() != path.read_bytes():
        raise v.Error("Compiled SOPS input differs from this host's managed ciphertext")


def validate_runtime(v, host):
    if not v.evaluate(host.repo, host.name, "services.vps.enable"):
        raise v.Error("The host does not enable the VLESS VPS profile")
    host.recipients()
    checked_ciphertext(v, host, "services.xray.declarative.secretFile", host.secret)
    payload = host.decrypt(host.secret)
    settings = v.evaluate(host.repo, host.name, "services.xray.declarative.settings")
    if settings.get("clients") != host.clients:
        raise v.Error("Compiled clients differ from this host's vless.json; review Nix overrides")
    for key in ("serverName", "target", "port"):
        default = 443 if key == "port" else None
        if settings.get(key, default) != host.public.get(key, default):
            raise v.Error("Compiled VLESS endpoint differs from this host's vless.json")
    settings["statsPort"] = v.evaluate(host.repo, host.name, "services.xray.declarative.statsPort")
    warp = (json.loads(payload["warp"]) if v.evaluate(host.repo, host.name, "services.xray.declarative.warp.enable") else None)
    runtime = v.load_renderer("vps_vless_renderer").render(
        settings, json.loads(payload["material"]), warp)
    telegram_enabled = v.evaluate(host.repo, host.name, "services.xrayMonitoring.telegram.enable")
    if host.settings_file.exists():
        chat = host.settings().get("chatId", 0)
        if telegram_enabled != (chat != 0) or (telegram_enabled and
                v.evaluate(host.repo, host.name, "services.xrayMonitoring.telegram.chatId") != chat):
            raise v.Error("Compiled Telegram binding differs from local chatId; check ciphertext/imports")
    if telegram_enabled:
        regular(v, host.monitoring)
        checked_ciphertext(v, host, "services.xrayMonitoring.telegram.secretFile", host.monitoring)
        recipients = set(re.findall(r"recipient:\s*(age1[0-9a-z]+)", host.monitoring.read_text()))
        if recipients != set(host.recipients()):
            raise v.Error("Monitoring recipients differ from the host ciphertext")
        token = host.decrypt(host.monitoring).get("bot_token", "")
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise v.Error("Missing/invalid Telegram token")
    package = v.evaluate(host.repo, host.name, "services.xray.package.outPath", raw=True)
    with tempfile.TemporaryDirectory(prefix="vps-vless-check-") as directory:
        path = Path(directory) / "config.json"
        path.write_bytes(json_bytes(runtime))
        path.chmod(0o600)
        v.run([package + "/bin/xray", "run", "-test", "-config", path], timeout=20)


def apply(v, args, host):
    before = fingerprint(v, host)
    print("Building and validating locally; VPS unchanged.", flush=True)
    built = v.run(["nix", "build", v.ref(host.repo, host.name) + ".system.build.toplevel",
                   "--no-link", "--print-out-paths"], timeout=3600).decode().strip()
    if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\s]+", built):
        raise v.Error("Unexpected system build output")
    validate_runtime(v, host)
    if fingerprint(v, host) != before:
        raise v.Error("Configuration changed during build; repeat apply after review")
    print("Local build and secret-backed Xray validation passed; live VLESS/WARP/Telegram remain unverified.")
    if args.check:
        return 0
    print("Switch may interrupt proxy/SSH. No disk formatting, backup or automatic rollback.")
    confirm(v, "APPLY " + host.name, args.force)
    with administrative_connection(v, args, host) as c, tempfile.TemporaryDirectory(prefix="vps-vless-copy-") as directory:
        # Pin nix copy to the same SSH options; use an absolute binary so the
        # PATH wrapper cannot recursively invoke itself.
        ssh_binary = shutil.which("ssh")
        if not ssh_binary:
            raise v.Error("Required tool not found: ssh")
        wrapper = Path(directory) / "ssh"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.join([str(Path(ssh_binary).resolve()), *v.ssh_args(c)[1:-1]]) + ' "$@"\n')
        wrapper.chmod(0o700)
        preflight = ('. /etc/os-release && test "$ID" = nixos && test "$(hostname)" = '
                     + shlex.quote(host.name) + " && sudo -n true")
        v.run(v.ssh_args(c) + [preflight], timeout=20)
        env = os.environ | {"NIX_SSHOPTS": "", "PATH": str(directory) + os.pathsep + os.environ.get("PATH", "")}
        v.run(["nix", "copy", "--no-check-sigs", "--to",
               "ssh-ng://" + c["user"] + "@" + c["address"] + "?remote-program=sudo%20-n%20/run/current-system/sw/bin/nix-daemon",
               built], env=env, timeout=1800)
        if fingerprint(v, host) != before:
            raise v.Error("Configuration changed before switch; closure copied but not activated")
        command = ("sudo -n /run/current-system/sw/bin/nix-env -p /nix/var/nix/profiles/system --set "
                   + shlex.quote(built) + " && sudo -n " + shlex.quote(built + "/bin/switch-to-configuration") + " switch")
        v.run(v.ssh_args(c) + [command], timeout=300)
        v.run(v.ssh_args(c) + ["systemctl is-active xray prometheus grafana alertmanager"], timeout=30)
    print("Generation switched. Verify clients, WARP and Telegram separately; no automatic rollback.")
    return 0


def dispatch(v, args, repo, state_root):
    if args.command == "list":
        return list_hosts(v, args, repo, state_root)
    mutates = args.command in {"telegram", "restart", "apply"} or (args.command == "client" and args.action != "link")
    with contextlib.ExitStack() as stack:
        if mutates:
            lock = stack.enter_context((repo / ".vps.lock").open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise v.Error("Another mutating VPS command is running in this checkout") from None
        # Load mutable inputs only after obtaining the lock, avoiding a stale
        # read followed by overwriting a just-completed invocation.
        host = Host(v, repo, args.name, state_root)
        if args.command == "client":
            return client(v, args, host)
        if args.command == "telegram":
            return telegram(v, args, host)
        if args.command == "apply":
            return apply(v, args, host)
        return remote_operation(v, args, host)
