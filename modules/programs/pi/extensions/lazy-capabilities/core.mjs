import { existsSync, readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { homedir } from "node:os";

const ENTRY = "lazy-capabilities";
const LOADERS = ["codemode_enable", "wiki_enable", "browser_enable"];
const isBrowserSkill = skill => skill.name === "terminal-browser";

function realPath(file) {
  try { return realpathSync(file); } catch { return undefined; }
}

function within(file, directory) {
  const relative = path.relative(directory, file);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

// Replay declarations, not tool-call text: an abandoned branch must not enable tools.
function declaredTools(messages) {
  let names;
  for (const message of messages) {
    if (message.role !== "system" || !("toolsAdded" in message || "toolsRemoved" in message)) continue;
    names ??= new Set();
    for (const tool of message.toolsRemoved ?? []) names.delete(tool.name);
    for (const tool of message.toolsAdded ?? []) names.add(tool.name);
  }
  return names;
}

export function registerLazyCapabilities(pi, {
  parameters, formatSkillsForPrompt, buildSessionContext,
  loadSkillsFromDir, agentDir,
  browserSkillDirs = [agentDir && path.join(agentDir, "skills", "terminal-browser"),
    path.join(homedir(), ".agents", "skills", "terminal-browser")].filter(Boolean),
  wikiRoots = ["/share/obsidian-wiki/skills", "/etc/agents/pi-skills/qmd-cli"],
  wikiInstructionsPath = "/etc/agents/pi-profiles/wiki/AGENTS.md",
}) {
  let enabled = { codemode: false, wiki: false, browser: false };
  let browserSkills = [];
  let wikiSkills = [];
  let wikiContext = [];
  let lazySkills;
  let lazyContext;
  let lazyBrowserSkills;
  function loadBrowserResources() {
    if (lazyBrowserSkills === undefined) {
      lazyBrowserSkills = [];
      for (const dir of browserSkillDirs) {
        if (!existsSync(dir)) continue;
        lazyBrowserSkills = loadSkillsFromDir({ dir, source: "user" }).skills.filter(isBrowserSkill);
        if (lazyBrowserSkills.length) break;
      }
    }
    browserSkills = [...new Map([...lazyBrowserSkills, ...browserSkills].map(skill => [skill.name, skill])).values()];
  }
  const lazySkillsDir = agentDir && path.join(agentDir, "wiki-skills");
  const lazyInstructions = agentDir && path.join(agentDir, "wiki-instructions.md");
  function loadWikiResources() {
    lazySkills ??= lazySkillsDir && existsSync(lazySkillsDir)
      ? loadSkillsFromDir({ dir: lazySkillsDir, source: "user" }).skills : [];
    lazyContext ??= lazyInstructions && existsSync(lazyInstructions)
      ? [{ path: lazyInstructions, content: readFileSync(lazyInstructions, "utf8") }] : [];
    wikiSkills = [...new Map([...wikiSkills, ...lazySkills].map(skill => [skill.name, skill])).values()];
    wikiContext = [...new Map([...wikiContext, ...lazyContext].map(file => [file.path, file])).values()];
  }
  const roots = wikiRoots.map(realPath).filter(Boolean);
  const instructionsPath = realPath(wikiInstructionsPath);
  const isWikiSkill = skill => {
    const file = realPath(skill.filePath);
    return file && roots.some(root => within(file, root));
  };
  const isWikiContext = file => instructionsPath && realPath(file.path) === instructionsPath;
  const wikiTools = () => pi.getAllTools().filter(tool => tool.name === "wiki_ask").map(tool => tool.name);

  function activate(kind) {
    if (kind === "wiki") loadWikiResources();
    if (kind === "browser") loadBrowserResources();
    const names = kind === "codemode" ? ["codemode"] : kind === "wiki" ? wikiTools() : [];
    if (kind === "browser" && !browserSkills.length) {
      throw new Error("The terminal-browser skill is not installed or enabled in this profile.");
    }
    const registered = new Set(pi.getAllTools().map(tool => tool.name));
    if (names.some(name => !registered.has(name))) throw new Error("Codemode is unavailable. Check the built-in codemode extension and this session's tool exclusions.");
    if (kind === "wiki" && !names.length && !wikiSkills.length && !wikiContext.length) {
      throw new Error("No wiki tools or obsidian-wiki skills are installed in this profile.");
    }
    const previous = pi.getActiveTools();
    pi.setActiveTools([...new Set([...previous, ...names])]);
    if (names.some(name => !pi.getActiveTools().includes(name))) {
      pi.setActiveTools(previous);
      throw new Error("Activation blocked by this session's tool selection or exclusions.");
    }
    if (!enabled[kind]) {
      enabled[kind] = true;
      pi.appendEntry(ENTRY, { kind });
    }
    return names;
  }

  function restore(_event, ctx) {
    enabled = { codemode: false, wiki: false, browser: false };
    browserSkills = [];
    wikiSkills = [];
    wikiContext = [];
    lazySkills = undefined;
    lazyContext = undefined;
    lazyBrowserSkills = undefined;
    const branch = ctx.sessionManager.getBranch();
    for (const entry of branch) {
      if (entry.type === "custom" && entry.customType === ENTRY && Object.hasOwn(enabled, entry.data?.kind)) {
        enabled[entry.data.kind] = true;
      }
    }
    // Preserve tools already declared in resumed sessions, including pre-loader sessions.
    const declared = declaredTools(buildSessionContext(branch).messages);
    if (declared?.has("codemode")) enabled.codemode = true;
    if (declared?.has("wiki_ask")) enabled.wiki = true;
    const optional = new Set(["codemode", ...wikiTools()]);
    const active = pi.getActiveTools().filter(name => !optional.has(name));
    pi.setActiveTools([...new Set([
      ...active, ...LOADERS,
      ...(enabled.codemode ? ["codemode"] : []),
      ...(enabled.wiki ? wikiTools() : []),
    ])]);
  }

  pi.on("session_start", restore);
  pi.on("session_tree", restore);
  pi.on("before_agent_start", (event) => {
    const options = event.systemPromptOptions;
    wikiSkills = options.skills.filter(isWikiSkill);
    browserSkills = options.skills.filter(isBrowserSkill);
    wikiContext = options.contextFiles.filter(isWikiContext);
    // Native /skill commands are expanded before this event. Keep explicit use working.
    const requestedSkill = event.prompt.match(/^<skill name="([^"]+)"/u)?.[1];
    if (!enabled.wiki && (requestedSkill === "wiki-ask" || wikiSkills.some(skill => skill.name === requestedSkill))) activate("wiki");
    if (!enabled.browser && browserSkills.some(skill => skill.name === requestedSkill)) activate("browser");
    if (!enabled.browser) options.skills = options.skills.filter(skill => !isBrowserSkill(skill));
    else {
      loadBrowserResources();
      options.skills = [...new Map([...options.skills, ...browserSkills].map(skill => [skill.name, skill])).values()];
    }
    if (!enabled.wiki) {
      options.skills = options.skills.filter(skill => !isWikiSkill(skill));
      options.contextFiles = options.contextFiles.filter(file => !isWikiContext(file));
    } else {
      loadWikiResources();
      options.skills = [...new Map([...options.skills, ...wikiSkills].map(skill => [skill.name, skill])).values()];
      options.contextFiles = [...new Map([...options.contextFiles, ...wikiContext].map(file => [file.path, file])).values()];
    }
  });

  for (const kind of ["codemode", "wiki", "browser"]) {
    const name = `${kind}_enable`;
    const description = kind === "codemode"
      ? "Enable codemode for JavaScript tool orchestration, parallel calls, output filtering, classifiers and image generation. Does not execute a script. Available on the next model request."
      : kind === "wiki"
        ? "Enable this profile's wiki tools and reveal its installed obsidian-wiki skill catalog. Use before wiki retrieval, capture, ingest, updates or maintenance. Does not read a vault, index, write or commit."
        : "Reveal the terminal-browser skill for browsing, showing pages beside the conversation and browser automation. Does not open a browser.";
    pi.registerTool({
      name, label: kind === "codemode" ? "Enable Codemode" : kind === "wiki" ? "Enable Wiki" : "Enable Browser",
      exposure: "model-only", description, parameters,
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
      promptSnippet: kind === "codemode"
        ? "Call to enable JavaScript tool orchestration when useful; codemode appears on the next request."
        : kind === "wiki"
          ? "Call before wiki work to reveal the installed wiki tools and skill catalog; then read the applicable SKILL.md."
          : "Call before browser work to reveal the terminal-browser skill; then read its SKILL.md.",
      async execute() {
        const names = activate(kind);
        const parts = [`Enabled ${kind}${names.length ? `: ${names.join(", ")}` : ""}. Remains enabled on this session branch.`];
        if (kind === "wiki") {
          // Tool results reach the very next request. No forced prompt or queued user turn.
          // Subsequent user turns advertise these skills in the normal structured prompt.
          const catalog = formatSkillsForPrompt(wikiSkills, "read").trim();
          if (catalog) parts.push(catalog);
          for (const file of wikiContext) parts.push(`Wiki profile instructions (${file.path}):\n${file.content}`);
          parts.push("Only the catalog is loaded; read the relevant SKILL.md before using a workflow. CLI tools remain accessible through bash.");
        }
        if (kind === "browser") {
          const catalog = formatSkillsForPrompt(browserSkills, "read").trim();
          if (catalog) parts.push(catalog);
          parts.push("Read SKILL.md before using the browser. The browser has not been opened.");
        }
        return { content: [{ type: "text", text: parts.join("\n\n") }], details: { capability: kind, enabled: names } };
      },
    });
  }
}
