# modules/programs/build123d.nix
#
# build123d modelled in VS Code, with the model shown by OCP CAD Viewer: the
# Python process tessellates and pushes the mesh over a websocket, the extension
# draws it with three.js inside a webview. Nothing on the Python side opens a
# window, which is what makes this stack cheap compared to a desktop CAD GUI.
#
# None of it is in nixpkgs — not `build123d`, not the OCCT bindings
# `cadquery-ocp` / `cadquery-ocp-novtk`, not `ocp-vscode`. Check with
# `nix eval nixpkgs#python3Packages.build123d.name` (seconds, and it suggests
# near-misses); `nix search nixpkgs build123d` evaluates all of legacyPackages
# and is useless for this.
#
# So: PyPI wheels, resolved by uv and built by uv2nix. The OCCT bindings ship
# manylinux_2_31 wheels, and any current Arch/Manjaro glibc is far past 2.31, so
# nothing is compiled from source. The closure is pinned in ./build123d/uv.lock —
# regenerate with
#   uv lock --directory modules/programs/build123d
#
# build123d depends on `cadquery-ocp-novtk`, not on `cadquery-ocp`. Same OCCT
# 7.9, same bindings, minus the VTK build — and VTK is what used to drag in a GL
# render window, 196 extra shared objects and the X client stack. Rendering
# happens in the editor here, so the trimmed wheel is the correct one and the
# whole VTK/Qt patching apparatus this module used to need is gone.
{ pyprojectInputs, workspaceRoot, viewerHash }:
{
  config,
  pkgs,
  lib,
  ...
}:

let
  python = pkgs.python313;

  workspace = pyprojectInputs.uv2nix.lib.workspace.loadWorkspace {
    inherit workspaceRoot;
  };

  # "wheel", not the default "sdist": from source, the OCCT bindings would
  # rebuild OCCT itself — hours of work to arrive at the same binaries PyPI
  # already publishes.
  overlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };

  # pyproject.nix installs wheels as they come; it never runs autoPatchelfHook
  # (that lives in build/hacks, for grafting nixpkgs-built packages instead).
  # A manylinux .so therefore keeps its bare `libstdc++.so.6` / `libGL.so.1`
  # DT_NEEDED entries, which resolve against nothing inside the store and fail
  # at import with "cannot open shared object file". Patching is mandatory.
  baseLibs = with pkgs; [
    stdenv.cc.cc.lib
    zlib
  ];

  # Every package here ships a native manylinux wheel; the rest of the closure
  # is `py3-none-any` and needs nothing. Re-derive the list after a lock bump:
  #
  #   grep -oE '[A-Za-z0-9_.-]+manylinux[^"]*x86_64\.whl' build123d/uv.lock \
  #     | grep -vE 'i686|aarch64' | cut -d- -f1 | tr 'A-Z_' 'a-z-' | sort -u
  #
  # Not `-cp313-`: a wheel can be tagged `abi3` or `py3-none-manylinux...` and
  # still carry ELF. The pattern above is a superset instead — it errs towards
  # patching a pure wheel, where autoPatchelfHook finds no ELF and does nothing,
  # rather than towards leaving a native one broken.
  nativeWheels = [
    "cadquery-ocp-novtk"
    "cffi"
    "charset-normalizer"
    "debugpy"
    "ezdxf"
    "fonttools"
    "lib3mf"
    "markupsafe"
    "numpy"
    "orjson"
    "pillow"
    "psutil"
    "pyyaml"
    "pyzmq"
    "scikit-learn"
    "scipy"
    "tornado"
    "websockets"
    "wrapt"
  ];

  # Everything else OCCT wants — fontconfig, freetype, FreeImage, OpenEXR,
  # libgomp, libuuid — is vendored inside the wheel under
  # `cadquery_ocp_novtk.libs/`. These three, plus libstdc++ and libz from
  # baseLibs, are the only DT_NEEDED entries in the wheel that point outside it
  # — checked with `readelf -d` over all 69 of its shared objects.
  ocpLibs = with pkgs; [
    expat # via the bundled fontconfig
    libGL
    libx11
  ];

  patchWheel =
    libs: drv:
    drv.overrideAttrs (old: {
      nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.autoPatchelfHook ];
      buildInputs = (old.buildInputs or [ ]) ++ libs;
    });

  pyprojectOverrides =
    final: prev:
    lib.genAttrs nativeWheels (name: patchWheel baseLibs prev.${name})
    // {
      # libGL and libX11 are not optional here even though this module never
      # draws: `libTKOpenGl` is a direct DT_NEEDED of `OCP.cpython-313-*.so`, so
      # both are loaded by `import OCP` itself. Loading them is all that
      # happens — no GL context is ever created from Python, so nothing here
      # ever reaches for a driver vendor the way a VTK or Qt viewer would.
      cadquery-ocp-novtk = patchWheel (baseLibs ++ ocpLibs) prev.cadquery-ocp-novtk;

      # The one package in the closure that PyPI publishes as an sdist only —
      # `ocp-vscode` pins `pyperclip==1.9.0`, and that release is a bare
      # setup.py with no `[build-system]` table. The PEP 517 fallback for a
      # missing table is setuptools, but nothing puts setuptools in the build
      # environment unless it is asked for, so the build ends at
      # "ModuleNotFoundError: No module named 'setuptools'". Pure Python
      # otherwise: no autoPatchelfHook here, nothing to patch.
      pyperclip = prev.pyperclip.overrideAttrs (old: {
        nativeBuildInputs =
          (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem { setuptools = [ ]; };
      });
    };

  pythonSet =
    (pkgs.callPackage pyprojectInputs.pyproject-nix.build.packages {
      inherit python;
    }).overrideScope
      (
        lib.composeManyExtensions [
          # `.wheel`, not `.default` — matches sourcePreference above.
          pyprojectInputs.pyproject-build-systems.overlays.wheel
          overlay
          pyprojectOverrides
        ]
      );

  build123dEnv = pythonSet.mkVirtualEnv "build123d-env" workspace.deps.default;

  # The extension and the `ocp_vscode` Python package are released as one
  # version pair and check each other at run time ("Version mismatch: ocp_vscode
  # X vs extension Y"), so the version is read out of the lock rather than
  # written down twice. A lock bump then forces the hash below to be updated —
  # a loud fixed-output failure instead of a viewer that silently refuses to
  # connect.
  lockedVersion =
    name:
    (lib.findFirst (p: p.name == name) (throw "${name} is not in build123d/uv.lock")
      (builtins.fromTOML (builtins.readFile (workspaceRoot + "/uv.lock"))).package
    ).version;

  ocpVscodeVersion = lockedVersion "ocp-vscode";

  # Fetched from the project's own GitHub release rather than through
  # `mktplcRef`: upstream publishes the .vsix and the matching wheel side by
  # side there, and it keeps the Visual Studio Marketplace terms of use out of
  # a build that has no need for them.
  ocpCadViewer = pkgs.vscode-utils.buildVscodeMarketplaceExtension {
    mktplcRef = {
      publisher = "bernhard-42";
      name = "ocp-cad-viewer";
      version = ocpVscodeVersion;
    };

    vsix = pkgs.fetchurl {
      url = "https://github.com/bernhard-42/vscode-ocp-cad-viewer/releases/download/v${ocpVscodeVersion}/ocp-cad-viewer-${ocpVscodeVersion}.vsix";
      hash = viewerHash;
    };

    meta = {
      description = "CAD viewer for build123d and CadQuery in VS Code";
      homepage = "https://github.com/bernhard-42/vscode-ocp-cad-viewer";
      license = lib.licenses.asl20;
      platforms = lib.platforms.all;
    };
  };

  # Where the environment is offered to the Python extension. A directory of
  # venvs rather than `python.defaultInterpreterPath`, which would make this
  # environment the starting interpreter of *every* Python project on the
  # machine. Under `python.venvPath` it is one named entry in
  # "Python: Select Interpreter" — picked per workspace, and the folder stays
  # usable for hand-made venvs.
  venvDir = "${config.home.homeDirectory}/.venvs";
