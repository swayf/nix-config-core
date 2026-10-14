import assert from "node:assert/strict";
import { test } from "node:test";
import extension, { parseLimits } from "../extensions/codex-limits.ts";

const usage = { rate_limit: {
  primary_window: { used_percent: 23, limit_window_seconds: 18000 },
  secondary_window: { used_percent: 61, limit_window_seconds: 604800 },
} };
const token = `header.${Buffer.from(JSON.stringify({
  "https://api.openai.com/auth": { chatgpt_account_id: "test-account" },
})).toString("base64url")}.signature`;
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const handlers = {};
  const commands = {};
  const statuses = [];
  const ctx = {
    mode: "tui",
    model: { provider: "openai-codex" },
    modelRegistry: { getApiKeyForProvider: async () => token },
    ui: { setStatus: (_key, text) => statuses.push(text), notify() {} },
  };
  extension({ on: (name, fn) => { handlers[name] = fn; },
    registerCommand: (name, command) => { commands[name] = command; } });
  return { handlers, commands, statuses, ctx };
}

test("parses remaining allowance, missing windows and reversed durations", () => {
  assert.equal(parseLimits(usage).fiveHour.remaining, 77);
  assert.equal(parseLimits(usage).weekly.remaining, 39);
  assert.equal(parseLimits({ rate_limit: { primary_window: { used_percent: 110 } } }).fiveHour.remaining, 0);
  const reversed = { rate_limit: {
    primary_window: usage.rate_limit.secondary_window,
    secondary_window: usage.rate_limit.primary_window,
  } };
  assert.equal(parseLimits(reversed).weekly.remaining, 39);
  assert.throws(() => parseLimits({}));
  assert.throws(() => parseLimits({ rate_limit: { primary_window: { used_percent: NaN } } }));
});

test("lifecycle, authentication headers, refresh errors and model switching", async t => {
  const h = harness();
  t.after(() => h.handlers.session_shutdown());
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls++;
    assert.equal(url, "https://chatgpt.com/backend-api/wham/usage");
    assert.equal(options.headers.Authorization, `Bearer ${token}`);
    assert.equal(options.headers["ChatGPT-Account-Id"], "test-account");
    return new Response(JSON.stringify(usage));
  });
  h.handlers.session_start({}, h.ctx);
  await tick();
  assert.equal(h.statuses.at(-1), "5h 77% │ 7d 39%");
  h.handlers.agent_end({}, h.ctx);
  await tick();
  assert.equal(calls, 1, "agent_end is throttled");
  globalThis.fetch = async () => new Response("secret response body", { status: 401 });
  await h.commands["codex-limits"].handler("", h.ctx);
  assert.match(h.statuses.at(-1), /устарело/);
  h.handlers.model_select({}, { ...h.ctx, model: { provider: "anthropic" } });
  assert.equal(h.statuses.at(-1), undefined);
});

test("late responses cannot restore a status after switching providers", async t => {
  const h = harness();
  t.after(() => h.handlers.session_shutdown());
  let resolve;
  t.mock.method(globalThis, "fetch", () => new Promise(r => { resolve = r; }));
  h.handlers.session_start({}, h.ctx);
  await tick();
  h.handlers.model_select({}, { ...h.ctx, model: { provider: "anthropic" } });
  resolve(new Response(JSON.stringify(usage)));
  await tick();
  assert.equal(h.statuses.at(-1), undefined);
});

test("missing five-hour allowance is hidden from the footer", async t => {
  const h = harness();
  t.after(() => h.handlers.session_shutdown());
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    rate_limit: { secondary_window: usage.rate_limit.secondary_window },
  })));
  h.handlers.session_start({}, h.ctx);
  await tick();
  assert.equal(h.statuses.at(-1), "7d 39%");
});

test("weekly limit uses compact reset duration", async t => {
  const h = harness();
  t.after(() => h.handlers.session_shutdown());
  const now = 1_800_000_000_000;
  t.mock.method(Date, "now", () => now);
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    rate_limit: { secondary_window: {
      used_percent: 27,
      limit_window_seconds: 604800,
      reset_at: now / 1000 + (5 * 24 + 4) * 3600,
    } },
  })));
  h.handlers.session_start({}, h.ctx);
  await tick();
  assert.equal(h.statuses.at(-1), "7d 73% ↻5d4h");
});

test("five-hour and weekly resets match the single-line format", async t => {
  const h = harness();
  t.after(() => h.handlers.session_shutdown());
  const now = 1_800_000_000_000;
  t.mock.method(Date, "now", () => now);
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    rate_limit: {
      primary_window: { used_percent: 84, reset_at: now / 1000 + 10 * 60 },
      secondary_window: { used_percent: 92, reset_at: now / 1000 + (20 * 60 + 50) * 60 },
    },
  })));
  h.handlers.session_start({}, h.ctx);
  await tick();
  assert.equal(h.statuses.at(-1), "5h 16% ↻10m │ 7d 8% ↻20h50m");
  globalThis.fetch = async () => new Response(JSON.stringify({
    rate_limit: { primary_window: { used_percent: 100, reset_at: now / 1000 - 1 } },
  }));
  await h.commands["codex-limits"].handler("", h.ctx);
  assert.equal(h.statuses.at(-1), "5h 0% (обновление…)");
});

test("non-interactive sessions never poll", async t => {
  const h = harness();
  t.after(() => h.handlers.session_shutdown());
  t.mock.method(globalThis, "fetch", () => { assert.fail("unexpected request"); });
  h.handlers.session_start({}, { ...h.ctx, mode: "json" });
  await tick();
  assert.equal(h.statuses.length, 0);
});
