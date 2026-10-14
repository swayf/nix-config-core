import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { getAgentDir, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";

const levels = ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
type ThinkingLevel = (typeof levels)[number];
interface Preset {
  name: string;
  provider: string;
  model: string;
  thinkingLevel: ThinkingLevel;
}

// Each agent directory owns its list, including custom PI_CODING_AGENT_DIR paths.
const configPath = () => join(getAgentDir(), "model-presets.json");

async function loadPresets(): Promise<Preset[]> {
  const config = JSON.parse(await readFile(configPath(), "utf8"));
  if (!Array.isArray(config?.presets)) throw new Error('Expected an object with a "presets" array');
  const names = new Set<string>();
  for (const preset of config.presets) {
    if (!preset || ["name", "provider", "model"].some(key =>
      typeof preset[key] !== "string" || !preset[key].trim() || /[\x00-\x1f\x7f]/.test(preset[key])) ||
      !levels.includes(preset.thinkingLevel)) {
      throw new Error("Each preset needs name, provider, model and a valid thinkingLevel");
    }
    if (names.has(preset.name)) throw new Error(`Duplicate preset name: ${preset.name}`);
    names.add(preset.name);
  }
  return config.presets;
}

export default function modelPresets(pi: ExtensionAPI) {
  let showing = false;
  let unsubscribeInput: (() => void) | undefined;

  async function show(ctx: ExtensionContext) {
    if (ctx.mode !== "tui" || showing) return;
    if (!ctx.isIdle()) {
      ctx.ui.notify("Дождитесь окончания ответа перед переключением модели", "warning");
      return;
    }
    showing = true;
    try {
      // Reload on every opening: editing the list needs neither rebuild nor /reload.
      const presets = await loadPresets();
      const available = await ctx.modelRegistry.getAvailable();
      const rows = presets.flatMap(preset => {
        const model = available.find(model => model.provider === preset.provider && model.id === preset.model);
        if (!model) return [];
        const active = ctx.model?.provider === preset.provider && ctx.model.id === preset.model &&
          pi.getThinkingLevel() === preset.thinkingLevel;
        return [{ preset, model, label: `${active ? "✓ " : ""}${preset.name} — ${preset.provider}/${preset.model} · ${preset.thinkingLevel}` }];
      });
      if (!rows.length) {
        ctx.ui.notify(`Нет доступных пресетов. Проверьте ${configPath()} и /login`, "warning");
        return;
      }
      const selected = await ctx.ui.select("Модель + thinking effort", rows.map(row => row.label));
      const row = rows.find(row => row.label === selected);
      if (!row) return; // Escape: no state changes.
      if (!ctx.isIdle()) {
        ctx.ui.notify("Агент уже работает; модель не изменена", "warning");
        return;
      }
      // Authentication may disappear while the picker is open. Never change effort on failure.
      if (!(await pi.setModel(row.model))) {
        ctx.ui.notify(`Не удалось выбрать ${row.preset.provider}/${row.preset.model}; проверьте /login`, "error");
        return;
      }
      pi.setThinkingLevel(row.preset.thinkingLevel);
      const actual = pi.getThinkingLevel();
      const clamped = actual !== row.preset.thinkingLevel;
      ctx.ui.notify(`${row.preset.name}: ${row.preset.model} · ${actual}${clamped ? " (уровень ограничен возможностями модели)" : ""}`,
        clamped ? "warning" : "info");
    } catch (error) {
      ctx.ui.notify(`Пресеты (${configPath()}): ${error instanceof Error ? error.message : String(error)}`, "error");
    } finally {
      showing = false;
    }
  }

  pi.registerCommand("model-presets", {
    description: "Выбрать готовую пару модель + thinking effort (model-presets.json текущего профиля)",
    handler: async (_args, ctx) => { await show(ctx); },
  });
  pi.registerShortcut("ctrl+m", {
    description: "Меню модель + thinking effort",
    handler: show,
  });

  pi.on("session_start", (_event, ctx) => {
    unsubscribeInput?.();
    unsubscribeInput = undefined;
    if (ctx.mode !== "tui") return;
    // Pi matches legacy CR both as Enter and Ctrl+M. Protect ordinary Enter by
    // converting it to an explicit unmodified Enter before shortcut dispatch.
    // Real Ctrl+M requires CSI-u/Kitty or modifyOtherKeys from the terminal.
    unsubscribeInput = ctx.ui.onTerminalInput(data => data === "\r" ? { data: "\x1b[13u" } : undefined);
  });
  pi.on("session_shutdown", () => {
    unsubscribeInput?.();
    unsubscribeInput = undefined;
  });
}
