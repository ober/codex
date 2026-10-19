import tempfile
import unittest
from pathlib import Path

from select_sdk_binaries import select_binaries


class SelectSdkBinariesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def output(self, relative_path, executable=False):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
        path.chmod(0o755 if executable else 0o644)
        return str(path)

    def test_selects_named_binaries_among_auxiliary_outputs(self):
        cli = self.output("bazel-out/fastbuild/bin/codex-rs/cli/codex", True)
        host = self.output(
            "bazel-out/fastbuild/bin/codex-rs/code-mode-host/codex-code-mode-host",
            True,
        )
        outputs = [
            self.output("bazel-out/fastbuild/bin/codex-rs/cli/codex.dwp"),
            host,
            self.output("bazel-out/fastbuild/bin/codex-rs/cli/codex.debug"),
            cli,
            self.output(
                "bazel-out/fastbuild/bin/codex-rs/code-mode-host/codex-code-mode-host.dwp"
            ),
            self.output(
                "bazel-out/fastbuild/bin/codex-rs/code-mode-host/codex-code-mode-host.debug"
            ),
        ]

        self.assertEqual(
            select_binaries(outputs, ["codex", "codex-code-mode-host"]),
            [Path(cli), Path(host)],
        )

    def test_rejects_missing_binary(self):
        cli = self.output("bazel-out/bin/codex", True)
        with self.assertRaisesRegex(ValueError, "found 0"):
            select_binaries(
                [cli, self.output("bazel-out/bin/codex-code-mode-host.debug")],
                ["codex", "codex-code-mode-host"],
            )

    def test_rejects_ambiguous_binary_name(self):
        first = self.output("bazel-out/a/bin/codex", True)
        second = self.output("bazel-out/b/bin/codex", True)
        host = self.output("bazel-out/bin/codex-code-mode-host", True)
        with self.assertRaisesRegex(ValueError, "found 2"):
            select_binaries([first, second, host], ["codex", "codex-code-mode-host"])

    def test_rejects_non_executable_binary(self):
        cli = self.output("bazel-out/bin/codex")
        host = self.output("bazel-out/bin/codex-code-mode-host", True)
        with self.assertRaisesRegex(ValueError, "not executable"):
            select_binaries([cli, host], ["codex", "codex-code-mode-host"])

    def test_rejects_duplicate_query_paths(self):
        cli = self.output("bazel-out/bin/codex", True)
        host = self.output("bazel-out/bin/codex-code-mode-host", True)
        with self.assertRaisesRegex(ValueError, "duplicate output paths"):
            select_binaries([cli, host, cli], ["codex", "codex-code-mode-host"])


if __name__ == "__main__":
    unittest.main()
