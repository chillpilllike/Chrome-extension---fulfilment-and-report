#!/usr/bin/env python3
"""Recover only the fulfilment container after sustained Docker health failure.

Run once per minute with systemd. Docker restart policies alone do not restart
an unhealthy container whose main process is still alive.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


FAILURE_SECONDS = 180
RESTART_COOLDOWN_SECONDS = 900


def evaluate(state, container_id, health, now):
    state = dict(state)
    if state.get("container_id") != container_id:
        state = {"container_id": container_id,
                 "last_restart": state.get("last_restart", 0)}
    if health != "unhealthy":
        state.pop("unhealthy_since", None)
        return state, False
    state.setdefault("unhealthy_since", now)
    due = now - state["unhealthy_since"] >= FAILURE_SECONDS
    cooled = now - state.get("last_restart", 0) >= RESTART_COOLDOWN_SECONDS
    return state, due and cooled


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=45).strip()


def public_health_ok():
    try:
        with urllib.request.urlopen("https://fulfilment.gofinch.com/health", timeout=5) as response:
            return response.status == 200 and json.loads(response.read(65536)).get("ok") is True
    except (OSError, ValueError, AttributeError):
        return False


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "gntjqbrkd1l1t131309fu82q"
    state_path = Path(os.environ.get("FULFILMENT_WATCHDOG_STATE", "/run/fulfilment-health-watchdog.json"))
    names = [name for name in docker("ps", "--format", "{{.Names}}").splitlines()
             if name.startswith(prefix + "-")]
    # Never guess during deployments or interfere with stopped containers.
    if len(names) != 1:
        return
    info = json.loads(docker("inspect", names[0]))[0]
    if not info["State"].get("Running"):
        return
    try:
        state = json.loads(state_path.read_text())
    except FileNotFoundError:
        state = {}
    now = time.time()
    health = info["State"].get("Health", {}).get("Status")
    # Docker can sample between intermittent freezes. Check the path staff use.
    if health in {"healthy", "unhealthy"} and not public_health_ok():
        health = "unhealthy"
    state, restart = evaluate(state, info["Id"], health, now)
    if restart:
        # Save the cooldown before attempting recovery, including failed attempts.
        state["last_restart"] = now
        state.pop("unhealthy_since", None)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(state_path)
    if restart:
        print("Fulfilment unhealthy for at least 180 seconds; restarting container.", flush=True)
        docker("restart", "--time", "30", names[0])


if __name__ == "__main__":
    main()
