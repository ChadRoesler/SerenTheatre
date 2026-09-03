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

function stubEl(id) {
    return {
        id: id || '', textContent: '', innerHTML: '', title: '', hidden: false,
        value: '', checked: false,
        querySelectorAll: () => [],
        querySelector: () => null,
        getAttribute: () => null,
        setAttribute: () => {},
    };
}
// ONE OBJECT PER ID, not a fresh stub per call. The shim used to hand back a
// new element every time, so anything a handler WROTE was unobservable - which
// is fine for a pure renderer and useless for testing the panel a click puts
// on screen. Backstage's whole failure was invisible output; a shim that
// cannot see output is not the tool for it.
const ELS = new Map();
function el(id) {
    if (!ELS.has(id)) ELS.set(id, stubEl(id));
    return ELS.get(id);
}
// CLICK HANDLERS ARE CAPTURED, not discarded. The old shim threw them away,
// which is precisely why nobody noticed that the tab buttons had none: every
// test asked whether a tab EXISTED and none asked whether it did anything.
const CLICKS = [];
globalThis.document = {
    getElementById: el,
    addEventListener: (kind, fn) => { if (kind === 'click') CLICKS.push(fn); },
    querySelectorAll: () => [],
    hidden: false,
};
// The shell's, not ours - stubbed so a click can be observed arriving at it.
globalThis.SHOWN = [];
globalThis.showTab = (id) => { globalThis.SHOWN.push(id); };
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
    archiveHtml, repHtml, repRecipeHtml, compareHtml,
    bsRefusalHtml, bsFieldRows, bsPost, bsShow,
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


// ── ⑤ the results panel ─────────────────────────────────────────────────────
//
// THE FAILURE THIS SECTION IS AGAINST IS INVISIBLE. The results panel appears
// only when a re-render happens, and a re-render happens only when the
// structural signature changes. If an eval landing does not move the
// signature, the panel never draws and NOTHING says so - the page just goes on
// showing a finished run with no results, which is indistinguishable from a
// run that was never evaluated. So both directions get asserted: results
// arriving must move the signature, and two identical polls must not.

const EVAL = {
    path: '/r/eval_report.json', schema_version: 1,
    build_id: '9c1f2a7b', generated: NOW - 3600,
    provenance: 'matches', ok: true, message: 'measured',
    dead_experts: [], undiscriminating: ['markdown'],
    caveats: ['<b>three rows only</b>'], unmeasured: ['csharp: no compiler'],
    quality: [{ name: 'python', domain: 'py', exact_match: 0.9, rouge1: 0.8,
                bleu: 0.7, scored: 3, attempted: 20, reasoned: null,
                status: 'done', note: '', thin: true,
                in_moe: { name: 'moe/python', domain: 'py', exact_match: 0.4,
                          rouge1: 0.3, bleu: 0.2, scored: 20, attempted: 20,
                          reasoned: 0.6, status: 'done', note: '',
                          thin: false, in_moe: null } }],
    routing: {
        status: '', reason: '',
        experts: [{ name: 'ghost', own_share: 0.001, others_share: 0.001,
                    enrichment: null, enrichment_reliable: false,
                    top_competitor: 'python', top_competitor_share: 0.9,
                    own_is_column_max: false, outranked: true }],
        excluded: [], named_experts: 2, own_is_max_count: 1,
        mean_enrichment: 1.02, p_value: 0.5, p_value_event: '',
        mean_js_bits: 0.0, input_blind: true, moe_layers: 12, top_k: 2,
        mean_gate_confidence: 0.49, uniform_confidence: 0.5,
        confidence_ceiling: 0.5, saturated: true,
        think_segments: {}, think_segment_errors: {} },
};
const GATE = {
    path: '/r/gate_experts.json', status: 'unmeasurable',
    findings: ['two experts are nearly identical'],
    unmeasured: ['cross-domain loss: no held-out rows'],
    divergence: { python: 0.031 }, pairwise: {},
    cross_loss: { python: { python: 1.2, markdown: 3.4 } }, config_audit: {},
};

function withResults(shift, over) {
    const b = board(shift, over);
    const r = b.stages[0].rungs[0];
    r.eval = Object.assign({}, EVAL, (over || {}).eval || {});
    r.gate = GATE;
    r.eval_error = (over || {}).eval_error || null;
    r.gate_error = null;
    return b;
}

const bare = board(0);
assert.ok(!T.stagesHtml(bare).includes('class="results"'),
    'a run with no eval and no gate drew a results panel anyway; a panel that '
    + 'says "nothing here" on every run teaches its reader to stop looking');

const res = T.stagesHtml(withResults(0));
assert.ok(res.includes('class="results"'), 'the results panel did not render');

