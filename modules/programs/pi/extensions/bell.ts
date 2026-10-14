import { existsSync } from "node:fs";
import { createConnection } from "node:net";
import { basename, join } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";

const SHORTCUT = "ctrl+alt+b";
const formatTokens = (n: number) => n < 1000 ? String(n)
  : n < 1_000_000 ? `${Number((n / 1000).toFixed(1))}k` : `${Number((n / 1_000_000).toFixed(1))}M`;
const singleLine = (text: string) => text.replace(/[\r\n\t]/g, " ");

const EVENT_NAME = /^[a-z][a-z0-9_-]{0,31}$/;
const USAGE = "Usage: /sound [on|off|test [event]] (/bell is an alias)";

/** Request playback by name; never write BEL or send a host file/command. */
export function playSound(event: string, signal?: AbortSignal): Promise<void> {
  if (!EVENT_NAME.test(event)) return Promise.reject(new Error("Invalid sound event"));
  const runtime = process.env.XDG_RUNTIME_DIR ?? `/run/user/${process.getuid?.() ?? 1000}`;
  const path = process.env.PI_SOUND_SOCKET ?? (existsSync("/run/agents-sound/socket")
    ? "/run/agents-sound/socket" : join(runtime, "agents-sound/socket"));
  return new Promise((resolve, reject) => {
    const socket = createConnection({ path, signal });
    let done = false;
    let response = "";
    const finish = (error?: Error) => {
      if (done) return;
      done = true;
      socket.destroy();
      if (error) reject(error); else resolve();
    };
    socket.setTimeout(1200, () => finish(new Error("Sound bridge timed out")));
    socket.on("error", (error) => finish(error));
    socket.on("close", () => finish(new Error("Sound bridge closed without a response")));
    socket.on("connect", () => socket.write(`${JSON.stringify({ event })}\n`));
    socket.on("data", (chunk) => {
      response += chunk.toString();
      if (response.length > 2048) return finish(new Error("Invalid sound bridge response"));
      const end = response.indexOf("\n");
      if (end < 0) return;
      try {
        const result = JSON.parse(response.slice(0, end));
        finish(result?.ok === true ? undefined : new Error(
          typeof result?.error === "string" ? singleLine(result.error) : "Sound playback failed",
        ));
      } catch {
        finish(new Error("Invalid sound bridge response"));
      }
    });
  });
}

