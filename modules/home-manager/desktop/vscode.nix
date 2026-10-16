# modules/programs/vscode.nix
#
# Webview panels -- Markdown Preview, the release notes, and the UI of every
# extension that draws one -- are iframes served from `vscode-webview://<uuid>`,
# and their resources are fetched through a service worker that the webview host
# page registers. VS Code rewrites that page's `sandbox` attribute and navigates
# it while the registration is still in flight, so blink can reach
# `ServiceWorkerContainer::register()` with an unset `provider_` and throw:
#
#   Error loading webview: Error: Could not register service worker:
#   InvalidStateError: Failed to register a ServiceWorker: The document is in an
#   invalid state.
#
# microsoft/vscode#125993 -- open since 2021, labelled `upstream` + `chromium`,
# still unfixed. Two properties of it drive everything below. The registration
# is scoped to the webview origin, so the failure has nothing to do with which
# file is being previewed and reproduces on any `.md`. And once the failed
# registration is on disk it is sticky: it survives Developer: Reload Window and
# a normal restart, because the next start finds the same cached state.
#
# Reports cluster right after an editor upgrade, and installing through
# `programs.vscode` turns that from an occasional event into the normal one:
# every `home-manager switch` that bumps the package moves the binary to a fresh
# store path while a running instance keeps executing the old one. The issue
# thread describes the same shape for snap, where the launcher stops seeing
# instances after an update because the executable path changed underneath them.
#
# Hence two halves, matching the two moments the bug can appear:
#
#   * `home.activation.vscodeWebviewCache` drops the cached registration when
#     the package changes, so a switch cannot carry a stale one into the new
#     version. It refuses to act while the editor is running -- that is exactly
#     the state the bug needs, and clearing the cache underneath a live Chromium
#     profile would be undone on exit anyway.
#   * `code-webview-reset` handles the session that already broke, where the
#     processes have to go first.
#
# Nothing here works around the race itself; when upstream fixes it, both halves
# become dead weight and should go.
{ httpProxy }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  vscode = config.programs.vscode.package;

  # `package` is nullable in home-manager, and the extension sets below are
  # built whether or not an editor is installed. Falling back to the nixpkgs
  # default keeps the compatibility check answerable in that case. That package
  # is what `programs.vscode.package` defaults to anyway.
  vscodeVersion = if vscode != null then vscode.version else pkgs.vscode.version;

  # `nameShort` in home-manager's modules/programs/vscode/mkVscodeModule.nix,
  # which builds `userDir = "${xdg.configHome}/${nameShort}/User"`. The module
  # exports that profile directory but not the user-data-dir above it, which is
  # what Chromium partitions live in -- so the folder name is spelled out.
  # `programs.vscodium` would want "VSCodium" here, and the `code` package from
  # Manjaro's own repository would be "Code - OSS"; neither is installed.
  userDataDir = "${config.xdg.configHome}/Code";

  serviceWorkerDir = "${userDataDir}/Service Worker";

  # Only these two hold the registration and its cached script. The rest of the
  # profile -- Local Storage, workspaceStorage, Backups, the extension host's
  # own state -- is left alone, so no unsaved editor state rides on this.
  staleCaches = [
    "${serviceWorkerDir}/CacheStorage"
    "${serviceWorkerDir}/ScriptCache"
  ];

  # Which package the caches were last cleared for. A symlink rather than a file
  # with the path written into it, so the activation stays dry-run correct:
  # `run ln -sfn` goes through $DRY_RUN_CMD, a shell redirect would write even
  # on a dry run. The target dangling after a garbage collection is harmless --
  # only the string is ever read.
  stamp = "${config.xdg.stateHome}/vscode-webview-service-worker";

  pgrep = lib.getExe' pkgs.procps "pgrep";

  # Deliberately not wired into activation: it kills the editor, which is the
  # user's call to make, not a side effect of `home-manager switch`.
  resetTool = pkgs.writeShellApplication {
    name = "code-webview-reset";
    runtimeInputs = [
      pkgs.procps
      pkgs.coreutils
    ];
    text = ''
      # Run this from a terminal outside VS Code -- it kills every `code`
      # process, the integrated terminal included.
      #
      # Both halves are needed and in this order. Orphaned processes are what
      # pins the broken registration: they outlive a menu Quit, and the issue
      # thread has repeated reports of the cache deletion alone doing nothing
      # until they are gone.

      swDir=${lib.escapeShellArg serviceWorkerDir}

      echo "Stopping VS Code..."
      pkill -x code || true

      # Renderers that already crashed linger as <defunct> children until their
      # parent reaps them, and the thread has cases where only SIGKILL cleared
      # them. Give the polite signal a few seconds before escalating.
      for _ in 1 2 3 4 5; do
        pgrep -x code >/dev/null || break
        sleep 1
      done

      if pgrep -x code >/dev/null; then
        echo "Still alive after SIGTERM, sending SIGKILL"
        pkill -9 -x code || true
        sleep 1
      fi

      if pgrep -x code >/dev/null; then
        echo "error: VS Code processes survived SIGKILL; cache left untouched" >&2
        pgrep -a -x code >&2
        exit 1
      fi

      rm -rf "$swDir/CacheStorage" "$swDir/ScriptCache"
      echo "Cleared the cached service worker registration under:"
      echo "  $swDir"
      echo "Start VS Code again -- the webview re-registers on first use."
    '';
  };

  # Extensions the marketplace has to supply directly: nixpkgs curates about
  # 900 of its tens of thousands, and none of these seven are in that set.
  #
  # The set comes from the `nix-vscode-extensions` input, so versions follow
  # that input's lock -- `nix flake update nix-vscode-extensions` moves all seven
  # at once and there is no per-extension hash to re-derive. The cost is that an
  # update is all-or-nothing, and that the data trails the marketplace by a day
  # or so.
  #
  # Two qualifiers, both needed:
  #
  #   * Check `engines.vscode` only on the extensions we select below.
  #     `forVSCodeVersion` filters the entire catalogue and costs millions of
  #     extra thunks. An incompatible release or unknown range syntax now fails
  #     evaluation explicitly, rather than disabling an extension at run time.
  #   * `-universal` rather than plain `vscode-marketplace`. That attrset ranks
  #     platform-specific builds above universal ones *by kind, not by version*,
  #     and Odoo IDE last shipped a linux-x64 build as 0.45.4 in December while
  #     going universal since -- so the unqualified set pins it eight months
  #     back. Everything here is JavaScript with no native payload, which is
  #     what makes the universal builds the right ones to take.
  checkMarketplaceExtension = import ./vscode/check-extension.nix { inherit lib vscodeVersion; };
  marketplaceExtension =
    publisher: name: checkMarketplaceExtension pkgs.vscode-marketplace-universal.${publisher}.${name};

  # VS Code profiles do not inherit. The documentation is explicit that a
  # profile cannot take settings from another one and override a few -- there is
  # no base and no chain. So this is a Nix-level list spliced into every
  # profile, not a profile the others extend: an extension absent from a
  # profile's own list is absent from that window, full stop. The theme and the
  # keymap are here for exactly that reason -- leave them out of one profile and
  # that window comes up looking like a different editor.
  commonExtensions =
    (with pkgs.vscode-extensions; [
      dracula-theme.theme-dracula
      yzhang.markdown-all-in-one

      # A second Markdown preview, not a replacement for the built-in one: its
      # selling point here is the sidebar table of contents (right-click in the
      # preview -> Toggle Sidebar TOC), which the built-in preview has no
      # equivalent of. It binds `ctrl+shift+v` and `ctrl+k v` for `.md` files,
      # so those keys stop opening VS Code's own preview -- that one is still
      # reachable as `Markdown: Open Preview`.
      #
      # Mermaid comes from its own bundled copy, so `bierner.markdown-mermaid`
      # below does not apply to this preview and the two render ```mermaid
      # fences through different versions of mermaid.js.
      #
      # Its runtime state (preview theme, per-file TOC toggle, custom CSS) lives
      # in `~/.local/state/crossnote`, outside this flake and not managed here.
      shd101wyy.markdown-preview-enhanced

      # Diagrams. `bierner.markdown-mermaid` swaps mermaid.js into the built-in
      # Markdown Preview, so ```mermaid fences render there and nowhere else --
      # it has no view of its own and no grammar for the fence body, which is
      # what the two marketplace entries below add.
      #
      # `jebbs.plantuml` is the one with a runtime outside the store path: its
      # local renderer shells out to `java`, and every diagram type that is not
      # a sequence diagram also wants Graphviz. Both are pinned in
      # `plantumlSettings` below.
      bierner.markdown-mermaid
      jebbs.plantuml

      # Every profile: this flake is edited from whichever window happens to be
      # open, and a profile without it shows `.nix` as plain text.
      jnoortheen.nix-ide

      # Containers. `docker.docker` is Docker's own extension, the two
      # `ms-azuretools` ones are Microsoft's older pair that it grew out of;
      # all three were installed, so all three are kept rather than guessed at.
      docker.docker
      ms-azuretools.vscode-docker
      ms-azuretools.vscode-containers
      ms-vscode-remote.remote-containers
    ])
    ++ [
      # Neither is in nixpkgs' curated set. The first is Mermaid's own
      # extension: a live preview and an editor panel for standalone `.mmd`
      # files, which the Markdown Preview never sees. The second is only a
      # TextMate grammar -- it colours the inside of a ```mermaid fence, which
      # is otherwise plain grey text.
      (marketplaceExtension "mermaidchart" "vscode-mermaid-chart")
      (marketplaceExtension "bpruitt-goddard" "mermaid-markdown-syntax-highlighting")

      # Obsidian-style live editing in one pane; MPE remains a manual preview.
      (marketplaceExtension "portwaylabs" "imark")
    ]
    ++ rstExtensions;

  # Spliced into every profile's `userSettings`, the same way
  # `jnoortheen.nix-ide` is spliced into every profile's extension list --
  # settings do not inherit either, so the extension would sit there with its
  # language server off in every profile but the one that names these.
  nixIdeSettings = {
    "nix.enableLanguageServer" = true;
    "nix.serverPath" = "${pkgs.nixd}/bin/nixd";

    # Only used when the language server is disabled: with the LSP on,
    # formatting goes through nix.serverSettings.nixd.formatting below.
    "nix.formatterPath" = "${pkgs.nixfmt}/bin/nixfmt";

    # Settings forwarded to nixd via workspace/configuration.
    "nix.serverSettings" = {
      nixd = {
        # Pinned to this flake's nixpkgs at build time, so pkgs/lib completion
        # works in every workspace, not just this flake.
        nixpkgs.expr = "import ${pkgs.path} { config.allowUnfree = true; }";

        formatting.command = [ "${pkgs.nixfmt}/bin/nixfmt" ];

        # Workspace-relative on purpose: only resolves when this flake is the
        # opened folder, which is the only place the options exist.
        options."home-manager".expr =
          "(builtins.getFlake (builtins.toString ./.)).homeConfigurations.${config.desktop.homeOutput}.options";
      };
    };

    "[nix]"."editor.defaultFormatter" = "jnoortheen.nix-ide";
    "[nix]"."editor.formatOnSave" = true;
  };

  # `jebbs.plantuml` shells out to a JVM, and nixpkgs already patches the
  # extension's `plantuml.java` default to `${pkgs.plantuml}/bin/plantuml` --
  # a binary wrapper around openjdk that also exports GRAPHVIZ_DOT. Rendering
  # works out of the box with it, but only rendering: that wrapper appends
  # `-jar <store plantuml.jar>` to the command line, so everything the
  # extension passes lands *after* `-jar` and reaches PlantUML's argv instead
  # of the JVM. `-Dplantuml.include.path=...`, which is how the extension
  # implements `plantuml.includepaths`, `plantuml.diagramsRoot` and plain
  # sibling-file includes, is one of those:
  #
  #   $ plantuml -Dplantuml.include.path=$PWD -jar $ext/plantuml.jar -pipe ...
  #   ERROR
  #   1
  #   cannot include sub.iuml            # exit 200
  #
  # So the java entry has to be a bare `java`, which then leaves Graphviz
  # unlocated -- without it PlantUML answers "Dot executable does not exist,
  # only sequence diagrams will be generated". `-graphvizdot` is PlantUML's own
  # flag for that and `jarArgs` is appended after the jar, which is exactly
  # where a jar flag belongs.
  #
  # None of the three paths enlarges the closure: `pkgs.jre` is the same
  # openjdk derivation `pkgs.plantuml` wraps, and both plantuml and graphviz
  # are already pulled in by the extension itself.
  plantumlSettings = {
    "plantuml.java" = "${pkgs.jre}/bin/java";
    "plantuml.jar" = "${pkgs.plantuml}/lib/plantuml.jar";
    "plantuml.jarArgs" = [
      "-graphvizdot"
      "${pkgs.graphviz}/bin/dot"
    ];
  };

  # reStructuredText. One extension, and deliberately not the obvious one:
  # `lextudio.restructuredtext` plus `swyddfa.esbonio` is the combination the
  # marketplace points at, and it previews Sphinx projects only. esbonio walks
  # up from the file looking for a `conf.py` and gives up at the filesystem
  # root, leaving an empty panel and no message:
  #
  #   [esbonio.SphinxManager] Trying path: /tmp/notes
  #   [esbonio.SphinxManager] Trying path: /tmp
  #   [esbonio.SphinxManager] Trying path: /
  #   [esbonio.SphinxManager] No applicable client for uri: .../index.rst
  #
  # It wants an open folder as well -- a lone file tab fails the same silent
  # way even when a `conf.py` is right there. Neither is fixable from the
  # client side: `sphinx-build -C` is how Sphinx itself skips `conf.py`, but
  # the agent then does `pathlib.Path(conf_dir).resolve()` on the `None` that
  # comes back and dies with "argument should be a str or an os.PathLike
  # object ... not 'NoneType'".
  #
  # `tht13.rst-vscode` is the older, smaller answer to a README.rst: its own
  # grammar, its own `rst.showPreview`, and a preview that is one docutils call
  # on one file, so no project layout is involved. The loss is Sphinx -- no
  # `toctree`, no `:ref:` resolution, no directives from a project's
  # extensions. For those, build the docs and read the HTML.
  #
  # The two cannot share an editor. lextudio lists this one in
  # `conflictingExtensions` and, offered a removal that cannot work against a
  # read-only store, ends its own activation; and they claim `.rst` under two
  # different language ids, `rst` here against `restructuredtext` there, of
  # which VS Code picks one.
  rstExtensions = [ (marketplaceExtension "tht13" "rst-vscode") ];

  rstSettings = {
    # The extension shells out to `<pythonPath> python/preview.py <file>` and,
    # finding no docutils there, offers `pip install docutils` -- an offer that
    # cannot succeed against a store path. An interpreter that already has it
    # skips the question. The script itself imports only `docutils.core`, but
    # pygments belongs in the same environment: without it every `code-block`
    # renders as a warning box in the preview rather than as code --
    # "System Message: WARNING/2 ... Cannot analyze code. Pygments package not
    # found." followed by the unhighlighted literal block.
    "rst.preview.pythonPath" = "${
      pkgs.python3.withPackages (ps: [
        ps.docutils
        ps.pygments
      ])
    }/bin/python3";
  };

  # Every profile, through mihomo. Nothing captures VS Code transparently --
  # ../services/mihomo.nix is a user unit with no TUN device, so a program
  # either asks 127.0.0.1:2080 for the connection itself or leaves directly,
  # and this editor left directly. Reaches the profiles through
  # `commonSettings` below, for the reason stated there.
  #
  # Three settings and not one, because "VS Code" is three network stacks and
  # `http.proxy` alone reaches the first of them:
  #
  #   * Chromium in the main process -- marketplace, extension downloads,
  #     update checks, webviews. out/main.js turns `http.proxy` and
  #     `http.noProxy` into `session.setProxy({proxyRules, proxyBypassRules})`,
  #     appending Chromium's own `<local>` to the bypass list. One guard in
  #     that same function is worth knowing: a proxy URL containing `@` is
  #     skipped for the session entirely, so credentials in `httpProxy` would
  #     leave this half unproxied without saying so.
  #   * Node's `http`/`https` in the extension host, reached by patching the
  #     global agent -- `http.proxySupport`. `override` rather than `on` so an
  #     extension carrying its own proxy setting cannot opt itself out.
  #   * Node's global `fetch`, which is undici, ignores that patched agent and
  #     needs `http.fetchAdditionalSupport` instead.
  #
  # The last two are already at these values by default (VS Code 1.110 schema:
  # "override" and true). Written out for the same reason `mutableExtensionsDir`
  # below is: whether extension traffic leaves through the tunnel should not
  # rest on a default that is invisible in this file.
  #
  # The HTTP form and not the SOCKS one -- an HTTP proxy is asked for the host
  # by name, so mihomo matches its domain rules on a name instead of on an
  # address the client resolved first. `services.mihomo.httpProxy` says it at
  # length.
  #
  # Not covered here: a process an extension spawns -- a language server, a CLI
  # tool. Those inherit the editor's environment, not its settings, and
  # reaching them means HTTP_PROXY on the VS Code process, which the integrated
  # terminal would then inherit too.
  #
  # With mihomo stopped the editor has no network at all: no marketplace, no
  # extension updates. Deliberate, and the same bargain Chrome and Yandex
  # stable already make in this configuration.
  proxySettings = {
    "http.proxy" = httpProxy;
    "http.proxySupport" = "override";
    "http.fetchAdditionalSupport" = true;

    # Suffix matching, and that is the entire matcher: @vscode/proxy-agent
    # rewrites each entry as `.<entry>` and tests `.<hostname>` against it with
    # `endsWith` (out/index.js, `shouldBypassProxy`). An exact host or a dotted
    # suffix works; a CIDR does not, so 192.168.0.0/16 cannot be spelled here
    # at all. A bare `*` in this list bypasses the proxy for everything.
    #
    # Which leaves the LAN to mihomo's own rules -- they send 10/8, 172.16/12
    # and 192.168/16 to DIRECT, so a LAN address still arrives, it just takes
    # the loopback hop to get there. What this list buys is the case where that
    # hop is gone: a dev server on localhost keeps working with mihomo stopped.
    "http.noProxy" = [
      "localhost"
      "127.0.0.1"
      "::1"
      ".local"
      ".lan"
    ];
  };

  # What every profile's `userSettings` starts from, the counterpart of
  # `commonExtensions` below -- settings do not inherit either, so a key left
  # out of a profile is absent from that window, full stop.
  commonSettings =
    nixIdeSettings
    // plantumlSettings
    // proxySettings
    // rstSettings
    // {
      # File-type recommendations -- "install the recommended extensions for
      # Python?" on the first `.py` opened in a profile that has no Python
      # extension. Its Install button cannot work here: `mutableExtensionsDir`
      # is false, so `~/.vscode/extensions` is a symlink into a read-only store
      # buildEnv and the install fails. Every extension comes from this file.
      #
      # Only recommendations. A banner about an unsatisfied
      # `extensionDependencies` entry still shows, which is wanted -- that one
      # means a profile's list here is incomplete.
      "extensions.ignoreRecommendations" = true;

      # `shd101wyy.markdown-preview-enhanced` ships its preview stylesheet
      # separately from the editor theme and defaults to `github-light.css`,
      # which is a white page next to Dracula. One of the extension's own
      # seventeen bundled themes, not a custom file -- `vscode.css` would
      # follow the editor theme instead, but it inherits the editor's own
      # colours and renders headings and code fences flatter than this one.
      "markdown-preview-enhanced.previewTheme" = "github-dark.css";

      # Render and edit Markdown in the same pane. Only associate file types
      # advertised by iMark's custom editor; other formats use the text editor.
      "workbench.editorAssociations" = lib.genAttrs [
        "*.md"
        "*.markdown"
        "*.mdown"
        "*.mkd"
      ] (_: "imark.editor");
      "imark.editor.defaultMode" = "live";
      "imark.theme.name" = "vscode";

      # Keep MPE available on demand, without taking over editor associations
      # (Previews Only does that) or opening a second pane automatically.
      "markdown-preview-enhanced.previewMode" = "Single Preview";
      "markdown-preview-enhanced.automaticallyShowPreviewOfMarkdownBeingEdited" = false;
    };

  # Keybindings are per profile too, so this goes into every one of them.
  # Back/forward through cursor history, the way a browser does it. The Linux
  # defaults are `ctrl+alt+-` and `ctrl+shift+-`.
  commonKeybindings = [
    {
      key = "alt+left";
      command = "workbench.action.navigateBack";
    }
    {
      key = "alt+right";
      command = "workbench.action.navigateForward";
    }
  ];

  # Shared by `pycode` and `odcode`, which must not drift: the same file
  # formatted under either has to come out identical, or switching profile
  # rewrites the diff.
  #
  # `source.fixAll.ruff` applies the autofixable lint rules and
  # `source.organizeImports.ruff` replaces isort. Both are "explicit", i.e. on
  # an explicit save only -- an auto-save would otherwise rewrite imports
  # mid-edit, while a half-typed name is still unused.
  pythonEditorSettings = {
    "editor.defaultFormatter" = "charliermarsh.ruff";
    "editor.formatOnSave" = true;
    "editor.codeActionsOnSave" = {
      "source.fixAll.ruff" = "explicit";
      "source.organizeImports.ruff" = "explicit";
    };
  };

  # `--profile` is the only way to pick one for a window that is about to open:
  # the association is fixed at open time, and VS Code otherwise reuses whatever
  # profile that folder was last opened under. Plain `code` is the default
  # profile and needs no wrapper.
  #
  # Watch for a typo here. A name VS Code does not know is not an error -- the
  # documentation says it creates a new empty profile under that name, so a
  # misspelled launcher opens a bare editor instead of failing.
  profileLauncher =
    name:
    pkgs.writeShellScriptBin name ''
      exec ${lib.getExe vscode} --profile ${name} "$@"
    '';
