"""Run the existing FastAPI and Vite applications together on Windows or Linux."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--snapshot", action="store_true", help="use the recorded atmosphere for deterministic demos/tests")
    parser.add_argument("--playback", default=None, help="replay a recorded ground session instead of listening for UDP telemetry")
    parser.add_argument("--playback-speed", type=float, default=1.0)
    args = parser.parse_args()
    for port in (args.api_port, args.web_port):
        with socket.socket() as sock:
            if sock.connect_ex(("localhost", port)) == 0:
                parser.error(f"port {port} is already in use; keep that server or choose another port")
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    npm = shutil.which("npm")
    if not python.exists() or not npm or not (ROOT / "frontend/node_modules").is_dir():
        parser.error("install the local Python and npm dependencies first; see README.md")
    env = os.environ.copy()
    env["AEROFIRE_PORT"] = str(args.api_port)
    env["VITE_API_URL"] = f"http://127.0.0.1:{args.api_port}"
    backend = [str(python), "backend.py", "--port", str(args.api_port)] + (["--snapshot"] if args.snapshot else [])
    if args.playback:
        backend += ["--playback", args.playback, "--playback-speed", str(args.playback_speed)]
    frontend = [npm, "run", "dev", "--", "--port", str(args.web_port), "--strictPort"]
    if os.name == "nt":
        frontend = ["cmd.exe", "/c", *frontend]
    children = []
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    try:
        children.append(subprocess.Popen(backend, cwd=ROOT, env=env, **options))
        children.append(subprocess.Popen(frontend, cwd=ROOT / "frontend", env=env, **options))
        print(f"AERO-FIRE: http://localhost:{args.web_port} (API 127.0.0.1:{args.api_port}); Ctrl+C stops both", flush=True)
        while all(p.poll() is None for p in children):
            time.sleep(0.25)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is not None:
                continue
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    sys.exit(main())
