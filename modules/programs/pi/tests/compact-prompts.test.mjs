import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { registerHooks } from "node:module";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@earendil-works/pi-coding-agent") return {
      url: `data:text/javascript,${encodeURIComponent(`
        export class UserMessageComponent {
          constructor(text) { this.text = text; }
          render() { return this.text.split("\\n"); }
          invalidate() { this.invalidated = true; }
          handleMouse(event) { this.lastMouse = event; }
        }
        export const stripFrontmatter = text => text.replace(/^---\\n[\\s\\S]*?\\n---\\n/, "");
      `)}`, shortCircuit: true,
    };
    if (specifier === "@earendil-works/pi-tui") return {
      url: `data:text/javascript,${encodeURIComponent(`
        export const truncateToWidth = (s, width) => Array.from(s).slice(0, Math.max(0, width)).join("");
        export const visibleWidth = s => Array.from(s).length;
      `)}`, shortCircuit: true,
    };
    return next(specifier, context);
  },
});
const { default: extension, expandQueued } = await import("../extensions/compact-prompts.ts");
const { UserMessageComponent } = await import("@earendil-works/pi-coding-agent");
hooks.deregister();
const theme = { fg: (_color, text) => text, bg: (_color, text) => text };
const key = text => createHash("sha256").update(text).digest("hex");
const clean = text => text.replace(/\x1b\][^\x07]*\x07/g, "");
const click = { type: "click", button: "left", x: 1, y: 0, width: 80, height: 1 };

function harness({ mode = "tui", entries = [], commands = [] } = {}) {
  const handlers = new Map();
  const ctx = { mode, ui: { theme, getToolsExpanded: () => false }, sessionManager: { getBranch: () => entries } };
  extension({
    on: (event, handler) => handlers.set(event, handler),
    getCommands: () => commands,
    appendEntry: (customType, data) => entries.push({ type: "custom", customType, data }),
  });
  const emit = (event, data = {}) => handlers.get(event)?.(data, ctx);
  emit("session_start");
  return { emit, ctx, entries, commands };
}
function metadata(text, invocation) {
  return { type: "custom", customType: "compact-prompt", data: { hash: key(text), invocation } };
}

test("persisted templates collapse; click is local; Ctrl+O expansion uses native text", () => {
  const text = "PROMPT_DETAIL_ONE\nPROMPT_DETAIL_TWO";
  const h = harness({ entries: [metadata(text, "/fixture-news topic")] });
  try {
    const a = new UserMessageComponent(text), b = new UserMessageComponent(text);
    const ordinary = new UserMessageComponent("Ordinary message");
    assert.deepEqual(a.render(80).map(clean).map(s => s.trimEnd()), ["▸ /fixture-news topic"]);
    assert.equal(a.handleMouse(click).handled, true);
    assert.deepEqual(a.render(80).map(s => s.trimEnd()), ["▾ /fixture-news topic", ...text.split("\n")]);
    assert.ok(!b.render(80).join("\n").includes("PROMPT_DETAIL_ONE"));
    assert.deepEqual(ordinary.render(80), ["Ordinary message"]);
    a.handleMouse(click);
    assert.equal(a.render(80).length, 1);
    a.setExpanded(true); b.setExpanded(true);
    assert.ok(a.render(80).join("\n").includes("PROMPT_DETAIL_TWO"));
    assert.ok(b.render(80).join("\n").includes("PROMPT_DETAIL_TWO"));
    a.setExpanded(false);
    for (const width of [0, 1, 2, 10, 80]) {
      assert.equal(a.render(width).length, 1);
      assert.ok(Array.from(clean(a.render(width)[0])).length <= width);
    }
    const wheel = { ...click, type: "wheel" };
    assert.equal(a.handleMouse(wheel), undefined);
    assert.equal(a.handleMouse({ ...click, shift: true }), undefined);
    a.setExpanded(true);
    a.handleMouse({ ...wheel, y: 2, height: 5 });
    assert.equal(a.lastMouse.y, 1);
    assert.equal(a.lastMouse.height, 4);
    h.entries.length = 0;
    h.emit("session_tree");
    assert.deepEqual(a.render(80), text.split("\n"));
  } finally { h.emit("session_shutdown"); }
  assert.equal(Object.hasOwn(UserMessageComponent.prototype, "setExpanded"), false);
});