in
{
  imports = [
    (import ./vscode/session.nix {
      inherit
        config
        lib
        pkgs
        userDataDir
        ;
    })
  ];

  home.packages =
    (with pkgs; [
      nixd
      nixfmt
    ])
    ++ lib.optionals (vscode != null) (
      [ resetTool ]
      ++ map profileLauncher [
        "pycode"
        "angcode"
        "odcode"
      ]
    );

  # Guarded rather than assumed: `package` is nullable in home-manager, and with
  # no package there is no upgrade to react to and no profile to clean.
  home.activation = lib.mkIf (vscode != null) {
    vscodeWebviewCache = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      if [ "$(readlink ${lib.escapeShellArg stamp} 2>/dev/null)" != ${lib.escapeShellArg "${vscode}"} ]; then
        if ${pgrep} -x code >/dev/null 2>&1; then
          warnEcho "vscode: upgraded while the editor is running -- webview cache left as is."
          warnEcho "vscode: if previews fail to load, quit VS Code and run 'code-webview-reset'."
        else
          run rm -rf ${lib.escapeShellArgs staleCaches}
          run mkdir -p ${lib.escapeShellArg (builtins.dirOf stamp)}
          run ln -sfn ${lib.escapeShellArg "${vscode}"} ${lib.escapeShellArg stamp}
        fi
      fi
    '';
  };

  programs.vscode = {
    enable = true;

    # Already false by default once a profile other than `default` exists, but
    # stated so the property does not depend on that: every extension has to be
    # in this file. `~/.vscode/extensions` becomes a symlink into a store
    # buildEnv, so the marketplace's Install button stops working -- which is
    # the point. A machine is then reproduced by `home-manager switch` alone,
    # with nothing to carry over by hand.
    #
    # Migrating to this the first time needs the old directory out of the way:
    # this repository sets no `home.backupFileExtension`, so activation stops at
    # "Existing file ... is in the way" rather than moving it itself. Take the
    # list first -- `code --list-extensions --show-versions` -- then rename the
    # directory; the list is what has to end up here.
    mutableExtensionsDir = false;

    # Four profiles, one launcher each: `code`, `pycode`, `angcode`, `odcode`.
    #
    # The split is not about tidiness. Language servers are the reason: Pylance
    # and Odoo IDE are both pyright forks reading the same `python.analysis.*`
    # settings, so in one profile every completion, hover and diagnostic would
    # arrive twice, out of two indexes of the same code. A profile decides which
    # extensions are *enabled*, which is where that can be settled once instead
    # of per workspace.
    #
    # Registering a non-default profile writes `User/globalStorage/storage.json`,
    # which VS Code holds in RAM and rewrites on exit. Run the switch with the
    # editor closed, or the entries are lost the moment it quits.

    # Everything that is not one of the three stacks below.
    profiles.default = {
      extensions = commonExtensions;
      keybindings = commonKeybindings;
      userSettings = commonSettings;
    };

    # Python, and the CAD work that is Python: ./build123d.nix adds the OCP CAD
    # viewer and the interpreter settings to this profile, so its extensions and
    # `python.venvPath` land here rather than in the default one.
    profiles.pycode = {
      extensions =
        commonExtensions
        ++ (with pkgs.vscode-extensions; [
          # Also listed by ./build123d.nix, which needs them for the OCP CAD
          # viewer and cannot rely on this module being imported. home-manager
          # flattens the profile's lists into one `buildEnv`, and a store path
          # appearing twice is the same symlink twice, so this costs nothing.
          ms-python.python
          ms-python.debugpy

          # Unfree, and licensed only for the Microsoft-branded build -- which
          # is what `programs.vscode` installs here. On VSCodium it would refuse
          # to start and `detachhead.basedpyright` would take its place.
          ms-python.vscode-pylance

          # Environment discovery and switching, split out of the Python
          # extension itself; it is what "Python: Select Interpreter" talks to.
          ms-python.vscode-python-envs

          # The vsix ships a `bundled/libs/bin/ruff` built against the FHS
          # loader; nixpkgs replaces it with a symlink to `pkgs.ruff`, so no
          # `ruff.path` is needed and lint/format follow this flake's pin.
          charliermarsh.ruff

          # Notebooks. The four satellites are separate extensions rather than
          # features of the first: keymap, output renderers, cell tags and the
          # slideshow view.
          ms-toolsai.jupyter
          ms-toolsai.jupyter-keymap
          ms-toolsai.jupyter-renderers
          ms-toolsai.vscode-jupyter-cell-tags
          ms-toolsai.vscode-jupyter-slideshow
        ])
        ++ [ (marketplaceExtension "openvmp" "partcad") ];

      keybindings = commonKeybindings;
      userSettings = commonSettings // {
        # "Default" already resolves to Pylance whenever it is installed; named
        # explicitly so a profile that later drops the extension fails loudly
        # instead of silently falling back to Jedi.
        "python.languageServer" = "Pylance";

        # No `python.defaultInterpreterPath`: ./build123d.nix argues the case at
        # length -- pointing it at one environment makes that environment the
        # starting interpreter of every Python workspace on the machine. The
        # interpreter is picked per workspace, out of `python.venvPath`.
        "[python]" = pythonEditorSettings;
      };
    };

    # Angular and the frontend around it.
    profiles.angcode = {
      extensions =
        commonExtensions
        ++ (with pkgs.vscode-extensions; [
          # The Angular language service: completion and navigation inside
          # templates, and the only thing here that understands `.html` as
          # Angular rather than as plain markup.
          angular.ng-template

          bradlc.vscode-tailwindcss
        ])
        ++ [
          # Free and paid ("Pro Edition") halves in one vsix, under a licence
          # that denies redistribution of either. Only the free half works
          # without a subscription.
          (marketplaceExtension "cyrilletuzi" "angular-schematics")
        ];

      keybindings = commonKeybindings;
      userSettings = commonSettings;
    };

    # Odoo.
    profiles.odcode = {
      extensions =
        commonExtensions
        ++ (with pkgs.vscode-extensions; [
          # A hard `extensionDependencies` entry of Odoo IDE -- VS Code refuses
          # to enable it at all without the Python extension, and home-manager
          # installs exactly the list given here, resolving nothing on its own.
          ms-python.python
          ms-python.debugpy

          # Formatting and lint behave the same as under `pycode`; only the
          # completion engine differs between the two.
          charliermarsh.ruff
        ])
        ++ [
          # Pure JavaScript plus pyright's typeshed stubs -- nothing in it
          # needs the FHS loader patched, which is why the universal build is
          # the one to take. See the marketplace selection note above.
          (marketplaceExtension "trinhanhngoc" "vscode-odoo")
        ];

      keybindings = commonKeybindings;
      userSettings = commonSettings // {
        # Pylance is deliberately not in the list above, and "Default" would
        # then fall back to Jedi -- a third source of completions next to Odoo
        # IDE's own server. "None" leaves the field to it.
        "python.languageServer" = "None";

        # Odoo IDE inherits pyright's diagnostics, and pyright reports nothing
        # at typeCheckingMode "off". Its readme names basic/standard as what
        # turns diagnostics on in Python files.
        "python.analysis.typeCheckingMode" = "basic";

        # Where the extension looks for `odoo.addons.*` and for custom addons
        # when the layout does not make that obvious. Left to the workspace:
        # the paths differ per checkout, so they belong in the project's
        # `.vscode/settings.json`, not in a machine-wide profile.
        #   "python.analysis.extraPaths": ["/path/to/odoo", "/path/to/addons"]

        # Model names, XML IDs and field names are completed inside string
        # literals, and VS Code suggests nothing there by default.
        "editor.quickSuggestions".strings = true;

        "[python]" = pythonEditorSettings;
      };
    };
  };
}
