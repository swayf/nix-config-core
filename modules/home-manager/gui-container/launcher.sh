# Included by writeShellApplication; GUI_* values are supplied by Nix.
set -E
umask 077
# Unset keeps the declared socket; explicit empty selects WAYLAND_DISPLAY.
# This lets waypipe supply its per-connection socket without changing WSLg defaults.
if [[ ${GCNT_WAYLAND_SOCKET+x} ]]; then
  export GUI_WAYLAND_SOCKET="$GCNT_WAYLAND_SOCKET"
fi
command=${1:-up}
if (( $# )); then shift; fi
usage() {
  echo 'Usage: gcnt {up|down|rebuild|update|shell|firefox [ARG...]|brave [ARG...]|chrome [ARG...]|exec COMMAND...|status|stats|logs|doctor|check|help}'
}
case "$command" in
  up|down|rebuild|update|status|stats|logs|doctor|check|shell|help|--help|-h) (( $# == 0 )) || { echo "Unexpected arguments for $command" >&2; exit 2; } ;;
  exec) (( $# )) || { echo 'Usage: gcnt exec COMMAND [ARG...]' >&2; exit 2; } ;;
  firefox|brave|chrome) set -- "$command" "$@"; command='exec' ;;
  *) usage >&2; exit 2 ;;
esac
# Help/update need neither a desktop, Podman nor the lifecycle lock.
case "$command" in
  help|--help|-h)
    usage
    printf '%s\n' \
      '  up       Prepare the Wayland/WSLg container without opening windows (default).' \
      '  rebuild  Prepare images and remove old containers/images; do not start.' \
      '  update   Update checkout nixpkgs lock only; rebuild afterwards.' \
      '  shell    Open Bash with GUI access; no browser is started automatically.' \
      '  exec     Run an application as a host Wayland/WSLg window, or any command.' \
      '  firefox/brave/chrome [ARG...]  Launch a browser; equivalent to exec BROWSER.' \
      '  down     Remove containers, not persistent data.' \
      '  status/stats/logs  Inspect containers, CPU/RAM or logs.' \
      '  doctor/check      Check host prerequisites or US egress.' \
      '  All except help/update clean stopped gcnt containers and unused old gcnt images.'
    exit 0
    ;;
  update)
    flake=${HM_FLAKE:-}
    if [[ -z $flake ]]; then
      if [[ -f flake.nix ]]; then flake=$PWD; else flake=$HOME/.config/home-manager; fi
    fi
    [[ -f $flake/flake.nix ]] || {
      echo "No flake.nix in $flake; set HM_FLAKE to your Home Manager checkout." >&2; exit 1
    }
    flake=$(cd -- "$flake" && pwd -P)
    echo 'Updating shared nixpkgs pin (also affects other Home Manager packages).' >&2
    nix flake update --flake "path:$flake" nixpkgs
    printf 'Lock updated. To apply, run: nix run %q -- rebuild\n' "path:$flake#${GUI_FLAKE_OUTPUT:-gcnt}"
    exit 0
    ;;
esac
(( EUID != 0 )) || { echo 'Run as your desktop user, not root.' >&2; exit 1; }

