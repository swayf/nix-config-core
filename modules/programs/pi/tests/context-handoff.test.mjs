import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, symlinkSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { findProjectRoot, readProjectPolicy, registerContextHandoff } from "../extensions/context-handoff/core.mjs";

const TOOL = "handoff_context";
const args = () => ({ task: "TASK1", goal: "Complete runtime ownership", completed: "Checkpoint one checked; plan remains open.",
  next: "Read the plan; reproduce CASE1 before fixing it.", constraints: ["Do not restart the shared container; no commit requested."],
  checks: ["Fixture tests passed; CASE1 is not reproduced yet."],
  readFirst: ["0_aidocs/plans/task.md"], stateFiles: ["0_aidocs/roadmap/active.md", "0_aidocs/plans/task.md"],
  statePersisted: true, nextAuthorized: true });

const policy = () => ({ version: 1, enabled: true,
  readFirst: ["AGENTS.md", "0_aidocs/roadmap/index.md"], stateRoots: ["0_aidocs/"] });

function fixture(t, { mode = "tui", factory = true, trusted = true } = {}) {
  const root = mkdtempSync(path.join(os.tmpdir(), "pi-handoff-test."));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const dir of ["0_aidocs/roadmap", "0_aidocs/plans", "src/nested", ".git", ".pi"]) mkdirSync(path.join(root, dir), { recursive: true });
  writeFileSync(path.join(root, "AGENTS.md"), "# Arbitrary project — instructions\n");
  const config = path.join(root, ".pi/context-handoff.json");
  const configure = value => writeFileSync(config, JSON.stringify(value));
  if (factory) configure(policy());
  for (const name of ["0_aidocs/roadmap/index.md", "0_aidocs/roadmap/active.md", "0_aidocs/plans/task.md"]) writeFileSync(path.join(root, name), "State: open\n");
  const handlers = new Map(), tools = new Map(), commands = new Map(), notices = [];
  let active = ["read", "bash"];
  const branch = [{ type: "message", id: "user", message: { role: "user", content: "Continue TASK1. Do not commit." } }];
  const pi = {
    on: (name, fn) => handlers.set(name, fn),
    registerTool: tool => { tools.set(tool.name, tool); active.push(tool.name); },
    registerCommand: (name, command) => commands.set(name, command),
    getActiveTools: () => active,
    setActiveTools: names => { active = names; },
    appendEntry: (customType, data) => branch.push({ type: "custom", customType, data }),
  };
  const ctx = { cwd: root, mode, isProjectTrusted: () => trusted, signal: new AbortController().signal, hasPendingMessages: () => false,
    ui: { notify: (...notice) => notices.push(notice) }, sessionManager: { getBranch: () => branch } };
  const buildSessionContext = entries => {
    const cut = entries.findLastIndex(entry => entry.type === "compaction");
    return { messages: entries.slice(cut + 1).filter(entry => entry.type === "message").map(entry => entry.message) };
  };
  registerContextHandoff(pi, { parameters: {}, buildSessionContext });
  const restore = () => handlers.get("session_start")({}, ctx);
  restore();
  const prepare = (packet = args(), id = "handoff-1") => tools.get(TOOL).execute(id, packet, ctx.signal, undefined, ctx);
  const boundary = (overrides = {}) => handlers.get("turn_end")({ outcome: "completed", entries: [],
    context: { pendingMessages: [] }, message: { role: "assistant", content: [{ type: "toolCall", id: "handoff-1", name: TOOL }] },
    toolResults: [{ toolCallId: "handoff-1", isError: false }], ...overrides }, ctx);
  return { root, pi, ctx, branch, handlers, tools, commands, notices, restore, prepare, boundary, config, configure };
}

test("explicit policy follows nested directories but stops at a Git boundary", t => {
  const f = fixture(t);
  assert.equal(findProjectRoot(path.join(f.root, "src/nested")), f.root);
  mkdirSync(path.join(f.root, "src/nested/.git"));
  assert.equal(findProjectRoot(path.join(f.root, "src/nested")), undefined);
});