// A SIBLING OF THE PLAYBILL, IN THE SAME COLUMN. Auto-placed into `.run`'s two
// columns a third child lands under the step ladder instead, which is not
// obviously wrong at a glance and so would survive review.
assert.ok(res.includes('<div class="run-side">'),
    'the right-hand column wrapper is gone; the results panel will auto-place '
    + 'into row 2 column 1, underneath the ladder');
assert.ok(res.indexOf('class="playbill"') < res.indexOf('class="results"'),
    'the results panel must sit under the playbill, not above it');

// Provenance, all three states, because the middle one is the whole point.
assert.ok(res.includes('res-prov ok'), 'a matching build_id did not say so');
const staleHtml = T.stagesHtml(withResults(0, {
    eval: { provenance: 'stale', build_id: 'old11111' } }));
assert.ok(staleHtml.includes('res-prov stale')
    && staleHtml.includes('earlier build'),
    'a stale eval rendered as though it described the model on disk - this is '
    + 'the C# 0/10 failure in a new coat');
const unknownHtml = T.stagesHtml(withResults(0, {
    eval: { provenance: 'unknown', build_id: '' } }));
assert.ok(unknownHtml.includes('res-prov unknown')
    && unknownHtml.includes('<b>unknown</b>'),
    'a viewer that cannot tell which build was graded must say so out loud');

// AN ABSENCE IS NOT A ZERO. `reasoned: null` is "never asked"; 0.00 would read
// as a model that never once produced a think block.
assert.ok(res.includes('—'), 'a missing measurement did not render as a dash');
assert.ok(!res.includes('>0.000<') || res.includes('0.900'),
    'sanity: the fixture scores should still be present');

// NOISE IS NOT A NUMBER. A starved expert's enrichment must not be printed.
assert.ok(res.includes('noise') && res.includes('starved'),
    'an unreliable enrichment was printed as a figure, which invites a reader '
    + 'to quote the best-looking number in the table');

// The two diagnoses share cannot tell apart, both stated.
assert.ok(res.includes('INPUT-BLIND'), 'the input-blind verdict is missing');
assert.ok(res.includes('SATURATED'),
    'the saturation verdict is missing; at top-k 2 the ceiling is 0.50 and '
    + '0.49 is saturated - a threshold against 1.0 would never fire');

// NOT MEASURED IS NOT A PASS, and it is kept apart from findings.
assert.ok(res.includes('res-unmeasured') && res.includes('cross-domain loss'),
    'the gate\'s unmeasured list is not rendered, so a check that could not '
    + 'run reads as a check that came back clean');
assert.ok(res.includes('res-findings'), 'the gate findings are not rendered');

// The matrix diagonal is marked, because finding it by counting columns is
// exactly the friction that stops anyone checking it.
assert.ok(res.includes('class="n own"'),
    'the cross-domain diagonal is not marked');

// Somebody else's text reaches the page ESCAPED. A caveat is free text written
// by the pipeline and read by a browser.
assert.ok(!res.includes('<b>three rows only</b>'),
    'a caveat went to the page unescaped');
assert.ok(res.includes('&lt;b&gt;three rows only'), 'the caveat was not escaped');

// A document that is present and unreadable is NOT the same as no document,
// and Backstage already taught this codebase what happens to an error list
// nothing renders.
const errHtml = T.stagesHtml(withResults(0, {
    eval_error: 'eval_report.json: not valid JSON' }));
assert.ok(errHtml.includes('not valid JSON'),
    'an unreadable results document was swallowed; that reports the viewer\'s '
    + 'own failure as a fact about the pipeline');

// -- the signature, both directions ------------------------------------------

assert.notStrictEqual(T.structuralSignature(bare),
                      T.structuralSignature(withResults(0)),
    'results arriving did not move the structural signature, so the page will '
    + 'never re-render and the panel will never appear');

// Two polls a few seconds apart, same results: nothing structural moved. If
// this fires, something clock-derived leaked into the results payload and the
// page is now rebuilding itself every five seconds with nothing saying so.
assert.strictEqual(T.structuralSignature(withResults(0)),
                   T.structuralSignature(withResults(7)),
    'the results payload carries a clock-derived field; add it to '
    + 'CLOCK_DERIVED or send a stamp instead of a duration');

// The age is a TICK, which is what makes the line above safe: the text updates
// without a re-render, the same way every other elapsed string on the page does.
const resTicks = T.collectTicks(withResults(0));
assert.ok([...resTicks.keys()].some((k) => k.startsWith('eval-age:')),
    'the eval age is not a tick node, so it would freeze at whatever it said '
    + 'when the panel was drawn');


// ── previous surgeries ──────────────────────────────────────────────────────
//
// THE ASSERTION THAT MATTERS is the one about `archived only`. Stages shows
// what is on disk; this shows what was RECORDED, and most of what was recorded
// no longer exists - that is the whole reason the archive is worth having. A
// row that renders identically to a live rung would have the viewer assert a
// directory exists when it does not, which is this codebase's oldest failure
// wearing its newest costume.

