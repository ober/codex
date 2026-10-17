"""Exercise the local Windows GNU fallback arguments without invoking Bazel."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run-bazel-ci.sh")
BAZELRC = SCRIPT.parents[2] / ".bazelrc"


class RunBazelPkgconfFallbackTests(unittest.TestCase):
    def assert_final_target_platform(self, args: list[str]) -> None:
        target_platforms = [arg for arg in args if arg.startswith("--platforms=")]
        self.assertTrue(target_platforms)
        self.assertEqual(target_platforms[-1], "--platforms=//:windows_x86_64_gnullvm")

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

        self.assertIn("--config=ci-windows-cross-local", args)
        self.assertIn("--host_platform=//:local_windows_msvc", args)
        self.assert_final_target_platform(args)
        self.assertNotIn("--config=ci-windows", args)
        self.assertNotIn("--config=ci-windows-cross", args)
        self.assertNotIn("--host_platform=//:rbe", args)
        self.assertNotIn(
            "--extra_execution_platforms=//:rbe,//:windows_x86_64_msvc", args
        )
        local_config = [
            line
            for line in BAZELRC.read_text(encoding="utf-8").splitlines()
            if line.startswith("common:ci-windows-cross-local ")
        ]
        self.assertIn(
            "common:ci-windows-cross-local --extra_execution_platforms=//:windows_x86_64_msvc",
            local_config,
        )
        self.assertIn(
            "common:ci-windows-cross-local --extra_toolchains=//:windows_gnullvm_tests_on_msvc_host_toolchain",
            local_config,
        )
        self.assertIn(
            "common:ci-windows-cross-local --config=ci-windows-msvc",
            local_config,
        )
        self.assertFalse(any("//:rbe" in line for line in local_config))
        msvc_config = [
            line
            for line in BAZELRC.read_text(encoding="utf-8").splitlines()
            if line.startswith("build:ci-windows-msvc ")
        ]
        self.assertTrue(
            any(
                "@local_config_cc//:cc-toolchain-x64_windows" in line
                for line in msvc_config
            )
        )
        self.assertFalse(
            any("BAZEL_MSVC_RUNTIME_VISUAL_STUDIO_EULA" in line for line in msvc_config)
        )
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
    def test_fallback_without_pkgconf_keeps_target_configuration(self):
        args = self.invoke_with_stub()

        self.assertIn("--config=ci-windows-cross-local", args)
        self.assert_final_target_platform(args)
        self.assertNotIn("--//third_party/voice:windows_pkgconf_only=true", args)

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
        self.assertIn("--config=ci-windows-cross", args)
        self.assert_final_target_platform(args)
        self.assertIn("--host_platform=//:rbe", args)


if __name__ == "__main__":
    unittest.main()
