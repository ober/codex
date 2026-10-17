"""Tests for the standalone Windows pkgconf Bazel repository exporter."""

import json
from pathlib import Path
import tempfile
import unittest

from voice_windows_pkgconf import export_repository


class VoiceWindowsPkgconfTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "pkgconf-image"
        self.native = self.image / "native"
        self.native.mkdir(parents=True)
        (self.native / "pkgconf.exe").write_bytes(b"native executable")
        (self.native / "libpkgconf-3.dll").write_bytes(b"runtime dependency")
        (self.image / "LICENSE").write_text("license notice\n", encoding="utf-8")

    def test_exports_executable_and_complete_runtime_tree(self):
        executable = export_repository(self.root, "x86_64-pc-windows-gnullvm")

        self.assertEqual(executable, self.native / "pkgconf.exe")
        build = (self.root / "BUILD.bazel").read_text(encoding="utf-8")
        self.assertIn('glob(["pkgconf-image/**"]', build)
        metadata = json.loads(
            (self.root / "pkgconf-tools.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["version"], "3.0.6")
        self.assertEqual(metadata["executable"], "pkgconf-image/native/pkgconf.exe")
        self.assertEqual(metadata["files"], 3)

    def test_rejects_multiple_executable_candidates(self):
        (self.image / "other").mkdir()
        (self.image / "other" / "pkgconf.exe").write_bytes(b"ambiguous")

        with self.assertRaisesRegex(ValueError, "exactly one pkgconf.exe"):
            export_repository(self.root, "x86_64-pc-windows-gnullvm")

    def test_rejects_reparse_or_symlink_entries(self):
        link = self.image / "escape"
        try:
            link.symlink_to(self.native / "pkgconf.exe")
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symlink creation unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "unsupported pkgconf image entry"):
            export_repository(self.root, "x86_64-pc-windows-gnullvm")


if __name__ == "__main__":
    unittest.main()
