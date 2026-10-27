# FreeBSD V8 patch provenance

The source helper builds the `v8` crate version **152.2.0** selected by
`codex-rs/Cargo.lock`. It verifies the crate archive against Cargo's locked
SHA-256 before extracting it. These patches adapt that crate's bundled V8
sources for native FreeBSD; they are not a replacement V8 distribution.

The reference FreeBSD Ports tree inspected on 2026-10-16 was commit
[`1915fd8f76fed6954539ccf5af66b9890022e4a4`](https://git.freebsd.org/ports.git/commit/?id=1915fd8f76fed6954539ccf5af66b9890022e4a4).
Its [`lang/v8/Makefile`](https://cgit.freebsd.org/ports/plain/lang/v8/Makefile?id=1915fd8f76fed6954539ccf5af66b9890022e4a4)
declares `LICENSE= APACHE20 BSD3CLAUSE MIT UNICODE30` with a combined license
expression and documents V8's BSD-3-Clause license plus separate ICU/Unicode
licensing. Ports currently targets V8 15.5.35.20, newer than the locked 15.2.2.0
crate source used here. The entries below are therefore provenance references
for adapted hunks, not claims that these local patches are byte-identical to
the newer Ports files.

The following patches contain hunks adapted from the named FreeBSD Ports files
at that commit:

| Local patch | FreeBSD Ports source |
| --- | --- |
| `v8-freebsd-buildconfig.patch` | [`patch-build_config_BUILDCONFIG.gn`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-build_config_BUILDCONFIG.gn?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-toolchain.patch` | [`patch-build_toolchain_toolchain.gni`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-build_toolchain_toolchain.gni?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-compiler.patch` | [`patch-build_config_compiler_BUILD.gn`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-build_config_compiler_BUILD.gn?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-compiler-cpu-abi.patch` | [`patch-build_config_compiler__cpu__abi.gn`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-build_config_compiler__cpu__abi.gn?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-clang-build.patch` | [`patch-build_config_clang_BUILD.gn`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-build_config_clang_BUILD.gn?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-gcc-toolchain.patch` | [`patch-build_toolchain_gcc__toolchain.gni`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-build_toolchain_gcc__toolchain.gni?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-platform-freebsd.patch` | [`patch-src_base_platform_platform-freebsd.cc`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-src_base_platform_platform-freebsd.cc?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-platform-posix.patch` | [`patch-src_base_platform_platform-posix.cc`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-src_base_platform_platform-posix.cc?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-virtual-address-space.patch` | [`patch-src_base_virtual-address-space.cc`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-src_base_virtual-address-space.cc?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-api.patch` | [`patch-src_api_api.cc`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-src_api_api.cc?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-partition-alloc-stack.patch` | [`patch-base_allocator_partition__allocator_src_partition__alloc_stack_stack.cc`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-base_allocator_partition__allocator_src_partition__alloc_stack_stack.cc?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-allocator-shim-internals.patch` | [`patch-third__party_partition__alloc_src_partition__alloc_shim_allocator__shim__internals.h`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-third__party_partition__alloc_src_partition__alloc_shim_allocator__shim__internals.h?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-allocator-shim-override-libc.patch` | [`patch-third__party_partition__alloc_src_partition__alloc_shim_allocator__shim__override__libc__symbols.h`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-third__party_partition__alloc_src_partition__alloc_shim_allocator__shim__override__libc__symbols.h?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-platform-selection.patch` | [`patch-BUILD.gn`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-BUILD.gn?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |
| `v8-freebsd-zone-alignment.patch` | [`patch-src_zone_zone.h`](https://cgit.freebsd.org/ports/plain/lang/v8/files/patch-src_zone_zone.h?id=1915fd8f76fed6954539ccf5af66b9890022e4a4) |

`v8-freebsd-sanitizers.patch` adapts the narrowly scoped Debian Chromium
LLVM 22 compatibility change
[`ignore-for-ubsan-feature.patch`](https://sources.debian.org/patches/chromium/154.0.8037.57-1/llvm-22/ignore-for-ubsan-feature.patch/).
The local version preserves the sanitizer and trap flags and removes only the
unsupported `-fsanitize-ignore-for-ubsan-feature` flag.

The remaining patches are local FreeBSD build/runtime integration changes;
they are not represented as FreeBSD Ports patches:

- `v8-freebsd-native-x64-toolchain.patch`
- `v8-freebsd-rust.patch`
- `v8-freebsd-rust-stdlib.patch`
- `v8-freebsd-libcxxabi-futex.patch`
- `v8-freebsd-ninja-rust-bootstrap.patch`
- `v8-system-rust.patch`

These diffs do not convey a standalone license for the upstream code they
modify. The build retains the full source archive and its license files, and
the applicable upstream and vendored-component notices continue to govern
their respective files. The helper pins the Chromium Rust DEPS tree, ICU data,
and `fadec` independently by immutable revision/blob identifiers and
SHA-256; see `build-v8-source.sh` for those exact values and verification.
