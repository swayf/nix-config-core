# Local CLI tools. Package set, identity and shell configuration belong to the caller.
{ pkgs, ... }: {
  programs = {
    bat.enable = true;
    btop.enable = true;
    jq.enable = true;
    # Yield Ctrl-R to atuin; preserve file/directory pickers.
    fzf = { enable = true; historyWidget.command = ""; };
    eza = { enable = true; git = true; extraOptions = [ "--group-directories-first" ]; };
    zoxide.enable = true;
    atuin = {
      enable = true;
      flags = [ "--disable-up-arrow" ];
      settings = {
        style = "compact";
        inline_height = 20;
        show_preview = true;
        update_check = false;
      };
      forceOverwriteSettings = true;
    };
    # Toolchains and existing history remain mutable, caller-managed state.
    mise.enable = true;
    direnv = { enable = true; nix-direnv.enable = true; };
  };
  home.packages = [ pkgs.kubectx ];
}
