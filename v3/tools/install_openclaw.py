#!/usr/bin/env python3
r"""Register the V3 MCP server in openclaw.json — safely.

    python v3\tools\install_openclaw.py            # do it
    python v3\tools\install_openclaw.py --dry-run  # show what would change

This edits a config file that a WORKING production path depends on (V1's
WhatsApp delivery runs through the same file), so it behaves the way the rest
of this project treats the user's files:

  * the original is copied to openclaw.json.bak.<timestamp> BEFORE anything
    is written, and the backup is verified readable;
  * the file is parsed as JSON first — if it does not parse, nothing is
    touched, because a half-understood config is not a config to edit;
  * every existing MCP server is preserved; only the `alirag` key is added or
    updated, and the previous value is printed if one existed;
  * the result is written to a temp file, re-read and re-parsed, and only then
    moved into place, so an interrupted write cannot leave a broken config;
  * --dry-run prints the exact change and writes nothing.

If anything looks wrong afterwards, the backup path is printed: copy it back.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

DEFAULT_CONFIG = Path(os.path.expanduser("~")) / ".openclaw" / "openclaw.json"
REPO_V3 = Path(__file__).resolve().parents[1]

ENTRY_NAME = "alirag"


def build_entry(v3_dir: Path, config_yaml: Path | None) -> dict:
    entry = {
        "command": sys.executable or "python",
        "args": ["-m", "alirag.mcp_server"],
        "cwd": str(v3_dir),
    }
    if config_yaml and config_yaml.exists():
        entry["env"] = {"ALIRAG_CONFIG": str(config_yaml)}
    return entry


def _servers_container(cfg: dict) -> dict:
    """Find where MCP servers live, tolerating both shapes seen in the wild:
    {"mcp": {"servers": {...}}} and a top-level {"mcpServers": {...}}."""
    if isinstance(cfg.get("mcp"), dict):
        return cfg["mcp"].setdefault("servers", {})
    if isinstance(cfg.get("mcpServers"), dict):
        return cfg["mcpServers"]
    # neither present: create the documented shape
    return cfg.setdefault("mcp", {}).setdefault("servers", {})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default=str(DEFAULT_CONFIG),
                    help=f"openclaw.json location (default: {DEFAULT_CONFIG})")
    ap.add_argument("--v3", default=str(REPO_V3), help="path to the v3 folder")
    ap.add_argument("--config", default=None,
                    help="alirag config.yaml (default: v3/config.kazasline.yaml)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.path)
    v3_dir = Path(args.v3).resolve()
    cfg_yaml = Path(args.config) if args.config else (v3_dir / "config.kazasline.yaml")

    if not path.exists():
        print(f"ERROR: {path} not found.")
        print("Is OpenClaw installed for this user? Pass --path if it lives "
              "somewhere else. Nothing was changed.")
        return 2
    if not (v3_dir / "alirag" / "mcp_server.py").exists():
        print(f"ERROR: {v3_dir} does not look like the v3 folder "
              "(alirag/mcp_server.py missing). Nothing was changed.")
        return 2

    raw = path.read_text(encoding="utf-8")
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: {path} is not valid JSON ({e}).")
        print("Nothing was changed — fix the file first, or the edit would "
              "guess at what you meant.")
        return 2

    entry = build_entry(v3_dir, cfg_yaml)
    servers = _servers_container(cfg)
    existing = servers.get(ENTRY_NAME)

    if existing == entry:
        print(f"Already registered and identical — nothing to do.\n"
              f"  {ENTRY_NAME}: {json.dumps(entry, indent=2)}")
        return 0
    if existing is not None:
        print("An 'alirag' entry already exists and will be REPLACED.")
        print("  was: " + json.dumps(existing, indent=2))
    print("  now: " + json.dumps(entry, indent=2))
    print(f"\nOther MCP servers kept untouched: "
          f"{sorted(k for k in servers if k != ENTRY_NAME) or 'none'}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    shutil.copy2(path, backup)
    if backup.read_text(encoding="utf-8") != raw:
        print(f"ERROR: backup at {backup} does not match the original. "
              "Nothing was changed.")
        return 3
    print(f"\nBackup written: {backup}")

    servers[ENTRY_NAME] = entry
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        json.loads(tmp.read_text(encoding="utf-8"))       # it must parse
    except json.JSONDecodeError as e:
        tmp.unlink(missing_ok=True)
        print(f"ERROR: the generated config did not parse ({e}). "
              "Original left untouched.")
        return 3
    os.replace(tmp, path)

    print(f"Updated: {path}")
    print("\nNow restart OpenClaw, then message it on WhatsApp:")
    print('   "berapa claim amount certified untuk Selgate?"')
    print("\nIf anything breaks, restore with:")
    print(f'   copy "{backup}" "{path}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
