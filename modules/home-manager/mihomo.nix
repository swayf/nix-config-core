# Proxy mechanism; caller owns credentials and pinned rule seeds.
{
  config,
  lib,
  pkgs,
  hasSecret,
  secretFile,
  ...
}:

let
  cfg = config.services.mihomo;

  workDir = "${config.xdg.stateHome}/mihomo";

  controller = "127.0.0.1:9999";

  dashboard = pkgs.zashboard;

  hasSub = hasSecret "mhsub";
  hasNodes = hasSecret "mhnodes";

  hasHwid = hasSecret "mhhwid";

  providers = lib.optional hasSub "sub" ++ lib.optional hasNodes "own";
  enabled = providers != [ ];
  egressNames = builtins.attrNames cfg.fixedEgress;
  listenerPorts = [ cfg.mixedPort 9999 ]
    ++ lib.optional cfg.us.enable cfg.us.port
    ++ lib.optional cfg.direct.enable cfg.direct.port
    ++ map (name: cfg.fixedEgress.${name}.port) egressNames;

  indentTail =
    n: text:
    let
      pad = lib.concatStrings (lib.replicate n " ");
    in
    lib.concatStringsSep "\n" (
      lib.imap0 (i: line: if i == 0 || line == "" then line else pad + line) (lib.splitString "\n" text)
    );

  subHeaders = [
    "User-Agent:"
    ''- "clash.meta"''
  ]
  ++ lib.optionals hasHwid [
    "x-hwid:"
    ''- "${config.sops.placeholder.mhhwid}"''
    "x-device-os:"
    ''- "Linux"''
    "x-device-model:"
    ''- "mihomo"''
  ];

  subProvider = lib.removeSuffix "\n" ''
    sub:
      type: http
      url: "${config.sops.placeholder.mhsub}"
      path: ./providers/sub.yaml

      interval: 3600

      header:
        ${indentTail 4 (lib.concatStringsSep "\n" subHeaders)}

      exclude-filter: '(?i)(поддерживается|восстановить|telegram|bypass\.click|наш сервис|выберите сервер|узнать больше)'
      proxy: DIRECT
      health-check:
        enable: true
        url: https://cp.cloudflare.com/generate_204
        interval: 300
  '';

  ownProvider = lib.removeSuffix "\n" ''
    own:
      type: file
      path: ./nodes.yaml
      health-check:
        enable: true
        url: https://cp.cloudflare.com/generate_204
        interval: 300
  '';

  proxyProviders = lib.concatStringsSep "\n\n" (
    lib.optional hasSub subProvider ++ lib.optional hasNodes ownProvider
  );

  sub-decode = pkgs.writeShellApplication {
    name = "sub-decode";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.openssl
      pkgs.jq
    ];
    text = ''
      # Public INCY protocol constant, not a caller credential. The legacy CTR
      # decoder does not authenticate the GCM tag; review its URL before use.
      INCY_CRYPT1_KEY=f6d40ea0c8a8899d7c682d09ba0d4165dfe2b3dd45e6bb3e25cb233cf00c2462

      usage() {
        cat >&2 <<'EOF'
      usage: sub-decode <link>
             wl-paste | sub-decode

      Prints the subscription URL carried inside a client deeplink -- happ://,
      incy://, v2raytun://, clash://install-config?url=... and the rest of them.
      EOF
      }

      note() {
        printf 'sub-decode: %s\n' "$1" >&2
      }

      emit() {
        printf '%s\n' "$1"

        case "$1" in
          http://*|https://*)
            if [ -t 1 ]; then
              note "this is what 'mhsub' wants -- edit-secrets"
            fi
            ;;
          vless://*|vmess://*|trojan://*|ss://*)
            note "share link, not a subscription -- this one belongs in 'mhnodes'"
            ;;
          *)
            note "not a URL -- routing rules travel in the same shape"
            ;;
        esac

        exit 0
      }

      b64d() {
        local s
        s=$(printf '%s' "$1" | tr -- '-_' '+/')
        while [ $(( ''${#s} % 4 )) -ne 0 ]; do
          s="$s="
        done
        printf '%s' "$s" | base64 -d 2>/dev/null
      }

      is_text() {
        [ -n "$1" ] || return 1
        case "$1" in
          *[![:print:][:space:]]*) return 1 ;;
        esac
      }

      urldecode() {
        local s=''${1//+/ }
        printf '%b' "''${s//%/\\x}"
      }

      clear_address() {
        local a=$1
        while :; do
          case "$a" in
            http://*|https://*|vless://*|vmess://*|trojan://*|ss://*)
              printf '%s' "$a"
              return 0
              ;;
            */*) a=''${a#*/} ;;
            *) return 1 ;;
          esac
        done
      }

      decrypt_incy() {
        local payload=$1 iv size plain name
        payload=''${payload%%\?*}
        payload=''${payload%%#*}
        payload=''${payload%/}

        iv=$(b64d "$payload" | head -c 12 | od -An -tx1 -v | tr -d ' \n')
        size=$(b64d "$payload" | wc -c)

        if [ "''${#iv}" -ne 24 ] || [ "$size" -lt 29 ]; then
          note "incy://crypt1 payload is too short to be a link"
          exit 1
        fi

        plain=$(
          b64d "$payload" | tail -c +13 | head -c "$(( size - 28 ))" |
            openssl enc -d -aes-256-ctr -K "$INCY_CRYPT1_KEY" -iv "''${iv}00000002"
        )

        if ! printf '%s' "$plain" | jq -e 'has("url")' > /dev/null 2>&1; then
          note "incy://crypt1 did not decrypt -- damaged link, or a re-keyed client"
          exit 1
        fi

        name=$(printf '%s' "$plain" | jq -r '.n // empty')
        if [ -n "$name" ]; then
          note "subscription name: $name"
        fi

        emit "$(printf '%s' "$plain" | jq -r '.url')"
      }

      case "''${1-}" in
        -h | --help)
          usage
          exit 0
          ;;
      esac

      link=''${1-}
      if [ -z "$link" ]; then
        if [ -t 0 ]; then
          usage
          exit 2
        fi
        link=$(cat)
      fi

      link=$(printf '%s' "$link" | tr -d '[:space:]')

      case "$link" in
        "")
          note "empty input"
          exit 2
          ;;
        http://*|https://*)
          printf '%s\n' "$link"
          note "already a plain subscription URL -- nothing to unwrap"
          exit 0
          ;;
        *://*) ;;
        *)
          note "not a deeplink: $link"
          exit 1
          ;;
      esac

      scheme=''${link%%://*}
      rest=''${link#*://}

      case "$scheme/$rest" in
        incy/crypt1/*)
          decrypt_incy "''${rest#crypt1/}"
          ;;
        incy/crypt*)
          note "incy://''${rest%%/*} is newer than crypt1 and its key is not here"
          exit 1
          ;;
        happ/crypt*)
          cat >&2 <<'EOF'
      sub-decode: encrypted Happ link (happ://crypt...). The subscription address
      is RSA-4096 encrypted and the keys are inside the Happ application -- hiding
      that address from you is what the format is for, so there is nothing here to
      unwrap it with. Ask for a plain subscription link instead.
      EOF
          exit 1
          ;;
      esac

      case "$rest" in
        *\?*)
          qs=''${rest#*\?}
          qs=''${qs%%#*}
          while [ -n "$qs" ]; do
            param=''${qs%%&*}
            case "$param" in
              url=*|URL=*)
                decoded=$(urldecode "''${param#*=}")
                [ -n "$decoded" ] && emit "$decoded"
                ;;
            esac
            case "$qs" in
              *\&*) qs=''${qs#*&} ;;
              *) qs="" ;;
            esac
          done
          ;;
      esac

      if out=$(clear_address "$rest"); then
        emit "$out"
      fi

      out=""
      for candidate in "''${rest##*/}" "''${rest#*/}" "$rest"; do
        candidate=''${candidate%%\?*}
        [ -n "$candidate" ] || continue

        if out=$(b64d "$candidate") && is_text "$out"; then
          break
        fi
        out=""
      done

      if [ -z "$out" ]; then
        note "no payload found in $link"
        exit 1
      fi

      emit "$out"
    '';
  };
in
{
  options.services.mihomo = {
    ruleSeed = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Caller-pinned directory containing domains_all.lst and ipsum.lst; required for an enabled provider.";
    };
    us = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = hasNodes;
        description = "Expose a dedicated US-only proxy backed by the private node provider.";
      };
      port = lib.mkOption {
        type = lib.types.port;
        default = 2081;
        description = "Stable HTTP/SOCKS5 port for US egress, including SOCKS5 UDP.";
      };
      listen = lib.mkOption {
        type = lib.types.str;
        default = "127.0.0.1";
        description = ''
          Address of the US listener. For a container gateway, use a dedicated
          host bridge address and restrict access with the host firewall.
          This listener has no authentication; do not expose it to the LAN.
        '';
      };
      nodeFilter = lib.mkOption {
        type = lib.types.str;
        default = "^us$";
        description = ''
          Exact-name regular expression selecting the US node from mhnodes.
          The default expects a VLESS share link ending in #us. No matching
          node means REJECT, never a direct connection or a subscription node.
        '';
      };
      httpProxy = lib.mkOption {
        type = lib.types.str;
        readOnly = true;
        default = "http://${cfg.us.listen}:${toString cfg.us.port}";
        description = "US HTTP proxy URL (listen must be a reachable address).";
      };
      socksProxy = lib.mkOption {
        type = lib.types.str;
        readOnly = true;
        default = "socks5h://${cfg.us.listen}:${toString cfg.us.port}";
        description = "US SOCKS5 URL with remote DNS resolution.";
      };
    };

    fixedEgress = lib.mkOption {
      default = {};
      description = "Additional loopback-only listeners, each pinned to one exact private node with REJECT on an empty provider match.";
      type = lib.types.attrsOf (lib.types.submodule ({ config, ... }: {
        options = {
          nodeName = lib.mkOption {
            type = lib.types.strMatching "[A-Za-z0-9_-]+";
            description = "Exact node label in mhnodes, not a regular expression or credential.";
          };
          port = lib.mkOption { type = lib.types.port; };
          httpProxy = lib.mkOption {
            type = lib.types.str;
            readOnly = true;
            default = "http://127.0.0.1:${toString config.port}";
          };
        };
      }));
    };

    direct = {
      enable = lib.mkEnableOption "a loopback-only proxy with forced DIRECT host egress";
      port = lib.mkOption {
        type = lib.types.port;
        default = 2082;
        description = "HTTP/SOCKS5 port for explicit direct egress via the host, bypassing routing rules.";
      };
    };

    mixedPort = lib.mkOption {
      type = lib.types.port;
      default = 2080;
      description = ''
        Port of mihomo's mixed HTTP+SOCKS listener -- the one every program that
        has to leave through the tunnel is pointed at, since a user unit cannot
        have a TUN device and nothing is captured transparently.

        Loopback only: `allow-lan` is false in the configuration below.
      '';
    };

    httpProxy = lib.mkOption {
      type = lib.types.str;
      readOnly = true;
      default = "http://127.0.0.1:${toString cfg.mixedPort}";
      defaultText = lib.literalExpression ''"http://127.0.0.1:''${toString config.services.mihomo.mixedPort}"'';
      description = ''
        The same listener as an HTTP proxy URL. Preferred wherever a program
        accepts either: an HTTP proxy is asked for the host by name
        (`CONNECT host:443`), so mihomo resolves it and matches its domain rules
        on a name instead of on an address the client resolved first.
      '';
    };

    socksProxy = lib.mkOption {
      type = lib.types.str;
      readOnly = true;
      default = "socks5://127.0.0.1:${toString cfg.mixedPort}";
      defaultText = lib.literalExpression ''"socks5://127.0.0.1:''${toString config.services.mihomo.mixedPort}"'';
      description = ''
        The same listener as a SOCKS5 URL, for the programs that take only that
        form.
      '';
    };
  };

  config = {
    assertions = [
      {
        assertion = egressNames == [] || hasNodes;
        message = "services.mihomo.fixedEgress requires the private mhnodes secret.";
      }
      {
        assertion = lib.all (name: builtins.match "[a-z][a-z0-9-]*" name != null) egressNames;
        message = "services.mihomo.fixedEgress names must contain only lowercase letters, digits and hyphens, starting with a letter.";
      }
      {
        assertion = egressNames == [] || lib.length listenerPorts == lib.length (lib.unique listenerPorts);
        message = "Mihomo listener and controller ports must be distinct, including fixedEgress ports.";
      }
      {
        assertion = !enabled || cfg.ruleSeed != null;
        message = "Mihomo providers require an explicitly pinned services.mihomo.ruleSeed.";
      }
      {
        assertion = !cfg.us.enable || hasNodes;
        message = "services.mihomo.us requires the mhnodes secret with the private US VLESS node.";
      }
      {
        assertion = !cfg.us.enable || (cfg.us.port != cfg.mixedPort && cfg.us.port != 9999);
        message = "services.mihomo.us.port must differ from the main proxy and controller ports.";
      }
      {
        assertion =
          !cfg.direct.enable
          || (
            cfg.direct.port != cfg.mixedPort
            && cfg.direct.port != 9999
            && (!cfg.us.enable || cfg.direct.port != cfg.us.port)
          );
        message = "services.mihomo.direct.port must differ from the main, US and controller ports.";
      }
    ];

    home.packages = [
      pkgs.mihomo
      sub-decode
    ];

    home.activation.mihomoSeed = lib.mkIf enabled (
      lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        seedFile() {
          [ -e "$2" ] && return 0
          run mkdir -p "$(dirname "$2")"
          run install -p -m644 "$1" "$2"
        }

        seedFile ${pkgs.v2ray-domain-list-community}/share/v2ray/geosite.dat ${workDir}/GeoSite.dat
        seedFile ${pkgs.v2ray-geoip}/share/v2ray/geoip.dat ${workDir}/GeoIP.dat
        seedFile ${cfg.ruleSeed}/domains_all.lst ${workDir}/rules/refilter-domains.lst
        seedFile ${cfg.ruleSeed}/ipsum.lst ${workDir}/rules/refilter-ip.lst
      ''
    );

    sops.secrets =
      lib.optionalAttrs hasSub {
        mhsub = { };
      }
      // lib.optionalAttrs hasHwid {
        mhhwid = { };
      }
      // lib.optionalAttrs hasNodes {
        mhnodes = {
          sopsFile = secretFile "mhnodes";
          path = "${workDir}/nodes.yaml";
          mode = "0400";
        };
      };

    sops.templates = lib.mkIf enabled {
      "mihomo-config.yaml" = {
        path = "${workDir}/config.yaml";

        content = ''
          mixed-port: ${toString cfg.mixedPort}
          allow-lan: false
          mode: rule
          log-level: info
          ipv6: false

          ${lib.optionalString (cfg.us.enable || cfg.direct.enable || egressNames != []) ''
            listeners:
            ${lib.optionalString cfg.us.enable ''
              - name: us
                type: mixed
                listen: ${builtins.toJSON cfg.us.listen}
                port: ${toString cfg.us.port}
                udp: true
                proxy: us
            ''}
            ${lib.optionalString cfg.direct.enable ''
              - name: host-direct
                type: mixed
                listen: "127.0.0.1"
                port: ${toString cfg.direct.port}
                udp: true
                proxy: DIRECT
            ''}${lib.concatMapStrings (name: ''
              - name: egress-${name}
                type: mixed
                listen: "127.0.0.1"
                port: ${toString cfg.fixedEgress.${name}.port}
                udp: true
                proxy: egress-${name}
            '') egressNames}
          ''}

          external-controller: ${controller}

          geodata-mode: true
          geodata-loader: memconservative
          geo-auto-update: true
          geo-update-interval: 24

          profile:
            store-selected: true

          proxy-providers:
            ${indentTail 2 proxyProviders}

          rule-providers:
            refilter-domains:
              type: http
              behavior: domain
              format: text
              url: https://raw.githubusercontent.com/1andrevich/Re-filter-lists/main/domains_all.lst
              path: ./rules/refilter-domains.lst
              interval: 86400
              proxy: DIRECT

            refilter-ip:
              type: http
              behavior: ipcidr
              format: text
              url: https://raw.githubusercontent.com/1andrevich/Re-filter-lists/main/ipsum.lst
              path: ./rules/refilter-ip.lst
              interval: 86400
              proxy: DIRECT

          proxy-groups:
            ${indentTail 2 (
              lib.optionalString cfg.us.enable ''
                - name: us
                  type: select
                  use: [ own ]
                  filter: ${builtins.toJSON cfg.us.nodeFilter}
                  empty-fallback: REJECT
              ''
            )}${indentTail 2 (lib.concatMapStrings (name: ''

              - name: egress-${name}
                type: select
                use: [ own ]
                filter: ${builtins.toJSON ("^" + cfg.fixedEgress.${name}.nodeName + "$")}
                empty-fallback: REJECT
            '') egressNames)}

            - name: PROXY
              type: select
              proxies: [ AUTO, DIRECT ]
              use: [ ${lib.concatStringsSep ", " providers} ]

            - name: AUTO
              type: url-test
              use: [ ${lib.concatStringsSep ", " providers} ]
              url: https://cp.cloudflare.com/generate_204
              interval: 300
              tolerance: 50

          rules:
            - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
            - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
            - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
            - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
            - DOMAIN-SUFFIX,lan,DIRECT
            - DOMAIN-SUFFIX,local,DIRECT

            - GEOSITE,category-ads-all,REJECT

            - RULE-SET,refilter-domains,PROXY
            - RULE-SET,refilter-ip,PROXY,no-resolve

            - GEOSITE,category-ru,DIRECT

            - GEOIP,RU,DIRECT,no-resolve

            - MATCH,PROXY
        '';
      };
    };

    systemd.user.services.mihomo = lib.mkIf enabled {
      Unit = {
        Description = "mihomo rule-based proxy core";
        Documentation = [ "https://wiki.metacubex.one/" ];

        Requires = [ "sops-nix.service" ];
        After = [ "sops-nix.service" ];
      };

      Service = {
        ExecStart = lib.concatStringsSep " " [
          (lib.getExe pkgs.mihomo)
          "-d ${workDir}"
          "-f ${config.sops.templates."mihomo-config.yaml".path}"
          "-ext-ui ${dashboard}"
        ];

        Restart = "on-failure";
        RestartSec = 10;
      };

      Install.WantedBy = [ "default.target" ];
    };
  };
}
