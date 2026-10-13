# Caller-owned SOPS sources. Import the caller's pinned SOPS HM module separately.
{ config, lib, pkgs, ... }:
let
  cfg = config.secrets;
  files = if cfg.file != null then { default = cfg.file; } else cfg.files;
  defaultGroup = if cfg.file != null then "default" else cfg.defaultGroup;
  groupFor = key: lib.findFirst
    (group: lib.elem key (cfg.layout.${group} or [ ])) defaultGroup (builtins.attrNames files);
  secretFile = key: files.${groupFor key} or (throw "No SOPS source configured for '${key}'");
  presentKeys = file: if !builtins.pathExists file then [ ] else
    lib.pipe (lib.splitString "\n" (builtins.readFile file)) [
      (map (builtins.match "([A-Za-z0-9_.-]+):.*"))
      (builtins.filter (m: m != null))
      (map builtins.head)
      (builtins.filter (key: key != "sops"))
      lib.unique
    ];
  hasSecret = key:
    if cfg.allowedKeys != null && !lib.elem key cfg.allowedKeys then false
    else if files == { } then false
    else let present = lib.elem key (presentKeys (secretFile key));
    in lib.warnIf (!present && cfg.warnMissing) (cfg.missingMessage key) present;
  expectedKeys = group: lib.unique (
    lib.mapAttrsToList (_: s: s.key)
      (lib.filterAttrs (_: s: s.sopsFile == files.${group}) config.sops.secrets)
    ++ lib.filter (key: groupFor key == group) cfg.requestedKeys
  );
  groups = builtins.attrNames files;
  editorPath = group: cfg.editor.fileNames.${group} or "${group}.yaml";
  editorGroups = lib.filter (group: expectedKeys group != [ ]) groups;
  editor = pkgs.writeShellApplication {
    name = "edit-secrets";
    runtimeInputs = [ pkgs.sops ];
    text = ''
      files=("$@")
      if [ "$#" -eq 0 ]; then
        files=(${lib.escapeShellArgs (map editorPath editorGroups)})
      fi
      declare -A placeholder=(
        ${lib.concatMapStringsSep "\n  " (key:
          "[${lib.escapeShellArg key}]=${lib.escapeShellArg (builtins.toJSON (cfg.editor.placeholders.${key} or "CHANGEME"))}"
        ) (lib.unique (lib.concatMap expectedKeys groups))}
      )
      for file in "''${files[@]}"; do
        case "$file" in
          ${lib.concatMapStringsSep "\n  " (group: ''
            ${lib.concatStringsSep "|" (lib.unique [
              (lib.escapeShellArg (editorPath group))
              (lib.escapeShellArg group)
              (lib.escapeShellArg "${group}.yaml")
              "*/${lib.escapeShellArg "${group}.yaml"}"
            ])})
              keys=(${lib.escapeShellArgs (expectedKeys group)})
              ;;
          '') groups}
          *) echo "edit-secrets: choose ${lib.concatStringsSep ", " groups}" >&2; exit 1 ;;
        esac
        case "$file" in
          ${lib.concatMapStringsSep "\n  " (group: ''
            ${lib.escapeShellArg group}) file=${lib.escapeShellArg (editorPath group)} ;;
          '') groups}
        esac
        if [ ! -e "$file" ]; then
          echo "edit-secrets: no such file: $file (configure the caller-owned encrypted source first)" >&2
          exit 1
        fi
        if ! sops decrypt "$file" >/dev/null 2>&1; then
          echo "edit-secrets: cannot decrypt $file -- is the configured age identity in place?" >&2
          exit 1
        fi
        for key in "''${keys[@]}"; do
          IFS=/ read -r -a parts <<< "$key"
          index=""
          for part in "''${parts[@]}"; do index+="[\"$part\"]"; done
          if sops decrypt --extract "$index" "$file" >/dev/null 2>&1; then
            continue
          fi
          echo "edit-secrets: seeding $key"
          sops set "$file" "$index" "''${placeholder[$key]}"
        done
        sops edit "$file" || {
          status=$?
          if [ "$status" -ne 200 ]; then exit "$status"; fi
        }
      done
    '';
  };
in {
  options.secrets = {
    file = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Encrypted per-device YAML; null permits secret-free bootstrap.";
    };
    files = lib.mkOption {
      type = lib.types.attrsOf lib.types.path;
      default = { };
      description = "Explicit legacy multi-file sources; mutually exclusive with file.";
    };
    layout = lib.mkOption {
      type = lib.types.attrsOf (lib.types.listOf lib.types.str);
      default = { };
      description = "Key names assigned to each source group; no ciphertext migration.";
    };
    defaultGroup = lib.mkOption {
      type = lib.types.str;
      default = "default";
      description = "Fallback group and default SOPS source in multi-file mode.";
    };
    allowedKeys = lib.mkOption {
      type = lib.types.nullOr (lib.types.listOf lib.types.str);
      default = null;
      description = "Consumer selection, not decryption access control.";
    };
    requestedKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Keys the optional editor seeds before consumers can be enabled.";
    };
    warnMissing = lib.mkOption { type = lib.types.bool; default = false; };
    missingMessage = lib.mkOption {
      type = lib.types.functionTo lib.types.str;
      default = key: "secrets: '${key}' is absent from its configured encrypted source; its consumer stays off.";
      description = "Caller-specific provisioning guidance, never a private core default.";
    };
    editor = {
      enable = lib.mkEnableOption "SOPS editor with non-overwriting missing-key seeding";
      fileNames = lib.mkOption {
        type = lib.types.attrsOf lib.types.str;
        default = { };
        description = "Mutable checkout filenames, not immutable store paths.";
      };
      placeholders = lib.mkOption {
        type = lib.types.attrsOf lib.types.str;
        default = { };
        description = "Non-secret seed values; unspecified keys use CHANGEME.";
      };
    };
  };
  config = {
    assertions = [
      { assertion = cfg.file == null || cfg.files == { };
        message = "Choose secrets.file or secrets.files, not both."; }
      { assertion = files == { } || builtins.hasAttr defaultGroup files;
        message = "secrets.defaultGroup must name a configured source."; }
      { assertion = lib.all (group: builtins.hasAttr group files) (builtins.attrNames cfg.layout);
        message = "Every secrets.layout group must have a configured source."; }
      { assertion = lib.all (group: builtins.match "[A-Za-z0-9_-]+" group != null) groups;
        message = "SOPS source group names must be shell-safe identifiers."; }
      { assertion = !cfg.editor.enable || files != { };
        message = "edit-secrets requires a configured encrypted source."; }
    ];
    _module.args = { inherit hasSecret secretFile; };
    home.packages = lib.optionals cfg.editor.enable [ pkgs.sops pkgs.age editor ];
    sops = {
      age.keyFile = lib.mkDefault "${config.home.homeDirectory}/.config/sops/age/keys.txt";
    } // lib.optionalAttrs (builtins.hasAttr defaultGroup files) {
      defaultSopsFile = files.${defaultGroup};
    };
  };
}
