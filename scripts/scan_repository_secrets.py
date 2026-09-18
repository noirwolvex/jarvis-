from __future__ import annotations

import subprocess
from pathlib import Path

MAX_BYTES = 2_000_000


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def main() -> int:
    failures: list[str] = []
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    private_key_suffix = " PRIVATE KEY-----"

    for path in tracked_files():
        normalized = path.as_posix()
        name = path.name.casefold()

        if name == ".env":
            failures.append(f"{normalized}: tracked .env file")
            continue

        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue

        text = raw.decode("utf-8", errors="ignore")
        if private_key_marker in text or (
            "-----BEGIN " in text and private_key_suffix in text
        ):
            # The scanner source constructs the marker dynamically, so a hit here
            # represents another tracked file containing actual key material.
            if normalized != "scripts/scan_repository_secrets.py":
                failures.append(f"{normalized}: private key material")

    if failures:
        print("Repository secret scan failed:")
        for item in failures:
            print(f" - {item}")
        return 1

    print("Repository secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
