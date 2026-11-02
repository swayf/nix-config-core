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

COMMANDS = {"list", "client", "telegram", "ssh", "tunnel", "status", "logs", "restart", "apply", "build", "warmup"}
SERVICES = ("xray", "prometheus", "grafana", "alertmanager", "xray-exporter",
            "prometheus-node-exporter", "prometheus-blackbox-exporter")
UIS = {"grafana": 3000, "prometheus": 9090, "alertmanager": 9093}


# Long help per command: description and examples, shown by `vps-vless HOST COMMAND -h`.
DETAILS = {
    "list": ("""\
Print a JSON summary of the desired configuration in the checkout: host,
layout, serverName, REALITY target, port and enabled clients. It reads only
public files, never decrypts secrets and never connects to the VPS, so it
shows what apply would deploy, not what is running now.""",
             ["vps-vless list                 # all hosts in the checkout",
              "vps-vless vps1 list            # one host"]),
    "client": ("""\
Manage VLESS clients in the local configuration. Changes reach the VPS only
after an explicit apply.

actions:
  add NAME     enable a client and generate its credentials
  remove NAME  disable a client (the diagnostic client `default` is kept)
  link NAME    print the vless:// link for an enabled client (decrypts, no write)
  rotate NAME  replace one client's UUID after typed confirmation; the old
               link stops working after apply, other clients stay unchanged
  sync         generate credentials for enabled clients that lack them
  import NAME  probe the client over HTTPS from this computer, then write one
               local client provider file (0600); no SSH and no apply""",
               ["vps-vless vps1 client add alice",
                "vps-vless vps1 client link alice",
                "vps-vless vps1 client link alice --address 203.0.113.5",
                "vps-vless vps1 client rotate alice",
                "vps-vless vps1 client remove alice",
                "vps-vless vps1 client sync",
                "vps-vless vps1 client import alice",
                "vps-vless vps1 apply            # deploy the change"]),
    "telegram": ("""\
Set or disable the local Telegram alert binding. `set` asks for the bot token
with hidden input (empty input keeps the saved token) and stores it encrypted;
the chat ID comes from --chat-id or a prompt. `disable` sets chat ID 0.
The VPS changes only after apply.""",
                 ["vps-vless vps1 telegram set --chat-id -1001234567890",
                  "vps-vless vps1 telegram set     # prompts for chat ID and token",
                  "vps-vless vps1 telegram disable",
                  "vps-vless vps1 apply"]),
    "ssh": ("""\
Open an interactive administrative SSH session with the profile user, port
and pinned host key. Options override the connection for SSH migrations.""",
            ["vps-vless vps1 ssh",
             "vps-vless vps1 ssh --key ~/.ssh/vps1-admin",
             "vps-vless vps1 ssh --address 203.0.113.5 --ssh-port 22"]),
    "tunnel": ("""\
Forward a monitoring web UI from the VPS loopback to 127.0.0.1 on this
computer. It runs in the foreground and Ctrl-C closes it.
UIs: grafana 3000, prometheus 9090, alertmanager 9093.""",
               ["vps-vless vps1 tunnel                     # grafana on http://127.0.0.1:3000/",
                "vps-vless vps1 tunnel prometheus",
                "vps-vless vps1 tunnel alertmanager --local-port 19093"]),
    "status": ("""\
Show systemd states of the profile services over SSH: xray, prometheus,
grafana, alertmanager and exporters. Read only.""",
               ["vps-vless vps1 status"]),
    "logs": ("""\
Print the journal of one allowlisted service over SSH. The journal may
contain secrets, so do not paste it publicly.""",
             ["vps-vless vps1 logs                      # last 100 lines of xray",
              "vps-vless vps1 logs grafana --lines 500",
              "vps-vless vps1 logs xray --follow"]),
    "restart": ("""\
Restart one allowlisted service over SSH after typed confirmation
(RESTART HOST SERVICE). It may interrupt the proxy or monitoring and does
not apply local configuration.""",
                ["vps-vless vps1 restart xray",
                 "vps-vless vps1 restart grafana --force   # skip typed confirmation"]),
    "apply": ("""\
Build the NixOS system locally, validate the Xray configuration with the
secrets, then switch the VPS after typed confirmation (APPLY HOST).
Never formats disks and makes no backup.

If the host flake declares deploy.nodes.HOST, deploy-rs performs the switch
with automatic and magic rollback: a failed activation or a lost SSH
connection returns the previous generation. Otherwise the closure is copied
and switched directly without rollback. Build progress and deploy output go
to the terminal. --check stops after local validation without SSH.""",
              ["vps-vless vps1 apply --check     # build and validate only",
               "vps-vless vps1 apply",
               "vps-vless vps1 apply --key ~/.ssh/vps1-admin"]),
    "build": ("""\
Build the NixOS system and validate the Xray configuration locally, showing
build progress. No SSH; the VPS is unchanged. Use it to fill the Nix store
before apply.""",
              ["vps-vless vps1 build"]),
    "warmup": ("""\
Alias for build: local build and validation with progress. No SSH; the VPS
is unchanged.""",
               ["vps-vless vps1 warmup"]),
}