const SURGERY = {
    run_key: 'abc123', build_id: 'cafe0001', stage: 'Lab',
    name: 'dryrun_0.5B', rung_path: '/mnt/nvme/fraunkensteinLab/dryrun_0.5B',
    started: NOW - 90000, finished: NOW - 86400, state: 'finished', ok: 1,
    rung_present: false,
    manifest: { build_id: 'cafe0001', name: 'dryrun_0.5B' },
    gate: { status: 'ok', findings: 0, unmeasured: 1,
            view: { status: 'ok', findings: [],
                    unmeasured: ['config audit: no config.json'],
                    divergence: { python: 0.031 },
                    cross_loss: { python: { python: 1.2 } } } },
    gradings: [{
        grading_key: 'g1', build_id: 'cafe0001', generated: NOW - 80000,
        provenance: 'matches', ok: 1,
        view: {
            provenance: 'matches', build_id: 'cafe0001',
            generated: NOW - 80000, ok: true, message: 'measured',
            caveats: [], undiscriminating: [], dead_experts: [],
            unmeasured: [],
            quality: [{ name: 'python', exact_match: 0.91, rouge1: 0.8,
                        bleu: 0.7, scored: 3, attempted: 20, reasoned: 0.62,
                        status: 'done', thin: true, in_moe: null }],
            routing: { experts: [], excluded: [], think_segments: {},
                       think_segment_errors: {} },
        },
    }],
};

function archiveState(over) {
    return Object.assign({ enabled: true, path: '/x/archive.db', total: 1,
                           error: '', surgeries: [SURGERY] }, over || {});
}

const arc = T.archiveHtml(archiveState());
assert.ok(arc.includes('archived only'),
    'a run whose directory has been deleted rendered without saying so - the '
    + 'viewer is now asserting that a rung exists when a stat disagrees');
assert.ok(arc.includes('dryrun_0.5B'), 'the surgery did not render');
assert.ok(arc.includes('cafe0001'), 'the build_id is missing');

// The SAME renderers as the live results panel, not a second implementation.
// Two renderers for one report eventually disagree about what a number means,
// on screen, somewhere nobody is checking.
assert.ok(arc.includes('Generation quality'),
    'the archived grading did not reuse the live quality renderer');
assert.ok(arc.includes('Expert gate'), 'the archived gate did not render');
assert.ok(arc.includes('res-unmeasured'),
    'the gate\'s unmeasured list vanished in the archive, so a check that '
    + 'could not run reads as one that came back clean');

const onDisk = T.archiveHtml(archiveState({
    surgeries: [Object.assign({}, SURGERY, { rung_present: true })] }));
assert.ok(onDisk.includes('on disk') && !onDisk.includes('archived only'),
    'a surgery whose rung is still there was marked as deleted');

// THREE VERDICTS. `ok: null` is a manifest that never said, and rendering it
// as a failure would invent a result.
const noVerdict = T.archiveHtml(archiveState({
    surgeries: [Object.assign({}, SURGERY, { ok: null })] }));
assert.ok(noVerdict.includes('no verdict'),
    'a run with no recorded verdict was given one');

// A run built and never evaluated is not a run that scored zero.
const unevaluated = T.archiveHtml(archiveState({
    surgeries: [Object.assign({}, SURGERY, { gradings: [] })] }));
assert.ok(unevaluated.includes('never evaluated'),
    'an unevaluated run rendered as an empty space where a table would be');

// OFF AND EMPTY ARE DIFFERENT SENTENCES, and both have to be sayable.
assert.ok(T.archiveHtml({ enabled: false, error: 'archive: disabled in config' })
    .includes('not running'), 'a disabled archive said nothing');
assert.ok(T.archiveHtml(archiveState({ surgeries: [], total: 0 }))
    .includes('Nothing archived yet'),
    'an empty archive is indistinguishable from a broken one');

// A stale grading has to be as loud here as it is in the live panel. A
// historical row is exactly where somebody will misread one.
const stale = JSON.parse(JSON.stringify(SURGERY));
stale.gradings[0].view.provenance = 'stale';
assert.ok(T.archiveHtml(archiveState({ surgeries: [stale] })).includes('stale'),
    'an archived grading of an earlier build did not say so');

// Free text from a pipeline reaches a browser. Escape it.
const hostileRun = JSON.parse(JSON.stringify(SURGERY));
hostileRun.name = '<img src=x onerror=alert(1)>';
const escaped = T.archiveHtml(archiveState({ surgeries: [hostileRun] }));
assert.ok(!escaped.includes('<img src=x'), 'a run name went to the page raw');


