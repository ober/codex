#!/bin/sh
# Build and install a native FreeBSD CLI together with its matching host.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
CODEX_RS="$REPO_ROOT/codex-rs"
BIN_DIR=${CODEX_FREEBSD_BIN_DIR:-"$HOME/.local/bin"}
DEFAULT_CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-"$CODEX_RS/target"}
CLI_TARGET_DIR=${CODEX_FREEBSD_CLI_TARGET_DIR:-"$DEFAULT_CARGO_TARGET_DIR"}
# The source-built V8 crate has a separate cache so its native build does not
# invalidate or duplicate the ordinary CLI dependency graph. Explicit
# CARGO_TARGET_DIR keeps fixture builds and callers' overrides unified.
HOST_TARGET_DIR=${CODEX_FREEBSD_HOST_TARGET_DIR:-"${CARGO_TARGET_DIR:-$REPO_ROOT/target/freebsd-code-mode}"}
CLI_NAME=codex-freebsd
HOST_NAME=codex-code-mode-host-freebsd
CLI_SOURCE="$CLI_TARGET_DIR/release/codex"
HOST_SOURCE="$HOST_TARGET_DIR/release/codex-code-mode-host"

if ! command -v freebsd-version >/dev/null 2>&1; then
  echo "error: this installer must run on native FreeBSD" >&2
  exit 2
fi
if [ "$(rustc -vV | sed -n 's/^host: //p')" != x86_64-unknown-freebsd ]; then
  echo "error: expected native x86_64-unknown-freebsd Rust toolchain" >&2
  exit 2
fi
for command_name in cargo file readelf elfctl install lockf; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "error: required command is missing: $command_name" >&2
    exit 2
  fi
done

# Serialize the complete build/install transaction for this prefix. In
# particular, two publishers must not interleave CLI and helper renames and
# leave a mismatched pair. `lockf` releases the advisory lock automatically
# on process exit, including signal exits.
if [ "${CODEX_FREEBSD_INSTALL_LOCK_HELD:-0}" != 1 ]; then
  mkdir -p "$BIN_DIR"
  export CODEX_FREEBSD_INSTALL_LOCK_HELD=1
  exec lockf -t 600 "$BIN_DIR/.codex-freebsd-install.lock" "$0" "$@"
fi

# Serialize every Cargo operation against direct source-helper calls. The lock
# lives at a canonical repo-root path, independent of target-dir overrides.
# Hand it to the nested V8 helper so it does not attempt to lock recursively.
if [ "${CODEX_FREEBSD_V8_LOCK_HELD:-0}" != 1 ]; then
  LOCK_DIR="$REPO_ROOT/target/freebsd-v8-source"
  mkdir -p "$LOCK_DIR"
  export CODEX_FREEBSD_V8_LOCK_HELD=1
  exec lockf -t 3600 "$LOCK_DIR/.cargo-lock-transaction.lock" "$0" "$@"
fi

if [ ! -x /usr/bin/clang ]; then
  echo "error: native /usr/bin/clang is required to add the FreeBSD ELF feature note" >&2
  exit 2
fi

mkdir -p "$CLI_TARGET_DIR/freebsd-install"
WX_NOTE_SOURCE="$CLI_TARGET_DIR/freebsd-install/wxneeded-note.S"
WX_NOTE_OBJECT="$CLI_TARGET_DIR/freebsd-install/wxneeded-note.o"
cat > "$WX_NOTE_SOURCE" <<'EOF'
.section .note.tag,"a",@note
.p2align 2
.long 8
.long 4
.long 4
.asciz "FreeBSD"
.p2align 2
.long 0
EOF
/usr/bin/clang -c "$WX_NOTE_SOURCE" -o "$WX_NOTE_OBJECT"

cd "$CODEX_RS"
export CARGO_TARGET_DIR="$CLI_TARGET_DIR"
cargo build --locked --release -p codex-cli --bin codex
export CARGO_TARGET_DIR="$HOST_TARGET_DIR"
export CODEX_FREEBSD_FEATURE_NOTE="$WX_NOTE_OBJECT"
"$REPO_ROOT/scripts/freebsd/build-v8-source.sh"