def examples(lines):
    return "examples:\n" + "\n".join("  " + line for line in lines)


def port(value):
    value = int(value)
    if not 1 <= value <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return value


def add_parsers(sub, formatter):
    for name in sorted(COMMANDS):
        description, sample = DETAILS[name]
        q = sub.add_parser(name, formatter_class=formatter, description=description,
                           epilog=examples(sample), help={
            "list": "list local public VLESS hosts/clients; no secrets or SSH",
            "client": "edit clients, export a link or import a verified local provider; no deploy",
            "telegram": "set/disable local Telegram binding; hidden token prompt, no deploy",
            "ssh": "open an administrative SSH session",
            "tunnel": "foreground loopback-only SSH tunnel; Ctrl-C closes it",
            "status": "show profile service states over SSH",
            "logs": "read a service journal over SSH (may contain secrets)",
            "restart": "explicitly restart one allowlisted service after confirmation",
            "apply": "build/validate locally, then explicitly switch NixOS without formatting",
            "build": "build and validate locally; show progress; VPS unchanged",
            "warmup": "alias for build; build and validate locally; VPS unchanged",
        }[name])
        q.add_argument("name", nargs="?" if name == "list" else None, metavar="HOST")
        if name == "client":
            q.add_argument("action", choices=("add", "remove", "link", "rotate", "sync", "import"))
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
    if args.action == "import":
        return import_client_provider(v, args, host)
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
    print("Local client configuration updated. VPS unchanged; run vps-vless " + host.name + " apply")


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
    print("Local Telegram binding updated. VPS unchanged; run vps-vless " + host.name + " apply")


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


def prepare_client_providers(v, host, *, client=None):
    helper = v.load(v.SOURCE / "vps-client-provider.py", "vps_client_provider")
    selected = {name: binding for name, binding in helper.bindings().items()
                if binding["host"] == host.name and (client is None or binding["client"] == client)}
    if not selected:
        return []
    # Snapshot desired address, not --address (the current administrative endpoint).
    address = host.address()
    try:
        material = json.loads(host.decrypt(host.secret)["material"])
        return [(helper.destination(binding, host.repo),
                 helper.project(binding, host.public, material, address), address)
                for binding in selected.values()]
    except Exception:
        pass
    # Raise OUTSIDE except: diagnostics deliberately traverse __context__, even
    # for `from None`. No secret tool stderr may survive in this error's chain.
    raise v.Error("Managed client preparation failed; details withheld; no providers published")


def publish_client_providers(v, host, prepared):
    if not prepared:
        return
    helper = v.load(v.SOURCE / "vps-client-provider.py", "vps_client_provider")
    if any(address != host.address() for _, _, address in prepared):
        raise v.Error("VPS switched but desired address changed; managed providers NOT published")
    published = False
    try:
        for path, projection, _ in prepared:
            helper.write(path, projection)
        published = True
    except Exception:
        pass
    if not published:
        raise v.Error("VPS switched but managed provider publication failed; no automatic rollback; details withheld")
    print("Client-only Mihomo providers published locally (0600); file watcher consumption remains unverified.")


