import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { registerHooks } from "node:module";

// Unit tests need no Pi installation; mirror its agent-directory lookup.
const hostStub = `
  import { homedir } from "node:os";
  import { join } from "node:path";
  export const getAgentDir = () => process.env.PI_CODING_AGENT_DIR || join(homedir(), ".pi", "agent");
`;
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@earendil-works/pi-coding-agent") return {
      url: `data:text/javascript,${encodeURIComponent(hostStub)}`, shortCircuit: true,
    };
    return next(specifier, context);
  },
});
const { default: extension } = await import("../extensions/model-presets.ts");
hooks.deregister();

function harness(t, options = {}) {
  const home = mkdtempSync(join(tmpdir(), "pi-model-presets-"));
  const oldHome = process.env.HOME;
  const oldAgentDir = process.env.PI_CODING_AGENT_DIR;
  process.env.HOME = home;
  delete process.env.PI_CODING_AGENT_DIR;
  t.after(() => {
    if (oldHome === undefined) delete process.env.HOME;
    else process.env.HOME = oldHome;
    if (oldAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = oldAgentDir;
    rmSync(home, { recursive: true, force: true });
  });
  const agentDir = join(home, ".pi/agent");
  mkdirSync(agentDir, { recursive: true });
  const preset = { name: "Быстро", provider: "test", model: "model", thinkingLevel: "low" };
  const save = value => writeFileSync(join(agentDir, "model-presets.json"), JSON.stringify(value));
  save({ presets: [preset, { ...preset, name: "Вдумчиво", thinkingLevel: "high" }] });
  const models = [{ provider: "test", id: "model" }];
  const commands = {}, shortcuts = {}, events = {};
  const changes = [], notices = [], selections = [];
  let thinking = "high", input, disposed = 0;
  const ctx = {
    mode: options.mode ?? "tui", isIdle: () => true,
    model: models[0], modelRegistry: { getAvailable: () => models },
    ui: {
      notify: (...args) => notices.push(args),
      async select(title, rows) {
        selections.push({ title, rows });
        return options.cancel ? undefined : rows[options.index ?? 0];
      },
      onTerminalInput(handler) { input = handler; return () => { disposed++; }; },
    },
  };
  extension({
    registerCommand: (name, definition) => { commands[name] = definition; },
    registerShortcut: (key, definition) => { shortcuts[key] = definition; },
    on: (name, handler) => { events[name] = handler; },
    getThinkingLevel: () => thinking,
    async setModel(model) { changes.push(["model", model]); return options.auth !== false; },
    setThinkingLevel(level) { thinking = options.clamp ?? level; changes.push(["thinking", level]); },
  });
  return { home, agentDir, preset, save, ctx, models, commands, shortcuts, changes, notices, selections, events,
    input: () => input, disposed: () => disposed,
    open: () => commands["model-presets"].handler("", ctx) };
}

test("Ctrl+M opens one menu and applies model then effort; marks the current pair", async t => {
  const h = harness(t);
  await h.shortcuts["ctrl+m"].handler(h.ctx);
  assert.equal(h.selections.length, 1);
  assert.match(h.selections[0].rows[0], /Быстро.*test\/model · low$/);
  assert.match(h.selections[0].rows[1], /^✓ Вдумчиво.*high$/);
  assert.deepEqual(h.changes, [["model", h.models[0]], ["thinking", "low"]]);
});

test("Escape changes nothing", async t => {
  const h = harness(t, { cancel: true });
  await h.open();
  assert.deepEqual(h.changes, []);
});

test("unavailable models are omitted and empty scope warns", async t => {
  const h = harness(t);
  h.save({ presets: [{ ...h.preset, provider: "unavailable" }, { ...h.preset, name: "Available" }] });
  await h.open();
  assert.equal(h.selections[0].rows.length, 1);
  h.models.length = 0;
  h.changes.length = 0;
  await h.open();
  assert.deepEqual(h.changes, []);
  assert.match(h.notices.at(-1)[0], /Нет доступных/);
});

test("authentication failure must not apply effort", async t => {
  const h = harness(t, { auth: false });
  await h.open();
  assert.deepEqual(h.changes, [["model", h.models[0]]]);
  assert.equal(h.notices.at(-1)[1], "error");
});

test("reports the actual supported effort after clamping", async t => {
  const h = harness(t, { clamp: "off" });
  await h.open();
  assert.match(h.notices.at(-1)[0], /off.*ограничен/);
  assert.equal(h.notices.at(-1)[1], "warning");
});

test("list is reread on opening, including order and a second model", async t => {
  const h = harness(t);
  await h.open();
  h.models.push({ provider: "test2", id: "another" });
  h.save({ presets: [{ ...h.preset, name: "Changed", provider: "test2", model: "another", thinkingLevel: "medium" }] });
  await h.open();
  assert.match(h.selections.at(-1).rows[0], /Changed.*test2\/another · medium$/);
  assert.deepEqual(h.changes.slice(-2), [["model", h.models[1]], ["thinking", "medium"]]);
});

test("each profile reads only its own list, including custom agent directories", async t => {
  const h = harness(t);
  const code = join(h.home, ".pi/profiles/code");
  const custom = join(h.home, "custom agent directory");
  for (const [directory, name] of [[code, "Code only"], [custom, "Custom only"]]) {
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, "model-presets.json"), JSON.stringify({ presets: [{ ...h.preset, name }] }));
    process.env.PI_CODING_AGENT_DIR = directory;
    await h.open();
    assert.equal(h.selections.at(-1).rows.length, 1);
    assert.ok(h.selections.at(-1).rows[0].includes(name));
  }
  delete process.env.PI_CODING_AGENT_DIR;
  await h.open();
  assert.equal(h.selections.at(-1).rows.length, 2);
  assert.ok(h.selections.at(-1).rows[0].includes("Быстро"));
});