test("input is untouched; authoritative expansion is durable UI metadata only", () => {
  const h = harness({ commands: [{ name: "fixture-news", source: "prompt" }] });
  try {
    const input = { text: "/fixture-news topic", images: [{ type: "image", data: "unchanged" }], source: "interactive" };
    const before = structuredClone(input);
    assert.equal(h.emit("input", input), undefined);
    assert.deepEqual(input, before);
    h.emit("before_agent_start", { prompt: "Native expansion: topic" });
    assert.deepEqual(h.entries, [metadata("Native expansion: topic", "/fixture-news topic")]);
    assert.equal(JSON.stringify(h.entries).includes("Native expansion"), false);
    for (const text of ["ordinary", "/skill:reviewer", "/missing"]) {
      h.emit("input", { text });
      h.emit("before_agent_start", { prompt: text });
    }
    assert.equal(h.entries.length, 1);
  } finally { h.emit("session_shutdown"); }
});

test("queued templates are recognised without taking over steering/follow-up", () => {
  const dir = mkdtempSync(join(tmpdir(), "compact-prompt-test-"));
  const path = join(dir, "fixture-news.md");
  writeFileSync(path, "---\ndescription: test\n---\nTopic: ${1:-all}, $ARGUMENTS");
  const h = harness({ commands: [{ name: "fixture-news", source: "prompt", sourceInfo: { path } }] });
  try {
    for (const streamingBehavior of ["steer", "followUp"]) {
      const event = { text: '/fixture-news "topic news"', streamingBehavior };
      assert.equal(h.emit("input", event), undefined);
      assert.deepEqual(event, { text: '/fixture-news "topic news"', streamingBehavior });
    }
    assert.deepEqual(h.entries, [metadata("Topic: topic news, topic news", '/fixture-news "topic news"')]);
    h.commands[0].sourceInfo.path = join(dir, "deleted.md");
    assert.doesNotThrow(() => h.emit("input", { text: "/fixture-news", streamingBehavior: "steer" }));
  } finally { h.emit("session_shutdown"); rmSync(dir, { recursive: true }); }
});

test("reload does not stack adapters; branch switch drops abandoned metadata", () => {
  const nativeRender = UserMessageComponent.prototype.render;
  const a = harness({ entries: [metadata("old body", "/old")] });
  const wrappedRender = UserMessageComponent.prototype.render;
  const b = harness({ entries: [metadata("new body", "/new")] });
  try {
    assert.equal(UserMessageComponent.prototype.render, wrappedRender);
    a.emit("session_shutdown"); // Stale owner must not remove the current adapter.
    assert.equal(UserMessageComponent.prototype.render, wrappedRender);
    assert.deepEqual(new UserMessageComponent("old body").render(80), ["old body"]);
    assert.ok(clean(new UserMessageComponent("new body").render(80)[0]).startsWith("▸ /new"));
    b.entries.length = 0;
    b.emit("session_switch");
    assert.deepEqual(new UserMessageComponent("new body").render(80), ["new body"]);
  } finally { b.emit("session_shutdown"); }
  assert.equal(UserMessageComponent.prototype.render, nativeRender);
});

test("non-TUI modes do not patch components or append entries", () => {
  const nativeRender = UserMessageComponent.prototype.render;
  for (const mode of ["rpc", "json", "print"]) {
    const h = harness({ mode, commands: [{ name: "fixture-news", source: "prompt" }] });
    h.emit("input", { text: "/fixture-news" });
    h.emit("before_agent_start", { prompt: "body" });
    assert.deepEqual(h.entries, []);
    assert.equal(UserMessageComponent.prototype.render, nativeRender);
  }
});

test("queued argument recognition follows native single-pass syntax", () => {
  assert.equal(expandQueued('$1|$2|$@|$ARGUMENTS|${1:-default}|${3:-default}|${@:-default}|${ARGUMENTS:-default}|${@:2}|${@:2:1}|${@:0}', '"topic news" shell'),
    "topic news|shell|topic news shell|topic news shell|topic news|default|topic news shell|topic news shell|shell|shell|topic news shell");
  assert.equal(expandQueued('${1:-$@} $2', ""), "$@ ");
  assert.equal(expandQueued('$1 $2', '"$2" "$1"'), "$2 $1");
});
