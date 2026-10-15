# CLI + all upstream skills/references/scripts, pinned together. No pipx/uv
# download at container startup; the Nix closure lives in the private store.
{ src, lib, python3Packages }:
python3Packages.buildPythonApplication rec {
  pname = "obsidian-wiki";
  version = "2026.10.1";
  format = "wheel";

  inherit src;

  # Optional [graph] extra: proper community detection instead of the fallback.
  dependencies = with python3Packages; [ igraph leidenalg ];
  pythonImportsCheck = [ "obsidian_wiki.cli" "obsidian_wiki.graph_analysis" ];

  postInstall = ''
    mkdir -p "$out/share"
    ln -s "$out/${python3Packages.python.sitePackages}/obsidian_wiki/_data" \
      "$out/share/obsidian-wiki"
    test -f "$out/share/obsidian-wiki/skills/llm-wiki/SKILL.md"
    test -f "$out/share/obsidian-wiki/bootstrap/AGENTS.md"
  '';

  meta = {
    description = "Skill-based Obsidian knowledge-base framework and CLI";
    homepage = "https://github.com/Ar9av/obsidian-wiki";
    license = lib.licenses.mit;
    mainProgram = "obsidian-wiki";
  };
}
