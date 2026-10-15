'use strict';

// Copied into the pinned Radar source's test/ directory by herdr-radar.nix.
const test = require('node:test');
const assert = require('node:assert/strict');
const herdr = require('../lib/herdr');
const state = require('../lib/state');
const view = require('../lib/view');
const { Frame } = require('../lib/frame');
const logos = require('../lib/logos');
const managed = require('../lib/managed-config');

test('tab labels use names or a leading number followed by the session title', () => {
  const title = 'synthetic-session';
  for (const [tab, expected] of [
    ['backend', 'backend'],
    ['  backend  ', 'backend'],
    ['1', '1 · synthetic-session'],
    [' 12 ', '12 · synthetic-session'],
    ['', title],
    ['   ', title],
    [null, title],
  ]) {
    assert.deepEqual(state.rowText('tab', tab, title), { tabLabel: '', title: expected });
  }
  assert.deepEqual(state.rowText('tab', '2', ''), { tabLabel: '', title: '2' });
  assert.deepEqual(state.rowText('title', '2', title), { tabLabel: '', title });
  assert.deepEqual(state.rowText('both', '2', title), { tabLabel: '2', title });
});

test('status occupies one cell before the tab number, never the title prefix', () => {
  const prefixes = new Set();
  for (const display of state.STATES) {
    for (const step of [0, 1, logos.PULSE_STEPS]) {
      const row = state.rowText('tab', '2', 'task');
      const line = state.composeLine({ name: 'pi' }, display, row.tabLabel, state.INDENTS[1], step);
      const tokens = state.stateTokens(display, line, row.title);
      assert.ok(tokens.radar_status, `${display}: visible status`);
      assert.equal(tokens[`title_${display}`], '2 · task');
      assert.equal(tokens[`state_${display}`], logos.stateGlyph(state.baseState(display)), 'held state survives daemon restart');
      prefixes.add(line.titlePrefix);
    }
  }
  assert.equal(prefixes.size, 1, 'working, question and done never shift the title');
  const named = state.composeLine({ name: 'pi' }, 'done', 'backend', '', 0);
  assert.equal(named.titlePrefix, 'backend ');
  assert.equal(named.status, logos.stateGlyph('done'));
});

for (const display of ['working', 'blocked']) {
  test(`${display} animation republishes only the status when its frame changes`, async (t) => {
    const writes = [];
    t.mock.method(herdr, 'reportMetadataAsync', async (_pane, _source, tokens) => {
      writes.push(tokens);
      return true;
    });
    const frame = new Frame('plugin:test');
    const entry = { pane: 'p1', tab: 't1', workspace: 'w1', name: 'pi', title: 'task' };
    const keys = { minuteKey: () => null, wsKeys: new Map(), tabKeys: new Map() };
    const render = async (spinStep) => {
      const jobs = [];
      frame.paneJobs(entry, display, {
        tabs: new Map([['t1', '2']]), keys, indent: '', spinStep,
      }, Date.now(), [], jobs);
      await Promise.all(jobs);
    };
    await render(0);
    const initial = writes.find((tokens) => tokens.radar_status)?.radar_status;
    assert.ok(initial);
    writes.length = 0;
    await render(display === 'working' ? 1 : logos.PULSE_STEPS);
    assert.equal(writes.length, 1);
    assert.deepEqual(Object.keys(writes[0]), ['radar_status']);
    assert.notEqual(writes[0].radar_status, initial);
    writes.length = 0;
    await render(display === 'working' ? 1 : logos.PULSE_STEPS);
    assert.equal(writes.length, 0, 'unchanged frame is still cached');
  });
}

test('managed rows put one styled status cell between the logo and title', () => {
  const block = managed.sidebarBlock('text');
  const agents = block.split('[ui.sidebar.spaces]')[0];
  for (const row of agents.split('\n').filter((line) => line.includes('$title_working'))) {
    assert.equal((row.match(/\$radar_status/g) ?? []).length, 1);
    assert.ok(row.indexOf('$logo_stale') < row.indexOf('$radar_status'));
    assert.ok(row.indexOf('$radar_status') < row.indexOf('$title_working'));
    assert.ok(row.includes('equals = "✓"'));
    assert.ok(row.includes('equals = "?"'));
  }
  assert.ok(state.OWNED_TOKENS.includes('radar_status'), 'orphan cleanup owns the status');
});

