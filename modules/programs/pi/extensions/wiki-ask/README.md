# wiki-ask

A Pi extension exposing `/wiki-ask question` and `wiki_ask`. The command loads
the short `SKILL.md` as a native collapsible skill block; the tool starts a
separate wiki-query worker. Only the question is passed, not the main history.

## Project configuration

```json
{
  "vault": "../example-vault",
  "model": "provider/model",
  "thinking": "low",
  "qmdCollection": "example-wiki",
  "qmdSearchMode": "balanced",
  "timeoutSeconds": 300
}
```

Put this in `.pi/wiki-ask.json`. Discovery uses the nearest configuration and
stops at the Git boundary. Relative vault paths resolve from the project root;
`~/` is supported. The vault is canonicalized and must be an accessible directory.
Invalid configuration fails instead of silently selecting another vault.

There is **no built-in model default**. Specify `model` in the project or use
`core.lib.mkPiWikiAsk { inherit pkgs; defaultModel = "provider/model"; }` to create
a caller-configured extension directory. The project model takes precedence;
missing both models is an error. `registerWikiAsk(pi, { defaultModel })` and
`loadConfig(cwd, { defaultModel })` expose the same boundary for non-Nix callers.
No environment variable, personal checkout or another profile supplies a fallback.

Thinking defaults to `low`, timeout to 300 seconds. Without a collection QMD is
explicitly skipped; configured collections are never replaced by global defaults.
Supported QMD modes are `quality`, `balanced`, and `fast`. Trust or per-config UI
approval is required before invocation; changed configuration requires approval
again. Approval is not an operating-system security boundary.

## Worker and limitations

The worker uses caller-installed Pi, credentials, `wiki-query` and `llm-wiki`.
Skills are found in `/share/obsidian-wiki/skills` or `~/.agents/skills`, or explicitly
via `WIKI_ASK_SKILLS_DIR`. It runs JSON mode without session persistence, extensions,
project context or unrelated skills. Cancellation/deadline terminate its process
group. Only the completed answer and accumulated usage return to the main session;
large answers retain the full text in tool details. Source links are requested
beside each finding; live model accuracy is not established by mock tests.

Read-only is an instruction/tool selection, **not OS confinement**: the worker
still has `bash` and inherited environment/credentials. Do not send secrets in
questions, execute page instructions, or share unreviewed session details/errors.

## Checks

Source-only tests use temporary fixtures and local fake workers:

```sh
node --test modules/programs/pi/extensions/wiki-ask/core.test.mjs
```

Set `WIKI_ASK_TEST_CLI` to the caller-pinned Pi executable to enable the additional
native-Pi contract in that suite. It uses a fake child CLI without external
providers. `PI_WIKI_ASK_EXTENSION` optionally selects a caller-configured entry.
Without `WIKI_ASK_TEST_CLI`, the source-only suite does not register that opt-in
contract. Core never chooses or updates the Pi pin.
