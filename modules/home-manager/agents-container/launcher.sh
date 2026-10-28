# Included by writeShellApplication; all AGENTS_* values come from Nix.
command=${1:-shell}
if (( $# )); then shift; fi

# Updating the checkout needs neither Podman nor the container lifecycle lock.
# Rebuild through the checkout afterwards: this launcher still pins old images.
if [[ $command == update ]]; then
  (( $# == 0 )) || { echo 'Usage: agents update (override checkout with HM_FLAKE)' >&2; exit 2; }
  flake=${HM_FLAKE:-}
  if [[ -z $flake ]]; then
    if [[ -f flake.nix ]]; then flake=$PWD; else flake=$HOME/.config/home-manager; fi
  fi
  [[ -f $flake/flake.nix ]] || {
    echo "No flake.nix in $flake; set HM_FLAKE to your Home Manager checkout." >&2
    exit 1
  }
  flake=$(cd -- "$flake" && pwd -P)
  @updateCommand@
  printf 'Lock updated. To apply, run: nix run %q -- rebuild\n' "path:$flake#${AGENTS_FLAKE_OUTPUT:-agents}"
  exit 0
fi

# conmon/slirp outlive the CLI: never let them inherit our lifecycle lock.
p() { "$AGENTS_PODMAN" "$@" 9>&-; }
work=@workspaceName@
gateway=@gatewayName@
# A separate PID/mount namespace: builders never run in the agent workspace.
daemon=$work-nix
# The same list drives host validation and workspace device mounts.
# TUN/FUSE remain required for networking and nested container workloads.
host_devices=(/dev/net/tun /dev/fuse)
if [[ ${AGENTS_KVM:-1} == 1 ]]; then host_devices+=(/dev/kvm); fi
IFS=: read -ra gpu_devices <<< "$AGENTS_DEVICES"
host_devices+=("${gpu_devices[@]}")
mkdir -p "$AGENTS_STATE"
chmod 700 "$AGENTS_STATE"
# Serialize namespace replacement, image loading and persistent-store seeding.
exec 9>"$AGENTS_STATE/lock"
flock 9

running() { [[ $(p inspect --format '{{.State.Running}}' "$1" 2>/dev/null) == true ]]; }

wait_workspace_ready() {
  # Creation and matching-workspace reuse share this acceptance barrier.
  # Foreground timeout keeps bounded clients in their caller's owned group.
  local startup_deadline=$((SECONDS + 30)) workspace_running
  while (( SECONDS < startup_deadline )); do
    if ! workspace_running=$(timeout --foreground --kill-after=1 2 "$AGENTS_PODMAN" inspect \
      --format '{{.State.Running}}' "$work" 9>&-); then break; fi
    if [[ $workspace_running != true ]]; then break; fi
    if timeout --foreground --kill-after=1 2 "$AGENTS_PODMAN" exec "$work" \
      /bin/test -f /tmp/agents-workspace-ready 9>&-; then return 0; fi
    sleep 0.2
  done
  timeout --foreground --kill-after=1 3 "$AGENTS_PODMAN" logs "$work" 9>&- >&2 || true
  echo 'Workspace startup/admission failed or timed out; no session was dispatched.' >&2
  return 1
}

wait_nix_daemon() {
  local deadline=$((SECONDS + 30)) daemon_running
  while (( SECONDS < deadline )); do
    if timeout --foreground --kill-after=1 2 "$AGENTS_PODMAN" exec --user 1000:1000 "$daemon" \
      /bin/nix --store daemon store ping 9>&- >/dev/null; then return 0; fi
    if ! daemon_running=$(timeout --foreground --kill-after=1 2 "$AGENTS_PODMAN" inspect \
      --format '{{.State.Running}}' "$daemon" 9>&-); then break; fi
    if [[ $daemon_running != true ]]; then break; fi
    sleep 0.2
  done
  timeout --foreground --kill-after=1 3 "$AGENTS_PODMAN" logs "$daemon" 9>&- >&2 || true
  echo 'Private Nix daemon startup failed; workspace was not started.' >&2
  return 1
}

# Never silently change ownership of a legacy single-user store, especially
# while its workspace is live. Conversion needs a separately approved stop and
# backup on the host. A marker alone does not override the ownership check.
validate_store_layout() {
  local store=$AGENTS_STATE/nix-root/nix
  if [[ -e $store ]]; then
    if [[ ! -f $AGENTS_STATE/nix-daemon-layout ]] ||
      [[ $(<"$AGENTS_STATE/nix-daemon-layout") != keep-id-root-v1 ]] ||
      [[ $(p unshare stat -c '%u:%g' "$store/store") != 1:30000 ]]; then
      echo 'Legacy or invalid private Nix store: offline ownership conversion and backup required on the host; no containers were changed.' >&2
      return 1
    fi
  fi
}

prepare_store() {
  mkdir -p "$AGENTS_STATE/nix-root"
  # Podman unshare root can update the offline store without trusting the agent
  # at the daemon. Its UID 1 is UID 0 in keep-id:uid=1000 (host user is UID 0
  # here, UID 1000 there). No host store or daemon socket enters a container.
  p unshare nix copy --option build-users-group '' --no-check-sigs \
    --to "local?root=$AGENTS_STATE/nix-root" "$AGENTS_ROOT"
  local nix=$AGENTS_STATE/nix-root/nix directory
  p unshare mkdir -p "$nix/var/nix/gcroots" "$nix/var/nix/daemon-socket"
  p unshare ln -sfn "$AGENTS_ROOT" "$nix/var/nix/gcroots/agents-image"
  p unshare chown -hR 1:1 "$nix"
  p unshare chown 1:30000 "$nix/store"
  p unshare chmod 1775 "$nix/store"
  # Only user profile/GC-root links remain writable in the workspace. The
  # database, store and daemon socket directory have no writable alias there.
  for directory in profiles gcroots; do
    p unshare mkdir -p "$nix/var/nix/$directory/per-user/$AGENTS_USER"
    p unshare chown -hR 0:0 "$nix/var/nix/$directory/per-user/$AGENTS_USER"
  done
  printf 'keep-id-root-v1\n' > "$AGENTS_STATE/nix-daemon-layout"
}

podman_step() {
  local label=$1
  shift
  local output pid started elapsed status next_report=10
  output=$(mktemp "$AGENTS_STATE/podman-step.XXXXXXXX")
  started=$SECONDS
  p "$@" >"$output" 2>&1 &
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
  if wait "$pid"; then
    status=0
  else
    status=$?
  fi
  elapsed=$((SECONDS - started))
  if [[ -t 2 ]]; then printf '\r\033[K' >&2; fi
  if (( status == 0 )); then
    printf '%s: done (%ds).\n' "$label" "$elapsed" >&2
  else
    printf '%s: failed after %ds.\n' "$label" "$elapsed" >&2
    cat "$output" >&2
  fi
  rm -f -- "$output"
  return "$status"
}

doctor() {
  [[ $(id -u) != 0 ]] || { echo 'Run agents as your normal host user.' >&2; return 1; }
  [[ $(p info --format '{{.Host.OCIRuntime.Name}} {{.Host.Security.Rootless}}') == 'crun true' ]] || {
    echo 'Rootless Podman with crun is required.' >&2; return 1;
  }
  local device
  for device in "${host_devices[@]}"; do
    [[ -r $device && -w $device ]] || { echo "No read/write access to $device" >&2; return 1; }
  done
  # Count the actual allocated mapping, not just a potentially stale /etc entry.
  local count
  count=$(p unshare cat /proc/self/uid_map | awk '{n += $3} END {print n}')
  (( count >= 65537 )) || { echo 'Need the host UID plus 65536 subordinate UIDs for nested Podman.' >&2; return 1; }
  count=$(p unshare cat /proc/self/gid_map | awk '{n += $3} END {print n}')
  (( count >= 65537 )) || { echo 'Need the host GID plus 65536 subordinate GIDs for nested Podman.' >&2; return 1; }
  echo 'Host devices, crun and subordinate IDs: OK'
}

load_images() (
  # Podman otherwise expands archives under /var/tmp, which can fill the root
  # filesystem even when its image store lives on a large separate disk.
  local image_tmp
  image_tmp=$(mktemp -d "$(p info --format '{{.Store.GraphRoot}}')/agents-import.XXXXXXXX")
  trap 'rm -rf -- "$image_tmp"' EXIT
  export TMPDIR="$image_tmp"
  if p image exists "$AGENTS_IMAGE_REF"; then
    echo 'Agent image is already present.' >&2
  else
    echo 'Importing agent image; this is several gigabytes on first launch...' >&2
    p load -i "$AGENTS_IMAGE"
  fi
  if p image exists "$AGENTS_GATEWAY_REF"; then
    echo 'Network gateway image is already present.' >&2
  else
    echo 'Importing network gateway image...' >&2
    p load -i "$AGENTS_GATEWAY_IMAGE"
  fi
)

prune_old_images() {
  # Only our two repositories; never prune unrelated or nested images. Keep
  # the pinned versions and anything used by a container (including stopped
  # containers). No --force: a concurrent user of an image must remain safe.
  local images image containers
  if ! images=$(p images --format '{{.Repository}}:{{.Tag}}'); then
    echo 'Warning: could not list old agent images; skipping cleanup.' >&2
    return 0
  fi
  while IFS= read -r image; do
    case "${image%:*}" in
      "${AGENTS_IMAGE_REF%:*}"|"${AGENTS_GATEWAY_REF%:*}") ;;
      *) continue ;;
    esac
    [[ $image != "$AGENTS_IMAGE_REF" && $image != "$AGENTS_GATEWAY_REF" ]] || continue
    if ! containers=$(p ps -a --filter "ancestor=$image" --format '{{.ID}}'); then
      echo "Warning: could not check containers using $image; keeping it." >&2
      continue
    fi
    [[ -z $containers ]] || continue
    echo "Removing old agent image: $image" >&2
    if ! p rmi "$image" >&2; then
      echo "Warning: could not remove $image; keeping it for a later cleanup." >&2
    fi
  done <<< "$images"
}

remove_containers() {
  # A workload must never survive a namespace replacement and accidentally join
  # an old namespace. Remove it first, preserving all bind-mounted state.
  p container exists "$work" && p rm -f "$work"
  p container exists "$daemon" && p rm -f "$daemon"
  p container exists "$gateway" && p rm -f "$gateway"
  return 0
}

# Stage only the dedicated Nix credentials, not the host's home or SOPS tree.
# Mount the directory, not a file inode: atomic SOPS rotations must be visible
# even when up() reuses an already-running workspace. Never log file contents.
refresh_nix_auth() (
  local source=${AGENTS_NIX_ACCESS_TOKENS:-}
  local directory=$AGENTS_STATE/nix-auth
  mkdir -p "$directory"
  chmod 700 "$directory"
  if [[ -z $source || ! -e $source ]]; then
    rm -f -- "$directory/access-tokens.conf"
    if [[ -n $source ]]; then
      echo 'Warning: host Nix access-tokens.conf is missing; private GitHub flakes may fail. Activate the SOPS template on the host.' >&2
    fi
    return
  fi
  if [[ ! -f $source || ! -r $source ]]; then
    rm -f -- "$directory/access-tokens.conf"
    echo 'Host Nix access-tokens.conf is not a readable regular file.' >&2
    return 1
  fi
  local temporary
  temporary=$(mktemp "$directory/.access-tokens.XXXXXXXX")
  trap 'rm -f -- "$temporary"' EXIT
  install -m 600 -- "$source" "$temporary"
  mv -fT -- "$temporary" "$directory/access-tokens.conf"
)

up() {
  # A launcher/spec update must not silently destroy sessions in an old
  # sleep-as-PID-1 workspace. Only explicit rebuild may replace that workspace.
  local workspace_exists=0 workspace_running=false init_enabled
  p container exists "$work" || workspace_exists=$?
  if (( workspace_exists != 0 && workspace_exists != 1 )); then
    echo 'Cannot verify workspace existence; no container was restarted.' >&2
    return 1
  fi
  if (( workspace_exists == 0 )); then
    if ! workspace_running=$(p inspect --format '{{.State.Running}}' "$work") ||
      [[ $workspace_running != true && $workspace_running != false ]]; then
      echo 'Cannot verify workspace state; no container was restarted.' >&2
      return 1
    fi
    if [[ $workspace_running == true ]]; then
      if ! init_enabled=$(p inspect --format '{{.HostConfig.Init}}' "$work"); then
        echo 'Cannot verify workspace reaping init; no container was restarted.' >&2
        return 1
      fi
      if [[ $init_enabled != true ]]; then
        echo 'Workspace has no verified reaping init. Finish/save active sessions, then run agents rebuild on the host. No container was restarted.' >&2
        return 1
      fi
    fi
  fi
  refresh_nix_auth
  # Contains only the explicitly declared keys, never the host's whole SOPS tree.
  # A directory mount makes atomic rotations visible to new entrypoint invocations.
  local secret_sources=${AGENTS_SECRET_SOURCES:-}
  [[ -n $secret_sources ]] || secret_sources='{}'
  # Keep plaintext on the host's per-login tmpfs, not in persistent container state.
  local secret_directory=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/agents-container-secrets
  python3 "$AGENTS_SECRET_HELPER" stage "$secret_sources" "$secret_directory"
  local -a clipboard_args=()
  if [[ $AGENTS_CLIPBOARD == 1 ]]; then
    # The broker uses the host session environment, never the container's.
    local variable
    for variable in WAYLAND_DISPLAY DISPLAY XAUTHORITY; do
      if [[ -n ${!variable:-} ]]; then systemctl --user import-environment "$variable"; fi
    done
    systemctl --user start agents-clipboard.service
    local clipboard_dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/agents-clipboard
    local ready=false
    for _ in {1..50}; do
      if [[ -S $clipboard_dir/socket ]]; then ready=true; break; fi
      sleep 0.1
    done
    [[ $ready == true ]] || { echo 'Clipboard bridge did not start.' >&2; return 1; }
    clipboard_args+=(--mount "type=bind,src=$clipboard_dir,dst=/run/agents-clipboard,ro=true")
  fi
  local -a sound_args=()
  if [[ ${AGENTS_SOUND:-0} == 1 ]]; then
    # The host broker knows the allowed files; guests get no PulseAudio socket.
    local variable
    for variable in PULSE_SERVER PULSE_COOKIE; do
      if [[ -n ${!variable:-} ]]; then systemctl --user import-environment "$variable"; fi
    done
    systemctl --user start agents-sound.service
    local sound_dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/agents-sound
    local sound_ready=false
    for _ in {1..50}; do
      if [[ -S $sound_dir/socket ]]; then sound_ready=true; break; fi
      sleep 0.1
    done
    [[ $sound_ready == true ]] || { echo 'Sound bridge did not start.' >&2; return 1; }
    sound_args+=(--mount "type=bind,src=$sound_dir,dst=/run/agents-sound,ro=true")
  fi
  local -a web_link_args=()
  if [[ ${AGENTS_WEB_LINKS:-0} == 1 ]]; then
    # Only the broker sees the desktop session; never mount display/D-Bus sockets.
    # systemd-run starts the browser under the host user manager's environment.
    local variable
    for variable in DISPLAY WAYLAND_DISPLAY XAUTHORITY XDG_CURRENT_DESKTOP \
      XDG_SESSION_DESKTOP DESKTOP_SESSION XDG_DATA_DIRS XDG_CONFIG_DIRS BROWSER PATH; do
      if [[ -n ${!variable:-} ]]; then systemctl --user import-environment "$variable"; fi
    done
    systemctl --user start agents-web-links.service
    local web_link_dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/agents-web-links
    local web_link_ready=false
    for _ in {1..50}; do
      if [[ -S $web_link_dir/socket ]]; then web_link_ready=true; break; fi
      sleep 0.1
    done
    [[ $web_link_ready == true ]] || { echo 'Host browser bridge did not start.' >&2; return 1; }
    web_link_args+=(--mount "type=bind,src=$web_link_dir,dst=/run/agents-web-links,ro=true")
  fi
  if [[ $workspace_running == true ]] && running "$gateway" && running "$daemon"; then
    local current_work current_gateway current_daemon current_spec
    current_work=$(p inspect --format '{{.ImageName}}' "$work")
    current_gateway=$(p inspect --format '{{.ImageName}}' "$gateway")
    current_daemon=$(p inspect --format '{{.ImageName}}' "$daemon")
    current_spec=$(p inspect --format '{{index .Config.Labels "home-manager.agents.spec"}}' "$work")
    if [[ $current_work == "$AGENTS_IMAGE_REF" && $current_daemon == "$AGENTS_IMAGE_REF" && $current_gateway == "$AGENTS_GATEWAY_REF" && $current_spec == "$AGENTS_SPEC" ]]; then
      wait_nix_daemon || return 1
      wait_workspace_ready || return 1
      prune_old_images
      return
    fi
  fi
  echo 'Checking host requirements...' >&2
  doctor >&2
  validate_store_layout
  remove_containers
  mkdir -p "$AGENTS_STATE/home" "$AGENTS_PROJECTS"
  echo 'Preparing the private daemon-owned Nix store...' >&2
  prepare_store
  load_images
  printf 'nameserver 1.1.1.1\noptions timeout:2 attempts:2\n' > "$AGENTS_STATE/resolv.conf"
  podman_step 'Creating network gateway' create --name "$gateway" --pull=never --http-proxy=false \
    --label "home-manager.agents.spec=$AGENTS_SPEC" \
    --userns=keep-id:uid=1000,gid=1000 --user 0:0 \
    --network=pasta:--map-host-loopback,10.0.2.2,--ipv4-only \
    --cap-drop=ALL --cap-add=NET_ADMIN --device=/dev/net/tun \
    --security-opt=no-new-privileges \
    "$AGENTS_GATEWAY_REF"
  podman_step 'Starting network gateway' start "$gateway"
  local ready=false
  for _ in {1..50}; do
    if p exec "$gateway" /bin/test -f /tmp/ready; then ready=true; break; fi
    if ! running "$gateway"; then break; fi
    sleep 0.1
  done
  if [[ $ready != true ]]; then
    p logs "$gateway" >&2
    echo 'Gateway/firewall initialization failed; workspace was not started.' >&2
    return 1
  fi
  echo 'Network gateway and default-deny firewall are ready.' >&2
  # Reuse the seeded workspace image, but not its agent entrypoint, HOME,
  # projects, devices, credentials environment or process namespace.
  podman_step 'Creating private Nix daemon' create --name "$daemon" --init --pull=never --http-proxy=false \
    --label "home-manager.agents.spec=$AGENTS_SPEC" \
    --userns=keep-id:uid=1000,gid=1000 --user 0:0 \
    --network="container:$gateway" --pids-limit=1024 \
    --cap-drop=ALL --cap-add=CHOWN --cap-add=DAC_OVERRIDE --cap-add=FOWNER \
    --cap-add=SETUID --cap-add=SETGID --cap-add=KILL \
    --security-opt=no-new-privileges \
    --env HOME=/root --env USER=root --env NIX_REMOTE=local --env XDG_RUNTIME_DIR=/tmp/runtime-0 \
    --mount "type=bind,src=$AGENTS_STATE/nix-root/nix,dst=/nix" \
    --mount "type=bind,src=$AGENTS_STATE/resolv.conf,dst=/etc/resolv.conf,ro=true" \
    --mount "type=bind,src=$AGENTS_STATE/nix-auth,dst=/run/agents-nix-auth,ro=true" \
    --entrypoint /bin/nix-daemon "$AGENTS_IMAGE_REF" --option build-users-group nixbld
  podman_step 'Starting private Nix daemon' start "$daemon"
  wait_nix_daemon || return 1
  # Nested slirp creates a TUN in its OWN child netns. The device grants no
  # NET_ADMIN over the outer namespace or the gateway firewall.
  local -a device_args=(--group-add=keep-groups)
  local device
  for device in "${host_devices[@]}"; do device_args+=("--device=$device"); done
  # Pixel/Chromium need more than Podman's 64 MiB shared-memory default.
  # A private tmpfs limit, not a reservation; do not share the host IPC namespace.
  podman_step 'Creating workspace container' create --name "$work" --init --init-path "$AGENTS_INIT" --pull=never --http-proxy=false \
    --label "home-manager.agents.spec=$AGENTS_SPEC" \
    --userns=keep-id:uid=1000,gid=1000 --user 1000:1000 \
    --network="container:$gateway" \
    --shm-size=1g --pids-limit=4096 \
    --cap-drop=ALL --cap-add=SETUID --cap-add=SETGID \
    --security-opt=seccomp=unconfined --security-opt=apparmor=unconfined \
    --security-opt=unmask=ALL \
    "${device_args[@]}" \
    "${clipboard_args[@]}" \
    "${sound_args[@]}" \
    "${web_link_args[@]}" \
    --mount "type=bind,src=$AGENTS_STATE/home,dst=$AGENTS_HOME" \
    --mount "type=bind,src=$AGENTS_PROJECTS,dst=$AGENTS_HOME/@projectDirectoryName@" \
    --mount "type=bind,src=$AGENTS_STATE/nix-root/nix,dst=/nix,ro=true" \
    --mount "type=bind,src=$AGENTS_STATE/nix-root/nix/var/nix/profiles/per-user/$AGENTS_USER,dst=/nix/var/nix/profiles/per-user/$AGENTS_USER" \
    --mount "type=bind,src=$AGENTS_STATE/nix-root/nix/var/nix/gcroots/per-user/$AGENTS_USER,dst=/nix/var/nix/gcroots/per-user/$AGENTS_USER" \
    --mount "type=bind,src=$AGENTS_STATE/resolv.conf,dst=/etc/resolv.conf,ro=true" \
    --mount "type=bind,src=$AGENTS_STATE/nix-auth,dst=/run/agents-nix-auth,ro=true" \
    --mount "type=bind,src=$secret_directory,dst=/run/agents-secret-environment,ro=true" \
    "$AGENTS_IMAGE_REF"
  podman_step 'Starting workspace container' start "$work"
  # Detached start is not entrypoint acceptance. Never restart on rejection.
  wait_workspace_ready || return 1
  prune_old_images
  echo 'Workspace ready. Run: agents shell'
}

case "$command" in
  up) up ;;
  down) remove_containers ;;
  rebuild) validate_store_layout; remove_containers; up ;;
  doctor) doctor ;;
  status) p ps -a --filter "name=^@workspaceName@" ;;
  logs) p logs "$gateway" ;;
  shell|exec)
    # Keep command stdout clean for pipes and command substitution.
    up >&2
    # Interactive shells must not hold the lifecycle lock.
    flock -u 9
    exec 9>&-
    tty_args=(-i)
    if [[ -t 0 && -t 1 ]]; then tty_args+=(-t); fi
    if [[ $command == shell ]]; then set -- @shellCommand@; fi
    (( $# )) || { echo 'Usage: agents exec COMMAND [ARG...]' >&2; exit 2; }
    exec "$AGENTS_PODMAN" exec "${tty_args[@]}" --user 1000:1000 "$work" /opt/agents/entrypoint "$@"
    ;;
  check)
    running "$work" || { echo 'Run agents up first.' >&2; exit 1; }
    # $1 is the KVM flag passed to the container shell, not a host expansion.
    # shellcheck disable=SC2016
    p exec --user 1000:1000 "$work" /bin/bash -c '
      set -e
      date +"%F %T %Z %z"
      if [[ $1 == 1 ]]; then test -r /dev/kvm && test -w /dev/kvm; fi
      test "$(stat -c %u /nix/store)" = 0
      case ",$(findmnt -n -o VFS-OPTIONS -T /nix/store)," in
        *,ro,*) ;; *) echo "agents: store mount is not read-only" >&2; exit 1 ;;
      esac
      nix --store daemon store ping
      podman unshare cat /proc/self/uid_map
      curl --noproxy "*" --fail --max-time 20 https://api.ipify.org
      echo
      dig +time=5 +tries=1 @1.1.1.1 example.com A
    ' agents-check "${AGENTS_KVM:-1}"
    echo 'Compare with host US listener:'
    curl --noproxy '' --proxy "$AGENTS_PROXY" --fail --max-time 20 https://api.ipify.org
    echo
    ;;
  *) echo 'Usage: agents {up|down|rebuild|update|shell|exec COMMAND...|status|logs|doctor|check}' >&2; exit 2 ;;
esac