for (const [label, text, repair] of [
  ['old managed rows', '# >>> herdr-radar sidebar block\nrows = [["$logo", "$title_working"]]\n# <<< herdr-radar sidebar block', true],
  ['current managed rows', '# >>> herdr-radar sidebar block\nrows = [["$logo", "$radar_status", "$title_working"]]\n# <<< herdr-radar sidebar block', false],
  ['sidebar switched off', '[ui]\n', false],
  ['user-owned rows', '[ui.sidebar.agents]\nrows = [["$logo", "$title_working"]]', false],
]) {
  test(`stamped startup migrates ${label} only when needed`, (t) => {
    const fs = require('node:fs');
    const setup = require('../lib/setup');
    const font = require('../lib/font');
    const stamp = setup.stamp();
    const readFile = fs.readFileSync;
    t.mock.method(fs, 'readFileSync', (file, ...args) =>
      file === stamp ? 'existing setup stamp' : readFile(file, ...args));
    const write = t.mock.method(fs, 'writeFileSync', () => {});
    t.mock.method(font, 'isInstalled', () => true);
    t.mock.method(font, 'installedPath', () => '/fonts/radar.ttf');
    t.mock.method(managed, 'inspect', () => ({ state: 'installed', text }));
    const apply = t.mock.method(managed, 'apply', () => ({ ok: true, message: 'rows refreshed' }));
    const reload = t.mock.method(herdr, 'reloadConfig', () => {});
    const notify = t.mock.method(herdr, 'notify', () => {});
    setup.ensure();
    assert.equal(apply.mock.callCount(), Number(repair));
    assert.equal(reload.mock.callCount(), Number(repair));
    assert.equal(write.mock.callCount(), Number(repair));
    assert.equal(notify.mock.callCount(), 0);
  });
}

test('state cleanup clears the dedicated status token', async (t) => {
  const writes = [];
  t.mock.method(herdr, 'reportMetadataAsync', async (_pane, _source, tokens) => {
    writes.push(tokens);
    return true;
  });
  await state.clearState('plugin:test', 'p1');
  assert.equal(Object.assign({}, ...writes).radar_status, null);
});

const entries = [
  { pane: 'p1', workspace: 'w1' },
  { pane: 'p2', workspace: 'w1' },
  { pane: 'p3', workspace: 'w2' },
];

test('interactive groups give every child a stable workspace identity', async (t) => {
  const writes = new Map();
  t.mock.method(herdr, 'reportMetadataAsync', async (pane, source, tokens) => {
    writes.set(pane, tokens);
    return true;
  });
  const result = await state.writeGroups('plugin:test', entries, new Map([['w1', 'first'], ['w2', 'second']]));
  assert.equal(result.ok, true);
  assert.equal(writes.get('p1').group_id, 'w1');
  assert.equal(writes.get('p2').group_id, 'w1');
  assert.equal(writes.get('p3').group_id, 'w2');
  assert.equal(writes.get('p1').group, 'first');
  assert.equal(writes.get('p2').group, null);
});

for (const mode of ['grouped', null, 'recent']) {
  test(`interactive group heads and members align in ${mode ?? 'panel-grouped'} mode`, async (t) => {
    const rows = [
      { pane: 'p1', workspace: 'w1' },
      { pane: 'p2', workspace: 'w1' },
      { pane: 'p3', workspace: 'w2' },
      { pane: 'p4', workspace: 'w2' },
    ];
    t.mock.method(state, 'snapshot', async () => rows);
    t.mock.method(state, 'labels', async () => ({
      tabs: new Map(), workspaces: new Map(), parents: new Map(), worktrees: new Map(),
    }));
    t.mock.method(view, 'mode', () => mode);
    t.mock.method(herdr, 'panelGrouped', () => true);
    const frame = new Frame('plugin:test');
    t.mock.method(frame, 'sortKeys', () => ({
      wsKeys: new Map(), nested: (workspace) => workspace === 'w2', splitChild: () => false,
    }));
    t.mock.method(frame.workspaceOrder, 'sync', async () => {});
    t.mock.method(frame, 'displayOrder', () => rows);
    t.mock.method(frame, 'displayFor', () => 'idle');
    t.mock.method(frame, 'spaceJobs', () => {});
    t.mock.method(frame, 'groupJobs', async () => {});
    const indents = new Map();
    t.mock.method(frame, 'paneJobs', (entry, _display, { indent }) => {
      indents.set(entry.pane, indent);
    });
    await frame.render(0);
    const grouped = mode !== 'recent';
    for (const pane of ['p1', 'p2']) {
      assert.equal(indents.get(pane), grouped ? state.INDENTS[1] : '', `${pane}: checkout indent`);
    }
    for (const pane of ['p3', 'p4']) {
      assert.equal(indents.get(pane), grouped ? state.INDENTS[2] : '', `${pane}: worktree indent`);
    }
  });
}

test('stale headings retain identity and flat mode removes it', async (t) => {
  const writes = new Map();
  t.mock.method(herdr, 'reportMetadataAsync', async (pane, source, tokens) => {
    writes.set(pane, tokens);
    return true;
  });
  await state.writeGroups('plugin:test', entries, new Map([['w1', 'first']]), new Set(['w1']));
  assert.equal(writes.get('p1').group, null);
  assert.equal(writes.get('p1').group_stale, 'first');
  assert.equal(writes.get('p2').group_id, 'w1');
  await state.clearGroups('plugin:test', entries);
  for (const tokens of writes.values()) assert.equal(tokens.group_id, null);
  assert.ok(state.OWNED_TOKENS.includes('group_id'), 'orphan cleanup must clear interactive identity');
});
