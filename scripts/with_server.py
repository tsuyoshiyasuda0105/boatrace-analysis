from __future__ import annotations

import argparse
import socket
import subprocess
import time


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise TimeoutError(f"server did not open {host}:{port} within {timeout:.1f}s: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start a local server command, wait for a port, then run a client command after -- ."
    )
    parser.add_argument("--server", required=True, help='Example: "python scripts/run_web.py --port 5010 --testing"')
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("client", nargs=argparse.REMAINDER, help="Command to run after --")
    args = parser.parse_args()

    client_cmd = list(args.client)
    if client_cmd and client_cmd[0] == "--":
        client_cmd = client_cmd[1:]
    if not client_cmd:
        parser.error("client command is required after --")

    server_proc = subprocess.Popen(args.server, shell=True)
    try:
        _wait_for_port(args.host, args.port, args.timeout)
        return subprocess.call(client_cmd)
    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
