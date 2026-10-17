#!/bin/sh
# Exercise the native installer in an isolated prefix with real FreeBSD ELF
# inputs and injected build/publication commands. Never touches ~/.local/bin.
set -eu

if ! command -v freebsd-version >/dev/null 2>&1; then
  echo "SKIP: build-install fixture test requires native FreeBSD" >&2
  exit 77
fi
if [ "$(rustc -vV | sed -n 's/^host: //p')" != x86_64-unknown-freebsd ]; then
  echo "SKIP: build-install fixture test requires native amd64 FreeBSD rustc" >&2
  exit 77
fi
if [ ! -x /usr/bin/clang ]; then
  echo "error: native /usr/bin/clang is required for small ELF fixtures" >&2
  exit 2
fi

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
INSTALLER="$REPO_ROOT/scripts/freebsd/build-install.sh"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/codex-freebsd-install-test.XXXXXX")
RELATIVE_BIN_DIR="target/codex-freebsd-relative-bin-$$"
cleanup() {
	if [ "${CODEX_FREEBSD_TEST_KEEP_TMP:-0}" = 1 ]; then
		echo "fixture scratch retained at $TMP_ROOT" >&2
	else
		rm -rf "$TMP_ROOT"
	fi
	rm -rf "$REPO_ROOT/$RELATIVE_BIN_DIR"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -n "${CODEX_FREEBSD_TEST_CLI_SOURCE:-}" ]; then
  SOURCE_CLI=$CODEX_FREEBSD_TEST_CLI_SOURCE
else
  SOURCE_CLI="$TMP_ROOT/native-cli-fixture"
  cat > "$TMP_ROOT/fixture_cli.c" <<'EOF'
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "--version") == 0) {
    puts("codex 0.0.0-freebsd-install-fixture");
    return 0;
  }
  return 64;
}
EOF
  /usr/bin/clang "$TMP_ROOT/fixture_cli.c" -o "$SOURCE_CLI"
fi
if [ ! -f "$SOURCE_CLI" ] || [ -L "$SOURCE_CLI" ]; then
  echo "error: native regular CLI fixture source is missing: $SOURCE_CLI" >&2
  exit 2
fi

# Compile a tiny, unmarked native helper fixture. This exercises the real
# FreeBSD ELF feature-note addition below without copying or executing the
# large Rust CLI/V8 host artifacts.
if [ -n "${CODEX_FREEBSD_TEST_HOST_SOURCE:-}" ]; then
  SOURCE_HOST=$CODEX_FREEBSD_TEST_HOST_SOURCE
else
  SOURCE_HOST="$TMP_ROOT/native-host-fixture"
  cat > "$TMP_ROOT/fixture_host.c" <<'EOF'
int main(void) { return 0; }
EOF
  /usr/bin/clang "$TMP_ROOT/fixture_host.c" -o "$SOURCE_HOST"
  if elfctl "$SOURCE_HOST" | grep -q 'wxneeded.*is set'; then
    echo "error: helper fixture unexpectedly starts with wxneeded set" >&2
    exit 1
  fi
fi
if [ ! -f "$SOURCE_HOST" ] || [ -L "$SOURCE_HOST" ]; then
  echo "error: native regular helper fixture source is missing: $SOURCE_HOST" >&2
  exit 2
fi

REAL_MV=/bin/mv
if [ ! -x "$REAL_MV" ]; then
	echo "error: expected native FreeBSD mv executable at $REAL_MV" >&2
	exit 2
fi
mkdir -p "$TMP_ROOT/repo/scripts/freebsd" "$TMP_ROOT/repo/codex-rs" "$TMP_ROOT/bin"
cp "$INSTALLER" "$TMP_ROOT/repo/scripts/freebsd/build-install.sh"
cat > "$TMP_ROOT/repo/scripts/freebsd/build-v8-source.sh" <<'EOF'
#!/bin/sh
set -eu
if [ "${CODEX_FREEBSD_V8_LOCK_HELD:-0}" != 1 ]; then
	echo "installer did not hand off the Cargo transaction lock to the V8 helper" >&2
	exit 95
fi
printf 'HOST:%s\n' "$CARGO_TARGET_DIR" >> "$CODEX_FREEBSD_TEST_BUILD_LOG"
mkdir -p "$CARGO_TARGET_DIR/release"
if [ "${CODEX_FREEBSD_TEST_OMIT_HOST:-0}" != 1 ]; then
  cat "$CODEX_FREEBSD_TEST_HOST_SOURCE" > "$CARGO_TARGET_DIR/release/codex-code-mode-host"
  chmod 755 "$CARGO_TARGET_DIR/release/codex-code-mode-host"
