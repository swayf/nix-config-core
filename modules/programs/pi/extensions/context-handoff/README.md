# Project context handoff (Pi 1.1.0+)

`handoff_context` replaces model context at a completed, documented checkpoint
and continues the **same authorized task**. Saved history is retained. It does
not write project files, change model/effort, commit, run checks, start a new
session, or call a summarization model.

## Explicit opt-in

Create a data-only `.pi/context-handoff.json` in an intended project:

```json
{
  "version": 1,
  "enabled": true,
  "readFirst": ["AGENTS.md", "docs/state/index.md"],
  "stateRoots": ["docs/state/"]
}
```

The named Markdown files/directories must exist. These four fields alone are
accepted. Both path arrays contain 1–8 distinct entries; paths are project-relative,
Markdown reading paths end in `.md`, and state directories end in `/`. Absolute
paths, traversal, `.git` segments, escaping symlinks, aliased config/state roots
and a root-wide allowance are refused. Config is bounded to 64 KiB and each
Markdown file to 1 MiB. Requested and canonical document paths must be allowed.
Discovery stops at the nearest config or Git boundary. Missing, malformed or
disabled config never inherits a parent opt-in.

Only TUI/RPC sessions with `ctx.isProjectTrusted() === true` expose the tool.
Use Pi's trust workflow or intentional one-invocation `--approve`; the extension
never grants trust. JSON/print workers remain off. Trust is not OS confinement
or authorization for another task.

## Checkpoint and safe boundary

Before calling, persist current progress, actual checks, limitations, constraints
and next action in the project's designated state documents. Supply `task`, `goal`,
`completed`, `next`, `constraints`, `checks`, `readFirst`, `stateFiles`,
`statePersisted: true` and `nextAuthorized: true`. At least one state file must be
under a configured state root. Call this tool alone after other tools finish.

At `turn_end` the extension rechecks trust, config identity, document hashes,
sole-tool success, cancellation and queued input. Another extension's boundary
proposal also cancels the handoff. Failure preserves old context and clears the
pending reset; success appends one retain-none compaction with continuation.
The next model request receives the handoff, not old exploration or tool outputs.
The original transcript remains stored. Re-read the named documents before acting.

Hashes verify point-in-time identity, **not completeness or permission**.
Progress/checks are agent-reported; the two boolean attestations are not proofs.
Repository instructions retain ownership of checkpoint policy and state. A handoff
cannot bypass a decision, finish a plan, authorize retries or override concurrent
work. Pi's ordinary auto-compaction is unchanged.

Active user requests are retained verbatim. Attachments, requests over 12000
characters and handoffs over 24000 characters are refused, not truncated. Already
lost constraints cannot be reconstructed. Branch-local history limits automatic
handoffs to twelve per user request and rejects duplicate `(task, next)` pairs.
Only a new user request renews the limit.

## Controls and delivery

```text
/handoff-context status
/handoff-context off
/handoff-context on
```

Off persists on the active branch; on cannot bypass trust or project opt-in.
Commands themselves never reset context. Config/document changes cancel pending
handoffs. Do not combine with another automatic context replacement extension.

Pass `core.lib.piExtensions.contextHandoff` to the existing nullable HM resource
or explicit `core.lib.piInstallers.contextHandoff`. Delivery remains code-profile
only and retains installer ownership/local-override rules. These instructions do
not authorize activation, container restarts or removal of local overrides.

Source-only `context-handoff.test.mjs` and caller-pinned
`test-context-handoff-pi.py` use synthetic projects and a loopback fake API. They
cover trust/opt-in, saved-history retention, resets/limits, branch controls and
cancellation. They do not prove real-model checkpoint selection or completeness
of retained requirements.
