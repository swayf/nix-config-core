import { readFileSync, realpathSync, statSync, existsSync, lstatSync } from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

const TOOL = "handoff_context";
const ENTRY = "context-handoff";
const CONFIG = ".pi/context-handoff.json";
const MAX_HANDOFFS = 12; // Bound automatic continuation without a new user request.
const MAX_REQUEST_CHARS = 12000;
const digest = data => createHash("sha256").update(data).digest("hex");
const interactive = ctx => ctx.mode === "tui" || ctx.mode === "rpc";

// The nearest explicit configuration owns the policy, even when disabled or invalid.
export function findProjectRoot(cwd) {
  let dir = realpathSync(cwd);
  for (;;) {
    try {
      lstatSync(path.join(dir, CONFIG));
      return dir;
    } catch (error) {
      if (!["ENOENT", "ENOTDIR"].includes(error.code)) throw error;
    }
    const parent = path.dirname(dir);
    if (parent === dir || existsSync(path.join(dir, ".git"))) return undefined;
    dir = parent;
  }
}

function relativeName(name, directory = false) {
  const value = directory && typeof name === "string" ? name.slice(0, -1) : name;
  if (typeof value !== "string" || !value || value.length > 300 || value.includes("\\") ||
      value.includes("\0") || path.isAbsolute(value) ||
      value.split("/").some(part => !part || part === "." || part === ".." || part === ".git") ||
      (directory ? !name.endsWith("/") : !name.endsWith(".md"))) {
    throw new Error(`Use a bounded repository-relative ${directory ? "directory ending in /" : "Markdown path"}: ${name}`);
  }
  return value;
}

function canonicalName(root, name) {
  const file = realpathSync(path.join(root, name));
  const relative = path.relative(root, file).split(path.sep).join("/");
  if (!relative || relative === ".." || relative.startsWith("../") || path.isAbsolute(relative)) {
    throw new Error(`Path escapes project: ${name}`);
  }
  return { file, relative };
}

const withinRoots = (policy, name) => policy.stateRoots.some(root => name.startsWith(root));
const allowed = (policy, name) => policy.readFirst.includes(name) || withinRoots(policy, name);

function readDocument(policy, name) {
  relativeName(name);
  if (!allowed(policy, name)) throw new Error(`Document is outside configured reading/state paths: ${name}`);
  const { file, relative } = canonicalName(policy.root, name);
  relativeName(relative); // Canonical symlink targets must also be permitted Markdown, never .git.
  if (!allowed(policy, relative)) throw new Error(`Document escapes configured reading/state paths: ${name}`);
  const stat = statSync(file);
  if (!stat.isFile() || stat.size > 1024 * 1024) throw new Error(`Document is not a bounded regular file: ${name}`);
  return { path: name, sha256: digest(readFileSync(file)) };
}

export function readProjectPolicy(cwd) {
  const root = findProjectRoot(cwd);
  if (!root) throw new Error("Project not configured; add .pi/context-handoff.json and explicitly grant Pi trust.");
  const { file, relative } = canonicalName(root, CONFIG);
  if (relative !== CONFIG) throw new Error("Project handoff configuration must not be an aliased path.");
  const stat = statSync(file);
  if (!stat.isFile() || stat.size > 65536) throw new Error("Project handoff configuration must be a bounded regular file.");
  const bytes = readFileSync(file);
  const config = JSON.parse(bytes.toString("utf8"));
  if (!config || typeof config !== "object" || Array.isArray(config) ||
      Object.keys(config).some(key => !["version", "enabled", "readFirst", "stateRoots"].includes(key)) ||
      config.version !== 1 || typeof config.enabled !== "boolean") {
    throw new Error("Invalid project handoff configuration: require version 1, boolean enabled and known fields only.");
  }
  for (const key of ["readFirst", "stateRoots"]) {
    if (!Array.isArray(config[key]) || config[key].length < 1 || config[key].length > 8 ||
        new Set(config[key]).size !== config[key].length) throw new Error(`Invalid project handoff ${key}: use 1–8 distinct paths.`);
  }
  const policy = { root, sha256: digest(bytes), readFirst: config.readFirst, stateRoots: config.stateRoots };
  for (const name of policy.stateRoots) {
    const directory = relativeName(name, true);
    const target = canonicalName(root, directory);
    if (target.relative !== directory || !statSync(target.file).isDirectory()) {
      throw new Error(`State root must be a canonical project directory: ${name}`);
    }
  }
  for (const name of policy.readFirst) readDocument(policy, name);
  if (!config.enabled) throw new Error("Project handoff configuration is disabled.");
  return policy;
}

function handoffsSinceUser(branch) {
  const user = branch.findLastIndex(entry => entry.type === "message" && entry.message.role === "user");
  return branch.slice(user + 1).filter(entry => entry.type === "compaction" && entry.details?.kind === ENTRY);
}

