# AlphaStock Harness

`agent_runtime.harness` is the one runtime kernel for agent execution.  It
keeps research and investment as business Profiles, rather than creating a
separate Harness per role.

| Concern | Module | Responsibility |
| --- | --- | --- |
| runtime kernel | `run.py` | opens a profile-bound run and exposes its handle |
| append-only state | `state.py` | event log, checkpoints and logical rollback |
| session persistence | `store.py` | PostgreSQL snapshot when configured, atomic local fallback otherwise |
| recovery | `recovery.py` | checkpoint, resume, rollback and terminal status |
| tool boundary | `tools.py` | capability check, retry/circuit policy, evidence reference and checkpoint |
| sandbox | `sandbox.py` | profile allowlist and fail-closed operation policy |
| evidence | `evidence.py` | compact references to existing tool-result artifacts |
| profiles | `profiles.py` | Research and Investment tool manifests |

## Data compatibility

No business or evaluation data is migrated, rewritten or deleted.  When
`POSTGRES_DSN` is configured, the runtime reuses the existing `checkpoints`
table with a `harness:<run_id>` namespace.  It never reads or overwrites
ordinary LangGraph checkpoint rows.  If the database is unavailable, one
failure opens a per-process breaker and the run continues with an atomic file
snapshot under `runtime/harness/`; it can be resumed after restart.  `runtime/`
is already local, disposable state and is gitignored.

## Sandbox contract

This is an application sandbox, not a claim of operating-system isolation.
All execution is profile-bound and every tool is compared with the registered
manifest before invocation.  Raw commands, file writes/deletes, publishing and
trading are immutable denials in all modes.

Authenticated runs snapshot the user's mode from the existing
`agent_approval_modes` record at start; this preserves the expiry and explicit
confirmation rules already used by the approval UI. `ALPHASTOCK_SANDBOX_MODE`
is the safe default for jobs without an actor (cron, CLI, tests):

- `safe`: automatic use of registered, read-only profile tools only.
- `assist`: the same safe boundary, with normal user-granted capabilities.
- `full_access`: bypasses only unmatched *application* policy after the
  immutable denials; it does not create a shell, file-write, publish or trade
  capability that the product does not expose.

Networked market tools remain named profile tools and can be disabled for an
incident using `ALPHASTOCK_SANDBOX_NETWORK=deny`.

## Compatibility entry points

`agent_runtime.agents.research_harness.run_research_harness` and
`agent_runtime.agents.investment_harness.run_investment_agent_loop` remain the
existing public entry points.  They now delegate normal tool/skill calls to
this kernel, preserving their old traces, output gate and API contracts while
adding a `harness` summary to their internal results.
