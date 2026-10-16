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
| Desktop B2 | Explicit VS Code/Build123d/Handy/MAX/font mechanisms, Kitty and shared shell tools; caller pins and private policy stay outside core |
| Agents software API | Explicit caller packages/resources composition; no recipes or personal defaults |
| Agents Radar recipe R2 | Opt-in caller-pinned Radar package with paired-Herdr patch requirement; no activation |
| Agents installers D2/D3 + prompt/wiki/Claude/Pi-shell slices D4 | Opt-in explicit-source seed/link helpers and explicit-policy session/wrapper mechanisms; no personal payloads, permission defaults or recipes |

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

### Weston patch context notice

The upstream-derived context in
[`weston-xwayland-unframed.patch`](modules/home-manager/desktop/max/weston-xwayland-unframed.patch)
comes from Weston's `xwayland/window-manager.c`. The local patch changes are
contributor-owned; the upstream context retains the following file-specific
notice, verified against
[Weston 13.0.0](https://gitlab.freedesktop.org/wayland/weston/-/blob/13.0.0/xwayland/window-manager.c).
This source notice is not a license audit of an Ubuntu/MAX image or its dependencies.
Retain it when copying the upstream-derived patch context separately.

```text
Copyright © 2011 Intel Corporation

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice (including the
next paragraph) shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT.  IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Desktop and shell API (local B2)

`lib.desktopModules` contains module factories, applied to explicit policy before
Home Manager applies its ordinary `config/lib/pkgs` arguments:

```nix
imports = [
  (core.lib.desktopModules.vscode { inherit httpProxy; })
  (core.lib.desktopModules.build123d {
    inherit pyprojectInputs workspaceRoot viewerHash;
  })
  (core.lib.desktopModules.handy { inherit httpProxy xkbLayout; })
  (core.lib.desktopModules.max { inherit version source containerfile; })
  (core.lib.desktopModules.msWindowsFonts {
    inherit defaultFiles defaultOutputHash iso version;
  })
  core.homeManagerModules.kitty
  core.homeManagerModules.shell-hm
  core.homeManagerModules.shell-zsh
  core.homeManagerModules.shell-colors
  core.homeManagerModules.shell-aliases
];
```

`pyprojectInputs` contains the caller's `uv2nix`, `pyproject-nix` and
`pyproject-build-systems` inputs. The workspace/lock, VSIX hash, MAX payload and
Containerfile (including its base pin/timezone), proprietary font files/hash/ISO
selection, proxy and keyboard policy remain caller-owned. No package set is
imported implicitly. VS Code and Handy expect `desktop.environment`; VS Code's
workspace-relative Nix options expression also uses `desktop.homeOutput`.

`lib.mkDesktopShellTools { inherit pkgs lib environment nixodooFlake; }` returns
clip, preview, pdf_opti, snap-clean, nxd and vless-qr packages without executing
them. `shell-hm` declares `programs.hm.profile/flakePath`; the caller selects its
profile. Personal aliases, SSH/VPS wrappers and nxd completion policy stay outside
these modules. Importing modules does not run Podman, reset a live editor, inject
input, configure root-owned uinput, or enable GNOME extensions automatically.

`lib.mkDesktopChecks { inherit pkgs; }` and `checks.x86_64-linux.desktop-contracts`
run synthetic HOME/clipboard/focus contracts. Native direct tests passed locally;
selected caller-package builds and image/runtime acceptance remain unverified
when their build closure is unavailable. A synthetic test or fixed-output reuse
is not evidence of redistribution rights for fonts/MAX/extensions.

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

### Explicit software factory

`lib.mkAgentsSoftware { pkgs; packages; environment; resources; homeInit; }`
returns exactly `{ packages; environment; etcFiles; homeInit; }` for the runtime
above. All five arguments are required, including explicit empty lists/sets/text.
It uses the caller's `pkgs.lib`, preserves package order/duplicates and string
contexts, and selects no package set or package inventory. For example:

```nix
software = core.lib.mkAgentsSoftware {
  inherit pkgs;
  packages = [ pkgs.hello ];
  environment.FIXTURE_ONLY = "example";
  resources = {
    "agents/example/settings.json" = { text = builtins.toJSON { enabled = false; }; };
    "agents/example/prompt.md" = { source = ./example-prompt.md; };
  };
  homeInit = ""; # Explicit caller shell text; never executed during evaluation.
};
```

`resources` maps safe relative file names **below `agents/`** to exactly one of
`{ text = <string>; }` or `{ source = <Nix file path>; }`. It is a text-file API,
not a directory copier, binary asset installer or runtime path resolver. Strings
are not accepted as `source`; callers can explicitly read generated resources
into `text` when needed. Sources are read during evaluation and must exist;
missing inputs, directory sources, unknown entry fields, unsafe names and
file/directory overlaps fail instead of searching a checkout or choosing a
fallback. Empty inputs produce empty output without inspecting HOME.

Projects, prompts, wiki instructions, profile lists, presets, reviewer rubrics,
preferences, shell flags and all seeding/linking policy belong to the caller.
There are no semantic resource roles with implicit content. Runtime/shared paths
and installer arguments must appear explicitly in caller `homeInit`; this API
alone neither installs resources into HOME nor migrates existing state. The
namespace restriction prevents this factory from replacing runtime-owned
account/network files; the existing raw runtime software API is unchanged.
Environment names must be ENV identifiers and values must be strings. Packages
must be derivations. All inputs are public store content: **never supply secrets**.

Run `python3 tests/test-agents-software.py` (or pass `--nixpkgs /explicit/source`)
and `python3 tests/test-agents-software-dependencies.py`. The former runs offline
Nix evaluations in a temporary local evaluator store and HOME; it does not build
packages or execute `homeInit`. Default `agents-software` evaluates the native
contracts in `tests/agents-software.nix` without a nested Nix CLI; Python additionally
covers filesystem errors, omitted/unknown arguments and a copied factory.
`agents-software-dependencies` wraps the bounded source guards. These checks do
not establish production parity, source/license acceptance or real
auth/session/runtime behavior.

### Explicit Agents package mechanisms (B1)

`lib.mkAgentsPackages { pkgs; sources; herdrResources; zcodeLock; }` returns
`herdr`, `plus`, `navigator`, `reviewr`, `fileViewer`, `annotate`, `browser`,
`code`, `wiki`, `zcode` and `microsandbox`. All inputs are required. It imports
no package set and fetches nothing implicitly: `sources` contains caller-owned
fetched derivations, not paths discovered from a checkout. The exact source
attribute names are listed in `modules/home-manager/agents-container/packages.nix`.
`herdrResources` supplies the four named patches and two Rust modules referenced
by `packages/herdr.nix`; `zcodeLock` is an explicit npm lock file. Versions,
modified Electron behavior, backend/native firmware ABI, hooks and wrappers are
preserved. A caller must keep Herdr's group/status patches paired with Radar.
This API does not run installers or assert upstream payload terms/correspondence.

`lib.mkAgentsNoticeBundle { pkgs; package; manifest; texts; }` transports a
caller-owned set into `out/share/licenses/PACKAGE/third-party` and
`source/third-party-notices/PACKAGE`. Add its `out` to software packages to use
an image root's existing `/share` transport. The manifest schema is version 1:
`files` is a nonempty list of `{ path; stored; bytes; sha256; }` records. `path`
is the destination, `stored` a relative file under `texts`. Metadata and manifest
bytes are preserved, never rewritten as legal approval. Validation rejects
unsafe/overlapping/duplicate paths, symlink inputs, missing/corrupt texts and
existing output directories before writing either layout. The API copies only
explicit files, not an inferred dependency graph. Completeness, binary linkage,
redistribution and source-availability gates remain caller responsibilities.
`agents-notices` tests byte transport using synthetic inputs only.

### Opt-in Radar package (R2)

`lib.mkAgentsRadarPackage { pkgs; }` packages pinned Radar 1.3.20 with its complete
relative resource layout, fonts/notices and group/status patches. It uses only
caller `pkgs` and does not install or start a plugin. The caller must supply
Herdr with the paired `group_id`/agent-groups **and** `radar_status`/sidebar-status
patches, plus runtime Node and fontconfig; this is not stock-Herdr compatibility.
Personal themes, plugin config and inventory stay caller-owned.

`tests/agents-radar-package.nix { pkgs; }` (default `agents-radar-package` check)
builds the recipe with `node --test` and `node tools/check.js` in synthetic
HOME/XDG paths with unavailable real Herdr/socket, then checks both TTF structures,
checksums and unchanged font/notice bytes. Font rebuild, brand/license acceptance,
real rendering/state behavior and publication remain separate gates.

### Explicit Agents installers (D2/D3, slices D4 and D5)

`lib.agentsInstallers` exports nine source paths, not activation hooks:

```sh
python3 HERDR_NAVIGATION POLICY_JSON ROOT CONFIG SOURCES...
python3 HERDR_RADAR POLICY_JSON CONFIG PLUGIN_CONFIG SOURCE
python3 HERDR_REVIEW_PLUGINS POLICY_JSON CONFIG SOURCES...
python3 HERDR_PROJECTS SOURCE --config-dir WRITABLE_PLUS_CONFIG
python3 PI_PROMPTS PROFILES_DIR SHARED_DIR FALLBACK_DIR
python3 PI_SHELL POLICY_JSON ABSOLUTE_HOME shell
python3 PI_SHELL POLICY_JSON ABSOLUTE_HOME pi
python3 PI_SHELL POLICY_JSON ABSOLUTE_HOME wrappers
python3 TERMINAL_BROWSER PACKAGED_PLUGIN_ROOT
bash WIKI_INIT PROFILE ASSETS QMD_SKILL INSTRUCTIONS CONFIG_DIR LEGACY_CONFIG_DIR QMD_DIR CONFIG_SEED INDEX_SEED
python3 CLAUDE_PROFILE POLICY_JSON install
python3 CLAUDE_PROFILE POLICY_JSON record       # SessionStart JSON on stdin
python3 CLAUDE_PROFILE POLICY_JSON run ARGS...
```

`HERDR_NAVIGATION` is `core.lib.agentsInstallers.herdrNavigation` (Python 3.11+).
It accepts exactly these policy fields; this is synthetic data, not defaults:

```json
{
  "plugins": ["fixture.chooser"],
  "directories": ["plugin-data/chooser/items"],
  "seeds": [{"path": "plugin-data/chooser/config.toml", "text": "[picker]\nauto = false\n"}],
  "bindings": [{"key": "prefix+x", "action": "fixture.chooser.open", "description": "Chooser"}],
  "backup_suffix": ".before-fixture",
  "binding_comment": "# Fixture shortcuts."
}
```

`ROOT` and the main TOML `CONFIG` are explicit paths; relative paths become
absolute against cwd without resolving symlinks. There is no HOME/XDG lookup
or bundled plugin ids, shortcuts, directories or seed content. Child
paths are relative to ROOT with no `..`. Supply exactly one absolute source argument
per plugin, in policy order; sources stay separate from JSON to retain caller Nix
package dependency contexts without embedding derivations in `builtins.toFile`. Policy and
seed/main TOML are validated before external calls. Each plugin is linked offline
only when its registered root changes, preserving explicit disables. Existing
seed files and symlinks (including dangling ones) are preserved. Missing shortcuts
are appended only for enabled plugins, without occupying a configured key or
replacing an action's remapped shortcut. A main-config symlink remains a symlink;
its resolved target is backed up once (0600), then atomically replaced with the
original permissions. Missing/dangling main configs are not repaired. Only an
actual binding change queries status and reloads an already-running server.
No download, plugin restart, server start/stop or seed-command execution occurs.
The caller must supply a compatible `herdr` executable on PATH. Tests mock Herdr.
Trusted parent symlinks are followed; this is not a sandbox or transaction across
all outputs, and concurrent invocations are not locked. Real linking/reload is
mutable runtime work requiring separate operator permission.

`HERDR_RADAR` is `core.lib.agentsInstallers.herdrRadar` (Python 3.11+).
All eight policy fields are mandatory; this example is synthetic, not defaults:

```json
{
  "plugin": "fixture.panel",
  "literal_tables": ["layout"],
  "main_values": [{"table": "palette", "values": [{"key": "style", "value": "\"fixture\""}]}],
  "plugin_seed": "variant = \"plain\"\n",
  "plugin_values": [{"key": "follow", "value": "false"}],
  "backup_suffix": ".before-fixture",
  "actions": {"stop": "halt", "configure": "apply", "start": "begin"}
}
```

Supply distinct explicit main/plugin config paths (relative to cwd allowed) and
an absolute packaged source. No HOME/XDG discovery, presentation defaults, seed
payloads or plugin/action ids are bundled. Table names are bare dotted names;
owned keys are bare names with single-line TOML value text. Ordered lists retain
caller key order and exact compatibility bytes. `literal_tables` requires literal
uncommented headers; conflicting inline/dotted/commented tables are refused,
not silently rewritten. Policy and both resulting TOML files are validated before
writes or external calls. Empty/missing plugin config uses the caller seed;
owned keys are updated while unowned content is kept. Write through valid
symlinks without replacing them or changing existing target modes; refuse
dangling config links. Before modifying nonempty files, save a link-local once
backup with the caller suffix (0600). Existing backups are not overwritten.
Link offline only on root changes, preserving explicit disables. Only for an
enabled plugin and already-running server: after config changes or an existing
plugin upgrade, invoke stop, configure, then start; otherwise invoke start only.
Each action waits for its matching log to succeed (60-second timeout, 0.2-second
poll); failure/timeout prevents later actions. No server/pane restart or download
occurs. The caller supplies compatible Herdr on PATH. Parent/backup symlinks are
trusted; no locking, atomic two-file transaction or rollback is provided. A later
unchanged run starts only, so a failed configure is not automatically retried.
Live linking/configuration/plugin actions require separate runtime permission.

`HERDR_REVIEW_PLUGINS` is `core.lib.agentsInstallers.herdrReviewPlugins`
(Python 3.11+). Its explicit policy has exactly five mandatory fields, for example:

```json
{
  "plugins": ["fixture.reader"],
  "defaults": [{"plugin": "fixture.reader", "text": "updates = false\n"}],
  "bindings": [{"key": "prefix+x", "action": "fixture.reader.open", "description": "Reader"}],
  "backup_suffix": ".before-fixture",
  "binding_comment": "# Fixture shortcuts."
}
```

There are no bundled ids, bindings, themes or config texts and no HOME/XDG lookup.
Supply an explicit main `CONFIG` path (relative to cwd if not absolute) and one
absolute source per plugin, in policy order. Sources stay outside the JSON to
preserve caller Nix package contexts. Policy, seed TOML and existing main TOML
are validated before external calls or changes. Linking, disables, occupied/remapped
shortcuts, main-config links, once-backup permissions and missing/dangling config
handling follow the Navigation contract above. For each supplied default, query
`herdr plugin config-dir ID` and seed `config.toml` only if absent and not a symlink,
including for disabled plugins. Existing files and valid/dangling links are kept.
Only actual binding changes run `herdr config check`, then status and reload of
an already-running server; a failed check stops before status/reload. This is not
a transaction: links/seeds/bindings already written are not rolled back. A later
run with no new bindings does not retry that check/reload. The compatible Herdr
executable and trusted config-dir responses are caller-owned. No plugin actions
(including capture/paste/send), downloads or server/pane start/stop/restart occur.
Parent symlinks remain trusted, with no concurrency lock or runtime acceptance.

`HERDR_PROJECTS` is `core.lib.agentsInstallers.herdrProjects`. Its required
`SOURCE` supplies `projects/*.toml` and `worktrees/*.toml`; no templates are
bundled or discovered in HOME. All TOML is parsed before writing. Files are
published exclusively as writable copies; existing files and symlinks are kept.
The compatibility marker `.workspace-templates-v1` preserves later edits and
deletions on repeat runs. If `--config-dir` is omitted, the helper asks the
caller-provided `herdr plugin config-dir cloudmanic.herdr-plus`. Template
commands are data and are never executed during seeding.

`TERMINAL_BROWSER` is `core.lib.agentsInstallers.terminalBrowser`. It queries
`herdr plugin list --json` and links the explicit source for
`zenbu-labs.terminal-browser`, only when its registered root differs. An explicit
user disable is preserved on upgrades. There is no download, browser installation,
server start/stop/restart or search for a source. The caller supplies compatible
Herdr and Python 3.11+; a real Herdr link is an explicit mutable-state operation,
not performed by evaluation or any default check.

`PI_PROMPTS` is `core.lib.agentsInstallers.piPrompts`. All three directories are
required: existing writable profile directories, preferred shared appends and
caller-selected fallback appends. Each source uses `<profile>/APPEND_SYSTEM.md`;
no profile names, prompt content, HOME lookup, environment override or image/checkout
path is supplied. To omit fallback content, explicitly select an absent/empty
directory. Relative arguments become absolute against the invocation directory,
without resolving symlinks. Only existing profiles are visited; no profile is
created. Shared regular files take precedence, otherwise a regular fallback is
used. Only links whose exact stored pathname matches one of those two current
sources may be switched. Local files/directories and user links, even dangling
ones, remain untouched. If both sources are missing, existing links remain as-is.
Changing source roots does not claim links to earlier roots. Atomic source saves
remain visible without rerunning the installer. The shared-first/fallback rule
and `APPEND_SYSTEM.md` filename retain the previous mechanism; source selection,
profile layout and all personal policy/content remain caller-owned.

`PI_SHELL` is `core.lib.agentsInstallers.piShell`. It requires Python 3 and an
explicit JSON policy; the home argument must be absolute. All policy fields
are mandatory, with no HOME/XDG discovery or bundled paths/profile defaults.
The three modes let the caller retain initialization order around other installers.
A complete synthetic policy is:

```json
{
  "directories": ["bin", "config", "state"],
  "bashrc": {
    "destination": "rc", "source": "/tmp/fixture/interactive",
    "marker": "source fixture-interactive", "append": "\\nsource fixture-interactive\\n"
  },
  "bash_profile": {
    "destination": "login", "source": "/tmp/fixture/login",
    "marker": "fixture-rc", "append": "\\n# fixture-rc\\n"
  },
  "links": [{"destination": "config/prompt", "source": "/tmp/fixture/prompt"}],
  "pi": {
    "wrapper": "bin/pi", "marker": "# Managed fixture wrapper.",
    "executable": "/tmp/fixture/client", "interpreter": "/bin/sh",
    "profiles_directory": "profiles", "legacy_agent_directory": "legacy",
    "default_profile": "discussion", "profiles": ["discussion", "builder"],
    "settings_source": "/tmp/fixture/profile-seeds",
    "clipboard_socket": "/tmp/fixture/bridge.sock", "clipboard_display": ":fixture",
    "profile_wrappers": {"bin/pi-build": "builder"},
    "legacy_wrapper": {"destination": "bin/pi-old", "content": "#!/bin/sh\\n# old fixture\\nexec pi\\n"}
  }
}
```

Home-relative destinations reject absolute/empty/dot components; sources and
executables must be absolute. Profile names are single components; the default
and alias profiles must be in the explicit seed list. Missing/extra fields,
invalid types and multiline/non-comment wrapper markers fail before mutation.
Seeds and append strings are data, not executed by the installer.

The `shell` mode creates the supplied home directories, links absent shell
startup files and appends caller text to regular startup files when markers are
missing (exact line for rc, substring for login). Rc symlinks are preserved.
The login file and extra seed links retain the legacy dangling-link error rather
than repairing or replacing the link; existing login-file symlinks may be appended
through. Source links are allowed to be dangling.

The `pi` mode upgrades only an absent wrapper or one containing the exact managed
marker line, preserving unmarked existing wrappers. It retains the old write-through
behavior for managed/dangling wrapper symlinks. The generated wrapper preserves
argv/exit status and explicit `PI_CODING_AGENT_DIR`; otherwise the first
`--session VALUE` before `--` restores an existing named profile or the legacy
agent directory. Unknown/missing paths and picker/continue arguments use the
caller default. Session files are never read. Clipboard DISPLAY is set only when
both display variables are empty and the supplied socket exists. All path values
are shell-quoted, and generated wrappers follow their runtime HOME.

Each explicit profile gets `extensions`, `skills`, `prompts`, `themes` and a
writable `settings.json` copied once from `settings_source/<profile>/settings.json`.
Only a symlink whose stored text exactly equals that source pathname migrates;
local files and other links (including dangling links and dot-path aliases) remain.
The `wrappers` mode removes only the caller's exact legacy alias signature
(line-count and line membership, matching the old grep checks), then rewrites
every explicitly selected profile alias. Aliases execute `pi` from PATH.

These are trusted-home compatibility mechanics, not a sandbox or transactional
installer: parent symlinks and alias destinations may be written through, profile
aliases are always overwritten, failures can leave partial changes, and concurrent
writers are not locked. Do not supply secrets in policy/seeds. No auth/catalog
content, session data, shell history, provider calls or live container lifecycle
are managed here. Test explicit policy in a temporary HOME before activation.

`WIKI_INIT` is `core.lib.agentsInstallers.wikiInit`. It requires Bash and coreutils
(`mkdir`, `readlink`, `basename`, `mv`, `rm`, `ln`, `cat`). All nine nonempty paths
are mandatory; relative paths become absolute against cwd without resolving
symlinks. The caller chooses the writable profile, asset root (`skills/*/SKILL.md`),
QMD skill, instructions, primary/legacy config directories, QMD directory and two
seed files. There is no HOME/XDG discovery, vault/model/collection default, bundled
instruction/seed content or upstream setup/index/download call. Seeds are copied
verbatim as data, without shell expansion or sourcing; callers must render any
intended path values themselves and keep secrets out of these public resources.

The `skills/`, `wiki-skills/`, `AGENTS.md`, `wiki-instructions.md`, `qmd-cli`,
`config` and `index.yml` names retain the compatibility protocol. Only links
matching the supplied current source pathnames migrate from automatic `skills/`
to `wiki-skills/`; conflicting user destinations and automatic overrides remain.
Only the exact managed `AGENTS.md` link is removed; instructions are linked outside
automatic discovery. Retired links under the current asset root are pruned, not
user files/directories/links to other roots. A missing QMD skill still produces
a dangling managed link, retaining the old behavior. Existing primary config
directories (including symlinks) beat legacy; an existing legacy directory is used
only when the primary path is absent and not a symlink. Existing `config` files,
directories and dangling links are untouched and suppress index seeding. A missing
config is seeded once; only then may a missing `index.yml` be seeded. Existing
indexes, including directories/dangling links, are preserved. Needed seed files
must be readable regular files before config creation; unused seeds need not exist.
Writes are not transactional, concurrent invocations are not locked, and trusted
parent/profile symlinks are not sandboxed. Selecting explicit paths is not a vault
migration or runtime acceptance. Test in an isolated writable tree first.

`CLAUDE_PROFILE` is `core.lib.agentsInstallers.claudeProfile`. It requires an explicit
JSON policy with exactly these fifteen fields (synthetic example, not defaults):

```json
{
  "state_dir": "/tmp/fixture/state",
  "settings_path": "/tmp/fixture/config/settings.json",
  "wrapper_path": "/tmp/fixture/bin/client",
  "executable": "/tmp/fixture/client",
  "argv0": "fixture-client",
  "python": "python3",
  "profile_env": "FIXTURE_PRESET",
  "presets": {"plain": [], "tagged": ["--tag", "fixture"]},
  "default_profile": "plain",
  "selection_rules": [{"profile": "tagged", "flags": [], "options": {"--tag": "fixture"}}],
  "explicit_flags": [],
  "explicit_options": ["--tag"],
  "resume_options": ["--resume", "-r"],
  "legacy_scripts": [],
  "legacy_store_suffixes": []
}
```

All fields are required, including empty lists. Presets map nonempty names to
argument lists; the default and every rule profile must be present. Rules are
ordered: the first with all exact `flags` and all option/value matches wins,
otherwise the default name is used. Option parsing accepts `--name VALUE` and
`--name=VALUE`; only arguments before `--` participate. `explicit_flags` matches
whole arguments, whereas `explicit_options` also recognizes equals forms. The
first nonempty value in `resume_options` selects a session; absent explicit
arguments, a known saved preset supplies its flags. Otherwise no extra flags are
added. Pickers/continue behavior is entirely determined by the supplied option
names. There are no embedded preset names, prompt paths or permission flags.

The executable must be absolute; `python` is a command name available in the
caller's PATH or an absolute interpreter. The policy argument becomes absolute
against cwd; relative state/settings/wrapper fields are anchored to the logical
policy parent, so later wrapper invocation from another cwd uses the same paths.
Symlinks are not resolved and `~`/HOME/XDG are not expanded.
The generated shell wrapper and SessionStart hook persist the logical script and
policy paths, so callers should use stable paths across image replacements.
`record` stores only the validated inherited profile name under a SHA-256 session
filename, atomically with mode 0600; malformed events, unknown profiles and
I/O errors are silently ignored. Missing/invalid policy is also silent for
`record` (bookkeeping must not block), but fails `install`/`run` before writes/exec.
No credentials, prompt or event content is logged or stored by the helper.

Installation preserves unrelated settings/hooks and user wrappers. It collapses
current exact interpreter/script/policy hooks and explicitly selected legacy
three-argument hooks (`python SCRIPT record`); legacy scripts match exact paths
or caller suffixes under `/nix/store/`. Other policy hooks stay intact. Legacy
interpreter matching uses the supplied `python` string. SessionStart, `session_id`,
saved `profile`, `/bin/sh` and the existing managed-wrapper marker remain protocol
compatibility identifiers. Managed wrappers are atomically replaced with mode
0755; custom files/directories and unmarked/dangling symlinks stay untouched.
Malformed settings are not repaired; filesystem failures may leave partial
installation. State is trusted caller-owned data, not authenticated authorization:
select presets and writable state paths accordingly. Parent symlinks, concurrent
writers and path aliases are not sandboxed or locked. Keep policy/script paths
stable, and test with fixtures before any real mutable-state operation.

Host/container callers may use these same paths directly or pass them explicitly
as `mkAgentsSoftware` file resources and choose invocation in their own `homeInit`.
No runtime module or software factory wires them automatically. Sources/config
paths are trusted caller inputs; these helpers do not sandbox symlinked parent
or profile directories, prevent concurrent filesystem changes or audit template
commands. Keep credentials out of store resources.

`agents-installers` runs synthetic temporary-HOME tests with a narrow fake Herdr
and an eighteen-file source set independent of a private checkout. It also evaluates
the exported paths through `mkAgentsSoftware`. `agents-installers-dependencies`
guards the bounded stdlib/shell/source/export contract and reruns all nine suites
after copying just those eighteen files into a temporary directory. Python-only suites
run with empty PATH; Pi-shell tests additionally receive an explicit shell interpreter,
and wiki tests receive only the declared Bash/coreutils tools. Synthetic auth/session
sentinels only demonstrate that unrelated fixture files are untouched, not real
auth/session, Pi or browser runtime acceptance. Recipes, personal templates and
production integration remain separate gates.

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
