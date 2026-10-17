# FreeBSD Codex tool test matrix

This matrix records runtime evidence for the FreeBSD `codex-freebsd` binary and
its configured MCP servers. A source test, Linux binary, model response without
a tool event, or generic hello does not count as a runtime pass. Record the
native binary path, version, model, command, tool-call event, tool result, and
exit status for each test. Redact credentials and private response data.

Use the supported model explicitly (`gpt-5.6-sol` or another model confirmed
available to the account). The inherited model `gpt-6.1-sol` is not accepted by
the native service and fails with HTTP 400 before any tool executes.

## Latest native-pair evidence (2026-10-16)

The latest installed pair is
`/home/user/.local/bin/codex-freebsd` (SHA-256
`b9743a5866103e6a6aab345c9fe819cece5f2f7d5c2652d8194f1dc292891cc8`)
and its sibling `/home/user/.local/bin/codex-code-mode-host-freebsd`
(SHA-256 `b597ca4355045a6b7fed78714266db21c637a69c865ab5ab42004574220deadf`).
Both are FreeBSD 15.1 amd64 ELFs; the helper carries the `wxneeded` feature
note. The Linux default `~/.local/bin/codex` remains SHA-256
`399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215`; it was
not replaced. The pair was rebuilt from the zip 9.0.1 security-fix source
commit `abe2be90b`; later deny-policy, Bazel lock, and Windows patch metadata
commits did not change the Rust source used for these binaries. Older
candidate hashes remain in prior run logs.

On this installed pair, the native Code Mode app-server suite passed 14/14
cases, including host startup, nested tool catalog, image decode,
resources/templates/resource read, goals, agent schema/lifecycle, `apply_patch`,
workspace-write fail-closed, all 51 Jerboa methods in 60 calls, and all five
OpenAI Developer Docs calls. The request-user-input roundtrip and exec-cell
wait case also passed. Logs are under
`~/work/codex-runtime-repro/installed-final-suite-abe2be90/`.

The Jerboa case made 60 calls and checked exact raw MCP item identity,
arguments, success/error status, result data, returned values, and fixture
postconditions. Three deliberate validation probes returned normal
`isError=true` MCP results. These app-server cases used the configured,
versioned patched provider described below. Earlier staged-pair runs remain in
historical logs.

The final live TUI PTY run on the latest installed pair reached Code Mode and
executed its scratch-file command successfully. The corrected runner waits for the typed marker to
appear, pauses 250 ms, then sends Enter separately. It accepted the actual
Rust `CommandExecution` rollout item and verified the completed event, marker,
and exact scratch-file contents. The run used the installed CLI/helper hashes
above and supported model `gpt-5.6-sol`. The CLI includes the
fail-closed sandbox guard; the explicit restricted-sandbox and JSONL builtin
gates are recorded in their own rows below.

## Installed suffix-pair verification

After the latest installation, `/home/user/.local/bin/codex-freebsd` was
verified as FreeBSD 15.1 amd64 ELF, SHA-256
`b9743a5866103e6a6aab345c9fe819cece5f2f7d5c2652d8194f1dc292891cc8`; its
sibling `/home/user/.local/bin/codex-code-mode-host-freebsd` has SHA-256
`b597ca4355045a6b7fed78714266db21c637a69c865ab5ab42004574220deadf` and
`wxneeded` set. The Linux `~/.local/bin/codex` remains SHA-256
`399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215`.

The installed CLI passed the app-server catalog, image decode, resource and
template listing, resource read, goal lifecycle, agent schemas/lifecycle,
`apply_patch`, and workspace-write fail-closed cases. The strict Jerboa replay
passed all 60 calls across 51 unique tools against the patched scratch provider;
all five OpenAI Developer Docs tools also passed through native Code Mode.
Logs are retained under
`~/work/codex-runtime-repro/installed-final-suite/`. Direct host IPC and the
interactive TUI were separately confirmed against these same installed hashes
by the runtime owner.