test("a missing profile list never falls back to another profile or the old shared file", async t => {
  const h = harness(t);
  writeFileSync(join(h.home, ".pi/model-presets.json"), JSON.stringify({ presets: [h.preset] }));
  const missing = join(h.home, ".pi/profiles/missing");
  mkdirSync(missing, { recursive: true });
  process.env.PI_CODING_AGENT_DIR = missing;
  await h.open();
  assert.deepEqual(h.changes, []);
  assert.deepEqual(h.selections, []);
  assert.equal(h.notices.at(-1)[1], "error");
  assert.ok(h.notices.at(-1)[0].includes(join(missing, "model-presets.json")));
});

test("invalid config or duplicate names fail without changing the session", async t => {
  const h = harness(t);
  for (const config of [[], {}, { presets: [null] }, { presets: [{ ...h.preset, thinkingLevel: "huge" }] },
    { presets: [h.preset, h.preset] }, { presets: [{ ...h.preset, name: "\x1b[31m" }] }]) {
    h.save(config);
    await h.open();
    assert.deepEqual(h.changes, []);
    assert.equal(h.notices.at(-1)[1], "error");
  }
  h.save({ presets: [h.preset] });
  await h.open(); // a failed menu releases the lock
  assert.equal(h.changes.length, 2);
});

test("does not change models during a run, including one started while selecting", async t => {
  const h = harness(t);
  h.ctx.isIdle = () => false;
  await h.open();
  assert.equal(h.selections.length, 0);
  h.ctx.isIdle = () => true;
  h.ctx.ui.select = async (_title, rows) => { h.ctx.isIdle = () => false; return rows[0]; };
  await h.open();
  assert.deepEqual(h.changes, []);
});

test("simultaneous openings cannot stack dialogs", async t => {
  const h = harness(t);
  let release;
  h.ctx.ui.select = async () => new Promise(resolve => { release = resolve; });
  const first = h.open();
  while (!release) await new Promise(resolve => setImmediate(resolve));
  await h.open();
  release(undefined);
  await first;
  assert.deepEqual(h.changes, []);
});

test("legacy Enter is made explicit; Ctrl+M, paste and other keys stay unchanged; cleanup works", t => {
  const h = harness(t);
  h.events.session_start({}, h.ctx);
  assert.deepEqual(h.input()("\r"), { data: "\x1b[13u" });
  for (const key of ["\x1b[109;5u", "\x1b[27;5;109~", "\x1b[13u", "\n", "\x1b[200~text\r\x1b[201~"])
    assert.equal(h.input()(key), undefined);
  h.events.session_start({}, h.ctx);
  assert.equal(h.disposed(), 1);
  h.events.session_shutdown();
  h.events.session_shutdown();
  assert.equal(h.disposed(), 2);
});

test("noninteractive modes neither open menus nor listen to terminal input", async t => {
  const h = harness(t, { mode: "rpc" });
  h.events.session_start({}, h.ctx);
  await h.open();
  assert.equal(h.input(), undefined);
  assert.deepEqual(h.changes, []);
  assert.deepEqual(h.selections, []);
});
