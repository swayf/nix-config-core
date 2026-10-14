import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { test } from "node:test";

const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@earendil-works/pi-tui") return {
      url: process.env.PI_TUI_MODULE ?? `data:text/javascript,${encodeURIComponent(`
        export class Text {
          constructor(text) { this.text = text; }
          render() { return this.text.split("\\n"); }
          invalidate() {}
        }
        export const truncateToWidth = (s, width) => Array.from(s).slice(0, Math.max(0, width)).join("");
        export const visibleWidth = s => Array.from(s).length;
      `)}`,
      shortCircuit: true,
    };
    return next(specifier, context);
  },
});
const { default: extension } = await import("../extensions/compact-tools.ts");
hooks.deregister();
const theme = { fg: (_color, text) => text, bg: (_color, text) => text, bold: text => text };
const text = value => ({ render: () => value.split("\n"), invalidate() {} });
const result = { content: [{ type: "text", text: "full output\nlast line" }], details: { diff: "+ added\n- removed" } };
function harness() {
  let resolve;
  // Rendering only: no API for changing tools, results, keys or model context.
  extension({ registerToolRenderer: fn => { resolve = fn; } });
  return { resolve };
}
function context(extra = {}) {
  return { args: {}, state: {}, lastComponent: undefined, expanded: false, isPartial: false,
    isError: false, argsComplete: true, executionStarted: true, showImages: true,
    cwd: "/work", toolCallId: "call-1", invalidate() {}, ...extra };
}
const original = {
  renderCall: (args, _theme, ctx) => text(`original ${ctx.expanded}\n${args.content ?? "command arguments"}`),
  renderResult: (res, opts) => text(`${opts.expanded}\n${res.content[0].text}`),
};

for (const [name, args, expected] of [
  ["read", { path: "src/a.ts", offset: 4, limit: 3 }, "src/a.ts:4-6"],
  ["read", { file_path: "src/a.ts", offset: null, limit: 2 }, "src/a.ts:1-2"],
  ["write", { path: "a.ts", content: "secret\nmore" }, "a.ts"],
  ["edit", { path: "a.ts", edits: [{ oldText: "old", newText: "new" }] }, "a.ts"],
  ["bash", { command: "printf 'hello'\nls\t-la" }, "printf 'hello' ls -la"],
  ["powershell", { command: "Get-ChildItem" }, "Get-ChildItem"],
  ["ls", {}, "."],
  ["find", { pattern: "*.ts", path: "src" }, "*.ts · src"],
  ["grep", { pattern: "TODO", path: "src" }, "TODO · src"],
  ["fetch_content", { url: "https://example.org/news", prompt: "hidden" }, "https://example.org/news"],
  ["web_search", { queries: ["Pi news", "Home Manager news"] }, "Pi news · Home Manager news"],
  ["get_search_content", { responseId: "abc123", offset: 100 }, "abc123"],
  ["wiki_ask", { question: "How does it work?" }, "How does it work?"],
  ["mcp__fetch", { url: "https://example.org" }, "https://example.org"],
]) test(`${name}: exactly one compact line; full native detail on expansion`, () => {
  const renderer = harness().resolve(name, () => original);
  const ctx = context({ args });
  const call = () => renderer.renderCall(args, theme, ctx).render(120).map(line => line.trimEnd());
  const output = () => renderer.renderResult(result, { expanded: ctx.expanded, isPartial: false }, theme, ctx).render(120).map(line => line.trimEnd());
  assert.equal(renderer.renderShell, "self");
  assert.deepEqual(call(), [`▸ ${name} ${expected}`]);
  assert.deepEqual(output(), []);
  // These flags are set by Pi's per-call MouseRegion or global Ctrl+O.
  ctx.expanded = true;
  assert.ok(call()[0].startsWith("▾ original true"));
  assert.ok(call().length > 1);
  assert.deepEqual(output(), ["true", "full output", "last line"]);
  ctx.expanded = false;
  assert.deepEqual(call(), [`▸ ${name} ${expected}`]);
  assert.deepEqual(output(), []);
});