The patched Jerboa provider is installed side-by-side at
`~/.local/lib/jerboa-mcp-freebsd/69c6992b531ac213c7e0332708832c2cd50ae2e1/`
(source commit `69c6992b531ac213c7e0332708832c2cd50ae2e1`, version 0.13.23,
staged `jmcp` SHA-256
`9d2602136b0ce71cd1a34bb6193ce0f86e36f8d034019f904a55443751580faf`, matching
`bin/jerboa` SHA-256
`0357c5cda6e141671c29794630b0528d85227d9c412721eb04fec9dd61f1aee8` and
`lib/libjerboa_native.so` SHA-256
`24cda139d89d9ce2f4681663ef52cb184caa10d9d9fa658a14cc7cb46ccf2633`). The
package includes its matching runtime, Chez files, libraries, and licenses;
its symlinks resolve inside the package. The active Jerboa server config points
to this package and its matching runtime; the prior `~/.local/bin/jerboa` and
`jmcp` remain unchanged, and the existing knowledge-data/repository settings
were preserved. The installed native Codex scratch replay passed the 60-call /
51-method suite against this exact package. The final installed pair also
passed a configured production-root read-only `jerboa_list_features` smoke:
one correlated completed call returned nonempty content, with only that tool
enabled and no `features_path` override. The production data tree remained
unchanged at 12 files (SHA-256
`2e31c058bc0cafb52d23545b3bbc37071e7c6e6bbe13e624baf2588c5b6ed07c`). The
test used the exact versioned provider package and an isolated Codex home/cache;
its sanitized evidence is
`~/work/codex-runtime-repro/installed-final-suite-abe2be90/jerboa-configured-read-final.jsonl`.
Earlier diagnostic harness attempts that emitted no MCP call are retained as
non-passing historical attempts; the final deterministic case verifies the
actual app-server item and result. The
formatter now accepts the legacy list and current string forms of
`implemented_in` and returns a typed tool error for unsupported values. The
scoped config backup is
`~/.codex/config.toml.backup-jerboa-mcp-20261017T014929Z` (mode 0600); only the
Jerboa command and its matching `JERBOA_HOME` / `JERBOA_MCP_JERBOA_PATH` values
changed.

The standalone `request_user_input` app-server test also passed against the
latest installed CLI. It used an isolated Plan-mode thread and a local
Responses stub:
the app-server emitted the exact blocking question and options, the harness
returned the simulated `Pass` answer, and the follow-up Responses request
contained one `function_call_output` correlated to the exact tool call with the
parsed answer payload. The matching `turn/completed` notification had status
`completed`. No live model request or human prompt was used. The runner's
`--self-test` rejects missing/wrong call IDs, a prompt-only option-label match,
wrong answers, altered questions, and failed/interrupted turn status. The
sanitized result is retained at
`~/work/codex-runtime-repro/installed-final-suite-abe2be90/request-user-input.log`.

## Runtime and package checks

| Surface                           | Evidence required for pass                                                                                                                                                              | Current evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Native executable identity        | `file` identifies FreeBSD ELF; `--version` exits 0                                                                                                                                      | Pass after latest installation: `/home/user/.local/bin/codex-freebsd`, FreeBSD 15.1 x86_64 ELF; SHA-256 `b9743a5866103e6a6aab345c9fe819cece5f2f7d5c2652d8194f1dc292891cc8`; `--version` succeeded                                                                                                                                                                                                                                                                                                                                                                        |
| Side-by-side install              | Inspect both command paths; run the harness with its explicit native path                                                                                                               | `~/.local/bin/codex` remains the separate SYSV static-pie Linux binary at SHA-256 `399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215`; `~/.local/bin/codex-freebsd` and its suffixed helper are installed FreeBSD ELFs.                                                                                                                                                                                                                                                                                                                                   |
| Native test PATH helpers          | Resolve each external command and inspect its ELF format                                                                                                                                | The inherited PATH resolves `codex` to the preserved Linux symlink and `rg` first to the app-server daemon's Linux static-pie binary; `/usr/local/bin/rg`, `git`, `python3`, `bash`, `cargo`, `file`, and `timeout` are FreeBSD binaries. `jmcp` resolves to the native FreeBSD Jerboa executable. The native app-server harness now prepends a private per-run directory linking `codex` to its explicit FreeBSD test binary and `rg` to `/usr/local/bin/rg`, then searches FreeBSD system directories and `~/.local/bin`; no global PATH or Linux install was changed. |
| Per-binary W^X metadata           | Inspect native CLI and matching helper with `elfctl` and ELF program headers; run JIT-enabled V8 evaluation and bounded hot-function stress                                             | Latest CLI has `wxneeded` unset and `GNU_STACK` is RW; latest helper has `wxneeded` set. Native app-server host startup/evaluation passed on the latest pair. The one-million-call hot-function stress/checksum passed on the preceding pair (`39d6558…` / `78a0011…`). These checks verify V8 evaluation in its JIT-enabled configuration, not optimized machine-code generation or behavior with global `kern.elf64.allow_wx=0`; the observed global setting was `1` and was not modified.                                                                             |
| MCP configuration discovery       | `codex-freebsd mcp list` exits 0 and reports configured/enabled state                                                                                                                   | Pass: `jerboa`, `jerbsearch`, `kagi`, `llm_search`, and `openaiDeveloperDocs` enabled; `gerbil` disabled. `mcp list` reports authentication metadata as `Unsupported`; the configured Kagi wrapper nevertheless completed the real read-only search recorded below.                                                                                                                                                                                                                                                                                                      |
| App-server catalog surface        | Initialize app-server and start an ephemeral thread; inspect returned fields for actual tool schemas                                                                                    | Native app-server initialize returned `userAgent`, `codexHome`, `platformFamily`, and `platformOs`; `thread/start` returned thread/model/provider/workspace/approval config, but no tool names or schemas. The local Responses recorder captured the later model request schema; no external model was used                                                                                                                                                                                                                                                              |
| Helper executable lookup          | Installed and relocated binary selects the correct adjacent or package-resource helper; helper executes and completes a code-mode cell                                                  | The installed final pair resolves its suffixed sibling and passed direct IPC and app-server tests. Earlier missing-companion checks used a separate staged copy                                                                                                                                                                                                                                                                                                                                                                                                          |
| Model/API reachability            | One native model request completes using an explicitly supported model                                                                                                                  | Pass on installed CLI `b9743a58…91cc8`: supported `gpt-5.6-sol` reached the service and live TUI PTY verified a completed command event plus exact scratch file                                                                                                                                                                                                                                                                                                                                                                                                          |
| Default sandbox behavior          | Invoke `exec` without `--sandbox`; report either a completed harmless command or a direct fail-closed sandbox error, never infer the outcome from prose                                 | Final exec-JSONL smoke reported `LIMITATION`: the native router logger recorded `filesystem sandbox cannot be enforced on this host`, the command did not execute, and the marker was absent. The runner does not count the assistant message as successful execution                                                                                                                                                                                                                                                                                                    |
| Explicit workspace-write sandbox  | Attempt a unique scratch marker write with `--sandbox workspace-write`; require direct `filesystem sandbox cannot be enforced` failure evidence and no completed command item or marker | Final exec-JSONL smoke reported the same `LIMITATION`, with the router logger recording the exact rejection, no command execution, and no marker. A deterministic app-server nested `exec_command` probe also returned the correlated error and confirmed marker absence                                                                                                                                                                                                                                                                                                 |
| Native model-facing outer tools   | Capture the actual request `tools` schema from a native model turn                                                                                                                      | Pass for the isolated local-provider fixture: `exec`, `wait`, and `request_user_input` were present in the request; this is not an account-wide catalog                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Code Mode nested built-in catalog | Run `ALL_TOOLS` inside actual native Code Mode and capture the result returned to the next model request                                                                                | Pass on installed pair: exact 15 nested names observed and asserted. Targeted native calls covered image decode, resources/templates/read, goals, agent schema/lifecycle, `apply_patch`, shell/PTY, `write_stdin`, waits, parallel dispatch, and error propagation. Coverage is aggregated across targeted runs, not one uninterrupted 15-case model turn. The outer request schema advertises `exec` as the JS entry point                                                                                                                                              |

