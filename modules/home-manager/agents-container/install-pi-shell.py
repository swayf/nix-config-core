"""Install shell/profile seeds and a Pi resume wrapper from explicit caller policy.

Usage: install-pi-shell.py POLICY_JSON HOME shell|pi|wrappers
No discovery, package lookup, auth access or session-file reads.
"""

import json
import os
from pathlib import Path
import shlex
import sys


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError("incorrect policy fields: " + names)


def text(value):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("expected nonempty text without NUL")
    return value


def relative(value):
    text(value)
    if value.startswith("/") or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("expected a home-relative path without empty/dot components")
    return value


def absolute(value):
    if not text(value).startswith("/"):
        raise ValueError("expected an absolute source path")
    return value


def component(value):
    if "/" in relative(value):
        raise ValueError("expected one path component")
    return value


def validate(policy):
    fields(policy, "directories bashrc bash_profile links pi")
    if not isinstance(policy["directories"], list):
        raise ValueError("directories must be a list")
    for path in policy["directories"]:
        relative(path)
    for name in ("bashrc", "bash_profile"):
        seed = policy[name]
        fields(seed, "destination source marker append")
        relative(seed["destination"])
        absolute(seed["source"])
        text(seed["marker"])
        text(seed["append"])
    if not isinstance(policy["links"], list):
        raise ValueError("links must be a list")
    for link in policy["links"]:
        fields(link, "destination source")
        relative(link["destination"])
        absolute(link["source"])
    pi = policy["pi"]
    fields(pi, "wrapper marker executable interpreter profiles_directory legacy_agent_directory "
           "default_profile profiles settings_source clipboard_socket clipboard_display "
           "profile_wrappers legacy_wrapper")
    for key in ("wrapper", "profiles_directory", "legacy_agent_directory"):
        relative(pi[key])
    for key in ("executable", "interpreter", "settings_source", "clipboard_socket"):
        absolute(pi[key])
    for key in ("marker", "clipboard_display"):
        text(pi[key])
    if "\n" in pi["marker"] or "\r" in pi["marker"] or not pi["marker"].startswith("#"):
        raise ValueError("wrapper marker must be a single comment line")
    if any(c in pi["interpreter"] for c in "\r\n \t"):
        raise ValueError("interpreter must be a shebang path")
    if not isinstance(pi["profiles"], list) or not pi["profiles"]:
        raise ValueError("profiles must be a nonempty list")
    for profile in pi["profiles"]:
        component(profile)
    if len(set(pi["profiles"])) != len(pi["profiles"]):
        raise ValueError("duplicate profiles")
    if component(pi["default_profile"]) not in pi["profiles"]:
        raise ValueError("default profile must be seeded")
    if not isinstance(pi["profile_wrappers"], dict):
        raise ValueError("profile_wrappers must be an object")
    for destination, profile in pi["profile_wrappers"].items():
        relative(destination)
        if component(profile) not in pi["profiles"]:
            raise ValueError("wrapper profile must be seeded")
    fields(pi["legacy_wrapper"], "destination content")
    relative(pi["legacy_wrapper"]["destination"])
    text(pi["legacy_wrapper"]["content"])


def home_expression(path):
    # The generated wrapper follows its runtime HOME, not the installation HOME.
    return '"$HOME"/' + shlex.quote(path)


def render_wrapper(pi):
    return f'''#!{pi["interpreter"]}
{pi["marker"]}
profiles_dir={home_expression(pi["profiles_directory"])}
legacy_agent_dir={home_expression(pi["legacy_agent_directory"])}
if [ -z "${{PI_CODING_AGENT_DIR:-}}" ]; then
  session_file=
  session_arg=false
  for arg in "$@"; do
    if [ "$session_arg" = true ]; then
      session_file=$arg
      break
    fi
    case "$arg" in
      --) break ;;
      --session) session_arg=true ;;
    esac
  done
  case "$session_file" in
    "$profiles_dir/"*/sessions/*)
      profile=${{session_file#"$profiles_dir/"}}
      profile=${{profile%%/sessions/*}}
      case "$profile" in
        ""|.|..|*/*) ;;
        *)
          if [ -d "$profiles_dir/$profile" ]; then
            export PI_CODING_AGENT_DIR="$profiles_dir/$profile"
          fi
          ;;
      esac
      ;;
    "$legacy_agent_dir/sessions/"*)
      export PI_CODING_AGENT_DIR="$legacy_agent_dir"
      ;;
  esac
  if [ -z "${{PI_CODING_AGENT_DIR:-}}" ]; then
    export PI_CODING_AGENT_DIR="$profiles_dir"/{shlex.quote(pi["default_profile"])}
  fi
fi
if [ -z "${{DISPLAY:-}}" ] && [ -z "${{WAYLAND_DISPLAY:-}}" ] && [ -S {shlex.quote(pi["clipboard_socket"])} ]; then
  export DISPLAY={shlex.quote(pi["clipboard_display"])}
fi
exec {shlex.quote(pi["executable"])} "$@"
'''


