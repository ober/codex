#!/usr/bin/env python3
"""Check that archive verification ignores timestamps but rejects changed files."""

import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "verifier", Path(__file__).with_name("verify-source-archive.py")
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class ArchiveVerificationTest(unittest.TestCase):
    def test_metadata_variation_and_content_change(self):
        entry = ("file", 0o644, 1, hashlib.sha256(b"a").hexdigest())
        expected_digest = hashlib.sha256(repr([entry]).encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            for name, content, mtime, expected in (
                ("original", b"a", 1, True),
                ("new_timestamp", b"a", 99, True),
                ("changed", b"b", 1, False),
            ):
                path = Path(directory) / (name + ".tar.gz")
                with tarfile.open(path, "w:gz") as archive:
                    member = tarfile.TarInfo("file")
                    member.mode, member.size, member.mtime = 0o644, 1, mtime
                    archive.addfile(member, io.BytesIO(content))
                self.assertEqual(verifier.verify(str(path), expected_digest), expected)


if __name__ == "__main__":
    unittest.main()
