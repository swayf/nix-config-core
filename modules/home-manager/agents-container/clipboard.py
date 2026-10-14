#!/usr/bin/env python3
"""Clipboard-only Unix socket bridge; no display sockets enter the workspace."""
import base64
import json
import os
import socket
import socketserver
import struct
import subprocess
import sys

MAX_BYTES = 32 * 1024 * 1024


def receive(sock):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ValueError("clipboard connection closed")
            data.extend(chunk)
        return data
    size = struct.unpack("!I", exact(4))[0]
    if size > MAX_BYTES * 2:
        raise ValueError("clipboard message too large")
    return json.loads(exact(size))


def send(sock, message):
    data = json.dumps(message).encode()
    sock.sendall(struct.pack("!I", len(data)) + data)


def command(tool, args, wayland):
    """Translate the supported CLI subset; never forward executable options."""
    paste = tool == "wl-paste"
    mime = None
    primary = False
    trim = False
    clear = False
    listing = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-o", "-out") and tool == "xclip":
            paste = True
        elif arg in ("-i", "-in") and tool == "xclip":
            paste = False
        elif arg in ("-selection", "-sel") and tool == "xclip":
            i += 1
            if args[i] not in ("clipboard", "primary"):
                raise ValueError("unsupported selection")
            primary = args[i] == "primary"
        elif arg in ("--primary", "-p"):
            primary = True
        elif arg in ("--type", "-t", "-target"):
            i += 1
            mime = args[i]
        elif arg in ("--no-newline", "-n") and paste:
            trim = True
        elif arg in ("--clear", "-c") and tool == "wl-copy":
            clear = True
        elif arg in ("--list-types", "-l") and paste:
            listing = True
        else:
            raise ValueError(f"unsupported clipboard option: {arg}")
        i += 1
    # TARGETS is an X11 format-list request, not a MIME type. Pi probes it
    # before reading an image through xclip when DISPLAY is set by our wrapper.
    if tool == "xclip" and paste and mime == "TARGETS":
        listing = True
    if listing:
        mime = None
    # xclip defaults to UTF8_STRING, unlike wl-copy's content autodetection.
    # Preserve that default for content reads/writes, but not format listing.
    if tool == "xclip" and mime is None and not listing:
        mime = "UTF8_STRING"
    if mime in ("UTF8_STRING", "STRING", "TEXT"):
        mime = "text/plain;charset=utf-8"
    if wayland:
        cmd = ["wl-paste" if paste else "wl-copy"]
        if primary:
            cmd.append("--primary")
        if mime:
            cmd += ["--type", mime]
        if paste:
            # xclip writes exact bytes; wl-paste normally adds a newline.
            if trim or tool == "xclip":
                cmd.append("--no-newline")
            if listing:
                cmd.append("--list-types")
        elif clear:
            cmd.append("--clear")
    else:
        if clear:
            raise ValueError("--clear requires Wayland")
        cmd = ["xclip", "-selection", "primary" if primary else "clipboard"]
        cmd.append("-out" if paste else "-in")
        if mime:
            cmd += ["-target", "UTF8_STRING" if mime.startswith("text/plain") else mime]
        if listing:
            cmd += ["-target", "TARGETS"]
    return cmd, paste, trim


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(30)
        try:
            request = receive(self.request)
            if request["tool"] not in ("wl-copy", "wl-paste", "xclip"):
                raise ValueError("unsupported clipboard command")
            cmd, paste, trim = command(request["tool"], request["args"], bool(os.getenv("WAYLAND_DISPLAY")))
            data = base64.b64decode(request.get("data", ""), validate=True)
            if len(data) > MAX_BYTES:
                raise ValueError("clipboard exceeds 32 MiB")
            # Clipboard owners fork and keep stdin/stdout open. Use a real file
            # for stdin and discard copy stdout to avoid waiting for that owner.
            import tempfile
            # stderr must also be a file: a forked owner can keep a pipe open.
            with tempfile.TemporaryFile() as stream, tempfile.TemporaryFile() as errors:
                stream.write(data)
                stream.seek(0)
                result = subprocess.run(cmd, stdin=stream, stdout=subprocess.PIPE if paste else subprocess.DEVNULL,
                                        stderr=errors, timeout=15)
                errors.seek(0)
                detail = errors.read(8192).decode(errors="replace").strip()
            output = result.stdout or b""
            if len(output) > MAX_BYTES:
                raise ValueError("clipboard exceeds 32 MiB")
            if trim and not os.getenv("WAYLAND_DISPLAY"):
                output = output.rstrip(b"\n")
            response = {"code": result.returncode, "data": base64.b64encode(output).decode()}
            if result.returncode:
                response["error"] = f"{cmd[0]} exited with code {result.returncode}: {detail or 'no stderr'}"
                print(f"agents clipboard: {response['error']}", file=sys.stderr, flush=True)
            send(self.request, response)
        except Exception as error:
            send(self.request, {"code": 1, "error": str(error)})


def main():
    if sys.argv[1] == "serve":
        path = sys.argv[2]
        os.umask(0o077)
        if os.path.exists(path):
            os.unlink(path)
        with socketserver.ThreadingUnixStreamServer(path, Handler) as server:
            server.daemon_threads = True
            server.serve_forever()
    else:
        tool, args = sys.argv[1], sys.argv[2:]
        _, paste, _ = command(tool, args, True)
        data = b"" if paste else sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("clipboard exceeds 32 MiB")
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(30)
            sock.connect("/run/agents-clipboard/socket")
            send(sock, {"tool": tool, "args": args, "data": base64.b64encode(data).decode()})
            result = receive(sock)
        if "error" in result:
            print(result["error"], file=sys.stderr)
        sys.stdout.buffer.write(base64.b64decode(result.get("data", "")))
        return result["code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, IndexError) as error:
        print(f"agents clipboard: {error}", file=sys.stderr)
        sys.exit(1)