The nested-catalog fixture requires these exact 15 names to be present in
`ALL_TOOLS`: `apply_patch`, `create_goal`, `exec_command`, `get_goal`,
`list_mcp_resource_templates`, `list_mcp_resources`, `read_mcp_resource`,
`update_goal`, `view_image`, `write_stdin`, and the five `multi_agent_v1__*`
operations (`close_agent`, `resume_agent`, `send_input`, `spawn_agent`, and
`wait_agent`). This is an asserted expectation,
The helper-backed catalog passed against both the staged candidate and the
installed final pair. The harness prints the observed names only after the
native Code Mode result reaches the local model stub.

The command record from the first native MCP attempt is:

```sh
codex-freebsd exec --json --ephemeral --sandbox read-only \
  -m gpt-5.6-sol -C /home/user/work/codex-freebsd-port \
  'Call configured Jerboa MCP read-only tool jerboa_list_features with query "freebsd codex" and limit 3. Do not use shell, edit files, or call any write tool.'
```

It failed before MCP dispatch with `failed to spawn code-mode host
/home/user/.local/bin/codex-code-mode-host: No such file or directory`. This is
a failed live test of the complete request path, not evidence about the
Jerboa server's own behavior.

## Tool-level runtime matrix

Run each row as an individual native request. A pass requires the matching
tool-call event and a result that demonstrates the requested operation. A
successful assistant response without that tool-call event is not a pass.

The standalone live-manifest rows below were run against the pre-security
installed candidate pair (`39d6558…` / `78a0011…`). The latest `abe2be90`
installed pair has the separately recorded 14/14 app-server suite, direct
host startup/evaluation, yielded exec-cell wait, request-user-input roundtrip,
and live TUI PTY results above. Do not treat the earlier manifest results as
reruns on the newer hashes.

