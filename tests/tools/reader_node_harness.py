"""Serial, test-only Node interpreter for the two real-JS DOM fixtures.

The worker owns no production source: callers supply the exact current reader
and witness bytes. Requests are bounded and never retried after a failure.
"""
import atexit
import json
import os
from pathlib import Path
import select
import subprocess
import threading
import time

HERE = Path(__file__).resolve().parent
WORKER = HERE / "reader_node_worker.cjs"
FIXTURES = {"closed_widget_dom.cjs", "entry_dom_fixture.js"}
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_LOCK = threading.Lock()
_OWNER_PID = os.getpid()
_CLIENT = None


class _Worker:
    def __init__(self, worker_path=WORKER):
        self.owner_pid = os.getpid()
        self.process = subprocess.Popen(
            ["node", str(worker_path)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.sequence = 0
        self.pending = bytearray()

    def close(self):
        if self.owner_pid != os.getpid():
            return  # Never signal a different interpreter's child after fork.
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        else:
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Reader worker response timed out")
        return remaining

    def _send(self, data, deadline):
        fd = self.process.stdin.fileno()
        offset = 0
        while offset < len(data):
            _, ready, _ = select.select([], [fd], [], self._remaining(deadline))
            if not ready:
                raise TimeoutError("Reader worker request write timed out")
            try:
                # Small nonblocking writes cannot deadlock on a full pipe when
                # the worker has not yet consumed a large real reader source.
                sent = os.write(fd, data[offset:offset + 4096])
            except BlockingIOError:
                continue
            if sent == 0:
                raise RuntimeError("Reader worker closed stdin")
            offset += sent

    def _receive(self, deadline):
        fd = self.process.stdout.fileno()
        while True:
            end = self.pending.find(b"\n")
            if end >= 0:
                line = bytes(self.pending[:end])
                del self.pending[:end + 1]
                return line
            if len(self.pending) > MAX_RESPONSE_BYTES:
                raise RuntimeError("Reader worker response exceeds limit")
            ready, _, _ = select.select([fd], [], [], self._remaining(deadline))
            if not ready:
                raise TimeoutError("Reader worker response timed out")
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not data:
                raise RuntimeError("Reader worker closed stdout mid-response")
            self.pending.extend(data)

    def request(self, fixture, argv, timeout):
        self.sequence += 1
        ident = self.sequence
        deadline = time.monotonic() + timeout
        payload = json.dumps({
            "id": ident, "fixture": str(fixture), "argv": argv,
            "timeout_ms": max(1, min(60000, int(timeout * 1000))),
        }).encode("utf-8") + b"\n"
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Reader worker request exceeds limit")
        self._send(payload, deadline)
        message = self._receive(deadline)
        try:
            result = json.loads(message)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("Malformed reader worker response") from exc
        if (not isinstance(result, dict) or result.get("id") != ident
                or not isinstance(result.get("ok"), bool)
                or not isinstance(result.get("stdout"), str)
                or not isinstance(result.get("stderr"), str)
                or (not result["ok"] and result.get("returncode") != 1)):
            raise RuntimeError("Invalid reader worker response envelope")
        return result


def _cleanup():
    global _CLIENT
    if _CLIENT is not None and _CLIENT.owner_pid == os.getpid():
        _CLIENT.close()
    _CLIENT = None


atexit.register(_cleanup)


def _client():
    global _CLIENT, _OWNER_PID
    current = os.getpid()
    if current != _OWNER_PID:
        # A fork must create its own worker, not borrow the parent's pipes.
        _CLIENT = None
        _OWNER_PID = current
    if _CLIENT is None or _CLIENT.process.poll() is not None:
        if _CLIENT is not None:
            _CLIENT.close()
        _CLIENT = _Worker()
    return _CLIENT


def reader_run(fixture, scenario, *scripts, timeout=15.0):
    """Return CompletedProcess with one-shot-like stdout/stderr; raise on errors."""
    global _CLIENT
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    fixture = Path(fixture)
    if fixture.name not in FIXTURES:
        raise ValueError("Unsupported DOM fixture")
    if not fixture.is_absolute():
        fixture = HERE / fixture
    if len(scripts) != (2 if fixture.name == "closed_widget_dom.cjs" else 1):
        raise ValueError("Wrong real reader/witness count for fixture")
    argv = [json.dumps(scenario), *scripts]
    if not all(isinstance(x, str) for x in argv):
        raise TypeError("Reader sources must be strings")
    command = ["node", str(fixture), *argv]
    with _LOCK:
        worker = _client()
        try:
            result = worker.request(fixture, argv, timeout)
        except (TimeoutError, RuntimeError, OSError, BrokenPipeError) as exc:
            worker.close()
            _CLIENT = None
            if isinstance(exc, TimeoutError):
                raise subprocess.TimeoutExpired(command, timeout) from exc
            raise
    if not result["ok"]:
        raise subprocess.CalledProcessError(
            result["returncode"], command,
            output=result["stdout"], stderr=result["stderr"],
        )
    return subprocess.CompletedProcess(
        command, 0, stdout=result["stdout"], stderr=result["stderr"],
    )


def reader_output(fixture, scenario, *scripts, timeout=15.0):
    """Text stdout, just like check_output(..., text=True)."""
    return reader_run(fixture, scenario, *scripts, timeout=timeout).stdout
