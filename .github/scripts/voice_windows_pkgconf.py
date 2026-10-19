"""Export a verified native pkgconf payload as a small Bazel repository."""

import argparse
import itertools
import json
from pathlib import Path
import stat


def export_repository(root: Path, target: str, version: str = "3.0.6") -> Path:
    if target not in ("x86_64-pc-windows-gnu", "x86_64-pc-windows-gnullvm"):
        raise ValueError(f"unsupported pkgconf execution target: {target}")
    root = root.resolve(strict=True)
    image = root / "pkgconf-image"
    if not image.is_dir():
        raise ValueError("pkgconf MSI image is missing")

    files = []
    for path in itertools.chain((image,), image.rglob("*")):
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
            or path.name.casefold() in ("build", "build.bazel")
        ):
            raise ValueError(
                f"unsupported pkgconf image entry: {path.relative_to(root)}"
            )
        if stat.S_ISREG(info.st_mode):
            files.append(path)

    executables = [path for path in files if path.name.casefold() == "pkgconf.exe"]
    if len(executables) != 1:
        raise ValueError("pkgconf image must contain exactly one pkgconf.exe")
    executable = executables[0]
    relative_executable = executable.relative_to(root).as_posix()
    if not relative_executable.startswith("pkgconf-image/"):
        raise ValueError("pkgconf executable must belong to the extracted image")

    outputs = ("BUILD.bazel", "MODULE.bazel", "pkgconf-tools.json")
    if any((root / name).exists() for name in outputs):
        raise ValueError("Bazel pkgconf repository outputs must be fresh")

    definitions = [
        'package(default_visibility = ["//visibility:public"])',
        'filegroup(name = "pkgconf_files", srcs = glob(["pkgconf-image/**"], allow_empty = False))',
    ]
    metadata = {
        "schemaVersion": 1,
        "target": target,
        "version": version,
        "executable": relative_executable,
        "files": len(files),
    }
    for name, contents in (
        ("BUILD.bazel", "\n".join(definitions) + "\n"),
        ("MODULE.bazel", 'module(name = "voice_windows_pkgconf")\n'),
        ("pkgconf-tools.json", json.dumps(metadata, indent=2) + "\n"),
    ):
        (root / name).write_text(contents, encoding="utf-8", newline="\n")
    return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--version", default="3.0.6")
    args = parser.parse_args()
    print(export_repository(args.root, args.target, args.version))
