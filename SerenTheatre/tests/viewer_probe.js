// Exercise the viewer pack's leaf logic in a shimmed DOM.
//
// WHY THIS EXISTS. scripts.js is what SHIPS, and until now the only thing
// asserting anything about it was a handful of substring checks in the python
// suite - which catch a deleted function and nothing else. The refresh work
// added logic with a genuinely invisible failure mode: if the structural
// signature stops matching, the page silently goes back to re-rendering every
// five seconds and no test, log line or badge says so.
//
// So this loads the real file, stubs the four things the SerenMeninges shell
// normally provides (document, escapeHtml, api, the timers), and asserts
// against a fixture payload. It is deliberately NOT a DOM implementation: the
// renderers are pure string builders on purpose, which is the property that
// makes them checkable without one.
//
// Run by tests/test_app.py, which skips - naming what it waits for - when node
// is not installed.

'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

// -- the shell's four contributions, shimmed ---------------------------------

const ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
globalThis.escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ENT[c]);

function stubEl() {
    return {
        textContent: '', innerHTML: '', title: '', hidden: false, value: '',
        querySelectorAll: () => [],
        querySelector: () => null,
        getAttribute: () => null,
        setAttribute: () => {},
    };
}
globalThis.document = {
    getElementById: stubEl,
    addEventListener: () => {},
    querySelectorAll: () => [],
    hidden: false,
};
// The module-level `load().then(schedule)` and `loadBackstage()` both run at
// import. Handing them a rejected promise sends each down its own catch, which
// is a path worth exercising anyway.
globalThis.api = () => Promise.reject(new Error('shimmed: no server here'));
// schedule() arms a real 5s interval and would hold the event loop open for
// ever, so the probe would pass and then hang. Neutered rather than cleared
// afterwards: a live timer is a handle this file has no business owning.
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};

// -- load the real file ------------------------------------------------------

const src = fs.readFileSync(process.argv[2], 'utf8');
assert.ok(!src.includes('\r'), 'scripts.js has a CR in it; this repo is LF');

// Top-level const/let land in the script's own lexical scope, so the only way
// to reach them is from inside the same script. Accessors rather than values
// for the two that are reassigned.
const EXPORT = `
;globalThis.__theatre = {
    structuralSignature, breakablePath, pbValue, collectTicks, retick,
    stagesHtml, logsHtml, fmtDur,
    get KEPT(){ return KEPT_PLAYBILLS; }, set KEPT(v){ KEPT_PLAYBILLS = v; },
};
`;
vm.runInThisContext(src + EXPORT, { filename: 'scripts.js' });
const T = globalThis.__theatre;

// -- a fixture board ---------------------------------------------------------

const NOW = 1_700_000_000;

