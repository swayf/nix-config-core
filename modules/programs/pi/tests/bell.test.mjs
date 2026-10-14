import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { test } from "node:test";

const tuiStub = `
  export const visibleWidth = s => Array.from(s).length;
  export const truncateToWidth = (s, width) => Array.from(s).slice(0, Math.max(0, width)).join("");
`;
const netStub = `
  import { EventEmitter } from "node:events";
  export const requests = [];
  export let error;
  export function setError(value) { error = value; }
  export function createConnection() {
    const socket = new EventEmitter();
    socket.destroy = () => {};
    socket.setTimeout = () => socket;
    socket.write = text => {
      requests.push(JSON.parse(text));
      queueMicrotask(() => socket.emit("data", Buffer.from(JSON.stringify(
        error ? { ok: false, error } : { ok: true }
      ) + "\\n")));
    };
    queueMicrotask(() => socket.emit("connect"));
    return socket;
  }
`;
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@earendil-works/pi-tui") return {
      url: process.env.PI_TUI_MODULE ?? `data:text/javascript,${encodeURIComponent(tuiStub)}`,
      shortCircuit: true,
    };
    if (specifier === "node:net") return {
      url: `data:text/javascript,${encodeURIComponent(netStub)}`, shortCircuit: true,
    };
    return next(specifier, context);
  },
});
const { default: extension } = await import("../extensions/bell.ts");
const { visibleWidth } = await import("@earendil-works/pi-tui");
const net = await import("node:net");
hooks.deregister();

function harness(mode = "tui") {
  net.requests.length = 0;
  net.setError(undefined);
  const handlers = {};
  const commands = {};
  const shortcuts = {};
  const events = new Map();
  let footer;
  let renders = 0;
  let unsubscribed = 0;
  const notifications = [];
  const statuses = new Map([["codex-limits", "7d 73% ↻5d4h"]]);
  const entries = [{ type: "message", message: { role: "assistant", usage: {
    input: 1200, output: 12, cacheRead: 10, cacheWrite: 0, cost: { total: 0.123 },
  } } }];
  const ctx = {
    mode,
    model: { id: "test-model", reasoning: true, contextWindow: 100000 },
    sessionManager: {
      getSessionId: () => "session-1", getLeafId: () => String(entries.length),
      getEntries: () => entries, getCwd: () => "/work/project", getSessionName: () => "task",
    },
    getContextUsage: () => ({ percent: 12.5, contextWindow: 100000 }),
    ui: {
      notify: (...args) => notifications.push(args),
      setFooter(factory) {
        footer?.dispose();
        footer = factory({ requestRender: () => { renders++; } }, { fg: (_color, s) => s }, {
          getGitBranch: () => "main",
          getExtensionStatuses: () => statuses,
          onBranchChange: () => () => { unsubscribed++; },
        });
      },
    },
  };
  extension({
    on: (name, fn) => { handlers[name] = fn; },
    events: { on(name, fn) { events.set(name, fn); return () => events.delete(name); } },
    registerCommand: (name, spec) => { commands[name] = spec; },
    registerShortcut: (key, spec) => { shortcuts[key] = spec; },
    getThinkingLevel: () => "high",
  });
  handlers.session_start({}, ctx);
  return { handlers, commands, shortcuts, ctx, events, entries, notifications,
    sounds: net.requests,
    settle: () => handlers.agent_settled({}, ctx),
    soundCommand: args => commands.sound.handler(args, ctx),
    statuses, footer: () => footer, renders: () => renders, unsubscribed: () => unsubscribed,
    status: (width = 240) => footer.render(width)[0],
  };
}

const ready = { event: "ready" };

