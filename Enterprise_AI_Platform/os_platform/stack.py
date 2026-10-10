"""One-command sitting: start Twin, Layer 2, GraphRAG, Eval, OS. Gateway is Docker :8000."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import Any

from os_platform.config import settings

ROOT = settings.repo_root
PY = settings.python_exe if os.path.isfile(settings.python_exe) else sys.executable


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


SERVICES: list[dict[str, Any]] = [
    {
        "name": "twin",
        "port": 8003,
        "cwd": os.path.join(ROOT, "Supply_Chain_Digital_Twin"),
        "argv": [PY, "-m", "twin.cli", "serve"],
    },
    {
        "name": "layer2",
        "port": 8001,
        "cwd": os.path.join(ROOT, "Multi_Agent_Platform"),
        "argv": [PY, "-m", "api.main"],
    },
    {
        "name": "graphrag",
        "port": 8100,
        "cwd": os.path.join(ROOT, "Enterprise_graphrag"),
        "argv": [PY, "-m", "graphrag.cli", "serve"],
    },
    {
        "name": "eval",
        "port": 8002,
        "cwd": os.path.join(ROOT, "Agent_Evaluation_Platform"),
        "argv": [PY, "-m", "eval_platform.cli", "serve"],
    },
    {
        "name": "os",
        "port": settings.port,
        "cwd": os.path.join(ROOT, "Enterprise_AI_Platform"),
        "argv": [PY, "-m", "os_platform.main"],
    },
]


def _spawn(svc: dict[str, Any]) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = svc["cwd"] + os.pathsep + env.get("PYTHONPATH", "")
    kw: dict[str, Any] = {
        "cwd": svc["cwd"],
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(svc["argv"], **kw)


def run(*, attach_os: bool = False) -> int:
    print("Enterprise sitting supervisor")
    print(f"  python={PY}")
    if _port_open(8000):
        print("  gateway :8000 already up")
    else:
        print("  gateway :8000 NOT listening — start AI_Gateway docker compose first")

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        for svc in SERVICES:
            if attach_os and svc["name"] == "os":
                continue
            if _port_open(svc["port"]):
                print(f"  skip {svc['name']} :{svc['port']} (already listening)")
                continue
            print(f"  start {svc['name']} :{svc['port']}")
            procs.append((svc["name"], _spawn(svc)))

        deadline = time.time() + 25
        for svc in SERVICES:
            if attach_os and svc["name"] == "os":
                continue
            while time.time() < deadline and not _port_open(svc["port"]):
                time.sleep(0.4)
            mark = "up" if _port_open(svc["port"]) else "WAITING"
            print(f"  {svc['name']} :{svc['port']} {mark}")

        print(f"\nOpen http://127.0.0.1:{settings.port}")
        print("Ctrl+C stops processes this supervisor started.\n")
        if attach_os:
            from os_platform.main import main as serve_os

            serve_os()
            return 0
        while True:
            time.sleep(2)
            for name, proc in procs:
                if proc.poll() is not None:
                    print(f"  {name} exited {proc.returncode}")
                    return proc.returncode or 1
    except KeyboardInterrupt:
        print("\nStopping…")
        return 0
    finally:
        for name, proc in procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                print(f"  stopped {name}")


def main(argv: list[str] | None = None) -> int:
    attach = "--attach" in (argv or sys.argv[1:])
    return run(attach_os=attach)


if __name__ == "__main__":
    raise SystemExit(main())
