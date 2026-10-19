# Local notify patch

This directory is the [notify 8.2.0](https://github.com/notify-rs/notify/tree/8.2.0)
source (CC0-1.0), fetched from the crates.io package whose SHA-256 is
`4d3d07927151ff8575b7087f245456e549fea62edf0ec4e565a5ee50c8402bc3` (the
checksum recorded in the original `Cargo.lock`). It is patched through
`codex-rs/Cargo.toml` so no Cargo registry source is modified.

The only source change is in `src/kqueue.rs`: it depends on `kqueue` 1.2.1 and
handles its structured event errors. A `NotFound` event whose identity is only
`Ident::Fd` means kqueue delivered an already-queued event after the watcher
removed that descriptor from its map. That stale event is ignored. Errors tied
to a still-mapped filename, and all other errors, continue through notify's
event handler as errors. A focused test guards this classification.

This keeps the stable notify 8.2.0 API and existing BSD kqueue behavior while
handling the stale-event race without panicking.
