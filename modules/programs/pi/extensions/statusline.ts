import { basename } from "node:path";
import { getAgentDir, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";

const formatTokens = (n: number) => n < 1000 ? String(n)
  : n < 1_000_000 ? `${Number((n / 1000).toFixed(1))}k` : `${Number((n / 1_000_000).toFixed(1))}M`;
const singleLine = (text: string) => text.replace(/[\r\n\t]/g, " ");
// Match the Claude statusline's terminal palette, independent of Pi's theme.
const color = (code: number, text: string) => `\x1b[${code}m${text}\x1b[0m`;
const percent = (text: string, remaining = false) => {
  const value = Number.parseFloat(text);
  if (!Number.isFinite(value)) return text;
  const used = remaining ? 100 - value : value;
  return color(used >= 80 ? 31 : used >= 50 ? 33 : 32, `${text}%`);
};

/** The former bell footer, without sound controls or notification handlers. */
export default function (pi: ExtensionAPI) {
  let context: ExtensionContext | undefined;
  let requestRender = () => {};

  pi.on("session_start", (_event, ctx) => {
    context = ctx;
    if (ctx.mode !== "tui") return;
    ctx.ui.setFooter((tui, theme, footerData) => {
      const profile = singleLine(basename(getAgentDir()));
      const redraw = () => tui.requestRender();
      requestRender = redraw;
      const unsubscribe = footerData.onBranchChange(redraw);
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
          const dim = (text: string) => theme.fg("dim", text);
          const contextPercent = usage?.percent == null ? dim("?%") : percent(String(Math.round(usage.percent)));
          const contextInfo = `${dim("ctx ")}${contextPercent}${dim(`/${formatTokens(usage?.contextWindow ?? model?.contextWindow ?? 0)}`)}`;
          const modelInfo = color(35, singleLine(model?.name || model?.id || "no-model"))
            + (model?.reasoning ? dim(` · ${singleLine(pi.getThinkingLevel())}`) : "");
          const primary = [color(1, color(36, project)), modelInfo, contextInfo];
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
                if (/^(5h|7d) /.test(section)) {
                  const window = /^(5h|7d) (\d+(?:\.\d+)?)%(.*)$/.exec(section);
                  // codex-limits reports remaining allowance, not used capacity.
                  primary.push(window ? `${dim(`${window[1]} `)}${percent(window[2], true)}${dim(window[3])}` : dim(section));
                } else extras.push(section);
              }
            } else extras.push(status);
          }
          // Reserve the right edge for the profile, even when details are clipped.
          const right = truncateToWidth(dim(profile), width);
          const rightWidth = visibleWidth(right);
          const left = truncateToWidth(primary.join(dim(" │ ")) + dim(` (${extras.join(" · ")})`),
            Math.max(0, width - rightWidth - 1));
          const padding = " ".repeat(Math.max(0, width - visibleWidth(left) - rightWidth));
          return [left + padding + right];
        },
      };
    });
  });
  pi.on("model_select", (_event, ctx) => { context = ctx; requestRender(); });
  pi.on("agent_start", (_event, ctx) => { context = ctx; });
  pi.on("session_shutdown", () => {
    context = undefined;
    requestRender = () => {};
  });
}
