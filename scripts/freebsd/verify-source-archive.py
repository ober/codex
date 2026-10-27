#!/usr/bin/env python3
"""Verify the contents of a pinned V8 dependency source archive.

Gitiles archives can vary in gzip and tar metadata for the same Git tree. The
expected inventory pins each entry's path, type, mode, size and content hash (or
link target), independent of archive timestamps and ordering.
"""

import hashlib
import sys
import tarfile
import zlib
from pathlib import PurePosixPath

EXPECTED = {
    "chromium-rust": "63f30caaab9d7a72d2d00e857ec25dba515e009ce25e42f77e475f276e875a0f",
    "fadec": "acee141f72a0ef477f3723cf3e4a4deec7f14678696c7f149b9e73da6492b86f",
}


def verify(path: str, expected: str) -> bool:
    entries = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                return False
            if member.isfile():
                digest = hashlib.sha256()
                stream = archive.extractfile(member)
                if stream is None:
                    return False
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                entries.append((member.name, member.mode, member.size, digest.hexdigest()))
            else:
                entries.append((member.name, member.mode, member.type.decode("latin1"), member.linkname))
    return hashlib.sha256(repr(sorted(entries)).encode()).hexdigest() == expected


if __name__ == "__main__":
    try:
        ok = len(sys.argv) == 3 and sys.argv[1] in EXPECTED and verify(sys.argv[2], EXPECTED[sys.argv[1]])
    except (EOFError, OSError, tarfile.TarError, ValueError, zlib.error):
        ok = False
    sys.exit(0 if ok else 1)