function originalRequests(branch, buildSessionContext) {
  const latestCompaction = branch.findLast(entry => entry.type === "compaction");
  const saved = latestCompaction?.details?.kind === ENTRY ? latestCompaction.details.originalRequests : [];
  const requests = [...(saved ?? [])];
  for (const message of buildSessionContext(branch).messages) {
    if (message.role !== "user") continue;
    if (typeof message.content === "string") requests.push(message.content);
    else {
      if (message.content.some(block => block.type !== "text")) {
        throw new Error("The active user request has attachments. Record their relevant facts in documents before a manual restart; automatic handoff refused.");
      }
      requests.push(message.content.map(block => block.text).join("\n"));
    }
  }
  if (requests.join("\n").length > MAX_REQUEST_CHARS) {
    throw new Error("Active user requests exceed 12000 characters. Automatic handoff refused rather than silently truncating requirements.");
  }
  return requests;
}

function summaryFor(packet) {
  const bullets = values => values.map(value => `- ${value}`).join("\n");
  const { args, policy, originals, documents } = packet;
  return [
    "# Project development handoff",
    `Repository: ${policy.root}\nTask: ${args.task}`,
    `## Goal\n${args.goal}`,
    `## Completed checkpoint (agent-reported)\n${args.completed}`,
    `## Checks and limitations (agent-reported, not independently verified)\n${bullets(args.checks)}`,
    `## Constraints and permission boundaries\n${bullets(args.constraints)}`,
    "Handoff does not grant new permission, close a plan, pass a review, or authorise paid retries. Stop for required user decisions and respect concurrent work. Follow the project's process and communication rules in its instructions, and all task constraints above.",
    `## Read before continuing, in order\n${bullets([...new Set([...policy.readFirst, ...args.readFirst])])}`,
    `## Durable state documents at this boundary\n${bullets(documents.map(doc => `${doc.path} (SHA-256 ${doc.sha256})`))}`,
    `Project configuration: ${CONFIG} (SHA-256 ${policy.sha256})`,
    "Repository documents own current progress. Re-read them; do not overwrite newer concurrent changes with this checkpoint. Follow the project's state-ownership rules.",
    `## Next already-authorised action\n${args.next}`,
    ...(originals.length ? ["## Active user requests (verbatim)", ...originals.map((text, i) => `### Request ${i + 1}\n${text}`)] : []),
    "Continue the same task from the next action. Read the named documents first. Use another handoff only after a different completed, documented checkpoint, never to skip a blocker or extend scope.",
  ].join("\n\n");
}

