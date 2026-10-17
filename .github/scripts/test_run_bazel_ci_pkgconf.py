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

    def invoke_with_stub(
        self,
        *,
        api_key: str = "",
        repository: str = "",
        cross_compile: bool = True,
    ) -> list[str]:
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
            INCLUDE="C:/VS/include",
            LIB="C:/VS/lib",
            LIBPATH="C:/VS/libpath",
            UCRTVersion="10.0.26100.0",
            UniversalCRTSdkDir="C:/Windows Kits/10",
            VCINSTALLDIR="C:/VS",
            VCToolsInstallDir="C:/VS/Tools",
            WindowsLibPath="C:/Windows Kits/lib",
            WindowsSdkBinPath="C:/Windows Kits/bin",
            WindowsSdkDir="C:/Windows Kits/10",
            WindowsSDKLibVersion="10.0.26100.0",
            WindowsSDKVersion="10.0.26100.0",
            VOICE_WINDOWS_PKGCONF_REPOSITORY=repository,
            BUILDBUDDY_API_KEY=api_key,
        )
        command = [str(SCRIPT)]
        if cross_compile:
            command.append("--windows-cross-compile")
        command.extend(
            [
                "--",
                "build",
                "--platforms=//:windows_x86_64",
                "--",
                "//some:target",
            ]
        )
        result = subprocess.run(
            command,
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
        self.assertIn("--host_action_env=PATH=C:/Windows/System32", args)
        self.assertIn("--test_env=PATH=C:/Windows/System32", args)
        self.assertFalse(any(arg.startswith("--action_env=") for arg in args))
        for variable in (
            "INCLUDE",
            "LIB",
            "LIBPATH",
            "UCRTVersion",
            "UniversalCRTSdkDir",
            "VCINSTALLDIR",
            "VCToolsInstallDir",
            "WindowsLibPath",
            "WindowsSdkBinPath",
            "WindowsSdkDir",
            "WindowsSDKLibVersion",
            "WindowsSDKVersion",
        ):
            self.assertIn(f"--host_action_env={variable}", args)
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
            "common:ci-windows-cross-local --config=ci-windows-msvc-host",
            local_config,
        )
        self.assertFalse(any("//:rbe" in line for line in local_config))
        host_config = [
            line
            for line in BAZELRC.read_text(encoding="utf-8").splitlines()
            if line.startswith("build:ci-windows-msvc-host ")
        ]
        self.assertIn(
            "build:ci-windows-msvc-host --extra_toolchains=//:local_windows_x64_msvc_cc_toolchain,//:local_windows_arm64_msvc_cc_toolchain",
            host_config,
        )
        self.assertTrue(any("--host_action_env=PATH" in line for line in host_config))
        self.assertFalse(any("--action_env=" in line for line in host_config))
        self.assertFalse(any("@local_config_cc//:" in line for line in host_config))
        self.assertFalse(
            any("BAZEL_MSVC_RUNTIME_VISUAL_STUDIO_EULA" in line for line in host_config)
        )
        build_file = (BAZELRC.parent / "BUILD.bazel").read_text(encoding="utf-8")
        self.assertIn(
            'name = "local_windows_x64_msvc_cc_toolchain",\n'
            "    exec_compatible_with = [\n"
            '        "@platforms//cpu:x86_64",\n'
            '        "@platforms//os:windows",\n'
            '        "@llvm//constraints/windows/abi:msvc",\n'
            "    ],\n"
            "    target_compatible_with = [\n"
            '        "@platforms//cpu:x86_64",\n'
            '        "@platforms//os:windows",\n'
            '        "@llvm//constraints/windows/abi:msvc",\n'
            "    ],\n"
            '    toolchain = "@local_config_cc//:cc-compiler-x64_windows",',
            build_file,
        )
        native_msvc_config = [
            line
            for line in BAZELRC.read_text(encoding="utf-8").splitlines()
            if line.startswith("build:ci-windows-msvc ")
        ]
        self.assertIn(
            "build:ci-windows-msvc --config=ci-windows-msvc-host",
            native_msvc_config,
        )
        self.assertTrue(any("--action_env=PATH" in line for line in native_msvc_config))
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
        self.assertIn("--action_env=PATH=/usr/bin:/bin", args)
        self.assertIn("--host_action_env=PATH=/usr/bin:/bin", args)

    @unittest.skipIf(os.name == "nt", "The shell wrapper test requires Bash")
    def test_native_windows_preserves_target_and_host_build_environment(self):
        args = self.invoke_with_stub(cross_compile=False)

        self.assertIn("--action_env=PATH=C:/Windows/System32", args)
        self.assertIn("--host_action_env=PATH=C:/Windows/System32", args)
        self.assertIn("--action_env=INCLUDE", args)
        self.assertIn("--host_action_env=INCLUDE", args)
        self.assertIn("--test_env=PATH=C:/Windows/System32", args)


if __name__ == "__main__":
    unittest.main()