fi
EOF
cat > "$TMP_ROOT/bin/cargo" <<'EOF'
#!/bin/sh
set -eu
printf 'CLI:%s\n' "$CARGO_TARGET_DIR" >> "$CODEX_FREEBSD_TEST_BUILD_LOG"
if [ -n "${CODEX_FREEBSD_TEST_CONCURRENCY_MARKER:-}" ]; then
  if ! mkdir "$CODEX_FREEBSD_TEST_CONCURRENCY_MARKER" 2>/dev/null; then
    echo "injected concurrent build entered the same prefix" >&2
    exit 94
  fi
  trap 'rmdir "$CODEX_FREEBSD_TEST_CONCURRENCY_MARKER" 2>/dev/null || true' EXIT HUP INT TERM
  sleep 1
  rmdir "$CODEX_FREEBSD_TEST_CONCURRENCY_MARKER"
  trap - EXIT HUP INT TERM
fi
if [ -n "${CODEX_FREEBSD_TEST_CARGO_ACTIVE_MARKER:-}" ]; then
  printf '%s\n' "$$" > "$CODEX_FREEBSD_TEST_CARGO_ACTIVE_MARKER"
  sleep 2
  rm -f "$CODEX_FREEBSD_TEST_CARGO_ACTIVE_MARKER"
fi
mkdir -p "$CARGO_TARGET_DIR/release"
cat "$CODEX_FREEBSD_TEST_CLI_SOURCE" > "$CARGO_TARGET_DIR/release/codex"
chmod 755 "$CARGO_TARGET_DIR/release/codex"
EOF
cat > "$TMP_ROOT/bin/mv" <<'EOF'
#!/bin/sh
set -eu
if [ "${CODEX_FREEBSD_TEST_FAIL_HOST_BACKUP:-0}" = 1 ]; then
  case "${1:-}" in
    */codex-code-mode-host-freebsd)
      case "${2:-}" in
        */.codex-freebsd-backup.*/codex-code-mode-host-freebsd)
          echo "injected failure backing up code-mode host" >&2
          exit 92
          ;;
      esac
      ;;
  esac
fi
if [ "${CODEX_FREEBSD_TEST_FAIL_HOST_PUBLISH:-0}" = 1 ]; then
  case "${1:-}" in
    */.codex-freebsd-stage.*/codex-code-mode-host-freebsd)
      echo "injected failure publishing code-mode host" >&2
      exit 91
      ;;
  esac
fi
if [ "${CODEX_FREEBSD_TEST_SIGNAL_HOST_PUBLISH:-0}" = 1 ]; then
  case "${1:-}" in
    */.codex-freebsd-stage.*/codex-code-mode-host-freebsd)
      echo "injected SIGTERM during helper publication" >&2
      kill -TERM "$PPID"
      exit 0
      ;;
  esac
fi
if [ "${CODEX_FREEBSD_TEST_BREAK_CLI_VERSION:-0}" = 1 ]; then
  case "${1:-}" in
    */.codex-freebsd-stage.*/codex-freebsd)
      case "${2:-}" in
        */codex-freebsd)
          "$CODEX_FREEBSD_TEST_REAL_MV" "$@"
          cat > "$CODEX_FREEBSD_TEST_BIN_DIR/codex-freebsd" <<'BADCLI'
#!/bin/sh
echo "injected installed CLI version failure" >&2
exit 93
BADCLI
          chmod 755 "$CODEX_FREEBSD_TEST_BIN_DIR/codex-freebsd"
          exit 0
          ;;
      esac
      ;;
  esac
fi
exec "$CODEX_FREEBSD_TEST_REAL_MV" "$@"
EOF
chmod 755 "$TMP_ROOT/repo/scripts/freebsd/build-v8-source.sh" "$TMP_ROOT/bin/cargo" "$TMP_ROOT/bin/mv"