function board(shift, over) {
    over = over || {};
    const quietFor = (over.quiet_for != null ? over.quiet_for : 2760) + shift;
    return {
        generated: NOW + shift,
        took_ms: 3.1 + shift,          // ticks for a different reason; same fix
        refresh_seconds: 5,
        version: '0.4.0',
        stages: [{
            name: 'Lab',
            path: '/mnt/nvme/fraunkensteinLab',
            exists: true,
            launch: {
                marker: '.stagehand-run.json',
                launched: true, launch_state: 'started', launch_detail: null,
                exit_code: null, log_tail: null, pid: 4242,
                recipe: '/r/x.yaml', cwd: '/mnt/nvme/fraunkensteinLab',
                started: NOW - 9000, command_line: 'ms-moe-maker build x.yaml',
                schema_version: 1, understands_schema: 1,
                files: [{ role: 'log', path: '/lab/msmoe.log', exists: true,
                          mtime: NOW - 12, size: 4096, since: 12 + shift }],
                last_activity: NOW - 12, since_activity: 12 + shift,
            },
            launch_error: null,
            logs: [{
                name: 'msmoe.log', path: '/lab/msmoe.log',
                mtime: NOW - 12, size: 4096, phase: 'router training',
                activity: null, subject: null, cfg: {}, budgets: [],
                step: { step: 3157, total: 4000, loss: 0.41, rate: '3.47s/it' },
                milestones: [], warnings: [], stalled_for: 12 + shift,
            }],
            current: '/mnt/nvme/fraunkensteinLab/dryrun_0.5B',
            earlier: 0,
            rungs: [{
                name: 'dryrun_0.5B',
                path: '/mnt/nvme/fraunkensteinLab/dryrun_0.5B',
                mtime: NOW - 30000, specialists: [], skeleton: false,
                final: false, gguf: null, smoketested: false, smoke: null,
                source: 'manifest', manifest_error: null,
                state: 'running', state_source: 'manifest + file activity',
                quiet: {
                    after_seconds: 900,
                    manifest_quiet_for: quietFor,
                    newest_activity_for: 14 + shift,
                    recent_write: true,
                    files: [
                        { role: 'log', path: '/lab/msmoe.log', exists: true,
                          quiet_for: 12 + shift },
                        { role: 'artifacts',
                          path: '/mnt/nvme/fraunkensteinLab/dryrun_0.5B',
                          exists: true, quiet_for: 14 + shift },
                    ],
                },
                manifest: {
                    schema_version: 1, recipe_id: 'r1', name: 'dryrun_0.5B',
                    size: '0.5B', base: 'Qwen/Qwen2.5-Coder-0.5B-Instruct',
                    experts: ['python', 'rust'],
                    // `updated` is a STAMP and does not move; what moves is
                    // the server's `now - updated`. Getting that backwards in
                    // the fixture is how this test would prove nothing.
                    started: NOW - 30000,
                    updated: NOW - (over.quiet_for != null ? over.quiet_for : 2760),
                    finished: null, ok: null,
                    state: 'stalled', stale: true,
                    done_count: 2, stage_count: 3, refusals: [],
                    build_id: over.build_id || '9c1f2a7b',
                    resolved: {
                        size: '0.5B',
                        base: 'Qwen/Qwen2.5-Coder-0.5B-Instruct',
                        optim: 'adamw_torch',
                        expert_names: ['python', 'rust'],
                    },
                    defaults_files: {
                        '/mnt/nvme/msMoEMaker/lib/python3.12/site-packages/ms_moe_maker/assets/defaults.yaml':
                            '3f0e3b6c1432',
                    },
                    extra: {},
                    stages: [
                        { id: 'preflight', label: 'Preflight', status: 'done',
                          known_status: true, started: NOW - 30000,
                          ended: NOW - 29990, elapsed: 10, artifact: null,
                          note: null },
                        { id: 'router',
                          label: over.label || 'Train router',
                          status: over.status || 'running',
                          known_status: true, started: NOW - 30000,
                          ended: null, elapsed: 30000 + shift,
                          artifact: null, note: null },
                    ],
                },
            }],
        }],
    };
}

// -- (1) the structural signature -------------------------------------------

const a = board(0);
const b = board(5);          // five seconds later, nothing else moved
assert.notStrictEqual(JSON.stringify(a), JSON.stringify(b),
    'the fixture does not actually tick; this test would prove nothing');
assert.strictEqual(T.structuralSignature(a), T.structuralSignature(b),
    'two identical boards five seconds apart read as different - the page is '
    + 'back to re-rendering every poll and nothing would have said so');

const changed = board(5, { status: 'done' });
assert.notStrictEqual(T.structuralSignature(a), T.structuralSignature(changed),
    'a stage going running -> done did not register as a change');

// The one clock-derived value that decides whether a BLOCK exists.
const under = board(0, { quiet_for: 800 });      // under after_seconds: 900
const over = board(0, { quiet_for: 1000 });      // over it
assert.notStrictEqual(T.structuralSignature(under), T.structuralSignature(over),
    'crossing after_seconds did not register - renderQuiet would appear or '
    + 'vanish with no re-render behind it');
assert.strictEqual(T.structuralSignature(over),
                   T.structuralSignature(board(30, { quiet_for: 1000 })),
    'two boards on the same side of the threshold read as different');

// Key order must not matter: a server refactor that reorders a dict is not a
// change to anything on screen.
const shuffled = JSON.parse(JSON.stringify(a));
shuffled.stages[0] = Object.fromEntries(
    Object.entries(shuffled.stages[0]).reverse());
assert.strictEqual(T.structuralSignature(a), T.structuralSignature(shuffled),
    'reordering a dict read as a structural change');

// -- (1) the ticks: every key rendered is a key retick can fill --------------