def append(path, content):
    with path.open("a") as stream:
        stream.write(content)


def shell_seed(policy, home):
    for directory in policy["directories"]:
        (home / directory).mkdir(parents=True, exist_ok=True)
    seed = policy["bashrc"]
    target = home / seed["destination"]
    if not target.exists() and not target.is_symlink():
        target.symlink_to(seed["source"])
    elif not target.is_symlink() and target.is_file() and seed["marker"].encode() not in target.read_bytes().split(b"\n"):
        append(target, seed["append"])
    seed = policy["bash_profile"]
    target = home / seed["destination"]
    # Retain the legacy login-shell behavior, including its dangling-link error.
    if not target.exists():
        target.symlink_to(seed["source"])
    elif target.is_file() and seed["marker"].encode() not in target.read_bytes():
        append(target, seed["append"])
    for link in policy["links"]:
        target = home / link["destination"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.symlink_to(link["source"])


def executable_write(target, content):
    target.write_text(content)
    target.chmod(target.stat().st_mode | 0o111)


def pi_seed(pi, home):
    target = home / pi["wrapper"]
    if not target.exists() or pi["marker"].encode() in target.read_bytes().split(b"\n"):
        executable_write(target, render_wrapper(pi))
    for profile in pi["profiles"]:
        directory = home / pi["profiles_directory"] / profile
        for child in ("", "extensions", "skills", "prompts", "themes"):
            (directory / child).mkdir(parents=True, exist_ok=True)
        target = directory / "settings.json"
        source = Path(pi["settings_source"]) / profile / "settings.json"
        if ((not target.exists() and not target.is_symlink()) or
                (target.is_symlink() and os.readlink(target) == str(source))):
            target.unlink(missing_ok=True)
            # Match cp creation permissions (source mode subject to process umask).
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 source.stat().st_mode & 0o777)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(source.read_bytes())
            target.chmod(target.stat().st_mode | 0o200)


def profile_wrappers(pi, home):
    legacy = pi["legacy_wrapper"]
    target = home / legacy["destination"]
    if target.is_file() and not target.is_symlink():
        content = target.read_bytes()
        expected = legacy["content"].encode()
        # The old grep checks did not require line order, but wc -l was exact.
        if content.count(b"\n") == expected.count(b"\n") and all(
                line in content.split(b"\n") for line in expected.rstrip(b"\n").split(b"\n")):
            target.unlink()
    for destination, profile in pi["profile_wrappers"].items():
        content = (f'#!{pi["interpreter"]}\n'
                   f'export PI_CODING_AGENT_DIR={home_expression(pi["profiles_directory"] + "/" + profile)}\n'
                   'exec pi "$@"\n')
        executable_write(home / destination, content)


def main(argv):
    if len(argv) != 3 or argv[2] not in ("shell", "pi", "wrappers"):
        raise ValueError("usage: install-pi-shell.py POLICY_JSON HOME shell|pi|wrappers")
    policy = json.loads(Path(argv[0]).read_text())
    validate(policy)
    home = Path(absolute(argv[1]))
    if argv[2] == "shell":
        shell_seed(policy, home)
    elif argv[2] == "pi":
        pi_seed(policy["pi"], home)
    else:
        profile_wrappers(policy["pi"], home)


if __name__ == "__main__":
    main(sys.argv[1:])