/** Custom ready sound, explicitly toggled for this running Pi instance/session. */
export default function (pi: ExtensionAPI) {
  let armed = false;
  let context: ExtensionContext | undefined;
  let requestRender = () => {};

  let playback = new AbortController();
  let lastError = "";
  let unsubscribeSound: (() => void) | undefined;

  async function ring(ctx: ExtensionContext, event = "ready", manual = false) {
    if (ctx.mode !== "tui") return;
    const signal = playback.signal;
    try {
      await playSound(event, signal);
      lastError = "";
    } catch (error) {
      if (signal.aborted) return;
      const message = `Sound: ${error instanceof Error ? error.message : String(error)}`;
      if (manual || message !== lastError) ctx.ui.notify(message, "warning");
      lastError = message;
    }
  }

  function toggle(ctx: ExtensionContext) {
    if (ctx.mode !== "tui") return;
    context = ctx;
    armed = !armed;
    requestRender();
  }

  function install(_event: unknown, ctx: ExtensionContext) {
    armed = false;
    context = ctx;
    playback.abort();
    playback = new AbortController();
    lastError = "";
    unsubscribeSound?.();
    unsubscribeSound = undefined;
    if (ctx.mode !== "tui") return;
    // Other extensions can emit sound:play with { event: "attention" }, etc.
    unsubscribeSound = pi.events.on("sound:play", (data) => {
      if (!armed || !context || typeof data?.event !== "string") return;
      void ring(context, data.event);
    });

    // One shared status line for every profile. Keep secondary information in
    // parentheses, with the sound button first for normalized mouse clicks.
    ctx.ui.setFooter((tui, theme, footerData) => {
      const redraw = () => tui.requestRender();
      requestRender = redraw;
      const unsubscribe = footerData.onBranchChange(redraw);
      let buttonRow = -1;
      let buttonWidth = 0;
      let renderedWidth = 0;
      let cachedSession: string | undefined;
      let cachedLeaf: string | null | undefined;
      let totals = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, cost: 0 };

      return {
        invalidate() {},
        dispose() {
          unsubscribe();
          if (requestRender === redraw) requestRender = () => {};
        },
        render(width: number): string[] {
          const current = context;
          if (!current) return [];
          const manager = current.sessionManager;
          const session = manager.getSessionId();
          const leaf = manager.getLeafId();
          // Do not scan the growing transcript on every streaming frame.
          if (session !== cachedSession || leaf !== cachedLeaf) {
            cachedSession = session;
            cachedLeaf = leaf;
            totals = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, cost: 0 };
            for (const entry of manager.getEntries()) {
              const usage = entry.type === "message"
                ? (entry.message.role === "assistant" || entry.message.role === "toolResult"
                  ? entry.message.usage : undefined)
                : (entry.type === "usage" || entry.type === "compaction" || entry.type === "branch_summary"
                  ? entry.usage : undefined);
              if (!usage) continue;
              totals.input += usage.input;
              totals.output += usage.output;
              totals.cacheRead += usage.cacheRead;
              totals.cacheWrite += usage.cacheWrite;
              totals.cost += usage.cost.total;
            }
          }

          const branch = footerData.getGitBranch();
          const name = manager.getSessionName();
          const cwd = manager.getCwd();
          const home = process.env.HOME;
          const path = home && (cwd === home || cwd.startsWith(`${home}/`))
            ? `~${cwd.slice(home.length)}` : cwd;
          const project = singleLine(basename(cwd) || cwd);
          const usage = current.getContextUsage();
          const model = current.model;
          const contextInfo = usage
            ? `${usage.percent === null ? "?" : Math.round(usage.percent)}%/${formatTokens(usage.contextWindow)}`
            : `?%/${formatTokens(model?.contextWindow ?? 0)}`;
          const modelInfo = singleLine(`${model?.name || model?.id || "no-model"}${model?.reasoning ? ` · ${pi.getThinkingLevel()}` : ""}`);
          const primary = [project, modelInfo, `ctx ${contextInfo}`];
          const extras = [
            singleLine(path),
            ...(branch ? [`git ${singleLine(branch)}`] : []),
            ...(name ? [`session ${singleLine(name)}`] : []),
            `↑${formatTokens(totals.input)} ↓${formatTokens(totals.output)}`,
            `R${formatTokens(totals.cacheRead)} W${formatTokens(totals.cacheWrite)}`,
            `$${totals.cost.toFixed(3)}`,
          ];
          for (const [key, text] of [...footerData.getExtensionStatuses().entries()]
            .sort(([a], [b]) => a.localeCompare(b))) {
            const status = singleLine(text);
            if (!status) continue;
            if (key === "codex-limits") {
              for (const section of status.split(/ │ | · /)) {
                if (/^(5h|7d) /.test(section)) primary.push(section);
                else extras.push(section);
              }
            } else extras.push(status);
          }
          const button = `🔔 ${armed ? "ON" : "OFF"}`;
          buttonRow = 0;
          buttonWidth = visibleWidth(button);
          renderedWidth = width;
          const statusLine = theme.fg(armed ? "success" : "dim", button)
            + theme.fg("dim", ` │ ${primary.join(" │ ")} (${extras.join(" · ")})`);
          return [truncateToWidth(statusLine, width)];
        },
        handleMouse(event) {
          // Do not toggle twice for press/release/click, steal editor focus, or
          // consume clicks on neighbouring status text. No partial hit target.
          if (buttonWidth > renderedWidth || event.y !== buttonRow || event.x < 0 || event.x >= buttonWidth || event.button !== "left") return;
          if (event.type === "click" && context) {
            toggle(context);
            return { handled: true, render: true };
          }
          if (event.type === "press") return { handled: true, render: false };
        },
      };
    });
  }

  const soundCommand = {
    description: "Toggle custom ready sound; /sound test [event] plays via the host broker, not BEL",
    handler: async (args: string, ctx: ExtensionContext) => {
      if (ctx.mode !== "tui") return;
      const action = args.trim();
      const parts = action.split(/\s+/);
      if (parts[0] === "test" && parts.length <= 2 && EVENT_NAME.test(parts[1] ?? "ready")) {
        await ring(ctx, parts[1] ?? "ready", true);
      } else if (action === "on" || action === "off") {
        context = ctx;
        armed = action === "on";
        requestRender();
      } else if (!action) {
        toggle(ctx);
      } else {
        ctx.ui.notify(USAGE, "warning");
      }
    },
  };
  pi.registerCommand("sound", soundCommand);
  pi.registerCommand("bell", soundCommand); // Keep existing muscle memory.
  pi.registerShortcut(SHORTCUT, {
    description: "Toggle custom ready sound",
    handler: async (ctx) => toggle(ctx),
  });
  // Covers startup, /reload, /new, /resume and /fork in Pi 0.99+.
  pi.on("session_start", install);
  pi.on("model_select", (_event, ctx) => { context = ctx; requestRender(); });
  pi.on("agent_start", (_event, ctx) => { context = ctx; });
  // agent_end can still be followed by automatic retries or queued work.
  pi.on("agent_settled", (_event, ctx) => {
    context = ctx;
    if (!armed || ctx.mode !== "tui") return;
    return ring(ctx);
  });
  pi.on("session_shutdown", () => {
    playback.abort();
    unsubscribeSound?.();
    unsubscribeSound = undefined;
    armed = false;
    context = undefined;
    requestRender = () => {};
  });
}