const html = T.stagesHtml(a) + T.logsHtml(a);
const rendered = new Set(
    [...html.matchAll(/data-tick="([^"]*)"/g)].map((m) => m[1]));
assert.ok(rendered.size >= 6, `only ${rendered.size} tick nodes were rendered`);
const collected = T.collectTicks(a);
for (const key of rendered) {
    assert.ok(collected.has(key),
        `rendered a data-tick="${key}" that collectTicks does not produce - `
        + `that node would freeze at whatever it said when it was drawn`);
}
for (const key of collected.keys()) {
    assert.ok(rendered.has(key),
        `collectTicks produces "${key}" with no node to put it in`);
}

// And the values actually advance. This is the check that would catch a render
// path and a collect path that agree on keys and disagree on text.
const later = T.collectTicks(board(60));
for (const key of collected.keys()) {
    assert.ok(later.has(key), `"${key}" vanished between polls`);
}
const quietKey = [...collected.keys()].find((k) => k.startsWith('quiet-manifest:'));
assert.ok(quietKey, 'the manifest-quiet duration is not a tick node');
assert.notStrictEqual(collected.get(quietKey), later.get(quietKey),
    'the manifest-quiet text did not move over a minute');

// retick, against nodes shaped like the ones innerHTML would have made.
const nodes = [...rendered].map((key) => ({
    _key: key, textContent: collected.get(key),
    getAttribute: (n) => (n === 'data-tick' ? key : null),
}));
document.querySelectorAll = (sel) => (sel === '[data-tick]' ? nodes : []);
T.retick(board(60));
const quietNode = nodes.find((n) => n._key === quietKey);
assert.strictEqual(quietNode.textContent, later.get(quietKey),
    'retick did not write the fresh reading into the live node');
document.querySelectorAll = () => [];

// -- (2) the playbill ---------------------------------------------------------

const rungPath = '/mnt/nvme/fraunkensteinLab/dryrun_0.5B';
assert.ok(html.includes(`data-playbill="${rungPath} 9c1f2a7b"`),
    'the playbill carries no adoption key, so it can never be adopted');

T.KEPT = new Set([`${rungPath} 9c1f2a7b`]);
const adopted = T.stagesHtml(a);
T.KEPT = new Set();
assert.ok(adopted.includes(`<aside class="playbill" data-playbill="${rungPath} 9c1f2a7b"></aside>`),
    'a known build_id still rebuilt the whole playbill');
assert.ok(!adopted.includes('defaults inherited'),
    'the placeholder still carried the panel body');

// A CHANGED build_id must not be adopted - that is the one case where the
// panel genuinely has to be rebuilt.
T.KEPT = new Set([`${rungPath} 9c1f2a7b`]);
const rebuilt = T.stagesHtml(board(0, { build_id: 'ffffffff' }));
T.KEPT = new Set();
assert.ok(rebuilt.includes('defaults inherited'),
    'a new build_id was adopted as if it were the old panel');

// -- (3) long paths -----------------------------------------------------------

const DEF = '/mnt/nvme/msMoEMaker/lib/python3.12/site-packages/'
          + 'ms_moe_maker/assets/defaults.yaml';
assert.ok(html.includes('pb-defaults'), 'the defaults block is still a kv grid');
assert.ok(html.includes('>defaults.yaml<'),
    'the filename does not lead the defaults row');
assert.ok(html.includes('sha256'),
    'the hash is still a bare hex blob with nothing saying what it is');
assert.ok(html.includes('3f0e3b6c1432'), 'the hash itself is gone');
assert.ok(html.includes(T.breakablePath(DEF)),
    'the defaults path is not being given break opportunities');

// Escape FIRST, then insert. Both halves asserted, because getting the order
// wrong shows up as either a visible "<wbr>" or as an injection.
const hostile = '/tmp/<script>alert(1)</script>/x.yaml';
const out = T.breakablePath(hostile);
assert.ok(!out.includes('<script>'), 'a path went to the page unescaped');
assert.ok(out.includes('&lt;script&gt;'), 'the path was not escaped');
assert.ok(out.includes('/<wbr>'), 'no break opportunity was inserted');
assert.ok(!out.includes('&lt;wbr&gt;'), 'the <wbr> was escaped into visible text');

assert.ok(T.pbValue('Qwen/Qwen2.5-Coder-14B-Instruct').includes('<wbr>'),
    'a model id gets no break opportunities');
assert.strictEqual(T.pbValue('adamw_torch'), 'adamw_torch',
    'a value with no separator in it was reformatted for no reason');
assert.strictEqual(T.pbValue(['python', 'rust']), 'python, rust',
    'a list value was reformatted; the joining spaces already wrap');

// -- (4) the artifacts reading reads as english ------------------------------

assert.ok(html.includes('artifacts written'),
    'the rung-directory reading renders as "artifacts wrote", which is not a '
    + 'sentence about a directory');
assert.ok(!html.includes('artifacts wrote'), 'the generic phrasing leaked out');

console.log('viewer probe OK');
