"""Safely apply a newer Deep Dev ZIP advertised by a JSON manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from deep_dev_installer import install


def version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.strip().lstrip("v").split("."))


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def update(manifest_url: str, current_version: str, user_profile: Path, python_executable: str, auto_update: bool = False) -> dict:
    manifest = fetch_json(manifest_url)
    latest = str(manifest["latest_version"])
    if version_key(latest) <= version_key(current_version):
        return {"status": "up_to_date", "version": current_version}
    package_url, expected = str(manifest.get("package_url", "")), str(manifest.get("sha256", "")).lower()
    if not package_url or len(expected) != 64:
        raise ValueError("Update manifest is missing a package URL or SHA-256")
    with tempfile.TemporaryDirectory(prefix="gemini-deep-dev-update-") as raw:
        temp = Path(raw)
        archive = temp / "release.zip"
        with urllib.request.urlopen(package_url, timeout=60) as response:
            archive.write_bytes(response.read())
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("Downloaded release SHA-256 does not match manifest")
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(temp / "release")
        roots = [path for path in (temp / "release").iterdir() if path.is_dir()]
        release_root = roots[0] if len(roots) == 1 else temp / "release"
        bundle = release_root / "bundle"
        result = install(bundle, user_profile, latest, manifest_url, python_executable, auto_update)
    return {"status": "updated", "from": current_version, "to": latest, **result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--user-profile", type=Path, default=Path.home())
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--auto-update", action="store_true")
    args = parser.parse_args()
    print(json.dumps(update(args.manifest_url, args.current_version, args.user_profile.resolve(), args.python_executable, args.auto_update)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
