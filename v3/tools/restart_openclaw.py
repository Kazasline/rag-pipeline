#!/usr/bin/env python3
r"""Find OpenClaw, stop it, start it again — without guessing.

    python v3\tools\restart_openclaw.py             # do it
    python v3\tools\restart_openclaw.py --dry-run   # look, change nothing
    python v3\tools\restart_openclaw.py --status    # just report

THE RULE THIS SCRIPT OBEYS: never kill what you cannot restart.

OpenClaw holds the operator's WhatsApp session and is the delivery path for a
system already in production. Stopping it with only a guess about how to bring
it back would turn a two-minute restart into a broken WhatsApp bot and a
support problem. So the order is: discover how it is ACTUALLY running, capture
that exact command line, and only then stop it. If the command line cannot be
recovered, the script refuses to stop anything and prints what it found so a
human can decide.

Everything it does is printed before it does it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Match the PROGRAM, not the folder.
#
# The first version matched any command line containing "openclaw". Every
# launcher on the operator's machine lives in C:\Users\User\.openclaw\, so
# that matched the PATH and killed nine unrelated processes — the even-terminal
# bridge for their glasses, whisper STT, a claude-bridge server, and their IDE
# — while restarting one. A substring match on a directory name is not
# identification.
#
# These patterns name the actual OpenClaw entry points.
PROGRAM_MARKERS = (
    "node_modules/openclaw/dist/index.js",
    "node_modules\\openclaw\\dist\\index.js",
    "node_modules/openclaw/openclaw.mjs",
    "node_modules\\openclaw\\openclaw.mjs",
)

# Never touch these, whatever else matches: they are other programs that merely
# live in, or refer to, the .openclaw directory.
NEVER = ("even-terminal", "whisper", "claude-bridge", "antigravity",
         "restart_openclaw", "powershell", "conhost.exe")
DEFAULT_ENTRY = (Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "npm"
                 / "node_modules" / "openclaw" / "openclaw.mjs")
LOGDIR = Path(os.path.expanduser("~")) / ".openclaw"


def _is_openclaw(cmdline: str) -> bool:
    """Is this the OpenClaw program itself?

    Requires the command line to invoke an OpenClaw ENTRY POINT, and rejects
    anything on the NEVER list outright. A process is only a restart candidate
    if we can point at the file it is executing.
    """
    low = (cmdline or "").lower().replace("\\\\", "\\")
    if not low:
        return False
    if any(bad in low for bad in NEVER):
        return False
    return any(m.lower() in low for m in PROGRAM_MARKERS)


def _powershell(script: str) -> str:
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, timeout=60)
        return p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"(could not run powershell: {e})")
        return ""


def find_processes() -> list[dict]:
    """Every running process whose command line mentions openclaw."""
    if os.name != "nt":
        # Non-Windows: best effort, so the logic can be exercised anywhere.
        try:
            out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                                 text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        found = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            pid, _, cmd = line.partition(" ")
            if _is_openclaw(cmd):
                found.append({"pid": int(pid), "cmdline": cmd.strip(), "cwd": ""})
        return found

    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -and ($_.CommandLine -match 'openclaw') } | "
        "Select-Object ProcessId,CommandLine,ExecutablePath | ConvertTo-Json -Depth 3"
    )
    raw = _powershell(script).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    found = []
    for d in data:
        cmd = (d.get("CommandLine") or "").strip()
        if not _is_openclaw(cmd):
            continue
        found.append({"pid": int(d["ProcessId"]), "cmdline": cmd,
                      "exe": d.get("ExecutablePath") or ""})
    return found


def _spawn(cmdline: str, cwd: str | None = None) -> subprocess.Popen:
    """Start OpenClaw detached, with output captured to a file.

    Detached so it survives this console closing — the previous instance was
    very likely started from a terminal, and the operator should not have to
    keep one open. The log is where to look if it dies.
    """
    LOGDIR.mkdir(parents=True, exist_ok=True)
    log = LOGDIR / f"openclaw_restart_{int(time.time())}.log"
    f = open(log, "ab")
    kwargs: dict = {"stdout": f, "stderr": subprocess.STDOUT, "cwd": cwd or None}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    print(f"  log: {log}")
    return subprocess.Popen(cmdline, shell=True, **kwargs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true", help="report and exit")
    ap.add_argument("--entry", default=str(DEFAULT_ENTRY),
                    help="openclaw.mjs, used only if it is not already running")
    args = ap.parse_args()

    procs = find_processes()
    print(f"OpenClaw processes found: {len(procs)}")
    for p in procs:
        print(f"  PID {p['pid']}: {p['cmdline'][:160]}")
    if args.status:
        return 0

    # ---------------------------------------------------------------- not running
    if not procs:
        entry = Path(args.entry)
        print("\nNot running.")
        if not entry.exists():
            print(f"ERROR: cannot find {entry}")
            print("Pass the right path with --entry, or start OpenClaw the way "
                  "you normally do. Nothing was changed.")
            return 2
        node = "node"
        cmd = f'"{node}" "{entry}"'
        print(f"Would start: {cmd}")
        if args.dry_run:
            print("--dry-run: nothing started.")
            return 0
        print("Starting…")
        _spawn(cmd, cwd=str(entry.parent))
        time.sleep(6)
        now = find_processes()
        if now:
            print(f"Started. PID {now[0]['pid']}.")
            return 0
        print("It did not stay running — check the log above. It may need to be "
              "started the way you normally start it (a shortcut, a terminal "
              "window, or a startup task).")
        return 3

    # ---------------------------------------------------------------- running
    restartable = [p for p in procs if p["cmdline"]]
    if not restartable:
        print("\nREFUSING TO STOP IT.")
        print("OpenClaw is running, but its command line could not be read, so "
              "there is no way to start it again the same way. Stopping it now "
              "would leave your WhatsApp bot down with no reliable way back.")
        print("Restart it the way you normally do instead.")
        return 4

    print(f"\nWill restart {len(restartable)} process(es), each with its own "
          "command line:")
    for p in restartable:
        print(f"  PID {p['pid']}: {p['cmdline'][:140]}")
    skipped = [p for p in procs if p not in restartable]
    if skipped:
        print(f"Leaving {len(skipped)} other process(es) alone.")
    if args.dry_run:
        print("\n--dry-run: nothing stopped or started.")
        return 0

    # Stop and restart the SAME set. The first version killed every match and
    # started one of them, which is how a restart became an outage.
    stopped: list[dict] = []
    for p in restartable:
        print(f"Stopping PID {p['pid']}…")
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"],
                           capture_output=True, text=True)
        else:
            try:
                os.kill(p["pid"], 15)
            except OSError as e:
                print(f"  ({e})")
        stopped.append(p)
    time.sleep(3)

    for p in stopped:
        print(f"Starting again: {p['cmdline'][:120]}")
        _spawn(p["cmdline"])
    for _ in range(10):
        time.sleep(2)
        now = find_processes()
        if now:
            print(f"\nOpenClaw is back. PID {now[0]['pid']}.")
            print("\nNow message it on WhatsApp:")
            print('   "berapa claim amount certified untuk Selgate?"')
            print("The first answer takes ~30s while the model loads.")
            return 0

    print("\nIt did not come back within 20 seconds.")
    print("Start it manually with the command line above — it is the exact one "
          "it was already using:")
    print(f"   {target['cmdline']}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