verify_freebsd_elf() {
  binary=$1
  description=$(file -b "$binary")
  case "$description" in
    *"ELF 64-bit"*"x86-64"*"FreeBSD"*) ;;
    *)
      echo "error: refusing to install non-native FreeBSD ELF: $binary ($description)" >&2
      return 1
      ;;
  esac
  header=$(readelf -h "$binary")
  printf '%s\n' "$header" | grep -q 'OS/ABI:.*FreeBSD' || {
    echo "error: ELF OS ABI is not FreeBSD: $binary" >&2
    return 1
  }
  printf '%s\n' "$header" | grep -q 'Machine:.*X86-64' || {
    echo "error: ELF machine is not amd64: $binary" >&2
    return 1
  }
}

for binary in "$CLI_SOURCE" "$HOST_SOURCE"; do
  if [ ! -f "$binary" ] || [ -L "$binary" ]; then
    echo "error: expected a regular native build artifact: $binary" >&2
    exit 1
  fi
  verify_freebsd_elf "$binary"
done

# V8 JIT mappings need the per-executable wxneeded feature on hosts whose
# kern.elf64.allow_wx is disabled. Keep this opt-in on the V8-using helper;
# never change a machine-wide sysctl.
elfctl -e +wxneeded "$HOST_SOURCE"
elfctl -l "$HOST_SOURCE" | grep -q "wxneeded.*is set" || {
  echo "error: failed to mark the code-mode host wxneeded" >&2
  exit 1
}

mkdir -p "$BIN_DIR"
STAGE=$(mktemp -d "$BIN_DIR/.codex-freebsd-stage.XXXXXX")
BACKUP=$(mktemp -d "$BIN_DIR/.codex-freebsd-backup.XXXXXX")
PUBLISHED=0
CLI_PUBLISH_ATTEMPTED=0
HOST_PUBLISH_ATTEMPTED=0
cleanup() {
  status=$?
  if [ "$PUBLISHED" -ne 1 ]; then
    if [ "$CLI_PUBLISH_ATTEMPTED" -eq 1 ]; then
      rm -f "$BIN_DIR/$CLI_NAME"
    fi
    if [ "$HOST_PUBLISH_ATTEMPTED" -eq 1 ]; then
      rm -f "$BIN_DIR/$HOST_NAME"
    fi
    if [ -e "$BACKUP/$CLI_NAME" ] || [ -L "$BACKUP/$CLI_NAME" ]; then
      mv "$BACKUP/$CLI_NAME" "$BIN_DIR/$CLI_NAME"
    fi
    if [ -e "$BACKUP/$HOST_NAME" ] || [ -L "$BACKUP/$HOST_NAME" ]; then
      mv "$BACKUP/$HOST_NAME" "$BIN_DIR/$HOST_NAME"
    fi
  fi
  rm -rf "$STAGE" "$BACKUP"
  exit "$status"
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

install -m 755 "$CLI_SOURCE" "$STAGE/$CLI_NAME"
install -m 755 "$HOST_SOURCE" "$STAGE/$HOST_NAME"
verify_freebsd_elf "$STAGE/$CLI_NAME"
verify_freebsd_elf "$STAGE/$HOST_NAME"
elfctl -l "$STAGE/$HOST_NAME" | grep -q "wxneeded.*is set" || {
  echo "error: staged code-mode host lost the wxneeded feature" >&2
  exit 1
}

# All outputs have been built and checked before touching the active install.
# Back up both old artifacts so an error in either rename rolls both back.
for name in "$CLI_NAME" "$HOST_NAME"; do
  if [ -e "$BIN_DIR/$name" ] || [ -L "$BIN_DIR/$name" ]; then
    mv "$BIN_DIR/$name" "$BACKUP/$name"
  fi
done
CLI_PUBLISH_ATTEMPTED=1
mv "$STAGE/$CLI_NAME" "$BIN_DIR/$CLI_NAME"
HOST_PUBLISH_ATTEMPTED=1
mv "$STAGE/$HOST_NAME" "$BIN_DIR/$HOST_NAME"

"$BIN_DIR/$CLI_NAME" --version
PUBLISHED=1
echo "Installed native FreeBSD Codex CLI and code-mode host to $BIN_DIR"
echo "Linux codex at $BIN_DIR/codex was not touched."
