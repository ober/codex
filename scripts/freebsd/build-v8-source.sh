#!/bin/sh
# Build Codex's code-mode host against a native FreeBSD V8 source build.
#
# rusty_v8 152.2.0 has no FreeBSD V8 archive or Chromium Rust toolchain
# artifact. This script copies the locked crate into target/, applies the
# checked-in narrow opt-in patch, and points Cargo at that copy. No Cargo
# registry source is modified.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
CODEX_RS="$REPO_ROOT/codex-rs"
CARGO_MANIFEST="$CODEX_RS/Cargo.toml"
V8_VERSION=152.2.0
PATCH_DIR="$REPO_ROOT/scripts/freebsd/patches"
PATCH_ID=$(cd "$PATCH_DIR" && cksum v8-*.patch | cksum | awk '{print $1}')
V8_COPY="$REPO_ROOT/target/freebsd-v8-source/v8-$V8_VERSION-system-rust-$PATCH_ID-verified-source"
# The V8 crate archive checksum comes from codex-rs/Cargo.lock. Its published
# contents intentionally omit Chromium's separately pinned third_party/rust
# DEPS repository, which supplies the GN-referenced crate sources.
V8_RUST_DEPS_REV=afbc96607d0e659715d803cc099607dd1737fc41
V8_RUST_DEPS_SHA256=a801dc9ba4d007e97983aae6ffa3ebf87a91b1ba40f8c0f96c82b2e7beae5ed3
V8_RUST_DEPS_URL="https://chromium.googlesource.com/chromium/src/third_party/rust/+archive/$V8_RUST_DEPS_REV.tar.gz"
FADEC_REV=c9f78f532b9004de278489019ab2c6c28ae9746e
FADEC_SHA256=f5b7fefbb7165fce1cb90fcbd291e8e40c3d1c69815002cfad5c21617ee297d2
FADEC_URL="https://chromium.googlesource.com/external/github.com/aengelke/fadec/+archive/$FADEC_REV.tar.gz"
# V8's DEPS pins chromium/deps/icu at this exact revision. The Rust crate
# archive omits common/icudtl.dat, which its generated GN rule nevertheless
# requires. Pin both the upstream Git blob and its transport-independent hash;
# do not substitute a reduced Flutter/Chromecast ICU data bundle.
ICU_DATA_REV=d578f2e8b7bd5938e21cfb6bf15c079e0aa5b738
ICU_DATA_BLOB=9655a2e17f08c56d6dbbc47f6e52e3fd3ef2436e
ICU_DATA_SHA256=9f48c7f9c7c94d516a14870707e910ab94d75ae640ff6842c4af53276cd26ebe
ICU_DATA_URL="https://chromium.googlesource.com/chromium/deps/icu/+/$ICU_DATA_REV/common/icudtl.dat?format=TEXT"
RUSTC_HOST=$(rustc -vV | sed -n 's/^host: //p')
SYSROOT=$(rustc --print sysroot)
RUSTC_VERSION=$(rustc -vV | awk '
  /^release: / { release = $2 }
  /^commit-hash: / { commit = $2 }
  END {
    if (release == "" || commit == "") exit 1
    print "freebsd-" release "-" commit
  }
')
MODE=${1:-}

# Direct source-helper calls also share Cargo.lock and the patched V8 target
# directory. Serialize the complete helper invocation so two builds cannot
# observe or restore each other's temporary lockfile state. The canonical
# repo-root lock is independent of CARGO_TARGET_DIR; --gn-only also shares the
# copied V8 source tree. The lock file is intentionally never removed.
if [ "${CODEX_FREEBSD_V8_LOCK_HELD:-0}" != 1 ]; then
  LOCK_DIR="$REPO_ROOT/target/freebsd-v8-source"
  mkdir -p "$LOCK_DIR"
  if ! command -v lockf >/dev/null 2>&1; then
    echo "error: required command is missing: lockf (needed to serialize Cargo.lock updates)" >&2
    exit 2
  fi
  exec lockf -t 3600 "$LOCK_DIR/.cargo-lock-transaction.lock" \
    env CODEX_FREEBSD_V8_LOCK_HELD=1 "$0" "$@"
fi
# End direct helper lock.

if [ "$MODE" = --gn-only ]; then
  shift
fi

if ! command -v freebsd-version >/dev/null 2>&1; then
  echo "error: this helper must run on the native FreeBSD host" >&2
  exit 2
fi
if [ "$RUSTC_HOST" != x86_64-unknown-freebsd ]; then
  echo "error: expected native x86_64-unknown-freebsd rustc" >&2
  exit 2
fi

GN=$(command -v gn || true)
NINJA=$(command -v ninja || true)
CLANG_BASE_PATH=${CLANG_BASE_PATH:-/usr/local/llvm22}
LIBCLANG_PATH=${LIBCLANG_PATH:-$CLANG_BASE_PATH/lib}
if [ -z "$GN" ] || [ -z "$NINJA" ]; then
  echo "error: install native FreeBSD gn and ninja packages first" >&2
  exit 2
fi
if ! command -v bindgen >/dev/null 2>&1 || ! command -v rustfmt >/dev/null 2>&1; then
  echo "error: install native FreeBSD rust-bindgen-cli and Rust packages first" >&2
  exit 2
fi
if [ ! -x "$CLANG_BASE_PATH/bin/clang" ] || [ ! -f "$LIBCLANG_PATH/libclang.so" ]; then
  echo "error: expected clang and libclang under $CLANG_BASE_PATH" >&2
  exit 2
fi
BUILD_TOOL_BIN="$REPO_ROOT/target/freebsd-v8-source/build-tools"
mkdir -p "$BUILD_TOOL_BIN"
ln -sf "$(command -v python3)" "$BUILD_TOOL_BIN/python"
export PATH="$BUILD_TOOL_BIN:$PATH"

# Chromium's GN build expects bindgen, rustfmt, and libclang under one
# rust_bindgen_root. FreeBSD packages them in separate system directories.
RUST_BINDGEN_ROOT="$REPO_ROOT/target/freebsd-v8-source/build-tools/rust-toolchain"
mkdir -p "$RUST_BINDGEN_ROOT/bin" "$RUST_BINDGEN_ROOT/lib"
ln -sf "$(command -v bindgen)" "$RUST_BINDGEN_ROOT/bin/bindgen"
ln -sf "$(command -v rustfmt)" "$RUST_BINDGEN_ROOT/bin/rustfmt"
ln -sf "$LIBCLANG_PATH/libclang.so" "$RUST_BINDGEN_ROOT/lib/libclang.so"

REGISTRY_CACHE=${CARGO_HOME:-$HOME/.cargo}/registry/cache
LOCKFILE="$CODEX_RS/Cargo.lock"
V8_LOCK_CHECKSUM=$(awk -v wanted_version="$V8_VERSION" '
  /^\[\[package\]\]$/ { package = ""; version = "" }
  /^name = "v8"$/ { package = "v8" }
  package == "v8" && /^version = / { version = $3; gsub(/"/, "", version) }
  package == "v8" && version == wanted_version && /^checksum = / {
    checksum = $3; gsub(/"/, "", checksum); print checksum; exit
  }
' "$LOCKFILE")
if [ -z "$V8_LOCK_CHECKSUM" ]; then
  echo "error: v8-$V8_VERSION checksum not found in $LOCKFILE" >&2
  exit 2
fi
V8_ARCHIVE=
SOURCE_CACHE="$REPO_ROOT/target/freebsd-v8-source/cache"
mkdir -p "$SOURCE_CACHE"
for candidate in "$REGISTRY_CACHE"/*/v8-$V8_VERSION.crate "$SOURCE_CACHE"/v8-$V8_VERSION-*.crate; do
  if [ -f "$candidate" ] && [ "$(sha256 -q "$candidate")" = "$V8_LOCK_CHECKSUM" ]; then
    V8_ARCHIVE=$candidate
    break
  fi
done
if [ -z "$V8_ARCHIVE" ]; then
  # The CLI build need not download V8, so a fresh Cargo cache may not contain
  # this crate. Download into a private, unique file and verify the lockfile
  # checksum before using it; never modify Cargo's registry cache.
  V8_ARCHIVE=$(mktemp "$SOURCE_CACHE/v8-$V8_VERSION-XXXXXXXX.crate")
  V8_URL="https://static.crates.io/crates/v8/v8-$V8_VERSION.crate"
  if command -v fetch >/dev/null 2>&1; then
    fetch -qo "$V8_ARCHIVE" "$V8_URL" || { rm -f "$V8_ARCHIVE"; exit 2; }
  elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$V8_URL" -o "$V8_ARCHIVE" || { rm -f "$V8_ARCHIVE"; exit 2; }
  else
    rm -f "$V8_ARCHIVE"
    echo "error: fetch or curl is required to download the v8 crate" >&2
    exit 2
  fi
  if [ "$(sha256 -q "$V8_ARCHIVE")" != "$V8_LOCK_CHECKSUM" ]; then
    rm -f "$V8_ARCHIVE"
    echo "error: v8-$V8_VERSION.crate does not match Cargo.lock checksum" >&2
    exit 2
  fi
fi

mkdir -p "$(dirname -- "$V8_COPY")"
PATCH_MARKER="$V8_COPY/.freebsd-v8-patches-applied"
EXPECTED_MARKER="v8=$V8_VERSION crate_sha256=$V8_LOCK_CHECKSUM patch_id=$PATCH_ID icu_sha256=$ICU_DATA_SHA256 rust_deps=$V8_RUST_DEPS_REV rust_sha256=$V8_RUST_DEPS_SHA256 fadec=$FADEC_REV fadec_sha256=$FADEC_SHA256"
if [ -d "$V8_COPY" ] && [ ! -f "$PATCH_MARKER" ]; then
  echo "error: incomplete V8 source copy exists at $V8_COPY; remove it before retrying" >&2
  exit 2
fi
if [ ! -d "$V8_COPY" ]; then
  mkdir -p "$V8_COPY"
  tar -xzf "$V8_ARCHIVE" --strip-components 1 -C "$V8_COPY"
  (
    cd "$V8_COPY"
    for v8_patch in \
      v8-freebsd-buildconfig.patch \
      v8-freebsd-toolchain.patch \
      v8-freebsd-gcc-toolchain.patch \
      v8-freebsd-compiler.patch \
      v8-freebsd-sanitizers.patch \
      v8-freebsd-compiler-cpu-abi.patch \
      v8-freebsd-clang-build.patch \
      v8-freebsd-native-x64-toolchain.patch \
      v8-freebsd-rust.patch \
      v8-freebsd-virtual-address-space.patch \
      v8-freebsd-platform-posix.patch \
      v8-freebsd-platform-freebsd.patch \
      v8-freebsd-api.patch \
      v8-freebsd-rust-stdlib.patch \
      v8-freebsd-partition-alloc-stack.patch \
      v8-freebsd-libcxxabi-futex.patch \
      v8-freebsd-allocator-shim-internals.patch \
      v8-freebsd-allocator-shim-override-libc.patch \
      v8-freebsd-platform-selection.patch \
      v8-freebsd-zone-alignment.patch; do
      patch -p0 --dry-run < "$PATCH_DIR/$v8_patch"
      patch -p0 < "$PATCH_DIR/$v8_patch"
    done
    patch -p0 --dry-run < "$PATCH_DIR/v8-system-rust.patch"
    patch -p0 < "$PATCH_DIR/v8-system-rust.patch"
    patch -p0 --dry-run < "$PATCH_DIR/v8-freebsd-ninja-rust-bootstrap.patch"
    patch -p0 < "$PATCH_DIR/v8-freebsd-ninja-rust-bootstrap.patch"
    printf '%s\n' "$EXPECTED_MARKER" > "$PATCH_MARKER"
  )
fi
if [ -f "$PATCH_MARKER" ]; then
  # Earlier helper revisions wrote an empty marker only after every patch had
  # applied successfully. Upgrade that known-good marker in place; reject any
  # populated marker for another source/patch/data set.
  PREVIOUS_MARKER="v8=$V8_VERSION crate_sha256=$V8_LOCK_CHECKSUM patch_id=$PATCH_ID icu_sha256=$ICU_DATA_SHA256 rust_deps=$V8_RUST_DEPS_REV rust_sha256=$V8_RUST_DEPS_SHA256"
  EARLIER_MARKER="v8=$V8_VERSION patch_id=$PATCH_ID icu_sha256=$ICU_DATA_SHA256"
  if [ ! -s "$PATCH_MARKER" ] || [ "$(cat "$PATCH_MARKER")" = "$PREVIOUS_MARKER" ] || [ "$(cat "$PATCH_MARKER")" = "$EARLIER_MARKER" ]; then
    printf '%s\n' "$EXPECTED_MARKER" > "$PATCH_MARKER"
  elif [ "$(cat "$PATCH_MARKER")" != "$EXPECTED_MARKER" ]; then
    echo "error: V8 source marker does not match expected version, patches, and ICU data: $PATCH_MARKER" >&2
    exit 2
  fi
fi

# Restore the full common ICU data omitted from the published crate archive.
# Cache it by the pinned content hash and verify every use. Git's blob ID ties
# the bytes directly to the file listed by the pinned upstream revision.
ICU_DATA_CACHE="$REPO_ROOT/target/freebsd-v8-source/cache/icudtl.dat-$ICU_DATA_SHA256"
mkdir -p "$(dirname -- "$ICU_DATA_CACHE")"
if [ ! -f "$ICU_DATA_CACHE" ] || \
  [ "$(sha256 -q "$ICU_DATA_CACHE" 2>/dev/null || true)" != "$ICU_DATA_SHA256" ] || \
  [ "$(git hash-object "$ICU_DATA_CACHE" 2>/dev/null || true)" != "$ICU_DATA_BLOB" ]; then
  rm -f "$ICU_DATA_CACHE" "$ICU_DATA_CACHE.tmp"
  ICU_DATA_B64="$ICU_DATA_CACHE.b64"
  rm -f "$ICU_DATA_B64"
  if command -v fetch >/dev/null 2>&1; then
    fetch -qo "$ICU_DATA_B64" "$ICU_DATA_URL"
  elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$ICU_DATA_URL" -o "$ICU_DATA_B64"
  else
    echo "error: fetch or curl is required to obtain the pinned V8 ICU data" >&2
    exit 2
  fi
  if ! base64 -d < "$ICU_DATA_B64" > "$ICU_DATA_CACHE.tmp"; then
    rm -f "$ICU_DATA_B64" "$ICU_DATA_CACHE.tmp"
    echo "error: could not decode the Gitiles response for pinned V8 ICU data" >&2
    exit 2
  fi
  rm -f "$ICU_DATA_B64"
  if [ "$(sha256 -q "$ICU_DATA_CACHE.tmp")" != "$ICU_DATA_SHA256" ] || \
    [ "$(git hash-object "$ICU_DATA_CACHE.tmp")" != "$ICU_DATA_BLOB" ]; then
    rm -f "$ICU_DATA_CACHE.tmp"
    echo "error: pinned V8 ICU data failed SHA256/Git blob verification" >&2
    exit 2
  fi
  mv "$ICU_DATA_CACHE.tmp" "$ICU_DATA_CACHE"
fi
mkdir -p "$V8_COPY/third_party/icu/common"
cp "$ICU_DATA_CACHE" "$V8_COPY/third_party/icu/common/icudtl.dat"

# V8's DEPS file pins this Chromium Rust subtree separately from the crate.
# Verify the pinned content inventory because Gitiles archive metadata can vary.
# Restore that exact tree into the disposable V8 copy; the archive includes the
# vendor/ sources and build scripts referenced by V8's generated GN wrappers.
V8_RUST_DEPS_CACHE="$REPO_ROOT/target/freebsd-v8-source/cache/chromium-rust-$V8_RUST_DEPS_REV.tar.gz"
if [ ! -f "$V8_RUST_DEPS_CACHE" ] || \
  ! python3 "$REPO_ROOT/scripts/freebsd/verify-source-archive.py" chromium-rust "$V8_RUST_DEPS_CACHE"; then
  rm -f "$V8_RUST_DEPS_CACHE" "$V8_RUST_DEPS_CACHE.tmp"
  if command -v fetch >/dev/null 2>&1; then
    fetch -qo "$V8_RUST_DEPS_CACHE.tmp" "$V8_RUST_DEPS_URL"
  elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$V8_RUST_DEPS_URL" -o "$V8_RUST_DEPS_CACHE.tmp"
  else
    echo "error: fetch or curl is required to obtain V8's pinned Chromium Rust sources" >&2
    exit 2
  fi
  if ! python3 "$REPO_ROOT/scripts/freebsd/verify-source-archive.py" chromium-rust "$V8_RUST_DEPS_CACHE.tmp"; then
    rm -f "$V8_RUST_DEPS_CACHE.tmp"
    echo "error: V8's pinned Chromium Rust source inventory failed verification" >&2
    exit 2
  fi
  mv "$V8_RUST_DEPS_CACHE.tmp" "$V8_RUST_DEPS_CACHE"
fi
RUST_DEPS_MARKER="$V8_COPY/.freebsd-rust-deps-$V8_RUST_DEPS_REV"
if [ ! -f "$RUST_DEPS_MARKER" ]; then
  mkdir -p "$V8_COPY/third_party/rust"
  tar -xzf "$V8_RUST_DEPS_CACHE" -C "$V8_COPY/third_party/rust"
  touch "$RUST_DEPS_MARKER"
fi
if [ ! -f "$V8_COPY/third_party/rust/chromium_crates_io/vendor/icu_calendar_data-v2/build.rs" ]; then
  echo "error: pinned Chromium Rust archive did not provide V8's expected vendor tree" >&2
  exit 2
fi

# V8's DEPS file also pins fadec, a small generator required by the default
# x64 build. The Rusty V8 crate archive does not contain this DEPS checkout.
FADEC_CACHE="$REPO_ROOT/target/freebsd-v8-source/cache/fadec-$FADEC_REV.tar.gz"
if [ ! -f "$FADEC_CACHE" ] || \
  ! python3 "$REPO_ROOT/scripts/freebsd/verify-source-archive.py" fadec "$FADEC_CACHE"; then
  rm -f "$FADEC_CACHE" "$FADEC_CACHE.tmp"
  if command -v fetch >/dev/null 2>&1; then
    fetch -qo "$FADEC_CACHE.tmp" "$FADEC_URL"
  elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$FADEC_URL" -o "$FADEC_CACHE.tmp"
  else
    echo "error: fetch or curl is required to obtain V8's pinned fadec source" >&2
    exit 2
  fi
  if ! python3 "$REPO_ROOT/scripts/freebsd/verify-source-archive.py" fadec "$FADEC_CACHE.tmp"; then
    rm -f "$FADEC_CACHE.tmp"
    echo "error: V8's pinned fadec source inventory failed verification" >&2
    exit 2
  fi
  mv "$FADEC_CACHE.tmp" "$FADEC_CACHE"
fi
FADEC_MARKER="$V8_COPY/.freebsd-fadec-$FADEC_REV"
if [ ! -f "$FADEC_MARKER" ]; then
  mkdir -p "$V8_COPY/third_party/fadec/src"
  tar -xzf "$FADEC_CACHE" -C "$V8_COPY/third_party/fadec/src"
  touch "$FADEC_MARKER"
fi
if [ ! -f "$V8_COPY/third_party/fadec/src/parseinstrs.py" ]; then
  echo "error: pinned fadec archive did not provide parseinstrs.py" >&2
  exit 2
fi

# Chromium's GN scripts still name the in-tree rustc for build-script inputs
# even when rust_sysroot_absolute selects the host toolchain. Keep those paths
# inside this disposable copy pointed at the validated native Rust install.
mkdir -p "$V8_COPY/third_party/rust-toolchain/bin"
for rust_tool in rustc cargo rustfmt; do
  if command -v "$rust_tool" >/dev/null 2>&1; then
    ln -sf "$(command -v "$rust_tool")" \
      "$V8_COPY/third_party/rust-toolchain/bin/$rust_tool"
  fi
done

export GN NINJA CLANG_BASE_PATH LIBCLANG_PATH
export V8_FROM_SOURCE=1
export RUSTY_V8_SKIP_RUST_TOOLCHAIN_DOWNLOAD=1
export GN_ARGS="enable_rust=true enable_pkeys=false use_udev=false removed_rust_stdlib_libs=[\"profiler_builtins\"] rust_sysroot_absolute=\"$SYSROOT\" rust_bindgen_root=\"$RUST_BINDGEN_ROOT\" rustc_version=\"$RUSTC_VERSION\" is_clang=true clang_base_path=\"$CLANG_BASE_PATH\" clang_version=\"22\" treat_warnings_as_errors=false symbol_level=0 use_custom_libcxx=true use_lld=true use_autogenerated_modules=false use_sysroot=false"

echo "Native host: FreeBSD $(freebsd-version -k)"
echo "Rust: $RUSTC_VERSION (sysroot $SYSROOT; native FreeBSD target $RUSTC_HOST)"
echo "GN: $GN; Ninja: $NINJA; clang: $CLANG_BASE_PATH/bin/clang"
echo "Using copied and patched crate: $V8_COPY"
echo "ICU data: DEPS revision $ICU_DATA_REV, SHA256 $ICU_DATA_SHA256"
echo "Chromium Rust DEPS: $V8_RUST_DEPS_REV, content inventory verified"
echo "fadec DEPS: $FADEC_REV, content inventory verified"
echo "Verified rusty_v8 crate archive SHA256: $V8_LOCK_CHECKSUM"
if [ "$MODE" = --gn-only ]; then
  cd "$V8_COPY"
  gn gen out.freebsd-source-probe --args="$GN_ARGS v8_enable_sandbox=true v8_enable_external_code_space=true v8_enable_pointer_compression=true"
  exit 0
fi

# The path override changes the lockfile source entry during resolution. Keep
# the repository's exact existing lockfile bytes and restore them on all exits.
LOCK_BACKUP=$(mktemp "${TMPDIR:-/tmp}/codex-freebsd-cargo-lock.XXXXXX")
LOCK_EXPECTED=$(mktemp "${TMPDIR:-/tmp}/codex-freebsd-cargo-lock-expected.XXXXXX")
cp -p "$LOCKFILE" "$LOCK_BACKUP"
awk -v wanted_version="$V8_VERSION" '
  /^\[\[package\]\]$/ { is_v8 = 0; wanted = 0 }
  /^name = "v8"$/ { is_v8 = 1 }
  is_v8 && /^version = / {
    version = $3; gsub(/"/, "", version); wanted = (version == wanted_version)
  }
  wanted && (/^source = / || /^checksum = /) { next }
  { print }
' "$LOCK_BACKUP" > "$LOCK_EXPECTED"
restore_lock() {
  if cmp -s "$LOCKFILE" "$LOCK_BACKUP"; then
    rm -f "$LOCK_BACKUP" "$LOCK_EXPECTED"
    return 0
  fi
  if cmp -s "$LOCKFILE" "$LOCK_EXPECTED"; then
    LOCK_RESTORE_TEMP=$(mktemp "$CODEX_RS/Cargo.lock.restore.XXXXXX")
    cp -p "$LOCK_BACKUP" "$LOCK_RESTORE_TEMP"
    mv -f "$LOCK_RESTORE_TEMP" "$LOCKFILE"
    rm -f "$LOCK_BACKUP" "$LOCK_EXPECTED"
    return 0
  fi
  PRESERVED_LOCK=$(mktemp "$REPO_ROOT/target/freebsd-v8-source/Cargo.lock.before-unexpected-change.XXXXXX")
  cp -p "$LOCK_BACKUP" "$PRESERVED_LOCK"
  rm -f "$LOCK_BACKUP" "$LOCK_EXPECTED"
  echo "error: Cargo.lock changed beyond the expected v8 source/checksum update; left it untouched and saved the pre-build bytes at $PRESERVED_LOCK" >&2
  trap - EXIT HUP INT TERM
  exit 2
}
trap restore_lock EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cd "$CODEX_RS"
if [ -n "${CODEX_FREEBSD_FEATURE_NOTE:-}" ]; then
  if [ ! -f "$CODEX_FREEBSD_FEATURE_NOTE" ]; then
    echo "error: FreeBSD ELF feature note object not found: $CODEX_FREEBSD_FEATURE_NOTE" >&2
    exit 2
  fi
  cargo --config "patch.crates-io.v8.path=\"$V8_COPY\"" \
    rustc --manifest-path "$CARGO_MANIFEST" --release -p codex-code-mode-host \
    --bin codex-code-mode-host "$@" -- -C "link-arg=$CODEX_FREEBSD_FEATURE_NOTE"
else
  cargo --config "patch.crates-io.v8.path=\"$V8_COPY\"" \
    build --manifest-path "$CARGO_MANIFEST" --release -p codex-code-mode-host "$@"
fi
