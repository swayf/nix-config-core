#!/usr/bin/env python3
"""Create current-user Desktop .lnk files via WSL Windows interop, on request."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def ps_string(value):
    return "'" + value.replace("'", "''") + "'"


def encoded_command(source):
    return base64.b64encode(source.encode("utf-16-le")).decode("ascii")


def shortcut_name(name, distro, username, app_id):
    # Profile labels can collide or contain Windows-invalid filename characters.
    suffix = app_id.removeprefix("gui-container-profile-") if app_id.startswith("gui-container-profile-") else ""
    label = f"{name} [{distro} - {username}]" + (f" [{suffix}]" if suffix else "")
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label)
    identity = hashlib.sha256((distro + "\0" + username + "\0" + app_id).encode()).hexdigest()[:8]
    return label[:170].rstrip(" .") + " [" + identity + "].lnk"


def request(manifest, distro):
    username = manifest["username"]
    scope = hashlib.sha256((distro + "\0" + username).encode()).hexdigest()[:16]
    apps = []
    for app in manifest["applications"]:
        app_id = app["id"]
        if not re.fullmatch(r"gui-container(?:-[A-Za-z0-9_-]+)?", app_id):
            raise ValueError(f"Invalid application ID: {app_id}")
        # PowerShell invokes wsl.exe with individual literal arguments; no Bash
        # -c, user labels, browser URLs or profile extraArgs are interpolated.
        args = ["--distribution", distro, "--user", username, "--exec", manifest["launcher"], app_id]
        array = "@(" + ", ".join(ps_string(arg) for arg in args) + ")"
        runtime = f"""$directory = Join-Path $env:LOCALAPPDATA 'GCNT\\{scope}'
$log = Join-Path $directory ('{app_id}-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.log')
$wslArgs = {array}
& (Join-Path $env:SystemRoot 'System32\\wsl.exe') @wslArgs *> $log
$code = $LASTEXITCODE
if ($code -ne 0) {{
  Add-Type -AssemblyName PresentationFramework
  [void][System.Windows.MessageBox]::Show(('GCNT failed (exit ' + $code + '). See log: ' + $log), 'GCNT')
}}
exit $code
"""
        apps.append({
            "id": app_id,
            "name": shortcut_name(app["name"], distro, username, app_id),
            "description": f"GCNT managed shortcut: {scope}/{app_id}",
            "icon": base64.b64encode(Path(app["icon"]).read_bytes()).decode("ascii"),
            "arguments": "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + encoded_command(runtime),
        })
    return {"scope": scope, "applications": apps}


POWERSHELL = r"""$ErrorActionPreference = 'Stop'
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) { throw 'Windows Desktop folder could not be resolved.' }
$assets = Join-Path $env:LOCALAPPDATA ('GCNT\' + $payload.scope)
[void][IO.Directory]::CreateDirectory($assets)
$target = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shell = New-Object -ComObject WScript.Shell
foreach ($app in $payload.applications) {
    $path = Join-Path $desktop $app.name
    $shortcut = $shell.CreateShortcut($path)
    if ((Test-Path -LiteralPath $path) -and $shortcut.Description -ne $app.description) {
        throw ('Refusing to overwrite an unmanaged shortcut: ' + $path)
    }
    $icon = Join-Path $assets ($app.id + '.ico')
    [IO.File]::WriteAllBytes($icon, [Convert]::FromBase64String($app.icon))
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $app.arguments
    $shortcut.WorkingDirectory = $assets
    $shortcut.Description = $app.description
    $shortcut.IconLocation = $icon + ',0'
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Output ('Created: ' + $path)
}
"""


def main(argv):
    if len(argv) != 1:
        raise ValueError("Usage: gcnt-windows-shortcuts (no arguments)")
    distro = os.environ.get("WSL_DISTRO_NAME")
    if not distro:
        raise ValueError("Run gcnt-windows-shortcuts inside the intended WSL distribution.")
    powershell = shutil.which("powershell.exe")
    if not powershell:
        fallback = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        if fallback.is_file():
            powershell = str(fallback)
    if not powershell or not shutil.which("wslpath"):
        raise ValueError("Windows interop is unavailable: powershell.exe and wslpath are required.")
    manifest = json.loads(Path(argv[0]).read_text())
    if not Path(manifest["launcher"]).is_file():
        raise ValueError("First activate the WSL Home Manager profile (hm switch).")
    payload = base64.b64encode(json.dumps(request(manifest, distro)).encode()).decode("ascii")
    # Use a temporary script, not a huge EncodedCommand (Windows command-line
    # length limit); icons are embedded and then persisted on the Windows side.
    with tempfile.TemporaryDirectory(prefix="gcnt-windows-shortcuts-") as directory:
        script = Path(directory) / "create.ps1"
        script.write_text(POWERSHELL.replace("__PAYLOAD__", payload), encoding="ascii")
        win_path = subprocess.check_output(["wslpath", "-w", str(script)], text=True).strip()
        subprocess.run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-File", win_path], check=True)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"gcnt-windows-shortcuts: {error}", file=sys.stderr)
        sys.exit(1)