// ── the repertoire ──────────────────────────────────────────────────────────
//
// THE WARNING IS THE FEATURE, not the list. A recipe naming an `eval.script`
// causes somebody else's file to be run with the interpreter, and the moment
// that has to be visible is when a person opens the book intending to stage
// it. A shelf that renders beautifully and drops that line is worse than no
// shelf, because it looks like it checked.

const BOOK = {
    book_id: 'a'.repeat(64), name: 'Handoff', created: NOW - 200000,
    imported: NOW - 100000, build_id: 'a4c83291f681', bytes: 3117,
    notes: '# Gauntlet notes\n\nrouter.epochs is the knob.',
    meta: { name: 'handoff', build_id: 'a4c83291f681',
            unpinnable: { use_vllm: false, lr_lora: 0.0002, seed: 42 } },
};

const rep = T.repHtml({ enabled: true, error: '', books: [BOOK] });
assert.ok(rep.includes('Handoff'), 'the book did not render');
assert.ok(rep.includes('a4c83291f681'), 'the build_id is missing');
assert.ok(rep.includes('router.epochs is the knob'),
    'the notes are the half a recipe cannot carry, and they were dropped');
assert.ok(rep.includes('3 settings cannot travel in a recipe'),
    'the unpinnable count is not shown - somebody would build this expecting '
    + 'it to match and never be told which knobs followed their own box');

// A bundle that makes no claim has to say so, not show a blank.
const noClaim = T.repHtml({ enabled: true, books: [
    Object.assign({}, BOOK, { build_id: '', meta: {} })] });
assert.ok(noClaim.includes('makes no claim'),
    'a bundle with no build_id rendered as though it had one');

// Empty and broken are different sentences.
assert.ok(T.repHtml({ enabled: true, books: [] }).includes('Nothing on the shelf'),
    'an empty shelf is indistinguishable from a broken one');
assert.ok(T.repHtml({ enabled: false, error: 'archive: disabled in config' })
    .includes('nowhere to keep prompt books'), 'a disabled archive said nothing');

// THE ONE THAT MATTERS. Executable content, in front of a person.
const danger = T.repRecipeHtml({
    recipe: 'name: x\n', bytes_present: true,
    executes: ["eval.script = './their_grader.py'"] });
assert.ok(danger.includes('This recipe runs code'),
    'a recipe that executes somebody else\'s script opened without a word');
assert.ok(danger.includes('their_grader.py'), 'the warning did not name the file');

const safe = T.repRecipeHtml({ recipe: 'name: x\n', bytes_present: true,
                               executes: [] });
assert.ok(!safe.includes('This recipe runs code'),
    'every recipe was flagged, which is how a warning stops being read');

// A row whose bytes are gone can still be read, and says it cannot be passed on.
assert.ok(T.repRecipeHtml({ recipe: 'name: x\n', bytes_present: false,
                            executes: [] }).includes('bytes are not'),
    'a book with no blob offered a download that would 404');

// Somebody else's file, somebody else's YAML. Both reach a browser.
const hostileBook = T.repHtml({ enabled: true, books: [
    Object.assign({}, BOOK, { name: '<img src=x onerror=alert(1)>',
                              notes: '<script>alert(2)</script>' })] });
assert.ok(!hostileBook.includes('<img src=x'), 'a book name went to the page raw');
assert.ok(!hostileBook.includes('<script>alert(2)'), 'notes went to the page raw');
assert.ok(T.repRecipeHtml({ recipe: '<script>alert(3)</script>',
                            bytes_present: true, executes: [] })
    .includes('&lt;script&gt;'), 'the recipe body was not escaped');


// ── comparing two runs ──────────────────────────────────────────────────────
//
// THE VERDICT IS THE THING UNDER TEST, not the tables. Two columns of numbers
// generate a conclusion in the reader whether or not one is warranted, so the
// sentence saying what may be concluded is the load-bearing part of the panel
// - and a rendering that dropped it would still look completely fine.

function diff(over) {
    const base = {
        a: { run_key: 'a1', name: 'dryrun_a', build_id: 'aaa1', started: NOW - 9000,
             rung_present: true },
        b: { run_key: 'b2', name: 'dryrun_b', build_id: 'bbb2', started: NOW - 3000,
             rung_present: false },
        config: { inputs: [], consequences: [], only_in_a: [], only_in_b: [],
                  unchanged: 74, has_glossary: true },
        attribution: 'none', incomparable_because: [], nondeterminism: false,
        same_build: false,
        outcome: { a_evaluated: true, b_evaluated: true, routing: [],
                   quality: [], headline: [] },
    };
    return Object.assign(base, over || {});
}

