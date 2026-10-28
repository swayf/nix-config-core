#!/usr/bin/env python3
"""Foreground loopback preview through an existing workspace; no lifecycle calls."""
import os
import signal
import socketserver
import subprocess
import sys
import threading
from urllib.parse import urlsplit, urlunsplit

# Fixed tool-owned code, sent to the existing image's isolated Python. No
# workspace file, shell expression, URL or host environment is evaluated there.
GUEST_CODE = r"""
import os, select, socket, sys, time
try:
    with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=5) as peer:
        if sys.argv[2] == 'probe':
            sys.exit(0)
        inputs = [0, peer.fileno()]
        deadline = None
        while inputs:
            timeout = None if deadline is None else max(0, deadline - time.monotonic())
            ready, _, _ = select.select(inputs, [], [], timeout)
            if not ready:
                break
            for source in ready:
                data = os.read(source, 65536)
                if source == 0:
                    if data:
                        peer.sendall(data)
                    else:
                        peer.shutdown(socket.SHUT_WR)
                        inputs.remove(0)
                        # Drain a half-closed request, but do not strand an exec
                        # after the host client/forwarder has disappeared.
                        deadline = time.monotonic() + 5
                elif not data:
                    sys.exit(0)
                else:
                    view = memoryview(data)
                    while view:
                        view = view[os.write(1, view):]
except (OSError, ValueError):
    sys.exit(1)
"""


def preview_url(url):
    """Never resolve a caller-selected hostname or expose a non-loopback port."""
    if not isinstance(url, str) or not url or len(url.encode('utf-8')) > 8192:
        raise ValueError('expected a loopback HTTP(S) URL of at most 8192 bytes')
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == '\\' for c in url):
        raise ValueError('URL contains whitespace, controls or backslashes')
    try:
        parts = urlsplit(url)
        port = parts.port
        valid = (parts.scheme in ('http', 'https')
                 and parts.hostname in ('127.0.0.1', 'localhost')
                 and parts.username is None and parts.password is None
                 and port is not None and 1 <= port <= 65535)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('use http(s)://127.0.0.1:PORT/... without credentials')
    return urlunsplit(parts._replace(netloc=f'127.0.0.1:{port}')), port


def stop_child(child):
    """Only signal the process group created for this relay, never the workspace."""
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=2)
    except ProcessLookupError:
        child.wait(timeout=2)


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        with server.children_lock:
            if server.stopping:
                return
            try:
                child = subprocess.Popen(
                    server.guest_command('relay'), stdin=self.request, stdout=self.request,
                    stderr=subprocess.DEVNULL, start_new_session=True,
                )
            except OSError:
                return
            server.children.add(child)
        try:
            child.wait()
        finally:
            stop_child(child)
            with server.children_lock:
                server.children.discard(child)


class PreviewServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, port, podman, workspace):
        self.podman = podman
        self.workspace = workspace
        self.target_port = port
        self.children = set()
        self.children_lock = threading.Lock()
        self.stopping = False
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(('127.0.0.1', port), Relay)

    def guest_command(self, mode):
        return [self.podman, 'exec', '-i', '--user', '1000:1000', self.workspace,
                '/bin/python3', '-I', '-u', '-c', GUEST_CODE, str(self.target_port), mode]

    def probe(self):
        try:
            subprocess.run(self.guest_command('probe'), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=8, check=True)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('workspace or its loopback service is unavailable') from None

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Never log request data, URLs or a subprocess environment/traceback.
        pass

    def close_relays(self):
        with self.children_lock:
            self.stopping = True
            children = list(self.children)
        for child in children:
            stop_child(child)


def open_browser(runner, opener, url):
    try:
        # Like the existing web-links broker, keep the browser outside the
        # forwarder's cgroup. Ctrl+C must not close the desktop browser.
        subprocess.run(
            [runner, '--user', '--collect', '--quiet', '--property=Type=exec',
             '--property=StandardOutput=null', '--property=StandardError=null',
             '--description=Agents local preview', '--', opener, url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError('host browser launcher failed; no forwarding was retained') from None


def open_preview(podman, workspace, runner, opener, url):
    url, port = preview_url(url)
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        try:
            server = PreviewServer(port, podman, workspace)
        except OSError:
            raise ValueError('host loopback port is occupied or cannot be bound') from None
        with server:
            server.probe()
            worker = threading.Thread(target=server.serve_forever,
                                      kwargs={'poll_interval': 0.1}, daemon=True)
            worker.start()
            try:
                if not stop.is_set():
                    open_browser(runner, opener, url)
                    print(f'Forwarding host 127.0.0.1:{port} to workspace loopback; '
                          'browser launch accepted. Ctrl+C stops forwarding.', flush=True)
                    stop.wait()
            finally:
                server.shutdown()
                server.close_relays()
                worker.join(timeout=2)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(args):
    # The first four arguments are fixed by the host package, never the URL.
    if len(args) == 5 and args[-1] in ('--help', '-h'):
        print('Usage: agents open http(s)://127.0.0.1:PORT/PATH\n'
              'Forward an existing workspace service and open the host browser.\n'
              'Runs in the foreground until Ctrl+C; never starts or rebuilds containers.')
        return 0
    if len(args) != 5:
        print('Usage: agents open http(s)://127.0.0.1:PORT/PATH', file=sys.stderr)
        return 2
    try:
        open_preview(*args)
    except (OSError, ValueError, UnicodeError):
        # Specific safe errors below never contain the supplied URL.
        print('agents open: unable to open local preview; check URL, host port, '
              'running workspace/service and desktop launcher', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