run_installer() {
  bin_dir=$1
  path_mode=$2
  host_source=${3:-$SOURCE_HOST}
  fail_host_publish=0
  fail_host_backup=0
  omit_host=0
  break_cli_version=0
  signal_host_publish=0
  concurrency_marker=
  cargo_active_marker=
  cli_target_dir=
  host_target_dir=
  if [ "$path_mode" = failure ]; then
    fail_host_publish=1
  elif [ "$path_mode" = backup-failure ]; then
    fail_host_backup=1
  elif [ "$path_mode" = version-failure ]; then
    break_cli_version=1
  elif [ "$path_mode" = signal-failure ]; then
    signal_host_publish=1
  elif [ "$path_mode" = missing-host ]; then
    omit_host=1
  elif [ "$path_mode" = separate-targets ]; then
    cli_target_dir="$TMP_ROOT/cli-output"
    host_target_dir="$TMP_ROOT/host-output"
  elif [ "$path_mode" = concurrent ]; then
    concurrency_marker="$TMP_ROOT/concurrent-cargo.active"
  elif [ "$path_mode" = cargo-lock-handoff ]; then
    cargo_active_marker="$TMP_ROOT/cargo-active"
  fi
  CARGO_TARGET_DIR="$TMP_ROOT/build-$path_mode" \
    CODEX_FREEBSD_CLI_TARGET_DIR="$cli_target_dir" \
    CODEX_FREEBSD_HOST_TARGET_DIR="$host_target_dir" \
    CODEX_FREEBSD_BIN_DIR="$bin_dir" \
    CODEX_FREEBSD_TEST_CLI_SOURCE="$SOURCE_CLI" \
    CODEX_FREEBSD_TEST_HOST_SOURCE="$host_source" \
    CODEX_FREEBSD_TEST_FAIL_HOST_PUBLISH="$fail_host_publish" \
    CODEX_FREEBSD_TEST_FAIL_HOST_BACKUP="$fail_host_backup" \
    CODEX_FREEBSD_TEST_OMIT_HOST="$omit_host" \
    CODEX_FREEBSD_TEST_REAL_MV="$REAL_MV" \
    CODEX_FREEBSD_TEST_BREAK_CLI_VERSION="$break_cli_version" \
    CODEX_FREEBSD_TEST_SIGNAL_HOST_PUBLISH="$signal_host_publish" \
    CODEX_FREEBSD_TEST_CONCURRENCY_MARKER="$concurrency_marker" \
    CODEX_FREEBSD_TEST_CARGO_ACTIVE_MARKER="$cargo_active_marker" \
    CODEX_FREEBSD_TEST_BIN_DIR="$bin_dir" \
    CODEX_FREEBSD_TEST_BUILD_LOG="$TMP_ROOT/build-paths.log" \
    PATH="$TMP_ROOT/bin:$PATH" \
    "$TMP_ROOT/repo/scripts/freebsd/build-install.sh"
}

# A direct source-helper invocation must wait while the installer is running
# its CLI Cargo command. Both use the same canonical repo-root lock even when
# their CARGO_TARGET_DIR values differ. Extract the actual production helper
# gate rather than reimplementing its lock behavior in this fixture.
awk '
  /^# Direct source-helper calls also share Cargo.lock/ { copy = 1 }
  copy && /^# End direct helper lock\./ { exit }
  copy { print }
' "$REPO_ROOT/scripts/freebsd/build-v8-source.sh" > "$TMP_ROOT/source-lock-gate.sh"
if [ ! -s "$TMP_ROOT/source-lock-gate.sh" ] || ! grep -q 'exec lockf -t 3600' "$TMP_ROOT/source-lock-gate.sh"; then
	echo "error: production source-helper lock gate was not extracted" >&2
	exit 2
fi
cat > "$TMP_ROOT/direct-source-helper.sh" <<'EOF'
#!/bin/sh
set -eu
REPO_ROOT="$CODEX_FREEBSD_TEST_REPO_ROOT"
MODE=${1:-}
EOF
cat "$TMP_ROOT/source-lock-gate.sh" >> "$TMP_ROOT/direct-source-helper.sh"
cat >> "$TMP_ROOT/direct-source-helper.sh" <<'EOF'
if [ -f "$CODEX_FREEBSD_TEST_CARGO_ACTIVE_MARKER" ]; then
	echo "direct helper entered during installer Cargo operation" >&2
	exit 96