in
{
  # Deliberately NOT `home.packages = [ build123dEnv ]`. The virtualenv's bin/
  # holds `python`, `python3`, `python3.13`, and about twenty more names on top
  # of them — `ipython`, `jupyter`, `flask`, `debugpy`, `f2py`, `fonttools`,
  # `ezdxf`, `ttx`. Since ~/.nix-profile/bin precedes /usr/bin in PATH, that
  # would make plain `python` resolve to this environment and shadow the
  # distribution interpreter system-wide.
  #
  # One unambiguous command instead: `build123d-python` is the interpreter for
  # headless work — modelling, STEP/STL/3MF export, scripts run from a shell or
  # from CI. Inside VS Code the interpreter comes from the venv link below, not
  # from this wrapper.
  home.packages = [
    (pkgs.writeShellScriptBin "build123d-python" ''
      exec ${build123dEnv}/bin/python "$@"
    '')
  ];

  # Pick it once per project: "Python: Select Interpreter" → build123d. The
  # extension then reads the same interpreter through the Python extension's
  # API, its Library Manager lists build123d and ocp_vscode as installed, and
  # new terminals come up with the environment activated (`bin/activate` is
  # there — this is a real venv, not a bin/ full of symlinks).
  #
  # Its install and upgrade buttons will not work, and should not: the store
  # path is read-only. Package changes go through ./build123d/pyproject.toml and
  # `uv lock`, which is the point of building it this way.
  home.file.".venvs/build123d".source = build123dEnv;

  # `pycode`, not `default`: ./vscode.nix splits VS Code into one profile per
  # stack, and CAD work here is Python work — it needs the interpreter, the
  # debugger and the venv setting, all of which live in that profile. Profiles
  # do not inherit from one another, so landing this anywhere else would put
  # the viewer in a window with no Python extension to ask which interpreter
  # is selected.
  programs.vscode.profiles.pycode = {
    extensions = [
      ocpCadViewer

      # A hard `extensionDependencies` entry of OCP CAD Viewer — it asks the
      # Python extension which interpreter is selected, and VS Code refuses to
      # enable it at all if that extension is missing. home-manager installs
      # exactly the list given here and resolves no dependencies of its own.
      pkgs.vscode-extensions.ms-python.python

      # Not required to show a model, required for the viewer's other half:
      # halt in the debugger and `show_all(locals())` renders whatever the
      # current frame holds. Without the debug adapter that feature is dead.
      pkgs.vscode-extensions.ms-python.debugpy
    ];

    userSettings = {
      "python.venvPath" = venvDir;

      # The viewer starts on its own when a file mentions ocp_vscode, and it
      # talks to the Python process over a localhost port. Left at the upstream
      # defaults (autostart on, port 3939) — spelled out here only so the port
      # is greppable when something else has taken it.
      "OcpCadViewer.advanced.initialPort" = 3939;

      # build123d is Z-up, which is also the extension's default; CadQuery
      # examples on the web assume it too. Stated rather than inherited so a
      # future default flip cannot silently rotate every model.
      "OcpCadViewer.view.up" = "Z";
    };
  };
}