| Tool or tool family                 | Safe probe                                                                                                      | Pass evidence                                                                       | Status                                                                                                                                                                                                                                                                                                                                                              |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Code-mode `exec` (freeform)         | Run a bounded `printf` command through `codex exec`                                                             | Completed `command_execution` item with stdout marker and exit 0                    | Pass on pre-security candidate CLI: exact `EXEC_OK` result in targeted smoke                                                                                                                                                                                                                                                                                        |
| Code-mode `wait`                    | Run `sleep 2; printf WAIT_CELL_OK`, then wait on the yielded cell if needed                                     | Completed command item with final marker and exit 0                                 | Pass on pre-security candidate CLI: yielded exec cell completed and emitted `WAIT_CELL_OK`; latest pair separately passed exec-cell wait                                                                                                                                                                                                                            |
| Shell PTY / `write_stdin`           | Start a shell that prints a ready marker and reads one input line; send `hello` into the same process           | Completed command item has both output markers and captured interactive input       | Pass on pre-security candidate CLI: same PTY process emitted `PTY_READY`, received `hello` through `write_stdin`, and completed successfully; latest pair separately passed the top-level interactive TUI PTY                                                                                                                                                       |
| `apply_patch`                       | Add one marker file under a disposable workspace                                                                | Completed file-change item names exact added path; final bytes match exactly        | Pass on pre-security candidate pair: raw exec JSONL emitted exact `file_change` events and marker bytes. Latest pair also passed the deterministic app-server `apply_patch` case and verified exact file bytes under danger-full-access                                                                                                                             |
| Parallel tool dispatch              | Start two shell calls or local fixture MCP calls with reciprocal bounded barriers                               | Both completed items/results plus both markers prove overlap                        | Pass on pre-security candidate CLI: parallel shell barriers completed; fixture-MCP overlap also passed with measured `max_concurrent=2`                                                                                                                                                                                                                             |
| Error propagation                   | Execute a deliberately nonexistent command or fixture MCP error                                                 | Exact failed command/MCP tool item and error text                                   | Pass on pre-security candidate CLI for the nonexistent command and fixture-MCP error; expected failed tool results matched                                                                                                                                                                                                                                          |
| Jerboa MCP read calls               | Bounded feature/search/balance/error-fix read calls                                                             | Completed MCP events identify exact server/tool/arguments and contain a result      | Targeted real-model calls for `list_features`, `howto`, and `check_balance` passed with matching completed MCP events; the original balance prompt was a model non-call, but a forced enabled-tool read probe passed. `error-fix` lookup passed. The deterministic local-stub app-server replay passed all 51 Jerboa names in 60 calls against the patched provider |
| Other configured MCP servers        | Query `tools/list` before invoking any tool                                                                     | Server starts; tool schemas are captured; calls are separately classified           | OpenAI Developer Docs schema, five direct reads and five native Code Mode calls passed. One bounded real Kagi `kagi_search_fetch` call also passed through the installed wrapper and native Code Mode path; `jerbsearch` and `llm_search` remain unvalidated.                                                                                                       |
| Missing MCP provider diagnostic     | Start app-server with an isolated configured command path that does not exist, then call `mcpServerStatus/list` | Failed status contains exact startup/ENOENT diagnosis; no provider is launched      | Native test passed: `MCP startup failed: No such file or directory (os error 2)`; response does not include the configured command path                                                                                                                                                                                                                             |
| Disabled or unavailable MCP servers | Inspect configuration/list output only                                                                          | State is reported accurately; no call is misreported as a successful tool execution | `gerbil` is disabled; `jerbsearch` and `llm_search` remain unvalidated                                                                                                                                                                                                                                                                                              |

### Every configured MCP server

The native CLI's configured-server inventory is broader than the isolated
Jerboa suite above. `codex-freebsd mcp list` reported Jerboa, Jerbsearch, Kagi,
LLM Search, and OpenAI Developer Docs enabled; Gerbil is disabled. The CLI
reported authentication status as `Unsupported` for these entries. The remote
OpenAI Developer Docs configuration contains no bearer token or HTTP headers.

OpenAI Developer Docs was contacted directly at its configured streamable HTTP
endpoint using MCP protocol `2025-03-26`. Initialize succeeded with server
`openai-docs-mcp` version `1.0.0`; `tools/list` returned five tools, each
annotated `readOnlyHint: true` and `destructiveHint: false`:

| Tool                 | Required arguments                                      | Read-only probe result                                                                                           |
| -------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `search_openai_docs` | `query: string`; optional `limit: 1..50`, `cursor`      | Actual search for “Responses API” succeeded and returned documentation hits                                      |
| `list_openai_docs`   | none; optional `limit: 1..50`, `cursor`                 | Actual `limit: 3` browse succeeded and returned page URLs                                                        |
| `fetch_openai_doc`   | `url: string`; optional `anchor`                        | Actual fetch of the MCP guide succeeded and returned its Markdown (`# MCP servers`)                              |
| `list_api_endpoints` | none                                                    | Actual call succeeded and returned the endpoint catalog, including `/responses`                                  |
| `get_openapi_spec`   | `url: string`; optional `languages`, `codeExamplesOnly` | Actual spec fetch for `https://api.openai.com/v1/responses` succeeded and returned the Responses endpoint schema |

These were actual remote MCP calls, not only schema or source inspection. The
repeatable provider-level check is `python3
scripts/freebsd/test_openai_docs_mcp.py`; it verifies the exact five-method
schema, read-only annotations, and a content marker from every successful
call. The native Code Mode replay is prepared as
`python3 scripts/freebsd/test_code_mode_appserver.py --case openai-docs`:
it enables only those five read-only tools in an ephemeral private config,
uses a local model-response stub, makes each call once, and requires matching
raw app-server MCP events with exact tool names/arguments/results plus the
validated output in the next model request. The first native run against the
installed older CLI failed as expected because no MCP calls were emitted
(0 of 5 raw events); this rejected a model-only claim of success. A later run
against the staged candidate passed five raw Code Mode MCP calls; the installed
final pair also passed all five. The test
does not edit `~/.codex` or require an API key for the model stub; do not count
provider-level success as Codex dispatch success.