fi
printf '%s\n' 'direct-helper-after-installer' >> "$CODEX_FREEBSD_TEST_HANDOFF_LOG"
EOF
chmod 755 "$TMP_ROOT/direct-source-helper.sh"
HANDOFF_BIN="$TMP_ROOT/cargo-lock-handoff-bin"
prepare_handoff_result() {
	mkdir -p "$HANDOFF_BIN"
	printf '%s\n' 'linux-codex-sentinel' > "$HANDOFF_BIN/codex"
}
prepare_handoff_result
run_installer "$HANDOFF_BIN" cargo-lock-handoff > "$TMP_ROOT/cargo-lock-handoff.log" 2>&1 &
HANDOFF_INSTALLER_PID=$!
handoff_attempt=0
while [ ! -f "$TMP_ROOT/cargo-active" ]; do
	if ! kill -0 "$HANDOFF_INSTALLER_PID" 2>/dev/null || [ "$handoff_attempt" -ge 100 ]; then
		cat "$TMP_ROOT/cargo-lock-handoff.log" >&2
		echo "error: installer did not enter its CLI Cargo operation" >&2
		exit 1
	fi
	handoff_attempt=$((handoff_attempt + 1))
	sleep 0.1
done
CODEX_FREEBSD_TEST_REPO_ROOT="$TMP_ROOT/repo" \
	CODEX_FREEBSD_TEST_CARGO_ACTIVE_MARKER="$TMP_ROOT/cargo-active" \
	CODEX_FREEBSD_TEST_HANDOFF_LOG="$TMP_ROOT/handoff.log" \
	CODEX_FREEBSD_V8_LOCK_HELD=0 \
	CARGO_TARGET_DIR="$TMP_ROOT/direct-target" \
	"$TMP_ROOT/direct-source-helper.sh" > "$TMP_ROOT/direct-source-helper.log" 2>&1 &
DIRECT_HELPER_PID=$!
if ! wait "$HANDOFF_INSTALLER_PID"; then
	cat "$TMP_ROOT/cargo-lock-handoff.log" >&2
	exit 1
fi
if ! wait "$DIRECT_HELPER_PID"; then
	cat "$TMP_ROOT/direct-source-helper.log" >&2
	exit 1
fi
grep -q '^direct-helper-after-installer$' "$TMP_ROOT/handoff.log"

SUCCESS_BIN="$TMP_ROOT/success-bin"
mkdir -p "$SUCCESS_BIN"
printf '%s\n' 'linux-codex-sentinel' > "$SUCCESS_BIN/codex"
if ! run_installer "$SUCCESS_BIN" success > "$TMP_ROOT/success.log" 2>&1; then
  cat "$TMP_ROOT/success.log" >&2
  exit 1
fi
grep -q '^linux-codex-sentinel$' "$SUCCESS_BIN/codex"
cmp -s "$SUCCESS_BIN/codex-freebsd" "$SOURCE_CLI"
file -b "$SUCCESS_BIN/codex-freebsd" | grep -q 'ELF 64-bit.*FreeBSD'
file -b "$SUCCESS_BIN/codex-code-mode-host-freebsd" | grep -q 'ELF 64-bit.*FreeBSD'
elfctl "$SUCCESS_BIN/codex-code-mode-host-freebsd" | grep -q 'wxneeded.*is set'
SOURCE_VERSION=$("$SOURCE_CLI" --version)
INSTALLED_VERSION=$("$SUCCESS_BIN/codex-freebsd" --version)
[ "$INSTALLED_VERSION" = "$SOURCE_VERSION" ]

mkdir -p "$RELATIVE_BIN_DIR"
printf '%s\n' 'linux-codex-sentinel' > "$RELATIVE_BIN_DIR/codex"
if ! run_installer "$RELATIVE_BIN_DIR" relative-bin > "$TMP_ROOT/relative-bin.log" 2>&1; then
	cat "$TMP_ROOT/relative-bin.log" >&2
	echo "error: installer failed with a relative bin prefix" >&2
	exit 1
fi
cmp -s "$RELATIVE_BIN_DIR/codex-freebsd" "$SOURCE_CLI"
elfctl "$RELATIVE_BIN_DIR/codex-code-mode-host-freebsd" | grep -q 'wxneeded.*is set'
grep -q '^linux-codex-sentinel$' "$RELATIVE_BIN_DIR/codex"
"$RELATIVE_BIN_DIR/codex-freebsd" --version | grep -q "$SOURCE_VERSION"
echo "PASS install success: relative bin prefix is canonicalized before builds"

SEPARATE_TARGET_BIN="$TMP_ROOT/separate-target-bin"
mkdir -p "$SEPARATE_TARGET_BIN"
printf '%s\n' 'linux-codex-sentinel' > "$SEPARATE_TARGET_BIN/codex"
if ! run_installer "$SEPARATE_TARGET_BIN" separate-targets > "$TMP_ROOT/separate-targets.log" 2>&1; then
  cat "$TMP_ROOT/separate-targets.log" >&2
  exit 1
