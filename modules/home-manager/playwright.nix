# The caller's pinned CLI includes matching browsers. Do not set global browser
# paths: project-local clients can require different browser revisions.
{ pkgs, ... }: {
  home.packages = [ pkgs.playwright-test ];
}