const ONE = diff({
    attribution: 'single',
    config: { inputs: [{ field: 'router_epochs', a: 3, b: 8,
                         knob: { summary: 'Passes over the router mix.',
                                 derived_from: null } }],
              consequences: [{ field: 'collect_token_target', a: 100, b: 250,
                               knob: null }],
              only_in_a: [], only_in_b: [], unchanged: 73, has_glossary: true },
    outcome: { a_evaluated: true, b_evaluated: true,
               headline: [{ label: 'mean enrichment', a: 1.02, b: 2.14,
                            delta: 1.12 }],
               routing: [{ name: 'python', a: 1.02, b: 2.14, delta: 1.12,
                           reliable: true }],
               quality: [{ name: 'python', thin: false,
                           exact_match: { a: 0.9, b: 0.9, delta: 0 },
                           rouge1: { a: 0.8, b: 0.8, delta: 0 },
                           bleu: { a: 0.7, b: 0.7, delta: 0 },
                           reasoned: { a: 0.11, b: 0.62, delta: 0.51 } }] },
});

const one = T.compareHtml(ONE);
assert.ok(one.includes('One input changed'), 'the single-variable verdict is missing');
assert.ok(one.includes('router_epochs'), 'the changed field is not named');
assert.ok(one.includes('evidence rather than proof'),
    'a single changed input was presented as proof; a seed and a corpus draw '
    + 'move underneath every run and the panel has to say so');
assert.ok(one.includes('derived value') && one.includes('followed'),
    'the consequence was not distinguished from the decision');
assert.ok(one.includes('Values that followed'),
    'derived values are not in their own block, so one decision reads as two');
// Direction carried by the SIGN, not only by colour - unreadable otherwise for
// a good number of people, and invisible in a screenshot pasted into chat.
assert.ok(one.includes('+1.12'), 'the delta lost its sign');
assert.ok(one.includes('+0.51'), 'the reasoned delta lost its sign');

const many = T.compareHtml(diff({
    attribution: 'multiple',
    config: { inputs: [{ field: 'router_epochs', a: 3, b: 8, knob: null },
                       { field: 'lora_r', a: 16, b: 32, knob: null }],
              consequences: [], only_in_a: [], only_in_b: [], unchanged: 72,
              has_glossary: true },
}));
assert.ok(many.includes('Nothing here tells you which one'),
    'several inputs changed and the panel did not refuse to attribute - which '
    + 'makes it a confidently-wrong claim generator with a nice table');
assert.ok(many.includes('2 inputs changed'), 'the count is missing');

// THE LOUD ONE. Identical inputs, moved numbers: a measurement of how
// repeatable the pipeline is, and the floor under every other comparison.
const nondet = T.compareHtml(diff({
    nondeterminism: true, same_build: true,
    outcome: { a_evaluated: true, b_evaluated: true, routing: [], quality: [],
               headline: [{ label: 'mean enrichment', a: 1.02, b: 1.47,
                            delta: 0.45 }] },
}));
assert.ok(nondet.includes('Same configuration, different'),
    'identical inputs with different numbers passed without comment - that is '
    + 'the most valuable finding this panel can make');
assert.ok(nondet.includes('cmp-verdict bad'), 'the loud case was rendered quietly');

assert.ok(T.compareHtml(diff({ attribution: 'none', same_build: true }))
    .includes('Identical configuration'), 'a matching pair said nothing');

// Two different experiments must not look like a controlled comparison.
assert.ok(T.compareHtml(diff({ incomparable_because: ["size: '0.5B' vs '7B'"] }))
    .includes('two different'),
    'a 0.5B against a 7B rendered as though it were one experiment');

// An older manifest cannot split decisions from consequences, and says so.
assert.ok(T.compareHtml(diff({ config: { inputs: [], consequences: [],
        only_in_a: [], only_in_b: [], unchanged: 70, has_glossary: false } }))
    .includes('no knob glossary'),
    'a manifest with no glossary presented a split it could not make');

// No numbers is not numbers that did not move.
assert.ok(T.compareHtml(diff({ outcome: { a_evaluated: true,
        b_evaluated: false, routing: [], quality: [], headline: [] } }))
    .includes('B was never evaluated'),
    'an unevaluated run rendered as a run whose numbers stayed put');

// A delta between two noises has no referent and must not be the best-looking
// figure in the table.
assert.ok(T.compareHtml(diff({ outcome: { a_evaluated: true, b_evaluated: true,
        quality: [], headline: [],
        routing: [{ name: 'ghost', a: 2.15, b: 0.98, delta: -1.17,
                    reliable: false }] } })).includes('starved on one side'),
    'a delta between two starved experts was printed as a result');

assert.ok(!T.compareHtml(diff({ a: { run_key: 'x', name: '<img src=x>',
        build_id: '', rung_present: true } })).includes('<img src=x'),
    'a run name went to the compare panel raw');