fi
cmp -s "$TMP_ROOT/cli-output/release/codex" "$SOURCE_CLI"
grep -F -q "CLI:$TMP_ROOT/cli-output" "$TMP_ROOT/build-paths.log"
grep -F -q "HOST:$TMP_ROOT/host-output" "$TMP_ROOT/build-paths.log"
cmp -s "$SEPARATE_TARGET_BIN/codex-freebsd" "$SOURCE_CLI"
cmp -s "$TMP_ROOT/host-output/release/codex-code-mode-host" "$SEPARATE_TARGET_BIN/codex-code-mode-host-freebsd"
file -b "$SEPARATE_TARGET_BIN/codex-code-mode-host-freebsd" | grep -q 'ELF 64-bit.*FreeBSD'
elfctl "$SEPARATE_TARGET_BIN/codex-code-mode-host-freebsd" | grep -q 'wxneeded.*is set'
grep -q '^linux-codex-sentinel$' "$SEPARATE_TARGET_BIN/codex"

prepare_old_pair() {
  bin_dir=$1
  mkdir -p "$bin_dir"
  printf '%s\n' 'old-native-cli' > "$bin_dir/codex-freebsd"
  printf '%s\n' 'old-native-host' > "$bin_dir/codex-code-mode-host-freebsd"
  printf '%s\n' 'linux-codex-sentinel' > "$bin_dir/codex"
}

check_old_pair() {
  bin_dir=$1
  grep -q '^old-native-cli$' "$bin_dir/codex-freebsd"
  grep -q '^old-native-host$' "$bin_dir/codex-code-mode-host-freebsd"
  grep -q '^linux-codex-sentinel$' "$bin_dir/codex"
}

MISSING_HOST_BIN="$TMP_ROOT/missing-host-bin"
prepare_old_pair "$MISSING_HOST_BIN"
if run_installer "$MISSING_HOST_BIN" missing-host > "$TMP_ROOT/missing-host.log" 2>&1; then
  echo "error: installer accepted a missing native host" >&2
  exit 1
fi
grep -q 'expected a regular native build artifact' "$TMP_ROOT/missing-host.log"
check_old_pair "$MISSING_HOST_BIN"

LINUX_HOST_SOURCE=$(readlink -f "$HOME/.local/bin/codex")
if [ ! -f "$LINUX_HOST_SOURCE" ] || [ -L "$LINUX_HOST_SOURCE" ]; then
  echo "error: expected a resolved Linux ELF source for the rejection fixture" >&2
  exit 2
fi
file -b "$LINUX_HOST_SOURCE" | grep -q 'SYSV.*static-pie'
WRONG_HOST_BIN="$TMP_ROOT/wrong-host-bin"
prepare_old_pair "$WRONG_HOST_BIN"
if run_installer "$WRONG_HOST_BIN" wrong-linux-host "$LINUX_HOST_SOURCE" > "$TMP_ROOT/wrong-host.log" 2>&1; then
  echo "error: installer accepted a Linux helper ELF" >&2
  exit 1
fi
grep -q 'refusing to install non-native FreeBSD ELF' "$TMP_ROOT/wrong-host.log"
check_old_pair "$WRONG_HOST_BIN"

FAILURE_BIN="$TMP_ROOT/failure-bin"
prepare_old_pair "$FAILURE_BIN"
if run_installer "$FAILURE_BIN" failure > "$TMP_ROOT/failure.log" 2>&1; then
  echo "error: injected staged-host publication failure unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'injected failure publishing code-mode host' "$TMP_ROOT/failure.log"
grep -q '^old-native-cli$' "$FAILURE_BIN/codex-freebsd"
grep -q '^old-native-host$' "$FAILURE_BIN/codex-code-mode-host-freebsd"
grep -q '^linux-codex-sentinel$' "$FAILURE_BIN/codex"
if find "$FAILURE_BIN" -maxdepth 1 -type d -name '.codex-freebsd-*' | grep -q .; then
  echo "error: failed installation left stage or backup directories behind" >&2
  exit 1
fi

BACKUP_FAILURE_BIN="$TMP_ROOT/backup-failure-bin"
prepare_old_pair "$BACKUP_FAILURE_BIN"
if run_installer "$BACKUP_FAILURE_BIN" backup-failure > "$TMP_ROOT/backup-failure.log" 2>&1; then
  echo "error: injected host backup failure unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'injected failure backing up code-mode host' "$TMP_ROOT/backup-failure.log"
