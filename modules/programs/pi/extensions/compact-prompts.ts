import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { UserMessageComponent, stripFrontmatter, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { truncateToWidth, visibleWidth, type TuiMouseEvent } from "@earendil-works/pi-tui";

const ENTRY = "compact-prompt";
const PATCH = Symbol.for("home-manager.pi.compact-prompts");
const hash = (text: string) => createHash("sha256").update(text).digest("hex");
const inline = (text: string) => text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")
  .replace(/[\x00-\x1f\x7f-\x9f]/g, " ").replace(/\s+/g, " ").trim();

type Metadata = { hash: string; invocation: string };
type View = { hash: string; expanded: boolean };
type State = {
  titles: Map<string, string>;
  views: WeakMap<UserMessageComponent, View>;
  ctx: ExtensionContext;
};

// Pi 1.0.2 exports this component but has no user-message renderer hook. This
// narrowly scoped, reversible adapter changes only its display, never messages,
// template expansion, input delivery, images, or the model context. The native
// Markdown component is still used for expanded content. A symbol prevents
// stacked wrappers on /reload; unrecognised messages always use native rendering.
function install(state: State) {
  const proto = UserMessageComponent.prototype as any;
  if (proto[PATCH]) {
    proto[PATCH].state = state;
    return;
  }
  const original = {
    render: proto.render, handleMouse: proto.handleMouse,
    setExpanded: Object.getOwnPropertyDescriptor(proto, "setExpanded"),
    mouse: Object.getOwnPropertyDescriptor(proto, "handleMouse"),
  };
  const patch = { state, original };
  proto[PATCH] = patch;

  function view(component: UserMessageComponent): View | undefined {
    const active: State = patch.state;
    // Private in TypeScript, an ordinary property in the pinned Pi runtime.
    const text = (component as unknown as { text?: string }).text;
    if (typeof text !== "string") return undefined;
    let current = active.views.get(component);
    if (!current) {
      current = { hash: hash(text), expanded: active.ctx.ui.getToolsExpanded() };
      active.views.set(component, current);
    }
    return active.titles.has(current.hash) ? current : undefined;
  }

  proto.render = function (width: number) {
    const current = view(this);
    if (!current) return original.render.call(this, width);
    const theme = patch.state.ctx.ui.theme;
    const title = inline(patch.state.titles.get(current.hash)!);
    const line = truncateToWidth(`${theme.fg("muted", current.expanded ? "▾" : "▸")} ${theme.fg("userMessageText", title)}`, width);
    const header = theme.bg("userMessageBg", line + " ".repeat(Math.max(0, width - visibleWidth(line))));
    if (current.expanded) return [header, ...original.render.call(this, width)];
    // Preserve the user's terminal semantic zone in the collapsed view too.
    return ["\x1b]133;A\x07" + header + "\x1b]133;B\x07\x1b]133;C\x07"];
  };
  proto.setExpanded = function (expanded: boolean) {
    const current = view(this);
    if (current) {
      current.expanded = expanded;
      this.invalidate();
    }
  };
  proto.handleMouse = function (event: TuiMouseEvent) {
    const current = view(this);
    if (!current) return original.handleMouse?.call(this, event);
    if (event.y === 0 && event.type === "click" && event.button === "left" && !event.shift && !event.alt && !event.ctrl) {
      this.setExpanded(!current.expanded);
      return { handled: true, render: true };
    }
    if (current.expanded && event.y > 0) {
      return original.handleMouse?.call(this, { ...event, y: event.y - 1, height: Math.max(0, event.height - 1) });
    }
    return undefined; // Wheel scrolling, selection and links remain with Pi.
  };
}

function uninstall(state: State | undefined) {
  const proto = UserMessageComponent.prototype as any;
  const patch = proto[PATCH];
  if (!patch || patch.state !== state) return;
  proto.render = patch.original.render;
  for (const [name, descriptor] of [["setExpanded", patch.original.setExpanded], ["handleMouse", patch.original.mouse]] as const) {
    if (descriptor) Object.defineProperty(proto, name, descriptor);
    else delete proto[name];
  }
  delete proto[PATCH];
}

// Queued input does not emit before_agent_start again. Mirror Pi's single-pass
// argument substitution for recognising its later display; never send this
// computed text to the agent. Idle turns use Pi's actual expanded prompt.
export function expandQueued(body: string, rawArgs: string): string {
  const args: string[] = [];
  let current = "", quote = "";
  for (const char of rawArgs) {
    if (quote) {
      if (char === quote) quote = "";
      else current += char;
    } else if (char === '"' || char === "'") quote = char;
    else if (/\s/.test(char)) {
      if (current) { args.push(current); current = ""; }
    } else current += char;
  }
  if (current) args.push(current);
  const all = args.join(" ");
  return body.replace(/\$\{(\d+|ARGUMENTS|@):-([^}]*)\}|\$\{@:(\d+)(?::(\d+))?\}|\$(ARGUMENTS|@|\d+)/g,
    (_match, target, fallback, start, length, simple) => {
      if (target) return (target === "@" || target === "ARGUMENTS" ? all : args[Number(target) - 1]) || fallback;
      if (start) {
        const offset = Math.max(0, Number(start) - 1);
        return args.slice(offset, length ? offset + Number(length) : undefined).join(" ");
      }
      return simple === "@" || simple === "ARGUMENTS" ? all : args[Number(simple) - 1] ?? "";
    });
}

export default function compactPrompts(pi: ExtensionAPI) {
  let state: State | undefined;
  let pending: string | undefined;

  function restore(ctx: ExtensionContext) {
    pending = undefined;
    if (ctx.mode !== "tui") { uninstall(state); state = undefined; return; }
    const titles = new Map<string, string>();
    for (const entry of ctx.sessionManager.getBranch()) {
      if (entry.type !== "custom" || entry.customType !== ENTRY) continue;
      const data = entry.data as Metadata | undefined;
      if (typeof data?.hash === "string" && typeof data.invocation === "string") titles.set(data.hash, data.invocation);
    }
    state = { titles, views: new WeakMap(), ctx };
    install(state);
  }

  function remember(text: string, invocation: string) {
    if (!state) return;
    const data = { hash: hash(text), invocation };
    if (state.titles.get(data.hash) === invocation) return;
    state.titles.set(data.hash, invocation);
    // Hash and command only: durable UI metadata, no duplicate prompt in history
    // and no extra text in the model context. Branch changes restore this map.
    pi.appendEntry(ENTRY, data);
  }

  pi.on("session_start", (_event, ctx) => restore(ctx));
  pi.on("session_switch", (_event, ctx) => restore(ctx));
  pi.on("session_tree", (_event, ctx) => restore(ctx));
  pi.on("session_shutdown", () => { uninstall(state); state = undefined; });
  pi.on("input", (event, ctx) => {
    if (ctx.mode !== "tui") return;
    pending = undefined;
    const match = /^\/([^\s]+)(?:\s+([\s\S]*))?$/.exec(event.text);
    if (!match) return;
    const command = pi.getCommands().find(command => command.name === match[1] && command.source === "prompt");
    if (!command) return; // Ordinary text, skills and extension commands unchanged.
    if (!event.streamingBehavior) pending = event.text;
    else {
      try {
        const body = stripFrontmatter(readFileSync(command.sourceInfo.path, "utf8"));
        remember(expandQueued(body, match[2] ?? ""), event.text);
      } catch { /* If a loaded template was deleted/changed, fail open to native display. */ }
    }
  });
  pi.on("before_agent_start", event => {
    if (pending) remember(event.prompt, pending);
    pending = undefined;
  });
}
