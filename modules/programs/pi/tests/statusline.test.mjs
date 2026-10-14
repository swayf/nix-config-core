import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { test } from "node:test";

const stripAnsi = s => s.replace(/\x1b\[[0-9;]*m/g, "");
const tuiStub = String.raw`
  export const visibleWidth = s => Array.from(s.replace(/\x1b\[[0-9;]*m/g, "")).length;
  export const truncateToWidth = (s, width) => {
    let columns = 0;
    let result = "";
    for (const token of s.match(/\x1b\[[0-9;]*m|[^]/gu) ?? []) {
      if (!token.startsWith("\x1b") && ++columns > Math.max(0, width)) break;
      result += token;
    }
    return result + "\x1b[0m";
  };
`;
const hostStub = `
  let agentDir;
  export const getAgentDir = () => agentDir;
  export const setAgentDir = value => { agentDir = value; };
`;
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@earendil-works/pi-coding-agent") return {
      url: `data:text/javascript,${encodeURIComponent(hostStub)}`, shortCircuit: true,
    };
    if (specifier === "@earendil-works/pi-tui") return {
      url: process.env.PI_TUI_MODULE ?? `data:text/javascript,${encodeURIComponent(tuiStub)}`,
      shortCircuit: true,
    };
    return next(specifier, context);
  },
});
const { default: extension } = await import("../extensions/statusline.ts");
const { visibleWidth } = await import("@earendil-works/pi-tui");
const { setAgentDir } = await import("@earendil-works/pi-coding-agent");
hooks.deregister();

function harness(mode = "tui", agentDir = "/test/.pi/agent") {
  setAgentDir(agentDir);
  const handlers = {};
  let footer;
  let renders = 0;
  let unsubscribed = 0;
  let scans = 0;
  const statuses = new Map([["codex-limits", "5h 16% ↻10m │ 7d 8% ↻20h50m · устарело"]]);
  const entries = [{ type: "message", message: { role: "assistant", usage: {
    input: 1200, output: 12, cacheRead: 10, cacheWrite: 0, cost: { total: 0.123 },
  } } }];
  const ctx = {
    mode,
    model: { id: "test-model", name: "Opus 5.5", reasoning: true, contextWindow: 1000000 },
    sessionManager: {
      getSessionId: () => "session-1", getLeafId: () => String(entries.length),
      getEntries: () => { scans++; return entries; },
      getCwd: () => "/work/home-manager", getSessionName: () => "task",
    },
    getContextUsage: () => ({ percent: 6.2, contextWindow: 1000000 }),
    ui: {
      setFooter(factory) {
        footer?.dispose();
        footer = factory({ requestRender: () => { renders++; } }, { fg: (_color, s) => `\x1b[2m${s}\x1b[0m` }, {
          getGitBranch: () => "main", getExtensionStatuses: () => statuses,
          onBranchChange: () => () => { unsubscribed++; },
        });
      },
    },
  };
  // No command/shortcut/sound APIs: statusline must be independent of bell.
  extension({ on: (name, fn) => { handlers[name] = fn; }, getThinkingLevel: () => "high" });
  handlers.session_start({}, ctx);
  return { handlers, ctx, statuses, entries, footer: () => footer,
    scans: () => scans, renders: () => renders, unsubscribed: () => unsubscribed,
    status: (width = 300) => stripAnsi(footer.render(width)[0]),
    rawStatus: (width = 300) => footer.render(width)[0] };
}

test("restores the former single-line footer without bell and preserves limits/stats", () => {
  const h = harness();
  h.statuses.set("web", "web ready");
  assert.equal(h.status(), "home-manager │ Opus 5.5 · high │ ctx 6%/1M │ 5h 16% ↻10m │ 7d 8% ↻20h50m (/work/home-manager · git main · session task · ↑1.2k ↓12 · R10 W0 · $0.123 · устарело · web ready)".padEnd(295) + "agent");
  assert.equal(h.footer().render(300).length, 1);
  assert.equal(h.footer().handleMouse, undefined);
  assert.equal(h.handlers.agent_settled, undefined);
  assert.equal(h.handlers.agent_end, undefined);
  for (const width of [0, 1, 4, 10, 20, 40, 80, 120, 300]) {
    assert.ok(h.footer().render(width).every(line => visibleWidth(line) <= width));
  }
});