export function registerContextHandoff(pi, { parameters, buildSessionContext }) {
  let policy;
  let enabled = false;
  let blocked = "Project not configured";
  let pending;

  function load(ctx) {
    if (!interactive(ctx)) throw new Error("Context handoff is disabled outside TUI/RPC sessions.");
    if (ctx.isProjectTrusted?.() !== true) throw new Error("Context handoff requires explicit Pi project trust.");
    return readProjectPolicy(ctx.cwd);
  }
  function syncTools() {
    const tools = pi.getActiveTools().filter(name => name !== TOOL);
    pi.setActiveTools(enabled ? [...tools, TOOL] : tools);
  }
  function restore(_event, ctx) {
    pending = undefined;
    policy = undefined;
    enabled = false;
    try {
      policy = load(ctx);
      const setting = ctx.sessionManager.getBranch().findLast(entry => entry.type === "custom" && entry.customType === ENTRY &&
        (entry.data?.root === undefined || entry.data.root === policy.root));
      enabled = setting?.data?.enabled !== false;
      blocked = enabled ? "" : "Disabled for this session branch";
    } catch (error) { blocked = error.message; }
    syncTools();
  }
  pi.on("session_start", restore);
  pi.on("session_tree", restore);
  pi.on("before_agent_start", restore);
  pi.on("session_shutdown", () => { pending = undefined; });
  pi.on("agent_settled", () => { pending = undefined; });

  pi.registerCommand("handoff-context", {
    description: "Project context handoff: status | on | off (opt-in, trusted TUI/RPC sessions only)",
    handler: async (args, ctx) => {
      const action = args.trim() || "status";
      if (!["status", "on", "off"].includes(action)) throw new Error("Usage: /handoff-context [status|on|off]");
      restore({}, ctx);
      if (action !== "status") {
        policy = load(ctx); // /on never substitutes for project opt-in or trust.
        enabled = action === "on";
        pending = undefined;
        blocked = enabled ? "" : "Disabled for this session branch";
        pi.appendEntry(ENTRY, { enabled, root: policy.root });
        syncTools();
      }
      ctx.ui.notify(`Context handoff: ${enabled ? "on" : "off"}; ${policy ? `project ${policy.root}` : blocked}; maximum ${MAX_HANDOFFS} resets per user request.`, "info");
    },
  });

  pi.registerTool({
    name: TOOL, label: "Context handoff", exposure: "model-only",
    description: `Replace conversation history with a focused project handoff and automatically continue the SAME authorised task. Requires explicit project opt-in, Pi trust and TUI/RPC mode. Use after a completed checkpoint when prior exploration is no longer useful. First persist verified progress, checks, blockers, limitations, constraints and next step in the existing project-designated state documents. Follow repository instructions; include all user constraints and explicit permission limits. Call this tool ALONE in its assistant turn, after other tools finish. Do not call when finished, blocked, awaiting approval, or when the user asked to stop after this step. nextAuthorized and statePersisted attest these conditions; paths/configuration/hashes are checked but these attestations are not independently verified. The reset occurs at turn_end. At most ${MAX_HANDOFFS} resets per user request; no extra summarisation model call.`,
    promptSnippet: "After a completed, documented checkpoint, use handoff_context alone to replace old history and continue the same authorised task. Never bypass a user decision or stop boundary; obey project state-ownership rules.",
    parameters,
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false },
    async execute(id, args, signal, _onUpdate, ctx) {
      if (!enabled || !policy) throw new Error(`Context handoff is disabled: ${blocked}`);
      const current = load(ctx);
      if (current.root !== policy.root || current.sha256 !== policy.sha256) throw new Error("Project configuration changed; reload/status before a handoff.");
      if (signal?.aborted || ctx.signal?.aborted) throw new Error("Handoff cancelled.");
      if (pending) throw new Error("A handoff is already pending; call it alone.");
      if (ctx.hasPendingMessages()) throw new Error("Queued user input must be processed before a handoff.");
      if (args.statePersisted !== true || args.nextAuthorized !== true) throw new Error("Persist state and obtain any required permission before a handoff.");
      const branch = ctx.sessionManager.getBranch();
      const recent = handoffsSinceUser(branch);
      if (recent.length >= MAX_HANDOFFS) throw new Error("Automatic handoff limit reached. Finish or stop and report; a new user request is needed for more resets.");
      const signature = digest(JSON.stringify([args.task.trim(), args.next.trim()]));
      if (recent.some(entry => entry.details.signature === signature)) throw new Error("This next action was already handed off; refusing a continuation loop.");
      if (!args.stateFiles.some(name => typeof name === "string" && withinRoots(policy, name) &&
          withinRoots(policy, canonicalName(policy.root, relativeName(name)).relative))) {
        throw new Error("stateFiles must name an existing task state document in configured stateRoots.");
      }
      const names = [...new Set([...policy.readFirst, ...args.stateFiles, ...args.readFirst])];
      const documents = names.map(name => readDocument(policy, name));
      const originals = originalRequests(branch, buildSessionContext);
      const packet = { id, args: structuredClone(args), policy: structuredClone(policy), documents, originals, signature };
      const summary = summaryFor(packet);
      if (summary.length > 24000) throw new Error("Handoff is too large; shorten the packet without dropping constraints.");
      pending = { ...packet, summary };
      return { content: [{ type: "text", text: "Handoff prepared. At this turn's safe boundary Pi will verify the project configuration, trust and document hashes, replace old conversation context, and continue from the named next action. No files changed; no new permission granted." }], details: { task: args.task, next: args.next, documents } };
    },
  });

  pi.on("turn_end", (event, ctx) => {
    if (!pending) return;
    const packet = pending;
    pending = undefined; // One-shot; failures never leave a reset for a later turn.
    const calls = event.message.content?.filter(block => block.type === "toolCall") ?? [];
    let reason;
    if (event.outcome !== "completed" || ctx.signal?.aborted) reason = "turn aborted or failed";
    else if (!enabled || calls.length !== 1 || calls[0].id !== packet.id || calls[0].name !== TOOL) reason = "handoff must be the only tool in the assistant turn";
    else if (event.toolResults.length !== 1 || event.toolResults[0].toolCallId !== packet.id || event.toolResults[0].isError) reason = "handoff result was not successful";
    else if (ctx.hasPendingMessages() || event.context.pendingMessages.length) reason = "new input is queued";
    else if (event.entries.length) reason = "another extension proposed context changes at this boundary";
    else {
      try {
        const current = load(ctx);
        if (current.root !== packet.policy.root || current.sha256 !== packet.policy.sha256) throw new Error("project configuration changed");
        for (const doc of packet.documents) {
          if (readDocument(current, doc.path).sha256 !== doc.sha256) throw new Error(`document changed: ${doc.path}`);
        }
      } catch (error) { reason = error.message; }
    }
    if (reason) {
      const content = `Context handoff cancelled: ${reason}. Old context is intact. Re-read state and process user input before considering another handoff.`;
      ctx.ui.notify(content, "warning");
      return { entries: [{ type: "custom_message", customType: ENTRY, content, display: true }] };
    }
    ctx.ui.notify(`Context handed off: ${packet.args.task} → ${packet.args.next}`, "info");
    return {
      entries: [{ type: "compaction", summary: packet.summary, firstKeptEntryId: null,
        details: { kind: ENTRY, task: packet.args.task, next: packet.args.next, signature: packet.signature,
          originalRequests: packet.originals, documents: packet.documents, configuration: { root: packet.policy.root, sha256: packet.policy.sha256 } } }],
      continue: true,
    };
  });
}