// ── the tabs are clickable ──────────────────────────────────────────────────
//
// THE TEST THAT WOULD HAVE CAUGHT IT, and the reason none of the others did.
//
// The shell defines showTab() and activates the FIRST tab on DOMContentLoaded.
// It binds nothing else - every leaf wires its own clicks, and Theatre's never
// did. So exactly one tab worked, and nothing anywhere said so: the tabbar
// rendered, the panels rendered, every route answered 200, and
// test_every_tab_has_a_panel_and_every_panel_a_tab passed because that pairing
// really was correct.
//
// Four tests, each asking a true question, none asking the only one that
// mattered: CAN A PERSON GET TO THIS PANEL. So this one clicks.

assert.ok(CLICKS.length, 'the leaf registered no click handlers at all');

function clickTab(id) {
    const before = globalThis.SHOWN.length;
    const button = {
        getAttribute: (a) => (a === 'data-tab' ? id : null),
        // What a real <button class="tab"> inside .tabbar answers.
        closest: (sel) => (sel === '.tabbar .tab' ? button : null),
        classList: { contains: () => false },
        id: '',
    };
    CLICKS.forEach((fn) => { try { fn({ target: button }); } catch (e) { /* other handlers */ } });
    return globalThis.SHOWN.length > before
        ? globalThis.SHOWN[globalThis.SHOWN.length - 1] : null;
}

for (const id of ['logs', 'surgeries', 'repertoire', 'backstage']) {
    assert.strictEqual(clickTab(id), id,
        `clicking the ${id} tab did not reach showTab(). The shell only ever `
        + `activates the FIRST tab; every other one is inert until the leaf `
        + `binds it, and an inert tab looks exactly like an empty panel.`);
}

// A click somewhere that is NOT a tab must not move the view out from under
// somebody - every panel in here has buttons of its own.
const notATab = {
    getAttribute: () => null,
    closest: () => null,
    classList: { contains: () => false },
    id: 'bs-save',
};
const beforeStray = globalThis.SHOWN.length;
CLICKS.forEach((fn) => { try { fn({ target: notATab }); } catch (e) {} });
assert.strictEqual(globalThis.SHOWN.length, beforeStray,
    'a click on an ordinary button switched tabs');

// ── the build refusal ───────────────────────────────────────────────────────
//
// WHAT ACTUALLY HAPPENED, because it is the only justification this section
// needs. ms-moe-maker declined to resume a run directory built under different
// settings. It named the two finished stages it would have inherited, listed
// nine changed fields, and offered three ways out. Backstage put every word of
// that in the response body. The browser showed:
//
//     500 Internal Server Error
//
// - because the shell's api() throws `new Error(status + " " + statusText)`
// and never reads the body. The operator ssh'd into the box and tailed a log
// to find a message the program had already sent him.
//
// Two things are under test here and they fail independently: that a failed
// write CARRIES its body at all, and that a refusal is rendered as the choice
// it is rather than as a wall of text with a flag in it.

const REFUSAL = {
    text: 'the build exited with code 1 within 0.4s of launch',
    exit_code: 1,
    command_line: 'ms-moe-maker build gauntlet.yaml --json --dryrun',
    log_tail: 'REFUSING TO RESUME: this run directory was built by a different build.',
    event: {
        event: 'error', stage: 'build', refusal: 'resume_drift',
        message: 'run directory belongs to a different build_id',
        headline: 'REFUSING TO RESUME: this run directory was built by a different build.',
        kept: '2 stage(s) already finished and would be kept as-is: preflight, abliterate.base',
        run_dir: '/mnt/nvme/gauntlet/msmoe_run_7B',
        finished: ['preflight', 'abliterate.base'],
        changed: ["abliterate_n_trials: 60 -> 200", "dryrun: False -> True"],
        fields: [
            { field: 'abliterate_n_trials', text: 'abliterate_n_trials: 60 -> 200',
              kind: 'moved', was: '60', now: '200' },
            { field: 'dryrun', text: 'dryrun: False -> True',
              kind: 'moved', was: 'False', now: 'True' },
            // THE NOISE, and there are seventy of these in a real refusal
            // against an older run directory. Same list, different news.
            { field: 'warmup_steps', text: "warmup_steps: '(absent)' -> 60",
              kind: 'first-recorded', was: "'(absent)'", now: '60' },
            { field: 'gone_knob', text: "gone_knob: 3 -> '(absent)'",
              kind: 'no-longer-recorded', was: '3', now: "'(absent)'" },
        ],
        options: [
            { id: 'force', do: '--force', flag: '--force', discards: true,
              what: 'rebuild everything with the new settings' },
            { id: 'defaults', do: '--defaults <the old file>', flag: null,
              discards: false, what: 'reproduce the original build' },
            { id: 'elsewhere', do: 'build somewhere else', flag: null,
              discards: false, what: 'change roots.output, keep both' },
        ],
    },
};

const ran = { name: 'gauntlet.yaml', dryrun: false };
const refused = T.bsRefusalHtml(REFUSAL, ran);

