import os

from python.runfiles import runfiles


def main():
    runfiles_ctx = runfiles.Create()
    if runfiles_ctx is None:
        raise AssertionError("could not initialize Bazel runfiles")

    workspace = os.environ["TEST_WORKSPACE"]
    for relative_path in (
        "pkgconf-image/native/pkgconf.exe",
        "pkgconf-image/native/libpkgconf-3.dll",
        "pkgconf-image/LICENSE",
    ):
        path = runfiles_ctx.Rlocation(
            f"{workspace}/third_party/voice/testdata/pkgconf_fixture/{relative_path}"
        )
        if path is None or not os.path.isfile(path):
            raise AssertionError(f"missing provider runfile: {relative_path}")


if __name__ == "__main__":
    main()
