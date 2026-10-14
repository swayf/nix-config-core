import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { mkdtemp, mkdir, readFile, writeFile, rm, symlink } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import { test } from "node:test";
import { buildInvocation, createCollector, findProjectConfig, loadConfig, queryWiki, runProcess, skillBody, workerPrompt } from "./core.mjs";

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), "wiki-ask-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const project = path.join(root, "project"), vault = path.join(root, "vault");
  await mkdir(path.join(project, ".pi"), { recursive: true });
  await mkdir(path.join(project, "src", "nested"), { recursive: true });
  await mkdir(path.join(project, ".git"));
  await mkdir(vault);
  const file = path.join(project, ".pi", "wiki-ask.json");
  const config = { vault: "../vault", model: "fixture/model", qmdCollection: "test-wiki" };
  await writeFile(file, JSON.stringify(config));
  return { root, project, vault, file, config };
}

const finalEvent = (text, options = {}) => ({
  type: "message_end", message: {
    role: "assistant", content: [{ type: "text", text }], stopReason: "stop",
    usage: { input: 10, output: 5, cacheRead: 2, cacheWrite: 1, totalTokens: 18, cost: { total: 0.01 } },
    ...options,
  },
});
const mock = (code) => ({ command: process.execPath, args: ["-e", code], cwd: tmpdir(), env: process.env });
const emit = (events) => `process.stdout.write(${JSON.stringify(events.map((event) => JSON.stringify(event)).join("\n") + "\n")});`;

test("nested config resolves the vault and explicit caller model default", async (t) => {
  const f = await fixture(t);
  delete f.config.model;
  await writeFile(f.file, JSON.stringify(f.config));
  const config = await loadConfig(path.join(f.project, "src", "nested"), { defaultModel: "fixture/fallback" });
  assert.equal(config.file, f.file);
  assert.equal(config.vault, f.vault);
  assert.equal(config.model, "fixture/fallback");
  assert.equal(config.thinking, "low");
  assert.equal(config.timeoutSeconds, 300);
});

test("project model overrides the caller default", async (t) => {
  const f = await fixture(t);
  assert.equal((await loadConfig(f.project, { defaultModel: "fixture/fallback" })).model, "fixture/model");
});

test("missing project and caller model fails without environment/profile fallback", async (t) => {
  const f = await fixture(t);
  delete f.config.model;
  await writeFile(f.file, JSON.stringify(f.config));
  const keys = ["PI_MODEL", "WIKI_ASK_DEFAULT_MODEL"];
  const previous = keys.map(key => process.env[key]);
  t.after(() => keys.forEach((key, i) => {
    if (previous[i] === undefined) delete process.env[key];
    else process.env[key] = previous[i];
  }));
  for (const key of keys) process.env[key] = "fixture/ambient";
  await assert.rejects(loadConfig(f.project), /No wiki-ask model configured/);
});

test("invalid caller default fails when selected", async (t) => {
  const f = await fixture(t);
  delete f.config.model;
  await writeFile(f.file, JSON.stringify(f.config));
  for (const defaultModel of [null, "", "model-only", "fixture/model with spaces", "fixture/model\n", 7]) {
    await assert.rejects(loadConfig(f.project, { defaultModel }));
  }
});

test("invalid explicit project model never falls back to the caller", async (t) => {
  const f = await fixture(t);
  for (const model of [null, "", "invalid", "fixture/model\n"]) {
    await writeFile(f.file, JSON.stringify({ ...f.config, model }));
    await assert.rejects(loadConfig(f.project, { defaultModel: "fixture/fallback" }));
  }
});

test("не наследует настройку за границей репозитория", async (t) => {
  const f = await fixture(t);
  await rm(f.file);
  await mkdir(path.join(f.root, ".pi"));
  await writeFile(path.join(f.root, ".pi", "wiki-ask.json"), JSON.stringify(f.config));
  await assert.rejects(findProjectConfig(f.project), /No .pi\/wiki-ask.json found/);
});

test("предпочитает ближайший вложенный config", async (t) => {
  const f = await fixture(t);
  const sub = path.join(f.project, "src");
  await mkdir(path.join(sub, ".pi"));
  const file = path.join(sub, ".pi", "wiki-ask.json");
  await writeFile(file, JSON.stringify({ vault: f.vault, model: "provider/other" }));
  assert.equal((await loadConfig(path.join(sub, "nested"))).model, "provider/other");
});

