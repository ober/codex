#!/bin/sh
# Exercise the production direct-helper lock with overlapping invocations.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
HELPER="$REPO_ROOT/scripts/freebsd/build-v8-source.sh"
TMP_ROOT=$(mktemp -d /tmp/codex-freebsd-lock-concurrency.XXXXXX)
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT HUP INT TERM

if ! command -v lockf >/dev/null 2>&1; then
	echo "error: lockf is required for this FreeBSD helper test" >&2
	exit 2
fi

# Extract exactly the lock gate used by the production helper, then make two
# fixture invocations contend on the same target lock. Each body records its
# start/end around a delay; any interleaving indicates a missed lock.
awk '
  /^# Direct source-helper calls also share Cargo.lock/ { copy = 1 }
  copy && /^# End direct helper lock\./ { exit }
  copy { print }
' "$HELPER" > "$TMP_ROOT/lock-gate.sh"
if [ ! -s "$TMP_ROOT/lock-gate.sh" ] || ! grep -q 'exec lockf -t 3600' "$TMP_ROOT/lock-gate.sh"; then
	echo "error: production lock gate was not extracted" >&2
	exit 2
fi

cat > "$TMP_ROOT/invoke.sh" <<'INVOKE'
#!/bin/sh
set -eu
REPO_ROOT="$TEST_REPO_ROOT"
MODE=${1:-}
INVOKE
cat "$TMP_ROOT/lock-gate.sh" >> "$TMP_ROOT/invoke.sh"
cat >> "$TMP_ROOT/invoke.sh" <<'INVOKE'
if [ "$MODE" = --gn-only ]; then
	shift
fi
printf 'start %s\n' "$1" >> "$TEST_LOG"
sleep 2
printf 'end %s\n' "$1" >> "$TEST_LOG"
INVOKE
chmod 755 "$TMP_ROOT/invoke.sh"

TEST_REPO_ROOT="$TMP_ROOT/repo" TEST_LOG="$TMP_ROOT/events.log" \
	CARGO_TARGET_DIR="$TMP_ROOT/target-a" "$TMP_ROOT/invoke.sh" --gn-only first &
FIRST_PID=$!
TEST_REPO_ROOT="$TMP_ROOT/repo" TEST_LOG="$TMP_ROOT/events.log" \
	CARGO_TARGET_DIR="$TMP_ROOT/target-b" "$TMP_ROOT/invoke.sh" second &
SECOND_PID=$!
wait "$FIRST_PID"
wait "$SECOND_PID"

awk '
  NR == 1 { if ($1 != "start") exit 1; first = $2 }
  NR == 2 { if ($1 != "end" || $2 != first) exit 1 }
  NR == 3 { if ($1 != "start" || $2 == first) exit 1; last_start = $2 }
  NR == 4 { if ($1 != "end" || $2 != last_start) exit 1 }
  END { if (NR != 4) exit 1 }
' "$TMP_ROOT/events.log" || {
	echo "error: source helper invocations interleaved despite lockf" >&2
	cat "$TMP_ROOT/events.log" >&2
	exit 1
}

echo "PASS V8 lock concurrency: direct helper invocations serialize around the Cargo.lock transaction"