check_old_pair "$BACKUP_FAILURE_BIN"
if find "$BACKUP_FAILURE_BIN" -maxdepth 1 -type d -name '.codex-freebsd-*' | grep -q .; then
  echo "error: host backup failure left stage or backup directories behind" >&2
  exit 1
fi

VERSION_FAILURE_BIN="$TMP_ROOT/version-failure-bin"
prepare_old_pair "$VERSION_FAILURE_BIN"
if run_installer "$VERSION_FAILURE_BIN" version-failure > "$TMP_ROOT/version-failure.log" 2>&1; then
  echo "error: injected installed CLI version failure unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'injected installed CLI version failure' "$TMP_ROOT/version-failure.log"
check_old_pair "$VERSION_FAILURE_BIN"
if find "$VERSION_FAILURE_BIN" -maxdepth 1 -type d -name '.codex-freebsd-*' | grep -q .; then
  echo "error: installed-version failure left stage or backup directories behind" >&2
  exit 1
fi

SIGNAL_FAILURE_BIN="$TMP_ROOT/signal-failure-bin"
prepare_old_pair "$SIGNAL_FAILURE_BIN"
signal_status=0
if run_installer "$SIGNAL_FAILURE_BIN" signal-failure > "$TMP_ROOT/signal-failure.log" 2>&1; then
  echo "error: injected SIGTERM during publication unexpectedly succeeded" >&2
  exit 1
else
  signal_status=$?
fi
if [ "$signal_status" -ne 143 ]; then
  cat "$TMP_ROOT/signal-failure.log" >&2
  echo "error: SIGTERM installer exit status was $signal_status, expected 143" >&2
  exit 1
fi
grep -q 'injected SIGTERM during helper publication' "$TMP_ROOT/signal-failure.log"
check_old_pair "$SIGNAL_FAILURE_BIN"
if find "$SIGNAL_FAILURE_BIN" -maxdepth 1 -type d -name '.codex-freebsd-*' | grep -q .; then
  echo "error: SIGTERM rollback left stage or backup directories behind" >&2
  exit 1
fi

CONCURRENT_BIN="$TMP_ROOT/concurrent-bin"
prepare_old_pair "$CONCURRENT_BIN"
EXPECTED_CONCURRENT_HOST="$TMP_ROOT/expected-concurrent-host"
cp "$SOURCE_HOST" "$EXPECTED_CONCURRENT_HOST"
elfctl -e +wxneeded "$EXPECTED_CONCURRENT_HOST"
run_installer "$CONCURRENT_BIN" concurrent > "$TMP_ROOT/concurrent-1.log" 2>&1 &
first_installer=$!
run_installer "$CONCURRENT_BIN" concurrent > "$TMP_ROOT/concurrent-2.log" 2>&1 &
second_installer=$!
if ! wait "$first_installer"; then
  cat "$TMP_ROOT/concurrent-1.log" >&2
  exit 1
fi
if ! wait "$second_installer"; then
  cat "$TMP_ROOT/concurrent-2.log" >&2
  exit 1
fi
if ! cmp -s "$CONCURRENT_BIN/codex-freebsd" "$SOURCE_CLI"; then
	echo "error: concurrent installer produced a CLI different from the staged source" >&2
	exit 1
fi
if ! cmp -s "$CONCURRENT_BIN/codex-code-mode-host-freebsd" "$EXPECTED_CONCURRENT_HOST"; then
	echo "error: concurrent installer produced a helper different from the wxneeded-marked source" >&2
	exit 1
fi
grep -q '^linux-codex-sentinel$' "$CONCURRENT_BIN/codex"
if grep -q 'injected concurrent build entered the same prefix' "$TMP_ROOT/concurrent-1.log" "$TMP_ROOT/concurrent-2.log"; then
  echo "error: installers ran concurrent build commands for the same prefix" >&2
  exit 1
fi

echo "PASS install success: unified and separate CLI/helper target directories, native pair, wxneeded, version, and Linux sentinel"
echo "PASS preflight rejection: missing host and Linux helper preserve old pair"
echo "PASS rollback: injected host publish and host backup failures preserve old pair"
echo "PASS rollback: installed CLI version failure restores old pair"
echo "PASS rollback: SIGTERM during helper publication exits 143 and restores old pair"
echo "PASS concurrency: same-prefix installer transactions serialize and publish a matched pair"
