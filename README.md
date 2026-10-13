# nix-config-core

Local, unpublished core under incremental migration. Reviewed groups:

| Group | Exports / scope |
|---|---|
| G1 | `nixosModules.vps-hardening` |
| G2 | `packages.x86_64-linux.xray-render` |
| G3 | `lib.xrayMonitoringRules` |
| G4 | `packages.x86_64-linux.xray-exporter` |
| G5 | Isolated REALITY/exporter runtime tests and synthetic shared fixture |
| G6 | `nixosModules.xray-declarative`, `xray-monitoring`, optional `xray-sops`, partial `vps-xray` profile and optional VM acceptance |
| G7 | `nixosModules.vps-base`: parameterized VPS baseline with caller-owned access and credential sources |
| G8 | HM secret provider and GNOME/KDE/standalone/integrated WSL capability profiles |
| G9 | Caller-pinned CLI tools and Playwright HM modules; NixOS-WSL system mechanism |

P3 is not complete. Remaining application implementations, fleet CLI,
deployment export and a VPS host/installer still require migration. Private integration is managed separately by
caller-owned adapters and a pinned core input.
No history from the private repository was copied; no publication is authorised.

## Runtime module API

The caller chooses package sets, host/user identity, hardware, access policy,
`stateVersion` and secret sources. Modules use standard NixOS arguments; there
are no fleet imports or implicit secrets. For example, in a caller-owned host:

```nix
{
  imports = [
    inputs.core.nixosModules.xray-declarative
    inputs.core.nixosModules.xray-monitoring
  ];
  services.xray.declarative = {
    enable = true;
    materialFile = "/run/credentials-source/xray-material";
    settings = {
      serverName = "target.example";
      target = "target.example:443";
      port = 443;
      tag = "public-inbound";
      clients = [ "alice" ];
    };
  };
  services.xrayMonitoring.enable = true;
}
```

Material and optional `warp.materialFile` must be absolute runtime paths outside
the store. They are passed to rendering through systemd credentials; rendered
configuration stays in `/run` with mode 0600. Public settings must not contain
credentials. Telegram is disabled by default; enabling it requires a nonzero
`telegram.chatId` and runtime `telegram.botTokenFile`.

Monitoring retains loopback-only Prometheus, Grafana, Alertmanager and exporters,
non-resetting Xray counters, a generic dashboard and independent exporter HTTP
while Xray is stopped. No monitoring port is added to the public firewall.
`vps-xray` composes these capabilities with hardening and enables Xray/monitoring
through overridable defaults. It is not a complete VPS host or installer: SSH
users/keys/ports, boot/storage, host identity and material are still mandatory
caller decisions. It does not import desktop or WSL settings.

### VPS baseline profile

`nixosModules.vps-base` adds the VPS access/network/tuning policy to the runtime
modules. It is a complete baseline profile, not a host definition or installer:
package set, host identity, hardware, storage and `stateVersion` remain caller-owned.
For a runtime-only host fragment:

```nix
{
  imports = [ inputs.core.nixosModules.vps-base ];
  services.vps = {
    enable = true;
    adminUser = "alice";
    adminKeys = [ "<caller-owned SSH public key>" ];
    sshPort = 2222;
    settings = {
      serverName = "target.example";
      target = "target.example:443";
      clients = [ "alice" ];
    };
  };
  services.xray.declarative = {
    materialFile = "/run/credentials-source/xray-material";
    warp.enable = false;
  };
}
```

Default administrator is `admin`, not the old personal login. Existing hosts
must explicitly retain their current login and keys before using this profile.
Key-only SSH, no root login and IPv4-only policy are forced constraints, not
ordinary defaults. Tuning uses overridable standard NixOS options; convenience
packages can be omitted through `services.vps.packages = []`.
Public settings are recursively merged with profile defaults; lists replace
rather than append. Child Xray settings, WARP and monitoring remain overridable.
WARP is enabled by default and requires its own runtime material or explicit
SOPS source; disabling it removes its default outbound/routing rule. Disabling
`services.vps.enable` removes this profile's hardening and policy contributions;
it does not disable separately enabled capabilities.

There are no `services.vps.secretFile` or `services.vps.telegram` options in core.
Use canonical child runtime paths or import the explicit SOPS adapter below.
Legacy native/generated declarations require a caller-owned compatibility adapter.

### Explicit SOPS adapter

Select `nixosModules.xray-sops` instead of importing a fleet configuration. Pass
only the caller's pinned SOPS NixOS module through `specialArgs`:

```nix
specialArgs.sopsModule = inputs.sops-nix.nixosModules.sops;
modules = [
  inputs.core.nixosModules.xray-sops
  {
    services.xray.declarative = {
      enable = true;
      secretFile = ./device-xray.yaml;
      # Public settings are supplied as in the runtime example above.
    };
    services.xrayMonitoring = {
      enable = true;
      telegram = {
        enable = true;
        chatId = -1;
        secretFile = ./device-telegram.yaml;
      };
    };
  }
];
```

The adapter consumes `material`, optionally `warp`, and `bot_token`; key paths,
files, recipients and identities belong to the caller. It never ships ciphertext
or decrypts during evaluation. Separate runtime and SOPS sources for the same
credential are rejected. When using SOPS material with a separate runtime WARP
file, explicitly set `warp.useSops = false`. Unlike the old private module, this
choice is not inferred from whether a runtime path happens to be set.
The adapter requires an explicitly supplied SOPS module; runtime-only modules do
not require a SOPS input. Contract tests use a declared-options stub, not real
decryption. The private compatibility check also evaluates the existing pinned
SOPS module against six synthetic runtime/SOPS combinations.

When WARP is enabled, the module supplies `xray-warp-probe`, a stdin-only isolated
userspace probe. It neither registers an account, edits ciphertext nor deploys.
Executing it explicitly contacts the WARP peer and Cloudflare trace endpoint;
core checks use mocks only and never invoke a real WARP probe. Existing personal
command aliases remain the private caller's responsibility.

## Home Manager profiles and secrets

`homeManagerModules` exports `platform`, `desktop`, `secrets` (`secrets-file` is
an alias), `desktop-common`, `gnome`, `kde`, `wsl-common`, `wsl-standalone` and
`wsl-integrated`. Profiles compose capability settings, not personal machines.
The caller must supply application modules implementing Agents, GUI/MAX, Mihomo,
RDP and browser options. KDE additionally needs the caller's pinned Plasma module;
standalone WSL needs its switching-helper module and `nixpkgsInput` special argument.
Identity, `stateVersion`, package sets, hardware, sound resources, credentials,
SSH/RDP destinations and policy resources belong to the caller. Profiles do not
pull private application implementations into core or claim they are public.

Import the caller's pinned SOPS HM module alongside `secrets`. Configure either
`secrets.file` for one device (null means secret-free), or explicit `secrets.files`,
`layout` and `defaultGroup` for a legacy layout. `hasSecret` and `secretFile` remain
module arguments; detection reads only flat top-level key names, never decrypts.
`allowedKeys` selects consumers, not decryption authority. No licence or backup
secret is declared implicitly. The caller may override `sops.age.keyFile`.

The optional `secrets.editor.enable` installs `edit-secrets`; `editor.fileNames`
selects mutable checkout files instead of store copies. `requestedKeys` and SOPS
declarations determine missing-key seeds; existing values are never replaced.
Nested declaration keys are supported by the editor, not by flat-key detection.
No file, recipients or identity is created or migrated automatically.
Integrated WSL rejects enabled containers without a device source, imports no
standalone helper and disables the legacy LAN listener. Its caller supplies
`wslProfile.hostName`. Standalone WSL can explicitly disable containers for a
secret-free bootstrap; enabling application credentials is caller policy.

There are no additional core inputs. Optional real HM/SOPS/Plasma contracts use
caller-owned pins and synthetic capability stubs:

```nix
core.lib.mkHomeManagerChecks {
  nixpkgs = inputs.nixpkgs;
  homeManager = inputs.home-manager;
  sopsModule = inputs.sops-nix.homeManagerModules.sops;
  plasmaModule = inputs.plasma-manager.homeModules.plasma-manager;
}
```

The returned `result` evaluates eleven contract groups and generation derivations;
`check` is a derivation recording successful evaluation. `editor` supplies the
synthetic editor used by `tests/test-secret-editor.py SCRIPT`. These are not full
application builds or real decryption tests. Default `home-profile-dependencies`
and the expanded boundary allowlist guard core imports and resources.

## Applications and NixOS-WSL

`homeManagerModules.cli` and `playwright` use only the caller's `pkgs`; they
neither select an identity nor set global Playwright browser paths. History,
project environments and installed toolchains remain mutable user state.