# Refuse HOME/ancestor mounts, including symlinks. Validate before mkdir.
validate_directory() {
  local directory host_home
  directory=$(realpath -m -- "$1")
  host_home=$(realpath -e -- "$HOME")
  if [[ $directory == / || $directory != /* || $directory == *,* || $directory == *$'\n'* || $host_home/ == "$directory/"* ]]; then
    echo "Refusing unsafe data directory: $1" >&2; return 1
  fi
}
validate_directory "$GUI_STATE"
validate_directory "$GUI_SHARED"
GUI_STATE=$(realpath -m -- "$GUI_STATE")
GUI_SHARED=$(realpath -m -- "$GUI_SHARED")
[[ $GUI_STATE/ != "$GUI_SHARED/"* && $GUI_SHARED/ != "$GUI_STATE/"* ]] || {
  echo 'State and shared directories must not overlap.' >&2; exit 1;
}
mkdir -p "$GUI_STATE"
chmod 700 "$GUI_STATE"
exec 9>"$GUI_STATE/lock"
flock 9
# conmon and the session must not inherit the lifecycle lock.
p() { "$GUI_PODMAN" "$@" 9>&-; }
work=hm-gui
gateway=hm-gui-gateway
running() { [[ $(p inspect --format '{{.State.Running}}' "$1" 2>/dev/null) == true ]]; }
remove_containers() {
  if p container exists "$work"; then p rm -f "$work"; fi
  if p container exists "$gateway"; then p rm -f "$gateway"; fi
}
# Resolve at runtime, not during Nix evaluation. Explicit devices remain strict.
select_gpu() {
  [[ -z $GUI_GPU && ${GUI_GPU_AUTO:-0} == 1 ]] || return 0
  local device vendor
  for device in /dev/dri/renderD[0-9]*; do
    [[ -c $device && -r $device && -w $device ]] || continue
    if ! IFS= read -r vendor 2>/dev/null < "/sys/class/drm/${device##*/}/device/vendor"; then continue; fi
    case "$vendor" in
      0x8086|0x1002) GUI_GPU=$device; break ;;
    esac
  done
}
doctor() {
  select_gpu
  [[ $(p info --format '{{.Host.OCIRuntime.Name}} {{.Host.Security.Rootless}}') == 'crun true' ]] || {
    echo 'Rootless Podman with crun is required.' >&2; return 1;
  }
  [[ -r /dev/net/tun && -w /dev/net/tun ]] || { echo 'No access to /dev/net/tun.' >&2; return 1; }
  if [[ -n $GUI_GPU ]]; then
    [[ -c $GUI_GPU && -r $GUI_GPU && -w $GUI_GPU ]] || { echo "No access to $GUI_GPU" >&2; return 1; }
  fi
  echo 'Rootless crun and devices: OK'
}
# Show elapsed time without polluting command stdout or inheriting the lock.
# Accept shell functions too, so a complete import pipeline is timed and logged.
timed_step() {
  local label=$1
  shift
  local output pid started elapsed status next_report=10
  output=$(mktemp "$GUI_STATE/podman-step.XXXXXXXX")
  started=$SECONDS
  "$@" >"$output" 2>&1 9>&- &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    elapsed=$((SECONDS - started))
    if [[ -t 2 ]]; then
      printf '\r%s... %ds' "$label" "$elapsed" >&2
    elif (( elapsed >= next_report )); then
      printf '%s is still running (%ds)...\n' "$label" "$elapsed" >&2
      next_report=$((next_report + 10))
    fi
    sleep 0.2
  done
  if wait "$pid"; then status=0; else status=$?; fi
  if [[ -t 2 ]]; then printf '\r\033[K' >&2; fi
  printf '%s: finished with status %s (%ds).\n' "$label" "$status" "$((SECONDS - started))" >&2
  if (( status != 0 )); then cat "$output" >&2; fi
  rm -f -- "$output"
  return "$status"
}
podman_step() {
  local label=$1
  shift
  timed_step "$label" p "$@"
}
load_gui_image() (
  # A generator failure must fail the import even if Podman exits successfully.
  set -o pipefail
  "$GUI_IMAGE" | p load
)
load_images() (
  # A cached start should not query GraphRoot or allocate an import directory.
  local need_work=false need_gateway=false
  if ! p image exists "$GUI_IMAGE_REF"; then need_work=true; fi
  if ! p image exists "$GUI_GATEWAY_REF"; then need_gateway=true; fi
  if [[ $need_work == false && $need_gateway == false ]]; then return 0; fi
  local temporary
  temporary=$(mktemp -d "$(p info --format '{{.Store.GraphRoot}}')/gui-import.XXXXXXXX")
  trap 'rm -rf -- "$temporary"' EXIT
  export TMPDIR=$temporary
  if [[ $need_work == true ]]; then timed_step 'Importing GUI image' load_gui_image; fi
  if [[ $need_gateway == true ]]; then podman_step 'Importing gateway image' load -i "$GUI_GATEWAY_IMAGE"; fi
)
prune_old_images() {
  # Only our repositories. Keep pinned images and images used by any container,
  # including stopped containers. Never force removal or prune unrelated images.
  local images image containers
  if ! images=$(p images --format '{{.Repository}}:{{.Tag}}'); then
    echo 'Warning: could not list old GUI images; skipping cleanup.' >&2
    return 0
  fi
  while IFS= read -r image; do
    case "${image%:*}" in
      "${GUI_IMAGE_REF%:*}"|"${GUI_GATEWAY_REF%:*}") ;;
      *) continue ;;
    esac
    [[ $image != "$GUI_IMAGE_REF" && $image != "$GUI_GATEWAY_REF" ]] || continue
    if ! containers=$(p ps -a --filter "ancestor=$image" --format '{{.ID}}'); then
      echo "Warning: could not check containers using $image; keeping it." >&2
      continue
    fi
    [[ -z $containers ]] || continue
    echo "Removing old GUI image: $image" >&2
    if ! p rmi "$image" >&2; then
      echo "Warning: could not remove $image; keeping it for a later cleanup." >&2
    fi
  done <<< "$images"
}
cleanup_unused() {
  # These are the only container names created by this launcher (including
  # older versions). Never force removal: a running/paused container stays.
  local container status
  for container in "$work" "$gateway"; do
    if ! p container exists "$container"; then continue; fi
    if ! status=$(p inspect --format '{{.State.Status}}' "$container"); then
      echo "Warning: could not inspect $container; skipping cleanup." >&2
      continue
    fi
    case "$status" in
      created|exited|stopped)
        echo "Removing stopped GUI container: $container" >&2
        if ! p rm "$container" >&2; then
          echo "Warning: could not remove $container; keeping it for a later cleanup." >&2
        fi
        ;;
    esac
  done
  prune_old_images
}
up() {
  : "${XDG_RUNTIME_DIR:?Run from a logged-in Wayland desktop}"
  local wayland_socket pulse_socket='' session_spec ready=false
  if [[ -n ${GUI_WAYLAND_SOCKET:-} ]]; then
    [[ $GUI_WAYLAND_SOCKET == /* ]] || { echo 'Host Wayland socket must be absolute.' >&2; return 1; }
    wayland_socket=$GUI_WAYLAND_SOCKET
  else
    : "${WAYLAND_DISPLAY:?A Wayland session is required}"
    case "$WAYLAND_DISPLAY" in
      /*) wayland_socket=$WAYLAND_DISPLAY ;;
      *) wayland_socket=$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY ;;
    esac
  fi
  wayland_socket=$(realpath -e -- "$wayland_socket")
  [[ -S $wayland_socket && $wayland_socket != *,* && $wayland_socket != *$'\n'* ]] || {
    echo 'Invalid host Wayland socket.' >&2; return 1;
  }
  local -a audio_args=() device_args=()
  if [[ $GUI_AUDIO == 1 ]]; then
    pulse_socket=$XDG_RUNTIME_DIR/pulse/native
    [[ -S $pulse_socket && $pulse_socket != *,* && $pulse_socket != *$'\n'* ]] || {
      echo 'Host PulseAudio/PipeWire-Pulse socket is missing or invalid.' >&2; return 1;
    }
    audio_args+=(--mount "type=bind,src=$pulse_socket,dst=/run/host-pulse,ro=true" --env PULSE_SERVER=unix:/run/host-pulse)
  fi
  select_gpu
  if [[ -n $GUI_GPU ]]; then
    device_args+=(--device="$GUI_GPU" --group-add=keep-groups --env LIBGL_ALWAYS_SOFTWARE=0)
  else
    device_args+=(--env LIBGL_ALWAYS_SOFTWARE=1)
  fi
  # Socket inode changes at logout/restart, even if its path stays the same.
  session_spec=$(printf '%s\n' "$GUI_SPEC" "$wayland_socket" "$(stat -Lc '%d:%i' "$wayland_socket")" \
    "$pulse_socket" "$(if [[ -n $pulse_socket ]]; then stat -Lc '%d:%i' "$pulse_socket"; fi)" "$GUI_GPU" | sha256sum)
  session_spec=${session_spec%% *}
  if running "$work" && running "$gateway" \
    && [[ $(p inspect --format '{{.ImageName}}' "$work") == "$GUI_IMAGE_REF" \
      && $(p inspect --format '{{.ImageName}}' "$gateway") == "$GUI_GATEWAY_REF" \
      && $(p inspect --format '{{index .Config.Labels "home-manager.gui.spec"}}' "$work") == "$session_spec" ]]; then
    cleanup_unused
    echo 'Wayland/WSLg container is already running.'; return
  fi
  doctor
  load_images
  remove_containers
  mkdir -p "$GUI_STATE/home" "$GUI_SHARED"
  printf 'nameserver 1.1.1.1\noptions timeout:2 attempts:2\n' > "$GUI_STATE/resolv.conf"
  # Remove partial starts on failure. Do not touch profile/shared data.
  trap 'remove_containers >/dev/null 2>&1 || true' ERR
  podman_step 'Creating gateway' create --name "$gateway" --pull=never --http-proxy=false \
    --userns=keep-id:uid=1000,gid=1000 --user=0:0 \
    --network=pasta:--map-host-loopback,10.0.2.2,--ipv4-only \
    --cap-drop=ALL --cap-add=NET_ADMIN --device=/dev/net/tun \
    --security-opt=no-new-privileges "$GUI_GATEWAY_REF"
  podman_step 'Starting gateway' start "$gateway"
  for _ in {1..50}; do
    if p exec "$gateway" /bin/test -f /tmp/ready; then ready=true; break; fi
    if ! running "$gateway"; then break; fi
    sleep 0.1
  done
  if [[ $ready != true ]]; then
    p logs "$gateway" >&2
    echo 'Gateway initialization failed; GUI session was not started.' >&2
    remove_containers
    trap - ERR
    return 1
  fi
  # Keep the default seccomp filter. SYS_CHROOT only unblocks Firefox's
  # chroot sandbox; no NET_ADMIN/SYS_ADMIN or access to gateway rules.
  # keep-id can copy/chown the large image on first use with native overlay.
  echo 'Creating GUI container; first keep-id setup may take several minutes.' >&2
  podman_step 'Creating GUI container' create --name "$work" --pull=never --http-proxy=false \
    --label "home-manager.gui.spec=$session_spec" \
    --userns=keep-id:uid=1000,gid=1000 --user=1000:1000 \
    --network="container:$gateway" \
    --cap-drop=ALL --cap-add=SYS_CHROOT --security-opt=no-new-privileges \
    --read-only --pid=private --ipc=private --pids-limit=1024 --shm-size=1g \
    --tmpfs /tmp:rw,nosuid,nodev,size=5g,mode=1777 \
    --mount "type=bind,src=$GUI_STATE/home,dst=/home/gui" \
    --mount "type=bind,src=$GUI_SHARED,dst=/shared,bind-nonrecursive" \
    --mount "type=bind,src=$GUI_STATE/resolv.conf,dst=/etc/resolv.conf,ro=true" \
    --mount "type=bind,src=$wayland_socket,dst=/run/host-wayland,ro=true" \
    "${audio_args[@]}" "${device_args[@]}" "$GUI_IMAGE_REF"
  podman_step 'Starting Wayland/WSLg session' start "$work"
  # Wait for the host Wayland connection and private D-Bus, not a fixed delay.
  ready=false
  for _ in {1..100}; do
    # Expand XDG_RUNTIME_DIR inside the container, not on the host.
    # shellcheck disable=SC2016
    if p exec --user=1000:1000 "$work" /bin/bash -c \
      'test -S "$WAYLAND_DISPLAY" && test -s "$XDG_RUNTIME_DIR/dbus-address" && test -f "$XDG_RUNTIME_DIR/session-ready"'; then
      ready=true; break
    fi
    if ! running "$work"; then break; fi
    sleep 0.1
  done
  if [[ $ready != true ]]; then
    p logs "$work" >&2
    echo 'Wayland/WSLg session failed to become ready.' >&2
    remove_containers
    trap - ERR
    return 1
  fi
  trap - ERR
  cleanup_unused
  echo 'Wayland/WSLg container ready (no windows opened). Run: gcnt firefox, brave, chrome or exec PROGRAM'
}
# Inspect/stop commands clean under the lifecycle lock, before using resources.
# Start/rebuild clean only after successful preparation, preserving the old
# desktop and its images if importing the replacement fails.
case "$command" in
  up|rebuild|shell|exec|down) ;;
  *) cleanup_unused ;;
esac
case "$command" in
  up) up ;;
  rebuild)
    # Import first: a failed import must not destroy a working desktop.
    # No Wayland/audio/device checks and no container creation or startup.
    load_images
    remove_containers
    cleanup_unused
    echo 'Images ready; containers stopped. Run: gcnt shell, exec, firefox, brave or chrome'
    ;;
  down) remove_containers; cleanup_unused ;;
  doctor) doctor ;;
  status) p ps -a --filter 'name=^hm-gui($|-gateway$)' ;;
  stats)
    # Do not hold up rebuild/down while collecting resource counters.
    flock -u 9
    p stats --no-stream "$work" "$gateway"
    ;;
  logs)
    for container in "$work" "$gateway"; do
      if p container exists "$container"; then
        printf '\n== %s ==\n' "$container"
        p logs "$container"
      fi
    done
    ;;
  shell|exec)
    up >&2
    flock -u 9
    exec 9>&-
    tty_args=(-i)
    if [[ -t 0 && -t 1 ]]; then tty_args+=(-t); fi
    if [[ $command == shell ]]; then set -- /bin/bash; fi
    exec "$GUI_PODMAN" exec "${tty_args[@]}" --user=1000:1000 "$work" /opt/gui/exec "$@"
    ;;
  check)
    if ! running "$work" || ! running "$gateway"; then
      echo 'Run gcnt up first.' >&2; exit 1
    fi
    echo 'Container (no application proxy settings):'
    p exec "$work" /bin/curl --noproxy '*' --fail --max-time 20 https://api.ipify.org
    echo
    p exec "$work" /bin/dig +time=5 +tries=1 @1.1.1.1 example.com A
    echo 'Host US listener:'
    curl --noproxy '' --proxy "$GUI_PROXY" --fail --max-time 20 https://api.ipify.org
    echo
    ;;
esac