The initial inventory found `jerbsearch`, `kagi`, and `llm_search` unavailable:
safe startup attempts failed before MCP initialize because their configured
executables were absent. Since then, a Kagi stdio server and wrapper were
installed side-by-side, and the installed native CLI completed one actual
read-only Kagi search through Code Mode. The test used the exact query
`OpenAI Codex GitHub repository` with limit 3, asserted one correlated
completed non-error `kagi_search_fetch` MCP result with nonempty text and three
validated GitHub URLs, and verified that the result reached the follow-up model
request. A local Responses fixture controls only the outer model turn; the
Kagi request was real. No token contents or authorization headers were logged,
and no raw search result text was retained. Sanitized evidence, including
installed CLI/helper and Kagi wrapper/stdio hashes, is at
`/home/user/work/codex-runtime-repro/kagi-real-search/installed-native-kagi-search.json`.

`jerbsearch` has been staged separately but its configured invocation remains
unvalidated. The `llm_search` probe invoked the direct `llm-search mcp` command,
not its configured shell wrapper, to avoid starting its unrelated daemon; its
configured invocation is also unvalidated. `gerbil` is disabled and was not
started. An all-configured-server pass is therefore still pending for those
two enabled local providers. Earlier Kagi startup attempts that failed before
MCP initialization are historical and are superseded by the successful
correlated search above.

Read-only dependency/source inspection found that `jerbsearch` is configured
to execute a frontend name different from the
`~/mine/jerboa-websearch/bin/jerbsearch-mcp` launcher, and that launcher also
requires a Chez Scheme runtime that is not installed. `llm-search` has a source
checkout at `~/mine/jerboa-llm-search` but its native executable is absent.
These are environment/configuration findings only. Jerbsearch and Jerboa are
staged at side-by-side versioned paths; the default Linux Codex and existing
Jerboa binaries were not replaced.

For each row, append the timestamp, exact command or prompt, native tool name,
result summary, exit status, and artifact path. Do not include secret-bearing
environment dumps or raw MCP payloads that contain private data.

The manifest-driven runner is `scripts/freebsd/test_tools.py`; disposable probe
cases live in `scripts/freebsd/smoke-cases.json`. It runs the installed native
binary with `exec --json --ephemeral --enable code_mode_host` and the sandbox
selected for each case. It requires completed events with exact expected item
types, status, output markers, and (for MCP) server/tool names and result
payload. The patch case also checks the exact file contents in a temporary
workspace, which the runner removes after each case. A model answer or
host-startup error without the expected tool event is a failure. Use
`--dry-run` to review command construction. Run it on the native host with:

```sh
python3 scripts/freebsd/test_tools.py
```

The manifest covers the default sandbox outcome and explicit workspace-write
fail-closed check, explicit read-only shell execution, persistent PTY input, a yielded-cell wait, patch application,
parallel shell execution, command-error propagation, four bounded Jerboa
reads, and disposable local MCP write/read/error/parallel dispatch. Parallel
shell and fixture-MCP cases use bounded reciprocal barriers, so sequential execution
fails within a few seconds instead of hanging. The local fixture server confines
writes to each temporary case directory, rejects traversal, and is independently
checked with a protocol-level test. A separate native FreeBSD provider-level test
invokes Jerboa's six knowledge-writer APIs only against a disposable data root;
it does not write to the user's durable knowledge store.

