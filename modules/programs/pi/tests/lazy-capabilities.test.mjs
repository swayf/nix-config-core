import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { registerLazyCapabilities } from "../extensions/lazy-capabilities/core.mjs";

function harness(t, { wikiTool = true, exclude = [], lazy = false, browserSkill = false, lazyBrowser = false } = {}) {
  const root = mkdtempSync(path.join(tmpdir(), "pi-lazy-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const assets = path.join(root, "assets");
  mkdirSync(assets);
  const skillFile = path.join(assets, "SKILL.md");
  writeFileSync(skillFile, "FULL_SKILL_BODY");
  const skillLink = path.join(root, "skill-link.md");
  symlinkSync(skillFile, skillLink);
  const instructions = path.join(root, "wiki-agent.md");
  writeFileSync(instructions, "WIKI_PROFILE_RULES");
  const skills = [{ name: "wiki-query", description: "CATALOG", filePath: skillLink },
    { name: "unrelated", description: "KEEP", filePath: path.join(root, "other.md") }];
  if (browserSkill) skills.push({ name: "terminal-browser", description: "BROWSER_CATALOG", filePath: path.join(root, "browser.md") });
  const contextFiles = [{ path: instructions, content: "WIKI_PROFILE_RULES" },
    { path: path.join(root, "AGENTS.md"), content: "PROJECT_RULES" }];
  const tools = new Map(["read", "bash", "codemode", ...(wikiTool ? ["wiki_ask"] : [])].map(name => [name, { name }]));
  const events = {}, entries = [];
  let active = ["read", "bash", "codemode", ...(wikiTool ? ["wiki_ask"] : [])];
  let branch = [];
  const ctx = { sessionManager: { getBranch: () => branch } };
  let scans = 0;
  if (lazy) {
    mkdirSync(path.join(root, "wiki-skills"));
    symlinkSync(instructions, path.join(root, "wiki-instructions.md"));
  }
  const browserDir = path.join(root, "browser-skills", "terminal-browser");
  if (lazyBrowser) mkdirSync(browserDir, { recursive: true });
  registerLazyCapabilities({
    getAllTools: () => [...tools.values()], getActiveTools: () => active,
    setActiveTools: names => { active = names.filter(name => tools.has(name) && !exclude.includes(name)); },
    appendEntry: (customType, data) => { const entry = { type: "custom", customType, data }; entries.push(entry); branch.push(entry); },
    registerTool: tool => { tools.set(tool.name, tool); active.push(tool.name); },
    on: (name, handler) => { events[name] = handler; },
  }, {
    parameters: {}, wikiRoots: [assets], wikiInstructionsPath: instructions,
    agentDir: root, browserSkillDirs: [browserDir],
    loadSkillsFromDir: ({ dir }) => {
      scans++;
      return { skills: [dir === browserDir
        ? { name: "terminal-browser", description: "LAZY_BROWSER_CATALOG", filePath: path.join(browserDir, "SKILL.md") }
        : { name: "wiki-lazy", description: "LAZY_CATALOG", filePath: skillFile }] };
    },
    buildSessionContext: entries => ({ messages: entries.filter(entry => entry.type === "message").map(entry => entry.message) }),
    formatSkillsForPrompt: skills => skills.filter(s => !s.disableModelInvocation).map(s => `${s.name} ${s.description} ${s.filePath}`).join("\n"),
  });
  const start = () => events.session_start({}, ctx);
  const before = (prompt = "ordinary") => {
    const event = { prompt, systemPromptOptions: { skills: [...skills], contextFiles: [...contextFiles] } };
    events.before_agent_start(event, ctx);
    return event.systemPromptOptions;
  };
  start();
  return { tools, events, entries, ctx, skills, contextFiles, start, before,
    active: () => active, scans: () => scans, branch: value => { branch = value; },
    enable: kind => tools.get(`${kind}_enable`).execute() };
}

test("new sessions advertise only loaders; wiki catalog and managed context are hidden", t => {
  const h = harness(t);
  assert.deepEqual(h.active(), ["read", "bash", "codemode_enable", "wiki_enable", "browser_enable"]);
  const options = h.before();
  assert.deepEqual(options.skills.map(s => s.name), ["unrelated"]);
  assert.deepEqual(options.contextFiles.map(f => f.content), ["PROJECT_RULES"]);
  assert.equal(h.tools.get("wiki_enable").exposure, "model-only");
});

test("activation preserves other tools and returns only the skill catalog, not full bodies", async t => {
  const h = harness(t);
  h.before();
  await h.enable("codemode");
  assert(h.active().includes("codemode"));
  assert(!h.active().includes("wiki_ask"));
  const output = await h.enable("wiki");
  assert(h.active().includes("wiki_ask"));
  assert.match(output.content[0].text, /wiki-query CATALOG/);
  assert.match(output.content[0].text, /WIKI_PROFILE_RULES/);
  assert.doesNotMatch(output.content[0].text, /FULL_SKILL_BODY/);
  assert.equal(h.entries.length, 2);
  await h.enable("wiki");
  assert.equal(h.entries.length, 2);
  assert.equal(h.before().skills.length, 2);
  assert.equal(h.before().contextFiles.length, 2);
});

test("resume and tree changes use branch-local state; new sessions reset it", async t => {
  const h = harness(t);
  h.before();
  await h.enable("codemode");
  await h.enable("wiki");
  const saved = [...h.entries];
  h.branch([]);
  h.events.session_tree({}, h.ctx);
  assert(!h.active().includes("codemode"));
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated"]);
  h.branch(saved);
  h.start();
  assert(h.active().includes("codemode"));
  assert(h.active().includes("wiki_ask"));
  assert.equal(h.before().skills.length, 2);
  h.branch([]);
  h.start();
  assert(!h.active().includes("codemode"));
});

test("pre-loader sessions retain tools declared by their current transcript", t => {
  const h = harness(t);
  h.branch([{ type: "message", message: { role: "system", toolsAdded: [{ name: "codemode" }, { name: "wiki_ask" }] } },
    { type: "message", message: { role: "system", toolsRemoved: [{ name: "codemode" }] } }]);
  h.start();
  assert(!h.active().includes("codemode"));
  assert(h.active().includes("wiki_ask"));
});

test("explicit native wiki skills enable wiki without hiding unrelated skills", t => {
  const h = harness(t);
  const options = h.before('<skill name="wiki-query" location="fixture">\nBODY\n</skill>');
  assert(h.active().includes("wiki_ask"));
  assert.equal(options.skills.length, 2);
  assert.equal(h.entries.length, 1);
});

test("explicit wiki-ask works in profiles without the full bundle", t => {
  const h = harness(t);
  h.skills.splice(0, 1);
  h.contextFiles.splice(0, 1);
  h.before('<skill name="wiki-ask" location="fixture">');
  assert(h.active().includes("wiki_ask"));
});

test("unavailable capabilities and CLI exclusions fail without recording activation", async t => {
  const h = harness(t, { wikiTool: false, exclude: ["codemode"] });
  h.skills.splice(0, 1);
  h.contextFiles.splice(0, 1);
  h.before();
  await assert.rejects(h.enable("wiki"), /No wiki tools/);
  await assert.rejects(h.enable("codemode"), /blocked/);
  assert.equal(h.entries.length, 0);
  h.tools.delete("codemode");
  await assert.rejects(h.enable("codemode"), /unavailable/);
});

test("wiki resources outside automatic discovery are read only after activation", async t => {
  const h = harness(t, { wikiTool: false, lazy: true });
  h.skills.splice(0, 1);
  h.contextFiles.splice(0, 1);
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated"]);
  assert.equal(h.scans(), 0);
  const output = await h.enable("wiki");
  assert.match(output.content[0].text, /LAZY_CATALOG/);
  assert.match(output.content[0].text, /WIKI_PROFILE_RULES/);
  assert.doesNotMatch(output.content[0].text, /FULL_SKILL_BODY/);
  assert.equal(h.scans(), 1);
  const options = h.before();
  assert.deepEqual(options.skills.map(s => s.name), ["unrelated", "wiki-lazy"]);
  assert.equal(options.contextFiles.length, 2);
  assert.equal(h.scans(), 1);
  h.branch([]);
  h.start();
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated"]);
  assert.equal(h.scans(), 1);
});

test("browser catalog is hidden until activation; other skills and tools are unchanged", async t => {
  const h = harness(t, { browserSkill: true });
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated"]);
  const active = h.active();
  const output = await h.enable("browser");
  assert.match(output.content[0].text, /terminal-browser BROWSER_CATALOG/);
  assert.doesNotMatch(output.content[0].text, /FULL_SKILL_BODY/);
  assert.match(output.content[0].text, /browser has not been opened/);
  assert.deepEqual(h.active(), active);
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated", "terminal-browser"]);
  await h.enable("browser");
  assert.equal(h.entries.length, 1);
  assert.equal(h.tools.get("browser_enable").exposure, "model-only");
  await h.enable("wiki");
  assert.deepEqual(h.before().skills.map(s => s.name), ["wiki-query", "unrelated", "terminal-browser"]);
});

test("browser activation follows the current branch and resets for new sessions", async t => {
  const h = harness(t, { browserSkill: true });
  h.before();
  await h.enable("browser");
  const saved = [...h.entries];
  h.branch([]);
  h.events.session_tree({}, h.ctx);
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated"]);
  h.branch(saved);
  h.start();
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated", "terminal-browser"]);
  h.branch([]);
  h.start();
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated"]);
});

test("native browser skill invocation activates browser but not wiki", t => {
  const h = harness(t, { browserSkill: true });
  const options = h.before('<skill name="terminal-browser" location="fixture">\nBODY\n</skill>');
  assert.deepEqual(options.skills.map(s => s.name), ["unrelated", "terminal-browser"]);
  assert(!h.active().includes("wiki_ask"));
  assert.deepEqual(h.entries.map(entry => entry.data.kind), ["browser"]);
});

test("browser directories outside native discovery are scanned only on activation or enabled resume", async t => {
  const h = harness(t, { lazyBrowser: true });
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated"]);
  assert.equal(h.scans(), 0);
  const output = await h.enable("browser");
  assert.match(output.content[0].text, /LAZY_BROWSER_CATALOG/);
  assert.doesNotMatch(output.content[0].text, /FULL_SKILL_BODY/);
  assert.equal(h.scans(), 1);
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated", "terminal-browser"]);
  await h.enable("browser");
  assert.equal(h.scans(), 1);
  const saved = [...h.entries];
  h.branch([]);
  h.start();
  h.before();
  assert.equal(h.scans(), 1);
  h.branch(saved);
  h.start();
  assert.deepEqual(h.before().skills.map(s => s.name), ["unrelated", "terminal-browser"]);
  assert.equal(h.scans(), 2);
});

test("missing browser skills fail without recording activation", async t => {
  const h = harness(t);
  h.before();
  await assert.rejects(h.enable("browser"), /terminal-browser skill is not installed/);
  assert.equal(h.entries.length, 0);
});

test("invocation-disabled browser skills stay out of the loader catalog", async t => {
  const h = harness(t, { browserSkill: true });
  h.skills.at(-1).disableModelInvocation = true;
  h.before();
  const output = await h.enable("browser");
  assert.doesNotMatch(output.content[0].text, /BROWSER_CATALOG/);
});

test("skill-only wiki profiles work; invocation-disabled skills are not advertised", async t => {
  const h = harness(t, { wikiTool: false });
  h.skills[0].disableModelInvocation = true;
  h.before();
  const output = await h.enable("wiki");
  assert.doesNotMatch(output.content[0].text, /CATALOG/);
  assert.equal(h.before().skills.length, 2);
});