test("right-aligns standard and custom profile names and reserves space on narrow terminals", () => {
  for (const profile of ["code", "wiki", "general", "custom-profile"]) {
    const h = harness("tui", `/test/.pi/profiles/${profile}/`);
    for (const width of [profile.length, profile.length + 1, 20, 80, 120, 300]) {
      const line = h.status(width);
      assert.equal(visibleWidth(h.rawStatus(width)), width);
      assert.ok(line.endsWith(profile), line);
      if (width > profile.length) assert.equal(line.at(-profile.length - 1), " ");
    }
    for (let width = 0; width < profile.length; width++) {
      assert.equal(visibleWidth(h.rawStatus(width)), width);
    }
  }
});

test("uses the custom agent directory name and keeps it on one line", () => {
  const h = harness("tui", "/elsewhere/research\nprofile");
  assert.ok(h.status().endsWith("research profile"));
  assert.ok(!h.status().includes("\n"));
});

test("colors project/model and dims separators, reasoning, reset times and extras", () => {
  const h = harness();
  const line = h.rawStatus();
  assert.ok(line.startsWith("\x1b[1m\x1b[36mhome-manager\x1b[0m\x1b[0m"));
  for (const text of [" │ ", " · high", "ctx ", "/1M", " ↻10m"]) {
    assert.ok(line.includes(`\x1b[2m${text}\x1b[0m`));
  }
  assert.ok(line.includes("\x1b[35mOpus 5.5\x1b[0m"));
  assert.ok(line.includes("\x1b[2m (/work/home-manager"));
});

test("context colors used capacity; Codex colors remaining allowance inversely", () => {
  const h = harness();
  for (const [used, remaining, code] of [[0, 100, 32], [49, 51, 32], [50, 50, 33], [79, 21, 33], [80, 20, 31], [100, 0, 31]]) {
    h.ctx.getContextUsage = () => ({ percent: used, contextWindow: 1000000 });
    h.statuses.set("codex-limits", `5h ${remaining}% ↻10m │ 7d ${remaining}% (обновление…)`);
    const line = h.rawStatus();
    assert.ok(line.includes(`\x1b[${code}m${used}%\x1b[0m`));
    assert.equal(line.split(`\x1b[${code}m${remaining}%\x1b[0m`).length - 1, used === remaining ? 3 : 2);
    assert.ok(line.includes("\x1b[2m (обновление…)\x1b[0m"));
  }
  h.ctx.getContextUsage = () => ({ percent: null, contextWindow: 1000000 });
  h.statuses.set("codex-limits", "5h н/д");
  assert.ok(h.rawStatus().includes("\x1b[2m?%\x1b[0m"));
  assert.ok(h.rawStatus().includes("\x1b[2m5h н/д\x1b[0m"));
});

test("caches transcript totals until the leaf changes and includes nested/summary usage", () => {
  const h = harness();
  h.status(); h.status();
  assert.equal(h.scans(), 1);
  for (const type of ["usage", "compaction", "branch_summary", "toolResult"]) {
    const usage = { input: 500, output: 8, cacheRead: 0, cacheWrite: 0, cost: { total: 0.1 } };
    h.entries.push(type === "toolResult" ? { type: "message", message: { role: type, usage } } : { type, usage });
  }
  assert.match(h.status(), /↑3.2k ↓44.*\$0.523/);
  assert.equal(h.scans(), 2);
});

test("missing context/model or limits are not invented", () => {
  const h = harness();
  h.ctx.model = undefined;
  h.ctx.getContextUsage = () => undefined;
  h.statuses.set("codex-limits", "Codex: лимиты загружаются…");
  assert.match(h.status(), /^home-manager │ no-model │ ctx \?%\/0 \(/);
  assert.match(h.status(), /Codex: лимиты загружаются…\) +agent$/);
  h.ctx.model = { id: "fallback", reasoning: false, contextWindow: 200000 };
  h.ctx.getContextUsage = () => ({ percent: null, contextWindow: 200000 });
  h.statuses.delete("codex-limits");
  assert.match(h.status(), /│ fallback │ ctx \?%\/200k \(/);
});

test("model changes, session replacement and shutdown use fresh state", () => {
  const h = harness();
  const ctx = { ...h.ctx, model: { id: "new-model", contextWindow: 200000 } };
  h.handlers.model_select({}, ctx);
  assert.equal(h.renders(), 1);
  assert.match(h.status(), /│ new-model │/);
  h.handlers.session_start({ reason: "resume" }, ctx);
  assert.equal(h.unsubscribed(), 1);
  h.handlers.session_shutdown({}, ctx);
  assert.deepEqual(h.footer().render(120), []);
  h.footer().dispose();
  assert.equal(h.unsubscribed(), 2);
});

test("RPC, JSON and print modes do not install a terminal footer", () => {
  for (const mode of ["rpc", "json", "print"]) assert.equal(harness(mode).footer(), undefined);
});