assert.ok(refused.includes('REFUSING TO RESUME'), 'the headline is missing');
assert.ok(refused.includes('/mnt/nvme/gauntlet/msmoe_run_7B'),
    'the panel does not say WHICH directory it is refusing to resume into - '
    + 'the one fact that decides whether the answer is --force or roots.output');
assert.ok(refused.includes('preflight') && refused.includes('abliterate.base'),
    'the stages that would be inherited are not named');

// THE DIFF AS A TABLE, from the builder's own split fields. Nothing here cuts
// a sentence on " -> "; a resolved value can contain an arrow.
assert.ok(refused.includes('dr-table'), 'the diff rendered as prose');
assert.ok(refused.includes('>abliterate_n_trials<'), 'a changed field is missing');
assert.ok(refused.includes('dr-was') && refused.includes('dr-now'),
    'was and now are not distinguished, so the reader has to work out which '
    + 'column the build would actually use');

// THE SPLIT, which is the difference between surfacing a diff and dumping one.
// A resume against a directory built before a field existed reports that field
// - correctly - and on a 92-field config that is seventy rows of "the previous
// manifest never said" around the one knob somebody moved. Rendering both the
// same buries the only line that decides between --force and roots.output.
const head = refused.slice(0, refused.indexOf('dr-rest'));
assert.ok(head.includes('abliterate_n_trials'),
    'a moved knob is not in the panel above the fold');
assert.ok(!head.includes('warmup_steps'),
    'a field the previous run never recorded is sitting in the main table '
    + 'next to a knob that actually moved');
assert.ok(refused.includes('2 more the previous run never recorded'),
    'the folded rows are not counted, so nothing says they exist');
assert.ok(refused.includes('warmup_steps'),
    'the unrecorded fields were DROPPED rather than folded - they are still '
    + 'evidence that resume cannot be verified');
assert.ok(refused.includes('not recorded'),
    "a field with no previous value rendered as though '(absent)' were one");
assert.ok(!refused.includes('dr-was">&#39;(absent)&#39;'),
    'a struck-through "(absent)" claims a value was removed, which is a '
    + 'different sentence from "we have no record of it"');

// Older builder, no `kind` on the rows at all: everything is a moved field
// rather than everything vanishing into a fold nobody opens.
const unlabelled = T.bsRefusalHtml({ text: 'x', event: Object.assign(
    {}, REFUSAL.event, { fields: [{ field: 'a', text: 'a: 1 -> 2',
                                    was: '1', now: '2' }] }) }, ran);
assert.ok(unlabelled.includes('dr-table') && unlabelled.includes('>a<'),
    'rows from a builder too old to label them disappeared');
assert.ok(!unlabelled.includes('dr-rest'),
    'unlabelled rows were folded away as noise');

// THE BUTTON, AND ONLY THE BUTTON WE CAN HONOUR.
assert.ok(refused.includes('id="bs-force"'),
    'the refusal names --force and the room still cannot perform it - a dead '
    + 'end with good manners');
assert.ok(refused.includes('data-name="gauntlet.yaml"'),
    'the force button does not carry the recipe that was actually refused, so '
    + 'it would rebuild whatever is in the name box at the time');
assert.ok(refused.includes('DISCARDS') && refused.includes('2 finished'),
    'the destructive button does not say what it destroys');
assert.ok(refused.includes('class="danger"'), 'the destructive button is not marked');
assert.ok(refused.includes('<code>--defaults &lt;the old file&gt;</code>'),
    'an option this room cannot perform was not shown as the instruction it is');
assert.strictEqual((refused.match(/<button/g) || []).length, 1,
    'more than one button in a refusal panel - the other two options are a '
    + 'file this room has never seen and an edit to the recipe above');

// NOT EVERY FAILURE IS A REFUSAL, and dressing one up as the other invents a
// diff. An older ms-moe-maker emits no structure at all.
const plain = T.bsRefusalHtml({ text: 'boom', event: null }, ran);
assert.ok(plain.includes('boom'), 'the prose was dropped along with the structure');
assert.ok(!plain.includes('bs-force'),
    'a force button appeared for a failure that was never a resume refusal - '
    + 'the button must be unreachable without the list of what it destroys');
assert.ok(!T.bsRefusalHtml({ text: 'boom' }, ran).includes('bs-force'),
    'a detail with no event at all still offered to discard finished work');

// An entry that is a SENTENCE about the comparison, not a field that moved.
assert.ok(T.bsFieldRows([{ field: null, text: 'the previous manifest is unreadable' }])
    .includes('dr-note'),
    'a sentence about the comparison was forced into a field/was/now row');

// Free text from another program reaches a browser.
const hostileRefusal = T.bsRefusalHtml({ text: 'x', event: Object.assign(
    {}, REFUSAL.event, { headline: '<img src=x onerror=alert(1)>',
                         fields: [{ field: '<b>f</b>', text: 't',
                                    was: '<i>1</i>', now: '2' }] }) }, ran);