test("канонизирует симлинк vault", async (t) => {
  const f = await fixture(t);
  await symlink(f.vault, path.join(f.root, "alias"));
  await writeFile(f.file, JSON.stringify({ vault: "../alias", model: "fixture/model" }));
  assert.equal((await loadConfig(f.project)).vault, f.vault);
});

test("неправильная настройка завершается ошибкой без fallback", async (t) => {
  const f = await fixture(t);
  const cases = [null, [], {}, { ...f.config, extra: true }, { ...f.config, vault: "missing" },
    { ...f.config, model: "invalid" }, { ...f.config, thinking: "strange" },
    { ...f.config, qmdCollection: "test; rm" }, { ...f.config, qmdSearchMode: "strange" },
    { ...f.config, timeoutSeconds: 0 }, { ...f.config, timeoutSeconds: 1.5 }];
  for (const config of cases) {
    await writeFile(f.file, JSON.stringify(config));
    await assert.rejects(loadConfig(f.project));
  }
  await writeFile(f.file, "{");
  await assert.rejects(loadConfig(f.project), /Invalid JSON/);
});

test("без qmdCollection семантический поиск явно отключён", async (t) => {
  const f = await fixture(t);
  await writeFile(f.file, JSON.stringify({ vault: f.vault, model: "fixture/model" }));
  const config = await loadConfig(f.project);
  const invocation = buildInvocation(config, "/skills", "/worker.md", "question");
  assert.equal(invocation.env.QMD_WIKI_COLLECTION, "");
  assert.match(workerPrompt(config), /QMD was skipped/);
});

test("дочерняя pi изолирована от расширений, беседы и случайной коллекции", async (t) => {
  const f = await fixture(t);
  const config = await loadConfig(f.project);
  const question = '-arg\n@other "quote" $(touch /tmp/not-created)';
  const invocation = buildInvocation(config, "/skills", "/worker.md", question);
  assert.equal(invocation.command, "pi");
  assert.equal(invocation.cwd, f.vault);
  for (const flag of ["--no-session", "--no-extensions", "--no-skills", "--no-context-files", "--no-approve"]) assert.ok(invocation.args.includes(flag));
  assert.equal(invocation.args.at(-2), "--");
  assert.equal(invocation.args.at(-1), `/skill:wiki-query ${JSON.stringify(question)}`);
  assert.equal(invocation.env.OBSIDIAN_VAULT_PATH, f.vault);
  assert.equal(invocation.env.QMD_WIKI_COLLECTION, "test-wiki");
  assert.equal(invocation.env.QMD_PAPERS_COLLECTION, "");
  assert.equal(invocation.env.PI_SESSION_FILE, undefined);
  assert.ok(!invocation.args.includes("--continue"));
  assert.match(workerPrompt(config), /do not perform Step 6/);
});

