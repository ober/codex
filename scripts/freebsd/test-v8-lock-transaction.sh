#!/bin/sh
# Run the production Cargo.lock transaction block against a disposable fixture.
# The fake cargo applies only the expected v8 source/checksum change, then fails.
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
HELPER="$REPO_ROOT/scripts/freebsd/build-v8-source.sh"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/codex-freebsd-lock-test.XXXXXX")
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT HUP INT TERM

mkdir -p "$TMP_ROOT/repo/codex-rs" "$TMP_ROOT/bin" "$TMP_ROOT/repo/target/freebsd-v8-source"
LOCKFILE="$TMP_ROOT/repo/codex-rs/Cargo.lock"
cat > "$LOCKFILE" <<'LOCK'
version = 4

[[package]]
name = "other"
version = "1.0.0"
source = "registry+https://example.invalid/index"
checksum = "0000000000000000000000000000000000000000000000000000000000000000"

[[package]]
name = "v8"
version = "152.2.0"
source = "registry+https://example.invalid/index"
checksum = "1111111111111111111111111111111111111111111111111111111111111111"
LOCK
cp "$LOCKFILE" "$TMP_ROOT/baseline.lock"

cat > "$TMP_ROOT/bin/cargo" <<'CARGO'
#!/bin/sh
set -eu
awk '
  /^\[\[package\]\]$/ { is_v8 = 0; wanted = 0 }
  /^name = "v8"$/ { is_v8 = 1 }
  is_v8 && /^version = "152\.2\.0"$/ { wanted = 1 }
  wanted && (/^source = / || /^checksum = /) { next }
  { print }
' "$TEST_LOCKFILE" > "$TEST_LOCKFILE.updated"
cat "$TEST_LOCKFILE.updated" > "$TEST_LOCKFILE"
rm -f "$TEST_LOCKFILE.updated"
cp "$TEST_LOCKFILE" "$TEST_MUTATED_LOCK"
echo "FAKE_CARGO_REACHED_HOST_BUILD" >&2
exit 71
CARGO
chmod 755 "$TMP_ROOT/bin/cargo"
awk '
  /^\[\[package\]\]$/ { is_v8 = 0; wanted = 0 }
  /^name = "v8"$/ { is_v8 = 1 }
  is_v8 && /^version = "152\.2\.0"$/ { wanted = 1 }
  wanted && (/^source = / || /^checksum = /) { next }
  { print }
' "$TMP_ROOT/baseline.lock" > "$TMP_ROOT/expected-mutation.lock"

# Execute the checked-in lock transaction verbatim with fixture path variables.
# Stop extraction before the actual cargo invocation's variable setup; the
# block depends only on these explicit paths and the fake cargo command.
awk '
  /^# The path override changes the lockfile source entry during resolution\./ { copy = 1 }
  copy { print }
' "$HELPER" > "$TMP_ROOT/lock-transaction.sh"
if [ ! -s "$TMP_ROOT/lock-transaction.sh" ]; then
  echo "error: production lock transaction block was not found" >&2
  exit 2
fi
if ! grep -q 'cargo --config "patch.crates-io.v8.path' "$TMP_ROOT/lock-transaction.sh"; then
  echo "error: production lock transaction extraction omitted the cargo invocation" >&2
  exit 2
fi
cat > "$TMP_ROOT/run-transaction.sh" <<'RUN'
#!/bin/sh
set -eu
REPO_ROOT="$TEST_REPO_ROOT"
CODEX_RS="$TEST_REPO_ROOT/codex-rs"
CARGO_MANIFEST="$CODEX_RS/Cargo.toml"
LOCKFILE="$CODEX_RS/Cargo.lock"
V8_COPY="$TEST_REPO_ROOT/target/freebsd-v8-source/v8-copy"
V8_VERSION=152.2.0
export TEST_LOCKFILE="$LOCKFILE"
export TEST_MUTATED_LOCK="$TEST_MUTATED_LOCK"
. "$TEST_LOCK_BLOCK"
RUN
chmod 755 "$TMP_ROOT/run-transaction.sh"

if TEST_REPO_ROOT="$TMP_ROOT/repo" TEST_LOCK_BLOCK="$TMP_ROOT/lock-transaction.sh" \
    TEST_MUTATED_LOCK="$TMP_ROOT/mutated.lock" \
    PATH="$TMP_ROOT/bin:$PATH" "$TMP_ROOT/run-transaction.sh" > "$TMP_ROOT/run.log" 2>&1; then
  echo "error: fake cargo failure unexpectedly succeeded" >&2
  exit 1
fi
cmp -s "$LOCKFILE" "$TMP_ROOT/baseline.lock" || {
  echo "error: failed source build did not restore exact Cargo.lock bytes" >&2
  diff -u "$TMP_ROOT/baseline.lock" "$LOCKFILE" >&2 || true
  exit 1
}
cmp -s "$TMP_ROOT/mutated.lock" "$TMP_ROOT/expected-mutation.lock" || {
  echo "error: fake cargo did not apply only the expected v8 lock change" >&2
  cat "$TMP_ROOT/run.log" >&2
  diff -u "$TMP_ROOT/expected-mutation.lock" "$TMP_ROOT/mutated.lock" >&2 || true
  exit 1
}
grep -q 'FAKE_CARGO_REACHED_HOST_BUILD' "$TMP_ROOT/run.log"
echo "PASS V8 lock transaction: expected v8 source/checksum rewrite was restored byte-for-byte after cargo failure"
