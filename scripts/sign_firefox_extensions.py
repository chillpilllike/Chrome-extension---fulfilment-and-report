from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ["fulfilment", "tracking", "manual-order-match", "amazon-invoice", "epost"]
THROTTLE_RE = re.compile(r"Expected available in (\d+) seconds")


def sign_extension(extension: str, artifacts_dir: Path) -> bool:
    command = [
        "npx",
        "--yes",
        "web-ext",
        "sign",
        "--source-dir",
        str(ROOT / "firefox-extensions" / extension),
        "--artifacts-dir",
        str(artifacts_dir / extension),
        "--channel",
        "unlisted",
        "--api-key",
        os.environ["AMO_JWT_ISSUER"],
        "--api-secret",
        os.environ["AMO_JWT_SECRET"],
    ]
    while True:
        print(f"Signing {extension}...")
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        output = f"{result.stdout}\n{result.stderr}".strip()
        print(output)
        if result.returncode == 0:
            return True
        if "Version 0.1.1 already exists" in output:
            print(f"{extension} 0.1.1 already exists on AMO.")
            return False
        match = THROTTLE_RE.search(output)
        if not match:
            raise SystemExit(result.returncode)
        wait_seconds = int(match.group(1)) + 15
        print(f"AMO throttled signing. Waiting {wait_seconds} seconds before retrying {extension}.")
        time.sleep(wait_seconds)


def main() -> None:
    if not os.environ.get("AMO_JWT_ISSUER") or not os.environ.get("AMO_JWT_SECRET"):
        raise SystemExit("AMO_JWT_ISSUER and AMO_JWT_SECRET are required.")
    artifacts_dir = ROOT / "firefox-extensions" / "signed-0.1.1"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for extension in EXTENSIONS:
        existing = list((artifacts_dir / extension).glob("*.xpi"))
        if existing:
            print(f"Skipping {extension}; signed XPI already exists.")
            continue
        sign_extension(extension, artifacts_dir)
    print("Signed files:")
    for xpi in sorted(artifacts_dir.glob("*/*.xpi")):
        print(xpi)


if __name__ == "__main__":
    main()
