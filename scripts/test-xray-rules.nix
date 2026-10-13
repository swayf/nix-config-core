{ pkgs, rulesGenerator ? import ../modules/nixos/xray-monitoring-rules.nix }:
let
  format = pkgs.formats.json { };
  fixture = builtins.readFile ./test-xray-rules.json;
  fixtureNode = "test-vps";
  baseline = rulesGenerator { node = fixtureNode; };
  forNode = node:
    let
      rulesData = rulesGenerator { inherit node; };
      expected = builtins.fromJSON (
        builtins.replaceStrings [ fixtureNode ] [ node ] (builtins.toJSON baseline)
      );
      scoped = pkgs.lib.all (group:
        pkgs.lib.all (rule:
          rule.labels.node == node && pkgs.lib.hasInfix ''node="${node}"'' rule.expr
        ) group.rules
      ) rulesData.groups;
      rules = format.generate "rules-${node}.json" rulesData;
      tests = format.generate "rule-tests-${node}.json" (
        (builtins.fromJSON (builtins.replaceStrings [ fixtureNode ] [ node ] fixture))
        // { rule_files = [ rules ]; }
      );
    in
    assert pkgs.lib.assertMsg (rulesData == expected && scoped)
      "Monitoring rules must use only the caller's node label";
    { inherit rules tests; };
  cases = map forNode [ fixtureNode "alternate-vps" ];
in
pkgs.runCommand "xray-monitoring-rule-tests" { nativeBuildInputs = [ pkgs.prometheus.cli ]; } ''
  ${pkgs.lib.concatMapStringsSep "\n" (case: ''
    promtool check rules ${case.rules}
    promtool test rules ${case.tests}
  '') cases}
  touch $out
''
