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
    stagesHtml, logsHtml, fmtDur, knobFor, pbBlock,
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
                    // THE WRITER'S GLOSSARY, keyed to the same names as
                    // `resolved` above and deliberately RAGGED: prose with no
                    // formula, prose with one, a blank summary, an entry that
                    // is not an object at all, and a knob naming a field this
                    // run never resolved. Every case the contract calls out,
                    // so the affordance rule is asserted against the shape a
                    // real writer will eventually emit rather than the happy
                    // one.
                    knobs: {
                        size: { summary: 'Parameter count of the base model.',
                                derived_from: null },
                        optim: { summary: 'Which optimiser the fine-tune uses.',
                                 derived_from: 'steps x tokens / headroom' },
                        base: { summary: '   ', derived_from: 'a x b' },
                        expert_names: 'not an object',
                        lora_r: { summary: 'A field this run never resolved.' },
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

// -- (5) the per-field explanations ------------------------------------------
//
// The rendered LOOK of these is eyeballed; what is checked here is the rule
// that decides they exist at all, which is the half with a silent failure
// mode - a `?` on every row, opening onto nothing, looks like a finished
// feature and is the opposite of one.

// ONLY WHERE CONTENT EXISTS. Four fields are resolved; two of them carry a
// usable summary. The blank one, the malformed one and the knob for a field
// this run never resolved must all render nothing whatsoever.
assert.strictEqual((html.match(/class="pb-why"/g) || []).length, 2,
    'the playbill drew an affordance for a field with no usable summary, or '
    + 'dropped one for a field that has words - either way the writer\'s '
    + 'coverage is no longer what is on screen');
assert.ok(html.includes('what is size?') && html.includes('what is optim?'),
    'a field with a summary got no explanation');
assert.ok(!html.includes('what is base?'),
    'a whitespace-only summary still drew a ? that opens onto nothing');
assert.ok(!html.includes('what is expert_names?'),
    'an entry that is not an object at all still drew a ?');
assert.ok(!html.includes('what is lora_r?'),
    'a knob naming a field this run never resolved was rendered anyway');

// DERIVED_FROM IS SET APART. One of the two has a formula; the other must not
// grow an empty labelled line.
assert.strictEqual((html.match(/pb-why-derived/g) || []).length, 1,
    'the derived-from line is drawn for a field that has no formula, or '
    + 'missing from the one that does');
assert.ok(html.includes('>derived from<'),
    'the formula is unlabelled, so it reads as more prose');
assert.ok(html.includes('steps x tokens /<wbr> headroom'),
    'the formula is not being given break opportunities, so a long one can '
    + 'push the panel wider than its column');

// REACHABLE WITHOUT A MOUSE. A native disclosure, not a hover tooltip.
assert.ok(html.includes('<dd class="pb-why"><details>'),
    'the explanation is not a <details>, so it is hover-only or script-only');
assert.ok(html.includes('<summary>'), 'the disclosure has no summary to focus');

// The rule itself, at the boundaries the contract names.
assert.strictEqual(T.knobFor(undefined, 'x'), null, 'no glossary at all');
assert.strictEqual(T.knobFor({}, 'x'), null, 'a field absent from the glossary');
assert.strictEqual(T.knobFor({ x: {} }, 'x'), null, 'an entry with no summary');
assert.strictEqual(T.knobFor({ x: { summary: '   ' } }, 'x'), null,
    'a whitespace-only summary counted as content');
assert.strictEqual(T.knobFor({ x: { derived_from: 'a x b' } }, 'x'), null,
    'a formula with no prose drew an affordance; the contract says the '
    + 'summary is what decides');
assert.strictEqual(T.knobFor({ x: 'a string' }, 'x'), null, 'a scalar entry');
assert.strictEqual(T.knobFor({ x: ['a'] }, 'x'), null, 'an array entry');
assert.strictEqual(T.knobFor('not a mapping', 'x'), null, 'a scalar glossary');
assert.deepStrictEqual(
    T.knobFor({ x: { summary: ' s ', derived_from: 42 } }, 'x'),
    { summary: 's', derived_from: '' },
    'a derived_from of the wrong type must read as absent, not as "42"');

// Somebody else's document reaches the page ESCAPED. Both halves, because the
// wrong order shows up either as visible markup or as an injection.
const hostileKnob = T.pbBlock('g', ['k'], { k: 1 }, {
    k: { summary: '<script>alert(1)</script>', derived_from: 'a/<b>' } });
assert.ok(!hostileKnob.includes('<script>'),
    'a knob summary went to the page unescaped');
assert.ok(hostileKnob.includes('&lt;script&gt;'), 'the summary was not escaped');
assert.ok(hostileKnob.includes('&lt;b&gt;'), 'the formula was not escaped');
assert.ok(hostileKnob.includes('/<wbr>'),
    'the formula lost its break opportunity to the escaping');

// (2) again: the explanations are inside the no-re-render skip, so an adopted
// panel is a bare placeholder and the OPEN ones on screen are the live nodes.
assert.ok(!adopted.includes('pb-why'),
    'the adoption placeholder carried explanations, so the panel was rebuilt '
    + 'after all and anything the reader had opened has just closed');

console.log('viewer probe OK');
