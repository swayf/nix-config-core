import type { ExtensionAPI, ToolRenderContext, ToolRenderers } from "@earendil-works/pi-coding-agent";
import { Text, truncateToWidth, visibleWidth, type Component } from "@earendil-works/pi-tui";

type ToolTheme = Parameters<NonNullable<ToolRenderers["renderCall"]>>[1];

// Reproduce Pi's standard tool backgrounds without the box's extra rows.
function backgroundLine(line: string, width: number, theme: ToolTheme, context: ToolRenderContext): string {
  const clipped = truncateToWidth(line, width);
  const padded = clipped + " ".repeat(Math.max(0, width - visibleWidth(clipped)));
  const color = context.isPartial ? "toolPendingBg" : context.isError ? "toolErrorBg" : "toolSuccessBg";
  return theme.bg(color, padded);
}

// Keep terminal controls and multiline arguments out of the compact header.
function inline(value: unknown): string {
  return typeof value === "string"
    ? value.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "").replace(/[\x00-\x1f\x7f-\x9f]/g, " ").replace(/\s+/g, " ").trim()
    : "";
}

function summary(name: string, args: any): string {
  const path = inline(args?.file_path ?? args?.path);
  if (name === "bash" || name === "powershell") return inline(args?.command);
  if (name === "find" || name === "grep") return [inline(args?.pattern), path || "."].filter(Boolean).join(" · ");
  if (name === "ls") return path || ".";
  if (name === "read" && (args?.offset != null || args?.limit != null)) {
    const start = args.offset ?? 1;
    return `${path}:${start}${args.limit != null ? `-${start + args.limit - 1}` : ""}`;
  }
  if (path) return path;
  // Third-party/MCP tools use different argument names. Prefer a useful target,
  // never a full JSON payload (which can include page content or written code).
  for (const key of ["url", "urls", "query", "queries", "question", "pattern", "command", "responseId"]) {
    const value = args?.[key];
    const detail = Array.isArray(value) ? value.map(inline).filter(Boolean).join(" · ") : inline(value);
    if (detail) return detail;
  }
  return "";
}

// Pi owns expansion state and mouse hit regions. Do not register a shortcut or
// a MouseRegion here: native clicks toggle just this call, Ctrl+O toggles all.
export default function compactTools(pi: ExtensionAPI) {
  pi.registerToolRenderer((name, next): ToolRenderers | undefined => {
    // Include web tools, MCP and tools without custom renderers, not just the
    // built-ins. Keep existing renderers for the expanded view in either slot.
    const original = next() ?? {};
    const renderCall: NonNullable<ToolRenderers["renderCall"]> = original.renderCall ?? ((args, theme) =>
      new Text(`${theme.fg("toolTitle", theme.bold(name))}\n${theme.fg("toolOutput", JSON.stringify(args ?? {}, null, 2))}`, 0, 0));
    const renderResult: NonNullable<ToolRenderers["renderResult"]> = original.renderResult ?? ((result, _options, theme) =>
      new Text(theme.fg("toolOutput", result.content
        .filter(block => block.type === "text")
        .map(block => block.text).join("\n")), 0, 0));

    function originalContext(context: ToolRenderContext, slot: "call" | "result") {
      const state = context.state.compactTools ??= { state: {}, call: undefined, result: undefined };
      return { state, context: { ...context, state: state.state, lastComponent: state[slot] } };
    }

    return {
      renderShell: "self",
      renderCall(args, theme, context): Component {
        if (!context.expanded) {
          return {
            invalidate() {},
            render(width) {
              const icon = theme.fg(context.isError ? "error" : "muted", "▸");
              const title = theme.fg("toolTitle", theme.bold(name));
              const detail = theme.fg("accent", summary(name, args));
              const error = context.isError ? theme.fg("error", " [error]") : "";
              return [backgroundLine(`${icon} ${title}${detail ? ` ${detail}` : ""}${error}`, width, theme, context)];
            },
          };
        }
        const saved = originalContext(context, "call");
        const component = renderCall(args, theme, saved.context);
        saved.state.call = component;
        return {
          invalidate: () => component.invalidate(),
          render(width) {
            const lines = component.render(Math.max(0, width - 2));
            return lines.map((line, index) => backgroundLine(
              `${index === 0 ? theme.fg("muted", "▾ ") : "  "}${line}`, width, theme, context,
            ));
          },
          handleMouse: event => component.handleMouse?.({ ...event, x: event.x - 2, width: Math.max(0, event.width - 2) }),
        };
      },
      renderResult(result, options, theme, context) {
        if (!options.expanded) return { render: () => [], invalidate() {} };
        const saved = originalContext(context, "result");
        const component = renderResult(result, options, theme, saved.context);
        saved.state.result = component;
        return {
          invalidate: () => component.invalidate(),
          render: width => component.render(width).map(line => backgroundLine(line, width, theme, context)),
          handleMouse: event => component.handleMouse?.(event),
        };
      },
    };
  });
}