test("starts disabled; plays custom ready on every final settlement until disabled", async () => {
  const h = harness();
  assert.match(h.status(), /^🔔 OFF │ project │ test-model · high │ ctx 13%\/100k │ 7d 73% ↻5d4h \(/);
  await h.settle();
  assert.deepEqual(h.sounds, []);
  await h.soundCommand("");
  assert.match(h.status(), /^🔔 ON │ project │ test-model · high │ ctx 13%\/100k │ 7d 73% ↻5d4h \(/);
  assert.equal(h.handlers.agent_end, undefined);
  h.handlers.agent_start({}, h.ctx);
  await h.settle();
  await h.settle();
  assert.deepEqual(h.sounds, [ready, ready]);
  assert.match(h.status(), /^🔔 ON /);
  await h.soundCommand("");
  await h.settle();
  assert.deepEqual(h.sounds, [ready, ready]);
});

test("/sound on and off are idempotent; /bell remains an alias", async () => {
  const h = harness();
  assert.equal(h.commands.bell, h.commands.sound);
  for (const action of ["on", " on "]) await h.soundCommand(action);
  await h.settle();
  assert.deepEqual(h.sounds, [ready]);
  for (const action of ["off", " off "]) await h.commands.bell.handler(action, h.ctx);
  await h.settle();
  assert.deepEqual(h.sounds, [ready]);
  assert.match(h.status(), /^🔔 OFF /);
});

test("test plays ready or a named event without changing the enabled state", async () => {
  const h = harness();
  await h.soundCommand("test");
  await h.soundCommand("test error");
  assert.deepEqual(h.sounds, [ready, { event: "error" }]);
  assert.match(h.status(), /^🔔 OFF /);
  await h.soundCommand("on");
  await h.soundCommand(" test attention ");
  await h.settle();
  assert.deepEqual(h.sounds, [ready, { event: "error" }, { event: "attention" }, ready]);
  assert.match(h.status(), /^🔔 ON /);
});

test("invalid arguments cannot send paths, options or commands", async () => {
  const h = harness();
  for (const action of ["unknown", "test /etc/passwd", "test --help", "test ready extra", "test ;sh"]) {
    await h.soundCommand(action);
  }
  assert.equal(h.notifications.length, 5);
  assert.ok(h.notifications.every(([text, level]) => text.startsWith("Usage: /sound") && level === "warning"));
  assert.match(h.status(), /^🔔 OFF /);
  assert.deepEqual(h.sounds, []);
});

test("missing sound/broker warns without BEL fallback; automatic warnings are deduplicated", async () => {
  const h = harness();
  net.setError("sound 'ready' is not configured on the host");
  await h.soundCommand("on");
  await h.settle();
  await h.settle();
  assert.equal(h.notifications.length, 1);
  assert.match(h.notifications[0][0], /not configured/);
  await h.soundCommand("test");
  assert.equal(h.notifications.length, 2);
  net.setError(undefined);
  await h.settle();
  net.setError("sound 'ready' is not configured on the host");
  await h.settle();
  assert.equal(h.notifications.length, 3);
});

test("other extensions can emit named sounds; off and shutdown suppress them", async () => {
  const h = harness();
  h.events.get("sound:play")({ event: "attention" });
  assert.deepEqual(h.sounds, []);
  await h.soundCommand("on");
  h.events.get("sound:play")({ event: "attention" });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(h.sounds, [{ event: "attention" }]);
  h.handlers.session_shutdown({}, h.ctx);
  assert.equal(h.events.size, 0);
});

test("shortcut toggles during a run without playing a sound", async () => {
  const h = harness();
  h.handlers.agent_start({}, h.ctx);
  await h.shortcuts["ctrl+alt+b"].handler(h.ctx);
  assert.match(h.status(), /^🔔 ON /);
  await h.shortcuts["ctrl+alt+b"].handler(h.ctx);
  await h.settle();
  assert.deepEqual(h.sounds, []);
  assert.ok(h.renders() >= 2);
});

test("fullscreen click toggles once without stealing focus or neighbouring text", async () => {
  const h = harness();
  h.status();
  const mouse = (type, x = 1, y = 0, button = "left") => h.footer().handleMouse({ type, button, x, y });
  assert.equal(mouse("press").handled, true);
  assert.match(h.status(), /^🔔 OFF /);
  assert.equal(mouse("release"), undefined);
  const click = mouse("click");
  assert.equal(click.handled, true);
  assert.equal(click.focus, undefined);
  assert.match(h.status(), /^🔔 ON /);
  const buttonWidth = visibleWidth("🔔 ON");
  for (const event of [
    ["click", buttonWidth], ["click", 30], ["click", -1], ["click", 1, 1],
    ["click", 1, 0, "right"], ["wheel"], ["drag"],
  ]) assert.equal(mouse(...event), undefined);
  await h.settle();
  assert.deepEqual(h.sounds, [ready]);
  mouse("click", buttonWidth - 1);
  assert.match(h.status(), /^🔔 OFF /);
  h.status(visibleWidth("🔔 OFF") - 1);
  assert.equal(mouse("click"), undefined);
});

test("one-line footer preserves stats and puts secondary statuses in parentheses", () => {
  const h = harness();
  h.statuses.set("web", "web ready");
  h.statuses.set("codex-limits", "5h 16% ↻10m │ 7d 8% ↻20h50m · устарело");
  h.ctx.model.name = "Opus 5.5";
  h.ctx.getContextUsage = () => ({ percent: 6.2, contextWindow: 1000000 });
  h.ctx.sessionManager.getCwd = () => "/work/home-manager";
  const lines = h.footer().render(300);
  assert.equal(lines.length, 1);
  assert.equal(lines[0], "🔔 OFF │ home-manager │ Opus 5.5 · high │ ctx 6%/1M │ 5h 16% ↻10m │ 7d 8% ↻20h50m (/work/home-manager · git main · session task · ↑1.2k ↓12 · R10 W0 · $0.123 · устарело · web ready)");
  for (const width of [0, 1, 4, 10, 20, 40, 80, 120, 300]) {
    assert.ok(h.footer().render(width).every(line => visibleWidth(line) <= width));
  }
  h.entries.push({ type: "usage", usage: {
    input: 500, output: 8, cacheRead: 0, cacheWrite: 0, cost: { total: 0.1 },
  } });
  assert.match(h.status(300), /↑1.7k ↓20.*\$0.223/);
});

test("missing context/model and unavailable limits are not invented", () => {
  const h = harness();
  h.ctx.model = undefined;
  h.ctx.getContextUsage = () => undefined;
  h.statuses.set("codex-limits", "Codex: лимиты загружаются…");
  assert.match(h.status(), /^🔔 OFF │ project │ no-model │ ctx \?%\/0 \(/);
  assert.match(h.status(), /Codex: лимиты загружаются…\)$/);
  h.ctx.model = { id: "fallback", reasoning: false, contextWindow: 200000 };
  h.statuses.delete("codex-limits");
  assert.match(h.status(), /│ fallback │ ctx \?%\/200k \(/);
});

test("session replacement and shutdown disarm and dispose subscriptions", async () => {
  const h = harness();
  await h.soundCommand("on");
  h.handlers.session_start({ reason: "resume" }, h.ctx);
  assert.equal(h.unsubscribed(), 1);
  assert.equal(h.events.size, 1);
  assert.match(h.status(), /^🔔 OFF /);
  await h.settle();
  assert.deepEqual(h.sounds, []);
  await h.soundCommand("on");
  h.handlers.session_shutdown({}, h.ctx);
  assert.deepEqual(h.footer().render(120), []);
  await h.settle();
  assert.deepEqual(h.sounds, []);
});

test("RPC, JSON and print modes neither render nor request playback", async () => {
  for (const mode of ["rpc", "json", "print"]) {
    const h = harness(mode);
    for (const action of ["", "on", "off", "test", "test error"]) await h.soundCommand(action);
    await h.shortcuts["ctrl+alt+b"].handler(h.ctx);
    assert.equal(h.footer(), undefined);
    assert.equal(h.events.size, 0);
    await h.settle();
    assert.deepEqual(h.sounds, []);
  }
});