test("standard background colors fill each row, including expanded details", () => {
  const renderer = harness().resolve("write", () => original);
  for (const [flags, color] of [
    [{ isPartial: false, isError: false }, "toolSuccessBg"],
    [{ isPartial: true, isError: false }, "toolPendingBg"],
    [{ isPartial: false, isError: true }, "toolErrorBg"],
    [{ isPartial: true, isError: true }, "toolPendingBg"],
  ]) {
    const painted = [];
    const coloredTheme = { ...theme, bg: (name, line) => {
      painted.push([name, line]);
      return line;
    } };
    const ctx = context({ args: { path: "a.ts" }, ...flags });
    const component = renderer.renderCall(ctx.args, coloredTheme, ctx);
    for (const width of [0, 1, 20, 80]) {
      const lines = component.render(width);
      assert.equal(lines.length, 1);
      assert.equal(Array.from(lines[0]).length, width);
      assert.equal(painted.at(-1)[0], color);
    }
    ctx.expanded = true;
    const lines = [
      ...renderer.renderCall(ctx.args, coloredTheme, ctx).render(80),
      ...renderer.renderResult(result, { expanded: true }, coloredTheme, ctx).render(80),
    ];
    assert.ok(lines.every(line => Array.from(line).length === 80));
    assert.ok(painted.every(([name]) => name === color));
  }
});

test("wraps third-party renderers and supplies defaults for missing slots", () => {
  const h = harness();
  const questionnaire = h.resolve("ask_user_question", () => original);
  assert.deepEqual(questionnaire.renderResult(result, { expanded: false }, theme, context()).render(80), []);
  assert.ok(questionnaire.renderResult(result, { expanded: true }, theme, context({ expanded: true })).render(80).join("\\n").includes("full output"));
  const missing = h.resolve("fetch", () => undefined);
  assert.equal(missing.renderCall({ url: "https://example.org" }, theme, context()).render(80)[0].trimEnd(), "▸ fetch https://example.org");
  assert.ok(missing.renderResult(result, { expanded: true }, theme, context({ expanded: true })).render(80).join("\\n").includes("full output"));
  const partial = h.resolve("ls", () => ({ renderCall: original.renderCall }));
  assert.ok(partial.renderCall({}, theme, context({ expanded: true })).render(80)[0].includes("original true"));
  assert.ok(partial.renderResult(result, { expanded: true }, theme, context({ expanded: true })).render(80).join("\\n").includes("last line"));
});

test("partial/invalid args, errors and narrow windows stay single-line", () => {
  const renderer = harness().resolve("write", () => original);
  for (const args of [undefined, {}, { path: "a\nb\t\x1b[31mc\x00" }]) {
    const ctx = context({ args, isError: true, isPartial: true });
    assert.match(renderer.renderCall(args, theme, ctx).render(200)[0].trimEnd(), /\[error\]$/);
    for (const width of [0, 1, 2, 4, 10, 40]) {
      const lines = renderer.renderCall(args, theme, ctx).render(width);
      assert.equal(lines.length, 1);
      assert.ok(Array.from(lines[0]).length <= width);
      assert.doesNotMatch(lines[0], /[\n\t\x00\x1b]/);
    }
    assert.deepEqual(renderer.renderResult(result, { expanded: false, isPartial: true }, theme, ctx).render(100), []);
  }
});

test("native components, state and lastComponent stay isolated per call and slot", () => {
  const native = {
    renderCall(_args, _theme, ctx) {
      ctx.state.calls = (ctx.state.calls ?? 0) + 1;
      assert.ok(!ctx.lastComponent || ctx.lastComponent.slot === "call");
      return ctx.lastComponent ?? { ...text("native"), slot: "call" };
    },
    renderResult(res, options, _theme, ctx) {
      assert.equal(res, result); // No transformed or truncated model-facing data.
      assert.equal(options.expanded, true);
      assert.ok(!ctx.lastComponent || ctx.lastComponent.slot === "result");
      return ctx.lastComponent ?? { ...text("result"), slot: "result" };
    },
  };
  const renderer = harness().resolve("edit", () => native);
  const a = context({ expanded: true }), b = context();
  for (let i = 0; i < 3; i++) {
    renderer.renderCall({}, theme, a).render(80);
    renderer.renderResult(result, { expanded: true }, theme, a).render(80);
    renderer.renderCall({}, theme, b).render(80);
    a.expanded = false;
    renderer.renderCall({}, theme, a).render(80);
    a.expanded = true;
  }
  assert.equal(a.state.compactTools.state.calls, 3);
  assert.equal(b.state.compactTools, undefined);
  assert.equal(b.expanded, false);
});
