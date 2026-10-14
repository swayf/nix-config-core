import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const PROVIDER = "openai-codex";
const STATUS = "codex-limits";
const USAGE_URL = "https://chatgpt.com/backend-api/wham/usage";
const POLL_MS = 60_000;

type Window = { remaining: number; resetAt?: number };
type Limits = { fiveHour?: Window; weekly?: Window };

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined;
}

export function parseLimits(value: unknown): Limits {
  const rate = object(object(value)?.rate_limit);
  if (!rate) throw new Error("Неизвестный формат лимитов");
  const result: Limits = {};
  for (const [key, fallback] of [["primary_window", 18_000], ["secondary_window", 604_800]] as const) {
    const raw = object(rate[key]);
    if (!raw || typeof raw.used_percent !== "number" || !Number.isFinite(raw.used_percent)) continue;
    const seconds = raw.limit_window_seconds ?? fallback;
    const window: Window = {
      remaining: Math.max(0, Math.min(100, 100 - raw.used_percent)),
      resetAt: typeof raw.reset_at === "number" && Number.isFinite(raw.reset_at) ? raw.reset_at : undefined,
    };
    if (seconds === 18_000) result.fiveHour = window;
    if (seconds === 604_800) result.weekly = window;
  }
  if (!result.fiveHour && !result.weekly) throw new Error("Нет данных о лимитах");
  return result;
}

function formatWindow(window: Window | undefined): string {
  if (!window) return "н/д";
  const percent = `${Math.floor(window.remaining)}%`;
  if (window.resetAt === undefined) return percent;
  const minutes = Math.ceil((window.resetAt * 1000 - Date.now()) / 60_000);
  // Do not invent a fresh allowance when the cached reset time passes.
  if (minutes <= 0) return `${percent} (обновление…)`;
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor(minutes % 1440 / 60);
  const rest = minutes % 60;
  const duration = days ? `${days}d${hours}h` : hours ? `${hours}h${rest}m` : `${rest}m`;
  return `${percent} ↻${duration}`;
}

export default function (pi: ExtensionAPI) {
  let context: ExtensionContext | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;
  let request: AbortController | undefined;
  let generation = 0;
  let lastAttempt = 0;
  let limits: Limits | undefined;
  let error: string | undefined;

  function render() {
    if (!context || context.mode !== "tui") return;
    if (context.model?.provider !== PROVIDER) {
      context.ui.setStatus(STATUS, undefined);
      return;
    }
    const sections = limits ? [
      limits.fiveHour ? `5h ${formatWindow(limits.fiveHour)}` : undefined,
      limits.weekly ? `7d ${formatWindow(limits.weekly)}` : undefined,
    ].filter(Boolean).join(" │ ") : undefined;
    context.ui.setStatus(STATUS, sections
      ? `${sections}${error ? " · устарело" : ""}`
      : `Codex: ${error ?? "лимиты загружаются…"}`);
  }

  async function refresh(force = false) {
    const ctx = context;
    if (!ctx || ctx.mode !== "tui" || ctx.model?.provider !== PROVIDER || request) return;
    if (!force && Date.now() - lastAttempt < 15_000) return;
    lastAttempt = Date.now();
    const version = generation;
    const controller = new AbortController();
    request = controller;
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      // Pi resolves OAuth credentials and refreshes expired tokens itself.
      const token = await ctx.modelRegistry.getApiKeyForProvider(PROVIDER);
      if (controller.signal.aborted) throw new Error("Request aborted");
      if (!token) throw new Error("выполните /login openai-codex");
      let accountId: unknown;
      try {
        const payload = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8"));
        accountId = payload["https://api.openai.com/auth"]?.chatgpt_account_id;
      } catch { /* Not a Codex OAuth token. */ }
      if (typeof accountId !== "string" || !accountId) throw new Error("нужна OAuth-авторизация Codex");
      const response = await fetch(USAGE_URL, {
        headers: {
          Authorization: `Bearer ${token}`,
          "ChatGPT-Account-Id": accountId,
          Accept: "application/json",
        },
        signal: controller.signal,
        redirect: "error",
      });
      // Never display response bodies or credentials.
      if (!response.ok) throw new Error(`лимиты недоступны (HTTP ${response.status})`);
      const next = parseLimits(await response.json());
      if (version !== generation) return;
      limits = next;
      error = undefined;
    } catch (cause) {
      if (version !== generation) return;
      error = controller.signal.aborted ? "таймаут запроса лимитов"
        : cause instanceof Error && /^(выполните|нужна|лимиты недоступны|Неизвестный|Нет данных)/.test(cause.message)
          ? cause.message : "не удалось получить лимиты";
    } finally {
      clearTimeout(timeout);
      if (version === generation) {
        request = undefined;
        render();
      }
    }
  }

  function stop() {
    generation++;
    if (timer) clearInterval(timer);
    timer = undefined;
    request?.abort();
    request = undefined;
    context?.ui.setStatus(STATUS, undefined);
    context = undefined;
    limits = undefined;
    error = undefined;
    lastAttempt = 0;
  }

  function start(ctx: ExtensionContext) {
    stop();
    context = ctx;
    if (ctx.mode !== "tui" || ctx.model?.provider !== PROVIDER) return;
    render();
    void refresh(true);
    timer = setInterval(() => { render(); void refresh(); }, POLL_MS);
    timer.unref();
  }

  pi.on("session_start", (_event, ctx) => start(ctx));
  pi.on("model_select", (_event, ctx) => start(ctx));
  pi.on("agent_end", (_event, ctx) => {
    context = ctx;
    void refresh();
  });
  pi.on("session_shutdown", () => stop());
  pi.registerCommand("codex-limits", {
    description: "Обновить оставшиеся лимиты Codex (5 часов и неделя)",
    handler: async (_args, ctx) => {
      if (ctx.mode !== "tui") return;
      if (ctx.model?.provider !== PROVIDER) {
        ctx.ui.notify("Выберите модель openai-codex", "info");
        return;
      }
      context = ctx;
      await refresh(true);
    },
  });
}
