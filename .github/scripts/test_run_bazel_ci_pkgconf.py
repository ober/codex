"""Exercise the local Windows GNU fallback arguments without invoking Bazel."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run-bazel-ci.sh")


class RunBazelPkgconfFallbackTests(unittest.TestCase):
    def invoke_with_stub(self, *, api_key: str = "", repository: str = "") -> list[str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        captured = root / "args.txt"
        bazel = root / "bazel-stub"
        bazel.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$CODEX_TEST_CAPTURE_ARGS"\n',
            encoding="utf-8",
        )
        bazel.chmod(0o755)
        environment = dict(
            os.environ,
            RUNNER_OS="Windows",
            CODEX_BAZEL_BIN=str(bazel),
            CODEX_TEST_CAPTURE_ARGS=str(captured),
            CODEX_BAZEL_WINDOWS_PATH="C:/Windows/System32",
            VOICE_WINDOWS_PKGCONF_REPOSITORY=repository,
            BUILDBUDDY_API_KEY=api_key,
        )
        result = subprocess.run(
            [
                str(SCRIPT),
                "--windows-cross-compile",
                "--",
                "build",
                "--platforms=//:windows_x86_64",
                "--",
                "//some:target",
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return captured.read_text(encoding="utf-8").splitlines()

    @unittest.skipIf(os.name == "nt", "The shell wrapper test requires Bash")
    def test_fallback_injects_only_the_pkgconf_provider(self):
        args = self.invoke_with_stub(repository="C:/runner/temp/voice-windows-pkgconf")

        self.assertIn(
            "--inject_repository=voice_windows_pkgconf=C:/runner/temp/voice-windows-pkgconf",
            args,
        )
        self.assertIn("--//third_party/voice:windows_pkgconf_only=true", args)
        self.assertIn(
            "--//third_party/voice:windows_installed_pkgconf=@voice_windows_pkgconf//:pkgconf_files",
            args,
        )
        self.assertNotIn("--//third_party/voice:windows_installed_tools", args)
        self.assertNotIn("--repo_env=BAZEL_MSVC_RUNTIME_VISUAL_STUDIO_EULA=1", args)

    @unittest.skipIf(os.name == "nt", "The shell wrapper test requires Bash")
    def test_remote_execution_does_not_select_pkgconf_only_provider(self):
        args = self.invoke_with_stub(
            api_key="test-placeholder",
            repository="C:/runner/temp/voice-windows-pkgconf",
        )

        self.assertNotIn("--//third_party/voice:windows_pkgconf_only=true", args)
        self.assertNotIn(
            "--//third_party/voice:windows_installed_pkgconf=@voice_windows_pkgconf//:pkgconf_files",
            args,
        )


if __name__ == "__main__":
    unittest.main()
