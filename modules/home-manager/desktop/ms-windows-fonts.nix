# Windows-font extraction mechanism only. The caller supplies exact file/hash/ISO
# policy and must hold the relevant rights. No archive or output is redistributed.
# Existing fixed outputs can be reused without re-reading their source; changing
# files with the default hash is rejected. Neither a successful build nor a local
# extraction grants redistribution rights. Archive creation/transport stay outside
# this module, and no external HOME path is discovered automatically.
{ defaultFiles, defaultOutputHash, iso, version }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.fonts.msWindows;

  mkWindowsFonts = source: pkgs.stdenvNoCC.mkDerivation {
    pname = "ms-windows-fonts";

    # The build number of the evaluation ISO below, not a version of anything
    # this repository maintains.
    inherit version;

    # The output path depends on its name and contents, not the extraction
    # tools or source path. Keep both source branches on the same hash.
    outputHashMode = "recursive";
    outputHashAlgo = "sha256";
    outputHash =
      # An existing fixed output would otherwise hide a changed file list.
      # Reordering the same list does not change the output.
      assert lib.assertMsg
        (lib.sort builtins.lessThan cfg.files == lib.sort builtins.lessThan defaultFiles
          || cfg.outputHash != defaultOutputHash)
        "fonts.msWindows: changing files requires updating outputHash (use lib.fakeHash to discover it)";
      cfg.outputHash;

    src = if source != null then builtins.path {
      path = source;
      name = "ms-windows-fonts.tar.gz";
    } else pkgs.fetchurl {
      inherit (cfg.iso) url hash;
      name = "windows-enterprise-eval.iso";
    };

    # Do not send proprietary font outputs to substituters.
    allowSubstitutes = false;

    nativeBuildInputs = [ pkgs.p7zip ];

    # 5 GB fetched and 5 GB unpacked, for an output measured in kilobytes:
    # sending that to a remote builder costs more than doing it here.
    preferLocalBuild = true;

    # `7z e` on the ISO takes only the one member out of it. Unpacking the
    # whole image first works too and is what one of the packages above does,
    # but it writes every one of those 5 GB to disk on the way to the same
    # file.
    unpackPhase = ''
      runHook preUnpack
      mkdir fonts
    '' + (if source != null then ''
      tar -xzf "$src" -C fonts -- ${lib.escapeShellArgs cfg.files}
    '' else ''
      7z e "$src" sources/install.wim
      7z e install.wim ${lib.escapeShellArgs (map (f: "Windows/Fonts/${f}") cfg.files)} -ofonts
      rm install.wim
    '') + ''
      runHook postUnpack
    '';

    # 7z exits 0 having extracted nothing when a member does not match, so a
    # renamed or Office-only file would otherwise install silently as an empty
    # font set. Wingdings 2 and 3 are the trap this catches: they ship with
    # Office, not with Windows, and are absent from this image.
    installPhase = ''
      runHook preInstall

      for f in ${lib.escapeShellArgs cfg.files}; do
        if [ ! -s "fonts/$f" ]; then
          echo "ms-windows-fonts: $f is not in this image" >&2
          exit 1
        fi
      done

      for f in ${lib.escapeShellArgs cfg.files}; do
        install -Dm444 "fonts/$f" "$out/share/fonts/truetype/windows/$f"
      done

      runHook postInstall
    '';

    meta = {
      description = "Segoe UI, Wingdings, Symbol and Marlett, from a Windows 11 evaluation image";
      # Not `unfreeRedistributable`: `lib.licenses.unfree` is the one whose
      # `redistributable = false`, which is what keeps this out of any cache
      # that respects the flag.
      license = lib.licenses.unfree;
      platforms = lib.platforms.all;
      # Nothing to gain from Hydra trying, and it must not publish the result.
      hydraPlatforms = [ ];
    };
  };
in
{
  options.fonts.msWindows = {
    enable = lib.mkEnableOption ''
      the Windows-only fonts that ../fonts.nix cannot supply from a free or
      redistributable source. Reads the 5 GB evaluation ISO named by
      `fonts.msWindows.iso` at build time unless a local source is set -- see the header of
      modules/fonts/ms-windows.nix before turning this on
    '';

    source = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Absolute path to a private tar.gz archive with the font files at its
        root. Null uses the pinned ISO. A missing archive fails during
        evaluation; an incomplete archive fails if a build is needed rather
        than silently downloading the ISO. An existing fixed output may be
        reused without reading the archive's contents.
        External local files require --impure when evaluating a flake.
      '';
    };

    outputHash = lib.mkOption {
      type = lib.types.str;
      default = defaultOutputHash;
      description = ''
        Recursive SHA-256 hash of the installed package, not of the ISO or
        tar.gz archive. The default covers the default file list from the
        pinned ISO. Identical fonts from either source share one output path
        and do not rebuild when the extraction tools change.

        Update this together with changes to the selected files or font bytes.
        To discover a new hash, set this to lib.fakeHash, build the package,
        and use the actual hash reported by Nix. Alternatively, run
        `nix hash path <built-package-directory>` on the intended output.

        Nix can reuse an existing output without running the builder, so a
        changed source with an unchanged hash is NOT guaranteed to fail.
        Changing the file list while retaining the default hash is rejected
        during evaluation; custom hashes must still be maintained manually.
      '';
    };

    isoPackage = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      internal = true;
      description = "ISO-backed package for bootstrapping the private archive.";
    };

    iso = {
      url = lib.mkOption {
        type = lib.types.str;
        default = iso.url;
        description = ''
          Windows evaluation image to take the fonts out of. The default is the
          en-us Windows 11 Enterprise evaluation build, which still answered
          when this was written; a language-specific image works the same way
          and additionally carries that language's fonts.
        '';
      };

      hash = lib.mkOption {
        type = lib.types.str;
        default = iso.hash;
        description = ''
          Hash of the image. Not downloaded and hashed here -- 5 GB was not
          spent on it -- but cross-checked against two projects that have
          nothing to do with Nix and pin this same URL for Packer, where the
          checksum is written in hex:

          ```
          $ python3 -c "import base64,binascii; print(binascii.hexlify(
              base64.b64decode('67x5EGcV9E9QIPd72QchsXxah3y8FaNTW5kVVJOhuz8=')).decode())"
          ebbc79106715f44f5020f77bd90721b17c5a877cbc15a3535b99155493a1bb3f
          ```

          which is character for character the `iso_checksum` in
          eryph-org/basecatlets-hyperv and valengus/packer for this URL.

          A mismatch would surface as an ordinary fixed-output hash error on
          the first build; the header of this file has the command to
          re-derive it.
        '';
      };
    };

    files = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = defaultFiles;
      description = ''
        Font files to take out of `Windows/Fonts` in the image. Everything
        already covered by ../fonts.nix is deliberately absent; add a name here
        to pull another one (`sylfaen.ttf`, `gabriola.ttf`, `ebrima.ttf` and
        the CJK `msgothic.ttc`/`simsun.ttc` are the usual candidates).

        Changes to this list require updating outputHash as well. A name the
        image does not carry fails the build rather than being skipped.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    fonts.msWindows.isoPackage = mkWindowsFonts null;
    home.packages = [ (mkWindowsFonts cfg.source) ];
  };
}