The earlier missing-companion negative checks are retained as historical
evidence only. Against the pre-security candidate pair, both sandbox smoke cases
recorded the router's exact `filesystem sandbox cannot be enforced on this
host` diagnostic, confirmed that the requested command did not execute, and
verified that the marker was absent. The runner reports these outcomes as
`LIMITATION`, rather than treating fail-closed behavior as successful command
execution. The app-server workspace-write case independently invoked
`exec_command`, checked its correlated Code Mode error result, and confirmed
marker absence.

The 11-case exec-JSONL model-driven batch initially recorded 7/11 strict
passes. Targeted follow-ups then confirmed completed calls for
`jerboa_list_features` and `jerboa_howto`; a forced enabled-tool probe also
confirmed `jerboa_check_balance` with a matching completed MCP event. The
default and explicit workspace-write cases are separately classified as
`LIMITATION` because native FreeBSD fails closed before executing shell
commands. The targeted aggregate covers the built-in tool cases, while the
real-model invocation history records the original model non-call behavior.

## MCP inventory and execution policy

Keep one inventory row per exposed tool, with these
fields: server, tool name, read-only/write/unknown classification, invocation
surface (interactive, `exec`, or both), harmless probe, observed tool event,
result validation, and status.

Invoke every read-only tool using bounded inputs. Do not invoke tools that
publish or save durable knowledge, send messages, change accounts/configuration,
deploy, delete, or otherwise create external side effects as part of this
smoke-test. Mark them as exposed but intentionally unexecuted, and identify the
missing authorization or safe fixture. If a durable-knowledge writer fails
during an explicitly authorized test, record the incident in `~/mcp-issues.md`
before continuing, as required by the environment instructions.

### Jerboa tools

A native MCP `initialize` plus `tools/list` request succeeded without invoking
any tool. It identified Jerboa MCP 0.13.3 (protocol 0.2.5, build
0.13.21+mcp.0.2.5) and 51 exposed tools. The schema marked every tool
`readOnlyHint=false` and `idempotentHint=false`, including apparent lookup
tools. Treat the annotation as a reason to constrain approval per-tool, not as
proof that a lookup itself writes data. The full schema capture is stored in
the runtime repro directory; no raw schema or user data is copied here.

The exact exposed tool names are:

```text
jerboa_eval
jerboa_check_syntax
jerboa_compile_check
jerboa_verify
jerboa_module_exports
jerboa_module_exists
jerboa_function_signature
jerboa_apropos
jerboa_howto
jerboa_howto_get
jerboa_cookbook_task_bundle
jerboa_request_advisor
jerboa_verify_plan
jerboa_failure_advisor
jerboa_anti_pattern_lookup
jerboa_anti_pattern_add
jerboa_security_scan
jerboa_run_tests
jerboa_make
jerboa_test_baseline_diff
jerboa_release_evidence_status
jerboa_release_evidence_toolchain_staleness
jerboa_target_proof_marker_verifier
jerboa_release_evidence_privacy_audit
jerboa_cross_repo_release_evidence_matrix
jerboa_security_profile
jerboa_read_source
jerboa_source_form_spans
jerboa_repo_snapshot
jerboa_generated_sls_source_layout_audit
jerboa_fleet_security_scan
jerboa_check_balance
jerboa_balanced_replace
jerboa_balanced_insert
jerboa_howto_add
jerboa_error_fix_lookup
jerboa_error_fix_add
jerboa_explain_error
jerboa_invalid_context_definition_diagnoser
jerboa_repair_error
jerboa_suggest_feature
jerboa_list_features
jerboa_vote_feature
jerboa_task_workflow_advisor
jerboa_compact_tool_manifest
jerboa_script_scaffold_verify
jerboa_prompt_to_code
jerboa_security_pattern_add
jerboa_write_file
jerboa_repair_balance
jerboa
```

The initial `jerboa_list_features` call stopped at code-mode host startup.
After the helper was built, the strict `jerboa-all` replay passed all 51
methods in 60 calls on installed CLI `b9743a58…91cc8` and the final patched
provider `9d260213…1580faf`. Separately,
`scripts/freebsd/test_jerboa_scratch.py` passed against the native Jerboa server:
`initialize` and `tools/list` returned 51 tools using protocol 2025-03-26, and
six writer APIs completed actual write/readback checks under a newly created
private temporary data root. It verified `howto_add`/`howto_get`,
`anti_pattern_add`/`anti_pattern_lookup`, `error_fix_add`/`error_fix_lookup`,
`suggest_feature`/`list_features`, `vote_feature`/`list_features`, and
`security_pattern_add`; persisted files were confined to that scratch root.
The feature catalog was parsed in its native S-expression representation and
the test asserted the exact persisted vote count `1` for the generated ID.
These provider-level calls validate Jerboa storage behavior, not Codex dispatch.
The nonwriter provider fixture also passed all 45 unique remaining methods in
49 calls: 46 non-error results and three deliberate negative validation
results. Every method had at least one non-error result; valid positive fixtures cover recipe lookup,
complete release evidence, a ready release-matrix target, and successful
`echo-args` plus `count-args` scaffold output verification.
The local app-server harness has native dispatch cases for `jerboa-read`,
`jerboa-writers`, and `jerboa-all`. Against the staged pair identified at the
top of this document, `jerboa-all` passed all 60 deterministic calls across 51
unique method names (49 nonwriter calls and 11 writer/readback calls). It
asserted that case names exactly equal the live provider's `tools/list`, then
required one raw app-server `mcpToolCall` item per call with exact server,
method, arguments, status, and result payload. It also checked returned
`isError` values, expected result text, and scratch project/catalog
postconditions. A loopback completion stub served the prompt-to-code request;
no external model was used. The three intentional validation-error results
were the missing recipe, incomplete release evidence, and missing
release-matrix target probes. The complete replay was also run against the
fresh renamed native CLI without its companion and correctly exited nonzero
after observing zero raw Jerboa MCP call items.
The live schema capture is recorded one row per tool in
[`freebsd-jerboa-tool-inventory.md`](freebsd-jerboa-tool-inventory.md), including
schema evidence, provisional risk class, and call status. Tests use temporary
config overrides that enable only the specific tool and set its approval mode
explicitly; they do not change persistent `config.toml`. Other
code-execution, build/test, repository, and file-mutation methods were exercised
with isolated project/data fixtures in the 60-call replay. This verifies those
methods against the fixture provider and does not make an unrestricted target
safe. The server's uniform false `readOnlyHint` annotations mean a tool name
alone cannot establish safety.

### Other configured MCP servers

The configured-server inventory and actual OpenAI Developer Docs schemas/calls
are recorded above. Three enabled local server commands still fail before MCP
initialization because their configured executables are absent; Gerbil remains
disabled and was not started. `mcp list` status means configured/enabled, not
available. Do not invent tool rows for a server that failed startup.

## Fixture and regression evidence

`python3 scripts/freebsd/test_tools.py --self-test` passed JSONL parsing, exact
MCP identity/argument/result matching, exact patch path matching, and negative
assistant-prose-only/unexpected-tool cases. `python3 scripts/freebsd/test_fixture_mcp_server.py` passed the fixture's
initialize, tool and resource listing, resource read/write,
path-traversal rejection, malformed-input/error, and concurrent-dispatch
protocol checks. The exec-JSONL manifest covers shell/PTY/write_stdin, wait,
apply_patch, parallel execution, error handling, sandbox behavior, and the
live/fixture MCP paths. It
asserts only item types the exec JSONL adapter actually serializes. That adapter
drops app-server `ImageView` and `FunctionCallOutput` variants, so those probes
use `scripts/freebsd/test_code_mode_appserver.py`. It checks the raw `imageView`
notification and the actual `custom_tool_call_output` in the next Responses
request (custom Code Mode output is not an app-server ThreadItem). The app-
server cases exercise host startup with fixed code, exact `ALL_TOOLS` nested
catalog, image decode, resource and template listing, resource reads, isolated
goal lifecycle, Jerboa read and writer operations, the complete 51-tool
Jerboa manifest, all five OpenAI docs tools, missing-provider diagnostics, and
`request_user_input` with its simulated answer round trip.
Image requires an `imageView` item; MCP cases require exact raw call identity,
status, and result payload; the broad Jerboa case also checks every argument,
status, and fixture postcondition. Each case requires terminal turn status
`completed` and verifies the actual Code Mode result in the next request
received by the local model stub. A local self-test rejects failed, interrupted,
missing-status, wrong-ID, and wrong-method terminal notifications. No assistant
prose marker alone counts as evidence. The staged candidate passed the
catalog, image/resources/goals, agent, Jerboa and OpenAI Docs dispatch cases;
the installed final pair has now passed these app-server cases as well.
The app-server assertion self-test passed. Its `host-smoke` and image cases were run against the
freshly built renamed native CLI with no sibling host; both rejected the fixed
expected result. The next actual model request contained the exact
`custom_tool_call_output` error naming the missing
`codex-code-mode-host-freebsd`. No Code Mode result marker or image item was
accepted. Sanitized transcripts are at
`~/work/codex-runtime-repro/appserver-host-smoke-missing-helper.log` and
`~/work/codex-runtime-repro/appserver-image-missing-host.log`. A later positive run
against the staged complete native pair passed; the earlier `tool-catalog` case
was also run against the same renamed CLI without its helper and failed with
the exact expected `codex-code-mode-host-freebsd` spawn error; it emitted no
catalog result.

`scripts/freebsd/test_code_mode_host_runtime.py` is a separate
fixture-free IPC test for the host binary. It negotiates protocol v1, opens a
session, evaluates fixed JavaScript inside the host's V8 runtime, checks the
exact returned text, and shuts down the session. Its optional JIT-enabled
stress probe calls a deterministic hot function one million times, checks the
exact result, and records `proccontrol`'s process W^X status. A separate
process-scoped W^X-denial mode records the actual completion or failure without
changing a system-wide sysctl; neither mode claims optimized machine code from
the loop alone. The final helper passed direct protocol evaluation and a
bounded one-million-iteration hot-function/checksum probe. Process-scoped W^X
status was recorded separately, without changing a global sysctl.

The earlier pre-guard native release CLI was copied into an isolated directory as
`/home/user/work/codex-runtime-repro/cases/fresh-native-missing-host/codex-freebsd`
with no sibling helper. It is FreeBSD amd64 ELF, SHA-256
`97b8a9a2a6f0890b0229f3fcff58b4f2f1ef33f438faaa3f9f33fad2d17ed09b`; its
`--version` command succeeded. The
local-only Responses recorder in
`scripts/freebsd/capture-native-tool-schema.py` then captured the actual
outgoing schema without contacting an external model or saving prompts or
credentials. The outgoing request exposed three outer functions:
`exec`, `wait`, and `request_user_input`. The recorder observed the expected
fail-closed diagnostic naming the missing sibling
`codex-code-mode-host-freebsd` and explaining how to install it. This validates
the renamed binary's resolution and the outer model-request schema only; it
does not validate tool dispatch or the code-mode host's runtime API catalog.
The latest installed pair and its successful app-server dispatch tests are
recorded at the top of this matrix.

Before final installation, the installed native `~/.local/bin/codex-freebsd`
was the older artifact with SHA-256
`05b345f185c1f7b81aac3253fa3fb2cc0f94af6eb68e5efd5a1be6c521acb8fd`; the
staged CLI at `codex-rs/target/release/codex` had SHA-256
`97b8a9a2a6f0890b0229f3fcff58b4f2f1ef33f438faaa3f9f33fad2d17ed09b`. The matching native host build target is `codex-rs/target/release/codex-code-mode-host`
with SHA-256 `78a00118cec9406d4a8bc0452389726678ba54c476bc396b47b029cd76a4a247`.
The Linux default `~/.local/bin/codex` remains the
separate static-pie Linux binary (SHA-256
`399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215`).

The outer `request_user_input` function was tested separately through the
native app-server with `scripts/freebsd/test_request_user_input_appserver.py`.
The harness used a local Responses stub and a temporary Codex home, started a
Plan thread, received the app-server's `item/tool/requestUserInput` request,
answered it with a simulated fixture selection, and verified that the next
model request contained that answer before the turn completed. No external
model or human input was involved. This validates the outer interactive
protocol path, not nested code-mode functions.

The separate top-level terminal path has a dedicated real-PTY runner at
`scripts/freebsd/test_interactive_pty.py`. It waits for the interactive
composer, submits a unique command prompt through the PTY, and requires both
the exact scratch-file contents and a persisted completed
`command_execution` item carrying the unique command/output marker. It uses a
temporary `CODEX_HOME` containing only a mode-0600 copy of `auth.json`, checks
the live process image with native `procstat`, reports the tested CLI/helper
hashes, and bounds cleanup to its own process group and temporary directory.
FreeBSD's default TUI path embeds the app-server (`tui/src/daemon_startup.rs`);
the runner uses that default without a `--no-daemon` override and checks for
unexpected isolated daemon pid/socket files after exit. Its evidence-parser
self-test passed. The final live TUI request used `gpt-5.6-sol`, created the
exact scratch file, and emitted a completed `CommandExecution` item with its
unique command/output marker. It passed against the latest installed CLI/helper
hashes at the top of this document.

## Regression coverage

`scripts/freebsd/test-build-install.sh` passed on native FreeBSD. It was
launched with the static native `/rescue/sh`; while the fixture was running,
`procstat -b` reported OSREL `1501000` for the test shell (`/rescue/sh`), the
installer (`/bin/sh`), and `lockf` (`/usr/bin/lockf`). In a private temporary
prefix it used real FreeBSD amd64 ELF inputs and mocked only the build
commands. Until a real helper artifact is available, the helper input is
a separate copy of the native CLI used only to exercise installer format and
feature-note checks. The success case verified both suffixed artifacts, the host's
`wxneeded` bit, CLI version output, and an unchanged `codex` sentinel. The
separate-target case verified that the fake CLI build wrote under its explicit
target directory and the helper build wrote under its own explicit target
directory. The test passed both unified and separate target layouts. The
failure cases injected errors during staged helper publication, helper backup
after the CLI backup, and the installed CLI version smoke check after both new
files were moved. Each rollback restored both prior files and preserved the
Linux-name sentinel. Preflight rejection of a missing helper and a Linux ELF
helper also preserved the old pair. A SIGTERM injected during helper
publication exited with status 143 and restored the old pair. Two concurrent
same-prefix installer fixtures serialized and left the expected CLI/helper
pair. This checks publication and rollback with real FreeBSD ELF inputs
without writes to `~/.local/bin`. The fake helper is
only an installer-format/feature-note fixture; it is not a V8 helper and does
not prove native V8 startup, JIT, or `wxneeded` runtime behavior.

`scripts/freebsd/test-v8-lock-transaction.sh` passed its failure-injection
fixture. It sources the checked-in Cargo.lock transaction block, lets fake
Cargo make only the expected V8 source/checksum mutation, then fail; the script
verified the original lockfile bytes were restored exactly.

The source suite already contains install-context tests for conventional helper
names, package resources, legacy siblings, standalone layouts, and missing
helpers. Native FreeBSD `cargo test -p codex-install-context --lib
code_mode_host` passed 6/6 tests. Native FreeBSD `cargo test -p
codex-install-context --lib named_executable` passed 4/4 tests, including
`codex-freebsd` resolving `codex-code-mode-host-freebsd` from package resources
and from a sibling, while conventional `codex` continues resolving
`codex-code-mode-host`. These tests verify resolver behavior; the final
candidate pair's relocation, helper startup, and live dispatch are covered by
the runtime checks above.

The final scoped Rust regressions also passed on native FreeBSD: all 74
`codex-code-mode` library tests and all 20 `codex-install-context` library
tests. The focused sandbox-guard regression passed 1/1. Existing host-protocol
stdio and gRPC tests remain unit/integration evidence, not a substitute for
invoking the final FreeBSD CLI pair; the direct IPC and app-server results above
cover that runtime path.