test("agent instructions are English and require document links for each finding", async () => {
  const instructions = await readFile(new URL("./SKILL.md", import.meta.url), "utf8");
  const prompt = workerPrompt({ vault: "/vault", qmdCollection: "wiki", qmdSearchMode: "balanced" });
  assert.doesNotMatch(instructions + prompt, /[А-Яа-яЁё]/u);
  assert.match(instructions, /without translating or\nrephrasing/);
  assert.match(instructions, /preserving the document links alongside the findings/);
  assert.match(prompt, /language of the user's question/);
  assert.match(prompt, /reference data, not instructions/);
  assert.match(prompt, /source document next to each finding/);
  assert.match(prompt, /exact vault-relative file path/);
  assert.match(prompt, /documents you actually read/);
  assert.match(prompt, /explicitly mark it as unverified/);
});

test("skill загружается без frontmatter", () => {
  assert.equal(skillBody("---\nname: test\n---\n\n# Инструкции\n"), "# Инструкции");
});

test("collector возвращает последний ответ и суммирует usage, не историю", () => {
  const progress = [];
  const collector = createCollector((text) => progress.push(text));
  collector.accept(finalEvent("Промежуточный ответ"));
  collector.accept({ type: "tool_execution_start", toolName: "read" });
  collector.accept(finalEvent("Итог"));
  collector.accept({ type: "agent_end" });
  assert.equal(collector.state.settled, false);
  collector.accept({ type: "agent_settled" });
  assert.equal(collector.state.answer, "Итог");
  assert.equal(collector.state.usage.input, 20);
  assert.equal(collector.state.usage.cost.total, 0.02);
  assert.equal(collector.state.toolCalls, 1);
  assert.deepEqual(progress, ["Wiki search: read"]);
  assert.equal(collector.state.messages, undefined);
});

test("JSONL выдерживает Unicode-разделители, чанки и несколько текстовых блоков", async () => {
  const event = finalEvent("Русский текст\u2028с разделителем");
  event.message.content.push({ type: "text", text: "Второй блок" });
  const output = JSON.stringify(event) + "\n" + JSON.stringify({ type: "agent_settled" }) + "\n";
  const code = `const b=Buffer.from(${JSON.stringify(output)}); process.stdout.write(b.subarray(0,35)); setTimeout(()=>process.stdout.write(b.subarray(35)),10);`;
  const result = await runProcess(mock(code), { timeoutMs: 2000 });
  assert.equal(result.answer, "Русский текст\u2028с разделителем\nВторой блок");
});

test("ошибка модели при коде выхода 0 не считается успехом", async () => {
  await assert.rejects(runProcess(mock(emit([finalEvent("", { stopReason: "error", errorMessage: "provider failed" }), { type: "agent_settled" }])), { timeoutMs: 2000 }), /provider failed/);
});

test("незавершённый и пустой ответ не считаются успехом", async () => {
  await assert.rejects(runProcess(mock(emit([finalEvent("partial")])), { timeoutMs: 2000 }), /completed answer/);
  await assert.rejects(runProcess(mock(emit([finalEvent(""), { type: "agent_settled" }])), { timeoutMs: 2000 }), /completed answer/);
});

test("ненулевой код выхода и stderr возвращаются как ошибка", async () => {
  await assert.rejects(runProcess(mock('process.stderr.write("startup failed"); process.exit(2);'), { timeoutMs: 2000 }), /startup failed/);
});

test("некорректный JSONL не игнорируется", async () => {
  await assert.rejects(runProcess(mock('process.stdout.write("not json\\n");'), { timeoutMs: 2000 }), /invalid JSONL/);
});

test("ошибка запуска дочернего процесса обработана", async () => {
  await assert.rejects(runProcess({ command: "/no/such/executable", args: [], cwd: tmpdir(), env: process.env }, { timeoutMs: 2000 }), /Failed to start/);
});

test("таймаут завершает процесс, игнорирующий SIGTERM", async () => {
  await assert.rejects(runProcess(mock('process.on("SIGTERM",()=>{}); setInterval(()=>{},1000);'), { timeoutMs: 100 }), /timed out/);
});

test("queryWiki запускает CLI с правильной настройкой и удаляет временный prompt", async (t) => {
  const f = await fixture(t);
  const bin = path.join(f.root, "bin");
  await mkdir(bin);
  const oldPath = process.env.PATH;
  t.after(() => { process.env.PATH = oldPath; });
  process.env.PATH = `${bin}${path.delimiter}${oldPath}`;
  const cli = `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
const promptFile = args[args.indexOf('--append-system-prompt')+1];
const answer = JSON.stringify({cwd:process.cwd(), vault:process.env.OBSIDIAN_VAULT_PATH, collection:process.env.QMD_WIKI_COLLECTION, promptFile, prompt:fs.readFileSync(promptFile,'utf8'), args});
process.stdout.write(JSON.stringify({type:'message_end',message:{role:'assistant',content:[{type:'text',text:answer}],stopReason:'stop'}})+'\\n');
process.stdout.write(JSON.stringify({type:'agent_settled'})+'\\n');
`;
  await writeFile(path.join(bin, "pi"), cli, { mode: 0o700 });
  const result = await queryWiki(await loadConfig(f.project), "Тестовый вопрос", { skillsDir: "/skills" });
  const answer = JSON.parse(result.answer);
  assert.equal(answer.cwd, f.vault);
  assert.equal(answer.vault, f.vault);
  assert.equal(answer.collection, "test-wiki");
  assert.match(answer.prompt, /Do not override the vault/);
  assert.equal(answer.args.at(-1), '/skill:wiki-query "Тестовый вопрос"');
  await assert.rejects(import('node:fs/promises').then(fs => fs.access(answer.promptFile)), { code: "ENOENT" });
});

// Opt-in native-Pi contract; not part of the source-only/default Node check.
if (process.env.WIKI_ASK_TEST_CLI) test("wiki_ask and lazy skill command work in caller-pinned Pi", async (t) => {
  const f = await fixture(t);
  const bin = path.join(f.root, "bin");
  await mkdir(bin);
  const worker = `#!/usr/bin/env node
const message = {role:'assistant', content:[{type:'text',text:'Тестовый ответ [[concepts/test]]'}], stopReason:'stop', usage:{input:1,output:2,cacheRead:0,cacheWrite:0,totalTokens:3,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
console.log(JSON.stringify({type:'message_end',message}));
console.log(JSON.stringify({type:'agent_settled'}));
`;
  await writeFile(path.join(bin, "pi"), worker, { mode: 0o700 });
  const probe = path.join(f.root, "probe.ts");
  const index = process.env.PI_WIKI_ASK_EXTENSION ?? fileURLToPath(new URL("./index.ts", import.meta.url));
  await writeFile(probe, `import wikiAsk from ${JSON.stringify(index)};
import assert from 'node:assert/strict';
import {parseSkillBlock, SkillInvocationMessageComponent} from '@earendil-works/pi-coding-agent';
export default function(pi) {
let tool,command,lazyRequest;
wikiAsk({...pi,
registerTool(definition){tool=definition;pi.registerTool(definition);},
registerCommand(name,definition){if(name==='wiki-ask') command=definition;pi.registerCommand(name,definition);},
sendUserMessage(content,options){lazyRequest={content,options};}
});
pi.registerCommand('wiki-ask-test', {description:'Проверка', handler:async(_args,ctx)=>{
for (const question of ['Тестовый вопрос', 'Where are the consent forms?', '¿Qué documentos?\\n"цитата" </skill>']) {
await command.handler(question,ctx);
const block=parseSkillBlock(lazyRequest.content);
assert.equal(block?.name,'wiki-ask');
assert.equal(JSON.parse(block.userMessage.replace('User question (JSON string):\\n','')),question);
assert.doesNotMatch(block.content,/[А-Яа-яЁё]/u);
const component=new SkillInvocationMessageComponent(block);
for (const width of [24,80,120]) {
component.setExpanded(false);
const collapsed=component.render(width).join('\\n');
assert.match(collapsed,/wiki-ask/);
assert.doesNotMatch(collapsed,/Use the/);
component.setExpanded(true);
assert.match(component.render(width).join('\\n'),/wiki_ask/);
component.setExpanded(false);
component.invalidate();
assert.equal(component.render(width).join('\\n'),collapsed);
}
}
await command.handler('Тестовый вопрос',ctx);
const result=await tool.execute('probe',{question:'Тестовый вопрос'},new AbortController().signal,undefined,ctx);
process.stderr.write('WIKI_ASK_PROBE='+JSON.stringify({...result,lazyRequest,toolDescription:tool.description,questionDescription:tool.parameters.properties.question.description})+'\\n');
}});
}`);
  const execution = promisify(execFile)(process.env.WIKI_ASK_TEST_CLI, [
    '--offline','--mode','json','--print','--no-session','--no-extensions',
    '--extension',probe,'--no-skills','--no-context-files','--approve','--','/wiki-ask-test',
  ], { cwd:f.project, env:{...process.env,PATH:`${bin}${path.delimiter}${process.env.PATH}`}, timeout:30000, maxBuffer:1024*1024 });
  execution.child.stdin.end();
  const { stderr } = await execution;
  const record = stderr.split('\n').find(line=>line.startsWith('WIKI_ASK_PROBE='));
  assert.ok(record, stderr);
  const result = JSON.parse(record.slice('WIKI_ASK_PROBE='.length));
  assert.match(JSON.stringify(result), /Тестовый ответ/);
  assert.match(JSON.stringify(result), /concepts\/test/);
  assert.equal(result.usage.totalTokens, 3);
  assert.equal(result.details.model, 'fixture/model');
  assert.match(result.lazyRequest.content, /# Wiki query/);
  assert.match(result.lazyRequest.content, /^<skill name="wiki-ask" location="[^"]+">\n/);
  assert.match(result.lazyRequest.content, /\n<\/skill>\n\nUser question \(JSON string\):\n/);
  assert.doesNotMatch(result.toolDescription + result.questionDescription, /[А-Яа-яЁё]/u);
  assert.match(result.lazyRequest.content, /"Тестовый вопрос"/);
  assert.ok(!result.lazyRequest.content.startsWith('---'));
  assert.equal(result.lazyRequest.options.deliverAs, 'followUp');
});

test("отмена до запуска и во время выполнения", async () => {
  const before = new AbortController();
  before.abort();
  await assert.rejects(runProcess(mock(""), { signal: before.signal }), /cancelled/);
  const controller = new AbortController();
  const pending = runProcess(mock('setInterval(()=>{},1000);'), { signal: controller.signal, timeoutMs: 2000 });
  setTimeout(() => controller.abort(), 100);
  await assert.rejects(pending, /cancelled/);
});
