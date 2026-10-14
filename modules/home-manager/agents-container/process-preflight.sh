# Sourced before secret loading/home initialization; use shell builtins only.
# The workspace's private cgroup namespace root accounts for all its threads.
agents_process_preflight() {
  local minimum=${1:-} cgroup=${2:-/sys/fs/cgroup}
  local current maximum free
  # At most 18 digits keeps every subtraction within signed 64-bit arithmetic.
  if [[ ! $minimum =~ ^[1-9][0-9]*$ || ${#minimum} -gt 18 ]]; then
    printf 'agents: invalid minimumFreePids; require a positive integer of at most 18 digits.\n' >&2
    return 75
  fi
  if ! {
    IFS= read -r current < "$cgroup/pids.current" &&
      IFS= read -r maximum < "$cgroup/pids.max"
  } 2>/dev/null; then
    printf 'agents: cannot verify process headroom: pids.current/pids.max unavailable; no session was started.\n' >&2
    return 75
  fi
  if [[ ! $current =~ ^(0|[1-9][0-9]*)$ || ${#current} -gt 18 ]] ||
    { [[ $maximum != max ]] &&
      [[ ! $maximum =~ ^[1-9][0-9]*$ || ${#maximum} -gt 18 ]]; }; then
    printf 'agents: cannot verify process headroom: invalid pids.current/pids.max; no session was started.\n' >&2
    return 75
  fi
  [[ $maximum != max ]] || return 0
  free=$((maximum - current))
  if (( free < minimum )); then
    printf 'agents: insufficient process headroom: current=%s max=%s free=%s minimum=%s\n' \
      "$current" "$maximum" "$free" "$minimum" >&2
    printf 'agents: No process was killed or container restarted. Finish/save active work; if zombies accumulated, run agents rebuild on the host only after all sessions are saved.\n' >&2
    return 75
  fi
  # This snapshot is not a reservation; other sessions may consume slots later.
  return 0
}
