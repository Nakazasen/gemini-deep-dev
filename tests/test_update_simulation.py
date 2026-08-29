from __future__ import annotations

import hashlib
import json
import codecs
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from deep_dev_installer import install
from deep_dev_update import update


class UpdateSimulationTests(unittest.TestCase):
    def test_installer_enables_auto_update_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            profile = Path(raw) / "profile"
            install(ROOT / "bundle", profile, "0.1.0", "https://example.invalid/update.json", sys.executable)
            config = json.loads((profile / ".gemini" / "config" / "deep-dev-update.json").read_text(encoding="utf-8"))
            self.assertTrue(config["auto_update"])

    def test_verified_file_url_update_installs_new_release(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            release_root = temp / "gemini-deep-dev-0.1.1"
            shutil.copytree(ROOT / "bundle", release_root / "bundle")
            archive = temp / "gemini-deep-dev-0.1.1.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for file in release_root.rglob("*"):
                    if file.is_file():
                        zip_file.write(file, file.relative_to(temp))
            manifest = temp / "update.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "latest_version": "0.1.1",
                "package_url": archive.as_uri(),
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            profile = temp / "profile"
            result = update(manifest.as_uri(), "0.1.0", profile, sys.executable, auto_update=True)
            self.assertEqual(result["status"], "updated")
            config = json.loads((profile / ".gemini" / "config" / "deep-dev-update.json").read_text(encoding="utf-8"))
            self.assertEqual(config["version"], "0.1.1")
            self.assertTrue(config["auto_update"])
            self.assertTrue((profile / ".gemini" / "config" / "skills" / "deep-dev" / "scripts" / "deep_dev_gate.py").is_file())
            self.assertTrue((profile / ".gemini" / "antigravity-ide" / "mcp" / "deep_dev_harness" / "execute_host_proposal.json").is_file())

    def test_rejects_download_with_wrong_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            archive = temp / "bad.zip"
            archive.write_bytes(b"not a release")
            manifest = temp / "update.json"
            manifest.write_text(json.dumps({"latest_version": "0.1.1", "package_url": archive.as_uri(), "sha256": "0" * 64}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                update(manifest.as_uri(), "0.1.0", temp / "profile", sys.executable)

    def test_accepts_utf8_bom_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            manifest = temp / "update.json"
            manifest.write_bytes(codecs.BOM_UTF8 + json.dumps({"latest_version": "0.1.0"}).encode("utf-8"))
            result = update(manifest.as_uri(), "0.1.0", temp / "profile", sys.executable)
            self.assertEqual(result, {"status": "up_to_date", "version": "0.1.0"})


if __name__ == "__main__":
    unittest.main()