test("handoff is one retain-none compaction with verbatim requests, hashes and continuation", async t => {
  const f = fixture(t);
  await f.prepare();
  const result = f.boundary();
  assert.equal(result.continue, true);
  assert.equal(result.entries.length, 1);
  const entry = result.entries[0];
  assert.equal(entry.type, "compaction");
  assert.equal(entry.firstKeptEntryId, null);
  for (const text of ["TASK1", "CASE1", "Do not commit.", "agent-reported", "does not grant new permission", "0_aidocs/roadmap/index.md"]) {
    assert.match(entry.summary.toLowerCase(), new RegExp(text.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.equal(entry.details.documents.length, 4);
  assert.match(entry.details.documents[0].sha256, /^[a-f0-9]{64}$/);
  assert.equal(f.boundary(), undefined);
});

for (const mode of ["json", "print"]) test(`bounded ${mode} opted-in workers do not expose or execute handoff`, async t => {
  const f = fixture(t, { mode });
  assert.ok(!f.pi.getActiveTools().includes(TOOL));
  await assert.rejects(f.prepare(), /disabled/);
});

test("unrelated project stays untouched", async t => {
  const f = fixture(t, { factory: false });
  assert.deepEqual(f.pi.getActiveTools(), ["read", "bash"]);
  await assert.rejects(f.prepare(), /disabled/);
});

test("off is durable and branch-relative; on restores just this tool", async t => {
  const f = fixture(t);
  const command = f.commands.get("handoff-context").handler;
  await command("off", f.ctx);
  f.restore();
  await assert.rejects(f.prepare(), /disabled/);
  f.branch.pop();
  f.handlers.get("session_tree")({}, f.ctx);
  assert.ok(f.pi.getActiveTools().includes(TOOL));
  await command("on", f.ctx);
  assert.deepEqual(new Set(f.pi.getActiveTools()), new Set(["read", "bash", TOOL]));
});

for (const [label, mutate] of [
  ["queued user input", f => { f.ctx.hasPendingMessages = () => true; }],
  ["unpersisted state", (_f, a) => { a.statePersisted = false; }],
  ["missing authorisation", (_f, a) => { a.nextAuthorized = false; }],
  ["missing document", (_f, a) => { a.readFirst = ["0_aidocs/missing.md"]; }],
  ["unconfigured reading path", (_f, a) => { a.readFirst = ["outside-state/state.md"]; }],
  ["path traversal", (_f, a) => { a.readFirst = ["0_aidocs/../AGENTS.md"]; }],
  ["absolute path", (f, a) => { a.readFirst = [path.join(f.root, "AGENTS.md")]; }],
  ["no configured state document", (_f, a) => { a.stateFiles = ["AGENTS.md"]; }],
  ["attachments", f => { f.branch[0].message.content = [{ type: "image", data: "base64" }]; }],
  ["oversize original request", f => { f.branch[0].message.content = "x".repeat(12001); }],
]) test(`refuses ${label} without any reset`, async t => {
  const f = fixture(t), a = args(); mutate(f, a);
  await assert.rejects(f.prepare(a));
  assert.equal(f.boundary(), undefined);
});

test("document symlinks may not escape configured reading/state paths", async t => {
  const f = fixture(t);
  mkdirSync(path.join(f.root, "outside-state"));
  writeFileSync(path.join(f.root, "outside-state/file.md"), "Not agent input");
  symlinkSync(path.join(f.root, "outside-state/file.md"), path.join(f.root, "0_aidocs/alias.md"));
  await assert.rejects(f.prepare({ ...args(), readFirst: ["0_aidocs/alias.md"] }), /escapes/);
});

for (const [label, modify] of [
  ["abort", (f, e) => { e.outcome = "aborted"; }],
  ["parallel sibling tool", (_f, e) => { e.message.content.push({ type: "toolCall", id: "sibling", name: "write" }); }],
  ["failed tool result", (_f, e) => { e.toolResults[0].isError = true; }],
  ["new user input", f => { f.ctx.hasPendingMessages = () => true; }],
  ["other boundary extension", (_f, e) => { e.entries.push({ type: "custom", customType: "other" }); }],
  ["concurrent document edit", f => { writeFileSync(path.join(f.root, "0_aidocs/roadmap/active.md"), "Concurrent update\n"); }],
  ["configuration edit", f => { f.configure({ ...policy(), enabled: false }); }],
  ["configuration deletion", f => { rmSync(f.config); }],
  ["trust revoked", f => { f.ctx.isProjectTrusted = () => false; }],
  ...[".git/notes.md", "data.json"].map(name => [`state retargeted to ${name}`, f => {
    const target = path.join(f.root, "0_aidocs", name);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, "Forbidden canonical state\n");
    const state = path.join(f.root, "0_aidocs/roadmap/active.md");
    rmSync(state); symlinkSync(target, state);
  }]),
]) test(`safe boundary cancels on ${label} and cannot reset a later turn`, async t => {
  const f = fixture(t);
  await f.prepare();
  const event = { outcome: "completed", entries: [], context: { pendingMessages: [] },
    message: { role: "assistant", content: [{ type: "toolCall", id: "handoff-1", name: TOOL }] },
    toolResults: [{ toolCallId: "handoff-1", isError: false }] };
  modify(f, event);
  const result = f.boundary(event);
  assert.equal(result.continue, undefined);
  assert.equal(result.entries[0].type, "custom_message");
  assert.match(result.entries[0].content, /cancelled.*Old context is intact/s);
  assert.equal(f.boundary(), undefined);
});

test("twelve resets survive reload, thirteenth refuses, duplicate guard and new-user renewal remain", async t => {
  const f = fixture(t);
  await f.prepare();
  f.branch.push(f.boundary().entries[0]);
  f.restore();
  await assert.rejects(f.prepare(), /already handed off/);
  assert.equal(f.boundary(), undefined);
  for (let n = 2; n <= 12; n++) {
    await f.prepare({ ...args(), next: `Continue step ${n}` });
    const result = f.boundary();
    assert.equal(result.continue, true);
    f.branch.push(result.entries[0]);
    f.restore(); // Reload reconstructs the counter from the active branch.
  }
  assert.equal(f.branch.filter(entry => entry.type === "compaction").length, 12);
  await assert.rejects(f.prepare({ ...args(), next: "Continue step 13" }), /limit/);
  assert.equal(f.boundary(), undefined);
  assert.equal(f.branch.filter(entry => entry.type === "compaction").length, 12);
  f.branch.push({ type: "message", id: "user2", message: { role: "user", content: "Continue step 13, still no commit." } });
  await f.prepare({ ...args(), next: "Continue step 13" });
  const result = f.boundary();
  assert.equal(result.continue, true);
  assert.deepEqual(result.entries[0].details.originalRequests, ["Continue TASK1. Do not commit.", "Continue step 13, still no commit."]);
});

test("status and model-facing description both advertise the twelve-reset limit", async t => {
  const f = fixture(t);
  await f.commands.get("handoff-context").handler("status", f.ctx);
  assert.match(f.notices.at(-1)[0], /maximum 12 resets per user request/);
  assert.match(f.tools.get(TOOL).description, /At most 12 resets per user request/);
});

for (const [label, change] of [
  ["missing", f => { rmSync(f.config); }],
  ["disabled", f => { f.configure({ ...policy(), enabled: false }); }],
  ["malformed JSON", f => { writeFileSync(f.config, "{"); }],
  ["unknown version", f => { f.configure({ ...policy(), version: 2 }); }],
  ["unknown field", f => { f.configure({ ...policy(), command: "run me" }); }],
  ["string enabled", f => { f.configure({ ...policy(), enabled: "true" }); }],
  ["empty readFirst", f => { f.configure({ ...policy(), readFirst: [] }); }],
  ["missing roots", f => { f.configure({ version: 1, enabled: true, readFirst: ["AGENTS.md"] }); }],
  ["root traversal", f => { f.configure({ ...policy(), stateRoots: ["../"] }); }],
  ["root-wide allowance", f => { f.configure({ ...policy(), stateRoots: ["./"] }); }],
  ["absolute readFirst", f => { f.configure({ ...policy(), readFirst: [path.join(f.root, "AGENTS.md")] }); }],
  ["oversize config", f => { writeFileSync(f.config, " ".repeat(65537)); }],
  ["trust denied", f => { f.ctx.isProjectTrusted = () => false; }],
  ["trust API absent", f => { delete f.ctx.isProjectTrusted; }],
]) test(`${label} policy stays off and /on cannot bypass it`, async t => {
  const f = fixture(t); change(f); f.restore();
  assert.ok(!f.pi.getActiveTools().includes(TOOL));
  await assert.rejects(f.prepare());
  await assert.rejects(f.commands.get("handoff-context").handler("on", f.ctx));
  assert.equal(f.boundary(), undefined);
});

test("disabled or malformed nearest project never inherits parent policy", t => {
  const f = fixture(t), nested = path.join(f.root, "src/nested");
  mkdirSync(path.join(nested, ".pi"));
  const config = path.join(nested, ".pi/context-handoff.json");
  writeFileSync(config, JSON.stringify({ ...policy(), enabled: false }));
  assert.equal(findProjectRoot(nested), nested);
  assert.throws(() => readProjectPolicy(nested));
  writeFileSync(config, "{");
  assert.throws(() => readProjectPolicy(nested));
});

test("arbitrary project heading, instruction filename and state tree work", async t => {
  const f = fixture(t);
  mkdirSync(path.join(f.root, "docs/state"), { recursive: true });
  writeFileSync(path.join(f.root, "INSTRUCTIONS.md"), "# Project rules\nReply in English\n");
  writeFileSync(path.join(f.root, "docs/state/task.md"), "Durable task state\n");
  f.configure({ ...policy(), readFirst: ["INSTRUCTIONS.md"], stateRoots: ["docs/state/"] });
  f.restore();
  await f.prepare({ ...args(), readFirst: ["docs/state/task.md"], stateFiles: ["docs/state/task.md"] });
  const summary = f.boundary().entries[0].summary;
  assert.match(summary, /INSTRUCTIONS.md/);
  assert.match(summary, /docs\/state\/task.md/);
  for (const fixed of ["FOREIGN_RULESET", "foreign-project", "Report in Russian", "0_aidocs/"]) assert.ok(!summary.includes(fixed));
});

test("configuration changes between restore and execute require refresh", async t => {
  const f = fixture(t);
  writeFileSync(f.config, JSON.stringify(policy(), null, 2));
  await assert.rejects(f.prepare(), /configuration changed/);
  assert.equal(f.boundary(), undefined);
  f.restore();
  await f.prepare();
  assert.equal(f.boundary().continue, true);
});

for (const kind of ["config", "state-root"]) test(`rejects escaping ${kind} symlink`, t => {
  const f = fixture(t), outside = mkdtempSync(path.join(os.tmpdir(), "handoff-foreign."));
  t.after(() => rmSync(outside, { recursive: true, force: true }));
  if (kind === "config") {
    writeFileSync(path.join(outside, "policy.json"), JSON.stringify(policy()));
    rmSync(f.config); symlinkSync(path.join(outside, "policy.json"), f.config);
  } else {
    symlinkSync(outside, path.join(f.root, "foreign-state"));
    f.configure({ ...policy(), stateRoots: ["foreign-state/"] });
  }
  assert.throws(() => readProjectPolicy(f.root), /escapes/);
  f.restore(); assert.ok(!f.pi.getActiveTools().includes(TOOL));
});

for (const name of [".git/notes.md", "data.json"]) test(`canonical ${name} is refused in stateFiles and configured readFirst`, async t => {
  const f = fixture(t), target = path.join(f.root, "0_aidocs", name);
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, "Forbidden canonical document\n");
  symlinkSync(target, path.join(f.root, "0_aidocs/alias.md"));
  await assert.rejects(f.prepare({ ...args(), stateFiles: ["0_aidocs/alias.md"] }), /Markdown path/);
  assert.equal(f.boundary(), undefined);
  f.configure({ ...policy(), readFirst: ["0_aidocs/alias.md"] });
  assert.throws(() => readProjectPolicy(f.root), /Markdown path/);
  f.restore(); assert.ok(!f.pi.getActiveTools().includes(TOOL));
});

test("a state alias to a readFirst-only file is not a durable state file", async t => {
  const f = fixture(t);
  symlinkSync(path.join(f.root, "AGENTS.md"), path.join(f.root, "0_aidocs/not-state.md"));
  await assert.rejects(f.prepare({ ...args(), stateFiles: ["0_aidocs/not-state.md"] }), /stateFiles/);
  assert.equal(f.boundary(), undefined);
});

test("oversize and non-file state cannot prepare a handoff", async t => {
  const f = fixture(t), state = path.join(f.root, "0_aidocs/big.md");
  writeFileSync(state, "x".repeat(1024 * 1024 + 1));
  await assert.rejects(f.prepare({ ...args(), stateFiles: ["0_aidocs/big.md"] }), /bounded/);
  rmSync(state); mkdirSync(state);
  await assert.rejects(f.prepare({ ...args(), stateFiles: ["0_aidocs/big.md"] }), /regular/);
});

test("reset metadata does not write any repository documents", async t => {
  const f = fixture(t);
  const before = readFileSync(path.join(f.root, "0_aidocs/roadmap/active.md"));
  await f.prepare(); f.boundary();
  assert.deepEqual(readFileSync(path.join(f.root, "0_aidocs/roadmap/active.md")), before);
});
