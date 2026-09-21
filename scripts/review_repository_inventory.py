"""Inventory tracked files and locally available branch tips without changing refs.

This records coverage, syntax checks and tree differences, not a claim of a manual
line-by-line audit. It never reads ignored runtime state, credentials or captures.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8", errors="strict").strip()


def inventory() -> dict:
    files, errors = [], []
    for name in git("ls-files", "-z").split("\0"):
        if not name:
            continue
        raw = (ROOT / name).read_bytes()
        row = {"path": name, "bytes": len(raw), "lines": len(raw.splitlines()),
               "sha256": hashlib.sha256(raw).hexdigest(), "check": "inventory"}
        suffix = Path(name).suffix
        try:
            if suffix == ".py":
                ast.parse(raw, filename=name)
                row["check"] = "python_ast"
            elif suffix == ".json":
                json.loads(raw)
                row["check"] = "json_parse"
            elif suffix == ".toml":
                tomllib.loads(raw.decode("utf-8-sig"))
                row["check"] = "toml_parse"
        except (SyntaxError, ValueError) as exc:
            row["check"] = "failed"
            errors.append({"path": name, "error": type(exc).__name__})
        files.append(row)
    refs = []
    tips = {}
    for line in git("for-each-ref", "--format=%(refname:short)|%(objectname)|%(symref)", "refs/heads", "refs/remotes").splitlines():
        name, oid, symbolic = line.split("|", 2)
        if symbolic:
            continue
        if oid not in tips:
            counts = git("rev-list", "--left-right", "--count", f"HEAD...{oid}").split()
            changes = git("diff", "--name-only", "HEAD", oid).splitlines()
            tips[oid] = {"ahead": int(counts[1]), "behind": int(counts[0]), "changed_paths": changes}
        refs.append({"ref": name, "commit": oid, **tips[oid]})
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "base_commit": git("rev-parse", "HEAD"),
            "scope": "Tracked working-tree files and local/remote-tracking refs; no fetch or branch checkout",
            "file_count": len(files), "line_count": sum(row["lines"] for row in files),
            "by_extension": dict(sorted(Counter(Path(row["path"]).suffix or "[none]" for row in files).items())),
            "syntax_errors": errors, "files": files, "branches": refs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inventory()
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": report["file_count"], "lines": report["line_count"],
                      "branches": len(report["branches"]), "syntax_errors": report["syntax_errors"]}))
    raise SystemExit(bool(report["syntax_errors"]))
