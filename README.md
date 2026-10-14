# nix-config-core

Reusable core under incremental migration. Reviewed groups:

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
| G10 | Mihomo, GUI container/resources, explicit Windows shortcuts and bounded WSL GUI/device profile |
| Pi S1a | Caller-pinned Pi extension recipes and explicit all-profile installer paths |
| Pi S1b | Opt-in HM Pi workspace mechanism and shared installer/helpers; resource selection and personal policy remain caller-owned |
| Pi S1c | Generic extension source bundle, explicit wiki model-policy factory and caller-pinned native checks |
| Agents S2 | Low-level HM/image/launcher runtime with required software/proxy injection and the shared fail-closed gateway |

P3 is not complete. Remaining application implementations, fleet CLI,
deployment export and a VPS host/installer still require migration. Private integration is managed separately by
caller-owned adapters and a pinned core input.
No history from the private repository was copied. Only separately reviewed code
and synthetic tests belong here; ciphertext and device identities remain caller-owned.

## License

Contributor-owned local code and documentation are licensed under the
[MIT License](LICENSE), copyright 2026 nix-config-core contributors.
Third-party code, upstream-derived portions of patches, fetched dependencies
and packaged software retain their respective licenses and notices; the root
license does not relicense them. Package metadata and lockfile license fields
are not a complete transitive license audit. Distribution of built packages or
container images requires separate compliance with their dependency licenses.
Caller-owned private resources are outside this grant.

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

### GUI/proxy applications

`homeManagerModules.mihomo`, `gui-container` and `gui-windows-shortcuts` export
mechanisms with their reviewed local resources. Supply `platform`, `secrets` and
the caller's SOPS HM module alongside them. Mihomo consumes the transitional
`hasSecret`/`secretFile` arguments; `services.mihomo.ruleSeed` must explicitly
reference a pinned directory with `domains_all.lst` and `ipsum.lst` when providers
are enabled. There is no implicit rule-list input or personal subscription.
The subscription decoder retains legacy behaviour, including the public INCY
protocol constant and its unauthenticated CTR decoding; review emitted URLs.

GUI retains the dedicated HOME/shared mounts, private D-Bus/session, rootless
Podman/crun, host Wayland socket and fail-closed gateway. Its gateway has no
DIRECT fallback. `gui-windows-shortcuts` installs an explicitly invoked generator;
it never creates Windows shortcuts during activation. Personal browser profiles
and extra application packages remain caller options.

`homeManagerModules.wsl-gui-device` is a separate bounded integrated profile,
not the existing full WSL profile. Pass the caller's `sopsModule` and
`mihomoRuleSeed` through HM extra arguments, supply username/HOME/stateVersion,
and set `wslProfile.containers.enable` plus `secrets.file`. It enables only GUI/US
proxy and CLI/Playwright, with no Agents, Pi, remote SSH listener or standalone
switching helper. Its sole credential consumer is `mhnodes`; consumer selection
is not decryption isolation. Validate recipients/file contents at the private
export boundary. Missing device sources fail assertions; no identity is copied,
created or decrypted at evaluation/build time.

`lib.mkGuiProxyChecks { nixpkgs; homeManager; sopsModule; ruleSeed; }` uses caller
pins and synthetic non-decryptable data. It exposes `result`, `check`, `activation`,
`image` and `gateway`; these are opt-in, not default full-application builds.
Default `gui-helpers` checks real private D-Bus/session with a mocked Wayland
handshake and launcher/Windows-generator mocks. Host-TUN-dependent launcher
cases may skip in the Nix sandbox; these checks do not prove WSLg/Podman egress.

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

## Pi packages and installers (S1a)

```nix
packages = core.lib.mkPiPackages { inherit pkgs; };
# { askUserQuestion; contextUsage; subagents; webAccess; }
installers = core.lib.piInstallers;
# Those four names plus sharedModels, disableBell and resource installer paths.
```

The caller supplies `pkgs`; no Pi, Home Manager or additional nixpkgs input is
selected. Versions remain questionnaire 2.12.0, context-usage 2.1.0, subagents
0.77.0 and web-access 0.35.0. The questionnaire retains its Herdr state events,
BEL suppression and fullscreen mouse patch. The other packages use their
upstream release code; no reviewer admission/accounting patch is included.
Locked npm runtime dependencies omit host-provided Pi/typebox peers and disable
lifecycle scripts. Source hashes and dependency licenses are retained; this is
not a complete upstream code, vulnerability or transitive licence audit.