assert.ok(!hostileRefusal.includes('<img src=x'), 'a headline went to the page raw');
assert.ok(!hostileRefusal.includes('<i>1</i>'), 'a value went to the page raw');


// ── a failed write carries its body ─────────────────────────────────────────

const CALLS = [];
function fakeFetch(status, payload) {
    globalThis.fetch = (path, init) => {
        CALLS.push({ path, init, body: init && init.body
                                      ? JSON.parse(init.body) : null });
        return Promise.resolve({
            ok: status >= 200 && status < 300,
            status,
            statusText: status === 409 ? 'Conflict' : 'OK',
            headers: { get: () => 'application/json' },
            json: () => Promise.resolve(payload),
            text: () => Promise.resolve(JSON.stringify(payload)),
        });
    };
}

async function refusalReachesTheCaller() {
    fakeFetch(409, { detail: REFUSAL });
    let caught = null;
    try {
        await T.bsPost('/api/backstage/run', { name: 'x', dryrun: false });
    } catch (e) { caught = e; }
    assert.ok(caught, 'a 409 resolved instead of throwing');
    assert.strictEqual(caught.status, 409,
        'the status was lost, so a refusal cannot be told from a crash');
    assert.ok(caught.detail && caught.detail.event,
        'THE BUG: the response body was thrown away and only the status line '
        + 'survived. This is what put "500 Internal Server Error" on screen '
        + 'while the refusal, the diff and the three ways out sat unread in '
        + 'the body.');
    assert.strictEqual(caught.detail.event.refusal, 'resume_drift');
}

// ── force is reachable only from the refusal, and rebuilds the right thing ──

async function forceRebuildsWhatWasRefused() {
    // The form says something ELSE by the time the button is clicked - which
    // is the realistic case, since the panel sits under an editable textarea.
    el('bs-name').value = 'a-different-recipe.yaml';
    el('bs-list').value = 'a-different-recipe.yaml';

    CALLS.length = 0;
    fakeFetch(200, { pid: 41, stage: 'Lab', command_line: 'ms-moe-maker build ...' });

    const button = {
        id: 'bs-force',
        getAttribute: (a) => ({ 'data-name': 'gauntlet.yaml',
                                'data-dryrun': '0' })[a] || null,
        closest: () => null,
        classList: { contains: () => false },
    };
    for (const fn of CLICKS) { await fn({ target: button }); }

    const run = CALLS.filter((c) => c.path === '/api/backstage/run');
    assert.strictEqual(run.length, 1, `force posted ${run.length} runs`);
    assert.strictEqual(run[0].body.name, 'gauntlet.yaml',
        'force rebuilt whatever was in the name box rather than the recipe '
        + 'that was actually refused - that destroys the wrong run directory');
    assert.strictEqual(run[0].body.force, true, 'force did not reach the wire');
    assert.strictEqual(run[0].body.dryrun, false,
        'the rebuild changed dryrun out from under the person');
}

async function theRunButtonNeverForces() {
    el('bs-name').value = 'gauntlet.yaml';
    el('bs-dryrun').checked = true;
    CALLS.length = 0;
    fakeFetch(200, { pid: 42, stage: 'Lab', command_line: 'ms-moe-maker build ...' });

    const button = {
        id: 'bs-run', getAttribute: () => null, closest: () => null,
        classList: { contains: () => false },
    };
    for (const fn of CLICKS) { await fn({ target: button }); }

    const run = CALLS.filter((c) => c.path === '/api/backstage/run');
    assert.strictEqual(run.length, 1, `Run posted ${run.length} runs`);
    assert.strictEqual(run[0].body.force, false,
        'the ordinary Run button sent force - a standing way to discard '
        + 'finished stages is exactly what the refusal panel exists to avoid');
}

async function aRefusedRunShowsThePanel() {
    el('bs-name').value = 'gauntlet.yaml';
    el('bs-out').innerHTML = '';
    fakeFetch(409, { detail: REFUSAL });
    const button = {
        id: 'bs-run', getAttribute: () => null, closest: () => null,
        classList: { contains: () => false },
    };
    for (const fn of CLICKS) { await fn({ target: button }); }
    const shown = el('bs-out').innerHTML;
    assert.ok(shown.includes('dr-table') && shown.includes('bs-force'),
        'a refused run still rendered as a wall of text with no way to act on '
        + 'it: ' + shown.slice(0, 200));
}

(async () => {
    await refusalReachesTheCaller();
    await forceRebuildsWhatWasRefused();
    await theRunButtonNeverForces();
    await aRefusedRunShowsThePanel();
    console.log('viewer probe OK');
})().catch((e) => { console.error(e); process.exit(1); });