def import_client_provider(v, args, host):
    """Explicit bootstrap from existing local credentials; never apply to the VPS."""
    if args.address:
        raise v.Error("client import uses the managed endpoint; --address is only supported by client link")
    before = fingerprint(v, host)
    print("Local import probes HTTPS, then replaces one local provider. No SSH, VPS apply or service restart.")
    confirm(v, "IMPORT " + host.name + " " + args.client, args.force)
    prepared = prepare_client_providers(v, host, client=args.client)
    if len(prepared) != 1 or prepared[0][1]["proxies"][0]["type"] != "vless":
        raise v.Error("Exactly one pinned provider for an enabled client is required; no providers published")
    path, projection, address = prepared[0]
    helper = v.load(v.SOURCE / "vps-client-provider.py", "vps_client_provider")
    verified = False
    try:
        helper.probe(projection)
        verified = True
    except Exception:
        pass
    # Discard all secret-bearing tool errors, including nested diagnostic chains.
    if not verified:
        raise v.Error("Local client probe failed; details withheld; provider unchanged; VPS unchanged; no apply attempted")
    if fingerprint(v, host) != before or host.address() != address:
        raise v.Error("Configuration/address changed during probe; provider unchanged; repeat after review")
    published = False
    try:
        helper.write(path, projection)
        published = True
    except Exception:
        pass
    if not published:
        raise v.Error("Local provider publication failed; details withheld; VPS unchanged")
    print("Verified client-only provider imported locally (0600). VPS unchanged; live Mihomo/ALT1 remain unverified.")
    return 0


def deploy_node(v, flake, name):
    """True when the host flake declares deploy.nodes.NAME; apply then uses deploy-rs."""
    expression = ("let f = builtins.getFlake " + json.dumps(flake) + "; in "
                  "f ? deploy && f.deploy ? nodes && f.deploy.nodes ? " + json.dumps(name))
    return v.run(["nix", "eval", "--impure", "--json", "--expr", expression], timeout=300).strip() == b"true"


def copy_and_switch(v, c, built):
    """Legacy path without a deploy-rs node: no automatic rollback."""
    with tempfile.TemporaryDirectory(prefix="vps-vless-copy-") as directory:
        # Pin nix copy to the same SSH options; use an absolute binary so the
        # PATH wrapper cannot recursively invoke itself.
        ssh_binary = shutil.which("ssh")
        if not ssh_binary:
            raise v.Error("Required tool not found: ssh")
        wrapper = Path(directory) / "ssh"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.join([str(Path(ssh_binary).resolve()), *v.ssh_args(c)[1:-1]]) + ' "$@"\n')
        wrapper.chmod(0o700)
        env = os.environ | {"NIX_SSHOPTS": "", "PATH": str(directory) + os.pathsep + os.environ.get("PATH", "")}
        v.run(["nix", "copy", "--no-check-sigs", "--to",
               "ssh-ng://" + c["user"] + "@" + c["address"] + "?remote-program=sudo%20-n%20/run/current-system/sw/bin/nix-daemon",
               built], env=env, timeout=1800, capture_stderr=False)


def deploy_rs(v, c, flake, name):
    """deploy-rs from the host flake's own lock with the pinned administrative SSH options.

    Magic and auto rollback are forced on: a failed activation or a lost SSH
    connection after it returns the previous generation. The node owns timeouts.
    """
    options = v.ssh_args(c)[1:-1]
    if any(not option or any(ch.isspace() for ch in option) for option in options):
        raise v.Error("deploy-rs splits --ssh-opts on whitespace; SSH key/known_hosts paths must not contain it")
    v.run(["nix", "run", "--inputs-from", flake, "deploy-rs#deploy-rs", "--",
           flake + "#" + name, "--skip-checks", "--magic-rollback", "true", "--auto-rollback", "true",
           "--hostname", c["address"], "--ssh-user", c["user"], "--ssh-opts", " ".join(options)],
          timeout=3600, capture_stdout=False, capture_stderr=False)


