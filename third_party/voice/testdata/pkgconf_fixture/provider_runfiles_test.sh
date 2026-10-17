#!/usr/bin/env bash

set -euo pipefail

runfiles_root="${TEST_SRCDIR}/${TEST_WORKSPACE}/third_party/voice/testdata/pkgconf_fixture/pkgconf-image"
test -f "${runfiles_root}/native/pkgconf.exe"
test -f "${runfiles_root}/native/libpkgconf-3.dll"
test -f "${runfiles_root}/LICENSE"