Invoke installers explicitly, with a writable `HOME`:

```sh
bash INSTALLER PACKAGE          # askUserQuestion / contextUsage
python3 INSTALLER PACKAGE       # subagents
python3 INSTALLER PACKAGE DEFAULTS_JSON  # webAccess
```

Nothing runs during Nix evaluation. These scripts discover the default agent,
standard profiles and custom profile directories. Each retains its original
link/settings migration rules; they are not a general safe-link abstraction.
Subagents preserves conflicting legacy extensions outside discovery and keeps
writable model settings. Web merges caller routing defaults and keeps unrelated
settings/filters, with private backups outside the store. No routing JSON, model
seed, agent rubric or personal skill is supplied. A missing defaults JSON means
an empty routing overlay (existing installer semantics), not a private fallback.
Do not put credentials in store inputs. Test with an isolated HOME before any
activation. No Agents image/launcher is exported by this slice.

Default `pi-dependencies` guards the exact reviewed recipe/resource edges and
public locked dependency sources; `pi-installers` runs 17 synthetic installer
tests without Pi, credentials, network or package builds (21 default checks in
total after S1a). Real Pi discovery and
selected caller-pinned package builds are separate opt-in verification, not
implied by these default checks.

## Pi workspace mechanism (S1b)

Import `core.homeManagerModules.pi` and set `programs.piWorkspace.enable = true`.
The module uses caller `pkgs`, preserves the existing HM activation DAG and
installs the four pinned packages. It never selects Pi/HM inputs or activates HOME
at evaluation/build time. `lib.piInstallers` additionally exports `sharedModels`,
`disableBell`, `packetReviewer`, `bell`, `statusline`, `compactPrompts`,
`compactTools`, `modelPresets`, `brainstorming`, `lazyCapabilities`, `wikiAsk` and
`contextHandoff`. Host and container callers use the same unchanged scripts;
container lifecycle/composition remains caller-owned.

All `programs.piWorkspace.resources` paths default to null:

- `packetReviewer`, `modelPresets`, `webSearch`, `brainstormingSkill`;
- `extensions.{bell,statusline,compactPrompts,compactTools,modelPresets,lazyCapabilities,wikiAsk,contextHandoff,codexLimits,clear}`.

Null omits that resource's installation, except `webSearch`: the web package is
still registered using an explicit empty routing overlay. Existing installer
semantics (including removal of obsolete provider/searchProvider selectors)
remain unchanged. Model presets require both extension and seed, or neither.
The module supplies no rubric, presets or default extension/skill resources.
Callers may select generic extensions from the S1c bundle below; personal policy
stays outside core. Caller resources entering the store must never contain credentials.
Bell suppression runs only when its caller extension is supplied. With all
resources supplied, the legacy 15 activation entries and eight direct links are
retained. There is no generic linker/discovery refactor or reviewer-policy patch.

`core.lib.mkPiChecks { nixpkgs; homeManager; }` returns `result`, `check`,
`activation` and `fullActivation`. It uses independent synthetic resources and
caller-owned pins, covering enabled/disabled/absent/incomplete resources, the
HM DAG and alternate HOME values. Generation builds do not execute activation.
The HOME-with-spaces case is eval-only: the pinned HM generation contains an
upstream unquoted profile path that fails shell syntax validation at build time.
Default `pi-workspace-installers` runs 12 synthetic helper/installer tests;
`pi-dependencies` guards the nullable resource boundary and source exports.

## Generic Pi extensions (S1c)

`lib.piExtensions` supplies the ten existing resource slots without changing
HM's nullable defaults or the shared installers. Pass selected paths explicitly
through `programs.piWorkspace.resources.extensions` and the same paths to a
container's caller-owned software adapter. The model-presets extension still
requires a caller seed. Bell and statusline remain separate; this slice does
not enable sound, restart Pi or initialize persistent HOME.

The bundle contains bell, statusline, clear, codex-limits, compact-prompts/tools,
model-presets, lazy-capabilities, context-handoff and wiki-ask. No private prompts,
rubrics, model/web seeds, legacy subagents, reviewer patches or audio assets are
included. Context-handoff's project opt-in/trust and lazy-capabilities' installed
resource injection contracts are unchanged. Neither is a sandbox.

The unconfigured wiki-ask bundle requires a project model. To retain a caller's
existing fallback without publishing it as a core preference:

```nix
extensions = core.lib.piExtensions // {
  wikiAsk = core.lib.mkPiWikiAsk {
    inherit pkgs;
    defaultModel = "provider/model";
  };
};
```

The factory returns a five-file extension directory compatible with the existing
installer. Its entry delegates to the same generic implementation through store
paths; no checkout lookup or environment model fallback is added. Project model
takes precedence and missing/invalid selected models fail explicitly. See the
generic [wiki contract](modules/programs/pi/extensions/wiki-ask/README.md) and
[handoff contract](modules/programs/pi/extensions/context-handoff/README.md).
Generic local-code/transitive license clearance is not implied by source guards.

Default `pi-extensions` uses Node 24 source-only mocks and fake workers.
`pi-extension-dependencies` guards the exact source/import/policy boundary, adding
two default checks (24 total). Real Pi and PTY contracts are explicit opt-in:

```nix
core.lib.mkPiExtensionChecks {
  inherit pkgs;
  pi = callerPinnedPi;
}
# { native; tui; }
```

These checks use temporary projects, local fake APIs/workers and synthetic
history, not provider credentials or live Agents containers. Core adds no
Pi/upstream input. compact-prompts still uses a narrow reversible adapter to
Pi's runtime component property; test it on the caller pin rather than updating
the SDK during migration. Native registration is not a TypeScript typecheck.
Source/standalone checks are not publication approval or runtime acceptance.

## Agents low-level runtime (S2)

Import `homeManagerModules.agents-container`, supplying standard HM identity and
caller `pkgs`. Enabling `services.agents-container` requires `software = {
packages; environment; etcFiles; homeInit; }` and `proxy.{port,httpProxy}`.
There is no implicit software import, SOPS selection, Mihomo enablement or
personal resource default. `proxy.listen` must be loopback; `directPort` defaults
to null and enables only explicit proxy opt-in, never a TUN fallback.
`proxy.service` optionally supplies a host unit dependency.

KVM, GPU devices, clipboard, sound and Nix token sharing are disabled by default.
The caller chooses image/container names, timezone, project directory name,
state/shared directories and Podman executable. `shellCommand` defaults to Bash;
`updateInput` defaults to null (explicit caller lock updates, no implicit agent
package input). `secretSources` maps environment
names to absolute runtime file strings or null, not secret values. Validation
lexically normalizes repeated separators, `.` and `..`, rejecting `/nix/store`
and its descendants without reading files. The original runtime strings are
preserved; evaluation does not resolve symlinks or verify their final targets.
The launcher stages only these files on runtime tmpfs and dedicated Nix auth in private state;
images, Docker Env and Podman arguments never receive their contents. Existing
processes retain inherited credentials after rotation. Sound event maps/audio
assets remain host-owned; guests send only event names. Host display, microphone,
Nix store and daemon sockets are not mounted.

`lib.mkAgentsImage { pkgs; lib; username; software; imageName; timeZone;
projectDirectoryName; minimumFreePids ? 256; }` exposes `image` and `root`.
`lib.mkAgentsLauncherText` renders validated technical names; compatibility
adapters can preserve their earlier `runtimeSpec` hash schema without a second
launcher implementation. `lib.agentsRuntimeHelpers` exposes the unchanged
preflight, secret, clipboard, sound and sound-client paths.

`lib.mkAgentsChecks { nixpkgs; homeManager; }` returns synthetic `result`, `check`,
`image`, `gateway`, `launcher` and `brokerLauncher` targets with caller-owned pins.
Default `agents-helpers` and `agents-dependencies` use mock Podman/backends and
temporary HOME/state, bringing default checks to 26. Builds do not activate or
start containers. Preflight is a snapshot, not a reservation or proof of ancestor
limits. The inherited seccomp/AppArmor/unmask allowances for nested workloads
remain a security trade-off; this slice is not a sandbox or publication/license
approval. Production integration and runtime acceptance are separate.

## Checks

```sh
nix flake check --offline --no-write-lock-file --option substitute false path:.
```

The original 15 default checks passed offline after separate preparation of
pinned cache outputs without changing inputs or hashes. G8 adds a sixteenth
`home-profile-dependencies` check; G9 adds `wsl-dependencies`, and G10 adds
`gui-proxy-dependencies`/`gui-helpers`. All 19 default checks passed offline with
substitutions disabled after fetching two pinned dev outputs. The new helper
check ran 35 launcher mocks, four private-session tests and five Windows-generator
tests without skips. Full application builds are not implied. These include boundary/dependency
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