def warmup(v, args, host):
    before = fingerprint(v, host)
    print("Building and validating locally; VPS unchanged.", flush=True)
    with v.ref(host.repo, host.name) as configuration:
        built = v.run(["nix", "build", configuration + ".system.build.toplevel",
                       "--no-link", "--print-out-paths"],
                      timeout=3600, capture_stdout=True, capture_stderr=False).decode().strip()
        if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\s]+", built):
            raise v.Error("Unexpected system build output")
        print("Built: " + built, flush=True)
        validate_runtime(v, host)
        if fingerprint(v, host) != before:
            raise v.Error("Configuration changed during build; repeat warmup after review")
    print("Local build and secret-backed Xray validation passed; ready for apply.")
    return 0


def apply(v, args, host):
    before = fingerprint(v, host)
    print("Building and validating locally; VPS unchanged.", flush=True)
    # The source snapshot stays open until deploy-rs has evaluated the same flake.
    with v.ref(host.repo, host.name) as configuration:
        built = v.run(["nix", "build", configuration + ".system.build.toplevel",
                       "--no-link", "--print-out-paths"],
                      timeout=3600, capture_stdout=True, capture_stderr=False).decode().strip()
        if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-[^/\s]+", built):
            raise v.Error("Unexpected system build output")
        print("Built: " + built, flush=True)
        validate_runtime(v, host)
        prepared = prepare_client_providers(v, host)
        if fingerprint(v, host) != before:
            raise v.Error("Configuration changed during build; repeat apply after review")
        print("Local build and secret-backed Xray validation passed; live VLESS/WARP/Telegram remain unverified.")
        if args.check:
            return 0
        flake = configuration.split("#", 1)[0]
        managed = deploy_node(v, flake, host.name)
        if managed:
            print("Switch may interrupt proxy/SSH. deploy-rs rolls back a failed activation or lost SSH; "
                  "no disk formatting or backup.")
        else:
            print("Switch may interrupt proxy/SSH. No disk formatting, backup or automatic rollback.")
        confirm(v, "APPLY " + host.name, args.force)
        with administrative_connection(v, args, host) as c:
            preflight = ('. /etc/os-release && test "$ID" = nixos && test "$(hostname)" = '
                         + shlex.quote(host.name) + " && sudo -n true")
            v.run(v.ssh_args(c) + [preflight], timeout=20)
            if managed:
                if fingerprint(v, host) != before or any(address != host.address() for _, _, address in prepared):
                    raise v.Error("Configuration/address changed before deploy; VPS unchanged")
                deploy_rs(v, c, flake, host.name)
            else:
                copy_and_switch(v, c, built)
                if fingerprint(v, host) != before or any(address != host.address() for _, _, address in prepared):
                    raise v.Error("Configuration/address changed before switch; closure copied but not activated")
                command = ("sudo -n /run/current-system/sw/bin/nix-env -p /nix/var/nix/profiles/system --set "
                           + shlex.quote(built) + " && sudo -n " + shlex.quote(built + "/bin/switch-to-configuration") + " switch")
                v.run(v.ssh_args(c) + [command], timeout=300)
            v.run(v.ssh_args(c) + ["systemctl is-active xray prometheus grafana alertmanager"], timeout=30)
    if prepared and fingerprint(v, host) != before:
        raise v.Error("VPS switched but configuration changed; managed providers NOT published")
    publish_client_providers(v, host, prepared)
    print("Generation switched. Verify clients, WARP and Telegram separately"
          + ("; deploy-rs confirmed the SSH connection." if managed else "; no automatic rollback."))
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
        if args.command in {"build", "warmup"}:
            return warmup(v, args, host)
        if args.command == "apply":
            return apply(v, args, host)
        return remote_operation(v, args, host)