`nixosModules.wsl` requires import-time `wslModule` and `homeManagerModule` in
NixOS `specialArgs`, from the caller's pinned NixOS-WSL and Home Manager inputs.
Also supply `nixpkgsInput` and `wslHomeModules` (a list of caller home modules).
Optional caller arguments use `home-manager.extraSpecialArgs`. Home modules must implement the integrated
`wslProfile.containers.enable` and `secrets.file` interfaces. User identity, home,
`stateVersion`, application composition, secret files and policy remain caller-owned.

The mechanism retains Podman/crun, subordinate ID ranges, lingering and
permission-only device rules. `workstation.wsl.checkoutPath` defaults to the user's
home plus `/home-manager`; `flakeOutput` defaults to the hostname. Both are
explicitly overridable for delivery; runtime `WSL_FLAKE` takes precedence.
`wsl-host build` does not activate anything; `switch` and `rollback` are explicit
sudo operations. This is not a deployment exporter or a Windows installer.

`lib.mkApplicationChecks { nixpkgs; homeManager; }` and
`lib.mkWslChecks { nixpkgs; homeManager; nixosWsl; }` accept caller-owned pins.
They expose `result` and `check`. WSL also exposes `helper` and a synthetic `system`
for optional builds with a minimal home stub, not the full application profile.

## Packages and pure helper

The stable input is pinned for checks and exported packages. Consumers may use
their own `pkgs.callPackage` with the retained package recipes. The renderer takes
explicit settings/material/output/optional WARP paths and atomically preserves
credentials; its wrapper supplies Python/OpenSSL. It does not decrypt or rotate.

`lib.xrayMonitoringRules { node = "example-vps"; }` is a pure data function, not a
module. Callers supply a label matching `[a-zA-Z0-9_-]+` and serialize with their
package set. Promtool checks 16 rules and seven scenarios / 22 alert expectations
for each of two synthetic node labels.

Exporter 1.0.0 retains the original verified source/vendor hashes and recipe.
Upstream license is MIT; no complete upstream/dependency security audit is
implied. The standalone package enables no service and upstream listens on
`:9550` by default: callers must choose safe listen/API flags explicitly.

## Checks

```sh
nix flake check --offline --no-write-lock-file --option substitute false path:.
```

The original 15 default checks passed offline after separate preparation of
pinned cache outputs without changing inputs or hashes. G8 adds a sixteenth
`home-profile-dependencies` check; application builds are not implied. These include boundary/dependency
guards, module evaluation, mocked probe lifecycle, packaged renderer/exporter,
promtool scenarios and a real loopback REALITY test. G5 actually tested tunnel
traffic, metrics, non-resetting counters, exporter availability with Xray stopped
and recovery without restarting the exporter. Children receive a sanitized
environment. Fixtures contain only synthetic identities and ephemeral keys.

The G7 profile check evaluates 13 condition groups, including default/different
administrators, another HOME, tuning/settings overrides, forced IPv4/key-only
policy, optional WARP, runtime without SOPS, disabled profile, invalid sources,
ports/keys/domains/Telegram, toplevel derivation and four synthetic SOPS-stub
contract combinations. It does not build a system or decrypt secrets. A separate
private harness repeats the original 13 V21 groups against this core export and
compares four combinations to the legacy profile with the existing real SOPS pin;
all four match with an explicitly identical administrator and SSH ports.

The G6 module check evaluates positive/negative runtime and SOPS-contract cases,
caller identity, loopback isolation, credentials, unit dependencies and the
partial profile. Dependency guards cover explicitly reviewed resource edges;
they are not a general dependency graph or permission to publish. Existing check
outputs can be reused by Nix; a successful repeated command does not imply all
test bodies ran again. Cache preparation does not ensure every build source is
available for a from-scratch build.

The expensive three-VM acceptance is separate from default checks:

```nix
core.lib.mkXrayMonitoringTest { pkgs = core.inputs.nixpkgs2605.legacyPackages.x86_64-linux; }
```

It uses restricted networking and synthetic credentials, TLS and Telegram
fixtures. It covers systemd integration, external port isolation, specialisation
rollback, invalid-config rejection, monitoring alerts and persisted state across
restarts/reboot. Building its derivation runs the VM test, not a production
activation. The pinned three-VM test passed offline with substitutions disabled:
502.42 seconds, including reboot, alert firing/resolution, history/silence/key
persistence and specialisation rollback. This does not test real SOPS decryption
or WARP connectivity. Passing isolated tests does not authorise a VPS apply or
publication.
