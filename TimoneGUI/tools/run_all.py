#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Timone Ground Station - Multi-Process Launcher (with simulator integration)
--------------------------------------------------------------------------

Starts the communicator + GUI listener scripts and monitors them. If a simulator
is running (simulate_embedded.py) and has written tools/sim_port.txt, this
launcher will automatically read that PTY path and pass it to communicator.py
via --port <path> (and --baud 115200).

Usage:
    # Typical workflow (run in two terminals):
    # Terminal A:
    #   python3 simulate_embedded.py --flight-log ... --state-file ...
    # Terminal B:
    #   python3 run_all.py --wait-sim 30

Options:
    --no-restart       Disable automatic restarts on crash
    --wait-sim N       Wait up to N seconds for tools/sim_port.txt to appear
    --baud B           Override baud for communicator.py (default 115200)
    --port P           Explicit serial port (overrides sim_port.txt if provided)

Logging:
    Logs are written to ./logs/<script>.log
"""

import os
import sys
import time
import signal
import subprocess
import threading
import argparse
from pathlib import Path

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
SCRIPTS = [
    "communicator.py",
    "gui_lora_915.py",
    "gui_radio_433.py",
    "gui_status.py",
    "gui_peripherals.py",
]

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

SIM_PORT_FILE = Path(__file__).resolve().parent / "sim_port.txt"
DEFAULT_BAUD = "115200"

# Optional simple circuit breaker to avoid restart flapping
MAX_FAILS = 5
FAIL_WINDOW_S = 20


# ---------------------------------------------------------
# Launcher class
# ---------------------------------------------------------
class ScriptSupervisor:
    def __init__(self, restart: bool = True, wait_sim: int = 0, baud: str = DEFAULT_BAUD, port_override: str | None = None):
        self.restart = restart
        self.wait_sim = int(wait_sim) if wait_sim and int(wait_sim) > 0 else 0
        self.baud = str(baud or DEFAULT_BAUD)
        self.port_override = port_override
        self.processes: dict[str, subprocess.Popen] = {}
        self._stop = threading.Event()
        self.fail_history: dict[str, list[float]] = {s: [] for s in SCRIPTS}

    # ---------- lifecycle ----------
    def start_all(self):
        """Start all scripts (communicator first)."""
        # Optionally wait for simulator port file
        sim_port = self._maybe_wait_for_sim_port()

        # Launch communicator first so GUI listeners have a server
        for script in SCRIPTS:
            self._launch(script, sim_port=sim_port)
        # Start watcher thread
        threading.Thread(target=self._watchdog_loop, name="watchdog", daemon=True).start()

    def stop_all(self):
        """Terminate all subprocesses cleanly."""
        self._stop.set()
        for name, proc in self.processes.items():
            try:
                if proc.poll() is None:
                    print(f"[STOP] Terminating {name} (pid={proc.pid})")
                    proc.terminate()
            except Exception:
                pass
        time.sleep(1)
        for name, proc in self.processes.items():
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
        print("[STOP] All processes terminated.")

    # ---------- communicator args ----------
    def _communicator_args(self, script_path: Path, sim_port: str | None) -> list[str]:
        """
        Build the argument list for communicator.py, preferring:
          1) --port provided by CLI --port
          2) tools/sim_port.txt (if present / waited for)
          3) no port flag (auto-detect)
        Always passes --baud <baud> if a port was specified by 1) or 2).
        """
        # Highest priority: explicit --port override
        if self.port_override:
            return [sys.executable, str(script_path), "--port", self.port_override, "--baud", self.baud]

        # Next: sim_port.txt
        if sim_port:
            return [sys.executable, str(script_path), "--port", sim_port, "--baud", self.baud]

        # Fallback: no explicit port (communicator will auto-detect)
        return [sys.executable, str(script_path)]

    # ---------- launching ----------
    def _launch(self, script: str, sim_port: str | None):
        """Launch a single script as a subprocess with logging."""
        path = Path(__file__).resolve().parent / script
        log_file = LOG_DIR / f"{Path(script).stem}.log"
        print(f"[LAUNCH] {script} -> {log_file}")

        # Build args
        if script == "communicator.py":
            args = self._communicator_args(path, sim_port)
        else:
            args = [sys.executable, str(path)]

        # Open file in append binary mode
        f = open(log_file, "ab", buffering=0)
        proc = subprocess.Popen(
            args,
            stdout=f,
            stderr=subprocess.STDOUT,
            cwd=path.parent,
        )
        # Keep handle so it doesn't get GC'd (and closed) while running
        proc._log_handle = f  # noqa: attach for cleanup
        self.processes[script] = proc

    # ---------- watchdog ----------
    def _watchdog_loop(self):
        """Monitors child processes and restarts if needed (with circuit breaker)."""
        while not self._stop.is_set():
            time.sleep(2)
            for script, proc in list(self.processes.items()):
                if proc.poll() is not None:  # exited
                    code = proc.returncode
                    print(f"[WATCHDOG] {script} exited with code {code}")
                    try:
                        # Close its log handle
                        if hasattr(proc, "_log_handle"):
                            proc._log_handle.close()
                    except Exception:
                        pass

                    # Record failure time
                    now = time.time()
                    fh = self.fail_history.setdefault(script, [])
                    fh.append(now)
                    self.fail_history[script] = [t for t in fh if now - t <= FAIL_WINDOW_S]

                    if not self.restart:
                        continue
                    if len(self.fail_history[script]) >= MAX_FAILS:
                        print(f"[WATCHDOG] {script} crashed {len(self.fail_history[script])} times in {FAIL_WINDOW_S}s; NOT restarting.")
                        continue

                    # Relaunch (communicator may want to re-read sim_port)
                    sim_port = self._read_sim_port() if script == "communicator.py" else None
                    print(f"[RESTART] Restarting {script}...")
                    self._launch(script, sim_port=sim_port)

    # ---------- simulator port helpers ----------
    def _read_sim_port(self) -> str | None:
        """Return the PTY path from tools/sim_port.txt if it exists, else None."""
        try:
            if SIM_PORT_FILE.exists():
                port = SIM_PORT_FILE.read_text(encoding="utf-8").strip()
                return port or None
        except Exception:
            pass
        return None

    def _maybe_wait_for_sim_port(self) -> str | None:
        """
        If --wait-sim N is set, poll for sim_port.txt up to N seconds.
        Otherwise, attempt a single read (non-blocking).
        """
        deadline = time.time() + self.wait_sim if self.wait_sim > 0 else time.time()
        while True:
            port = self._read_sim_port()
            if port:
                print(f"[SIM] Using simulator port: {port}")
                return port
            if time.time() >= deadline:
                if self.wait_sim > 0:
                    print("[SIM] sim_port.txt not found within wait window; communicator will auto-detect.")
                return None
            time.sleep(0.25)


# ---------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Launch communicator + GUI scripts (sim-friendly)")
    ap.add_argument("--no-restart", action="store_true", help="Disable automatic restarts for crashed scripts")
    ap.add_argument("--wait-sim", type=int, default=0, help="Wait up to N seconds for tools/sim_port.txt to appear")
    ap.add_argument("--baud", default=DEFAULT_BAUD, help="Baud for communicator when a port is provided (default 115200)")
    ap.add_argument("--port", default=None, help="Explicit serial port (overrides sim_port.txt)")
    args = ap.parse_args()

    supervisor = ScriptSupervisor(
        restart=not args.no_restart,
        wait_sim=args.wait_sim,
        baud=args.baud,
        port_override=args.port,
    )

    def _signal_handler(signum, frame):
        print("\n[CTRL-C] Shutting down…")
        supervisor.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    supervisor.start_all()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _signal_handler(None, None)


if __name__ == "__main__":
    main()
