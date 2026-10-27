## Installing & building

### System requirements

| Requirement                 | Details                                                                      |
| --------------------------- | ---------------------------------------------------------------------------- |
| Operating systems           | macOS 12+, Ubuntu 20.04+/Debian 10+, FreeBSD 15+, or Windows 11 **via WSL2** |
| Git (optional, recommended) | 2.23+ for built-in PR helpers                                                |
| RAM                         | 4-GB minimum (8-GB recommended)                                              |

FreeBSD source builds target `x86_64-unknown-freebsd`. Install Rust/Cargo, a C compiler, `cmake`, `pkgconf`, Git, Python, and `protobuf` (`protoc`) from the host package manager. The workspace pins Rust 1.95.0, while the native FreeBSD build exercised here uses Rust/Cargo 1.98.1; the minimum Rust version for the V8 source build has not been established. Its helper passes the active native Rust compiler's sysroot to GN for V8's Rust glue instead of downloading Chromium's Linux Rust toolchain. The code-mode host also needs native FreeBSD `gn` (2345), `ninja` (1.13.2), and LLVM 22 (22.1.8, including `libclang`); the helper uses the system `protoc`, so set `PROTOC` when it is not on `PATH`.

These are the versions used for the verified build, not enforced package-version
constraints: the helper checks for the required native tools and LLVM paths but
does not reject other GN, Ninja, or Rust versions. Reproducible rebuilds should
use the same toolchain package set. An optimized native build can require tens
of gigabytes of memory.

```sh
git clone https://github.com/openai/codex.git
cd codex
scripts/freebsd/build-install.sh
"$HOME/.local/bin/codex-freebsd" --version
```

Launch the native FreeBSD CLI with an unrestricted sandbox mode:

```sh
"$HOME/.local/bin/codex-freebsd" --sandbox danger-full-access
```

FreeBSD has no sandbox backend in this release, so restricted modes fail closed;
use `danger-full-access` to run tools without filesystem or process isolation.
This command names `codex-freebsd` explicitly and does not change the existing
`codex` command or its Linux installation. No `--no-daemon` option is needed.

The build helper builds the CLI and its V8-backed code-mode host, verifies that
both outputs are native FreeBSD amd64 ELF binaries, and installs them as
`codex-freebsd` and `codex-code-mode-host-freebsd`. It marks only the V8 host
with the FreeBSD `wxneeded` ELF feature required by V8's JIT; it does not change
machine-wide sysctls. The distinct helper name lets this install share
`~/.local/bin` with a Linux `codex` without selecting a Linux companion. A
normally packaged executable named `codex` keeps the conventional
`codex-code-mode-host` resource name.

FreeBSD has no Codex sandbox backend in this release. Requests that require a
filesystem or process sandbox fail closed; do not map FreeBSD to the Linux
bubblewrap backend. The native CLI, PTY execution, file operations, MCP, and
TUI remain available subject to their host dependencies. There is no published
FreeBSD npm or DotSlash artifact; run the binary built above.

### DotSlash

The GitHub Release also contains a [DotSlash](https://dotslash-cli.com/) file for the Codex CLI named `codex`. Using a DotSlash file makes it possible to make a lightweight commit to source control to ensure all contributors use the same version of an executable, regardless of what platform they use for development.

### Build from source

```bash
# Clone the repository and navigate to the root of the Cargo workspace.
git clone https://github.com/openai/codex.git
cd codex/codex-rs

# Install the Rust toolchain, if necessary.
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
rustup component add rustfmt
rustup component add clippy
# Install helper tools used by the workspace justfile:
cargo install --locked just
# DotSlash fetches pinned development tools such as buildifier on first use.
cargo install --locked dotslash
# Install nextest for the `just test` helper.
cargo install --locked cargo-nextest

# Build Codex.
cargo build

# Launch the TUI with a sample prompt.
cargo run --bin codex -- "explain this codebase to me"

# After making changes, use the root justfile helpers (they default to codex-rs):
just fmt
just fix -p <crate-you-touched>

# Run the relevant tests (project-specific is fastest), for example:
just test -p codex-tui
# `just test` runs the test suite via nextest:
just test
# Avoid `--all-features` for routine local runs because it increases build
# time and `target/` disk usage by compiling additional feature combinations.
```

## Tracing / verbose logging

Codex is written in Rust, so it honors the `RUST_LOG` environment variable to configure its logging behavior.

The TUI records diagnostics in bounded local stores by default. Set `log_dir` explicitly to enable a plaintext TUI log for a run:

```bash
codex -c log_dir=./.codex-log
tail -F ./.codex-log/codex-tui.log
```

The non-interactive mode (`codex exec`) defaults to `RUST_LOG=error`, but messages are printed inline, so there is no need to monitor a separate file.

See the Rust documentation on [`RUST_LOG`](https://docs.rs/env_logger/latest/env_logger/#enabling-logging) for more information on the configuration options.
