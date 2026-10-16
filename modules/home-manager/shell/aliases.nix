# General interactive aliases; remote hosts, browser profiles and personal commands stay caller-owned.
{ pkgs, ... }: {
  home.shellAliases = {
    l = "eza -l";
    ll = "eza -la";
    cat = "bat --paging=never";
    vi = "vim";
    open = "xdg-open";
    st = "subl";
    subl3 = "sublime3";
    cp = "cp -i"; # confirm before overwriting
    df = "df -h";
    free = "free -m";
    gitu = "git add . && git commit && git push";
    c = "clip copy";
    v = "clip paste";
    zfs_list_snapshots = "zfs list -r -t snapshot";
    kctx = "kubectx";
    kns = "kubens";
    kcc = "kubectx"; # change context
    kcn = "kubens"; # change namespace
    krc = "kubectx -c"; # current context
    krn = "kubens -c"; # current namespace
    clean_snap_cache = "snap-clean cache";
    clean_snap_shots = "snap-clean snapshots";
    clean_snap_revs = "snap-clean revisions";
    sedutil-cli = "sudo ${pkgs.sedutil}/bin/sedutil-cli";
    sedutil-scan = "sudo ${pkgs.sedutil}/bin/sedutil-cli --scan";
    happ-decode = "sub-decode";
  };
}
