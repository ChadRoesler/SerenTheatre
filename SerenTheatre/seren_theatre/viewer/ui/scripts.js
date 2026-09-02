// ── SerenTheatre - leaf logic on the SerenMeninges shell ────────────────────
// The shell provides api() (same-origin, attaches the saved bearer),
// escapeHtml(), showTab() and the 🔑 token modal. We call them.
//
// Theatre is READ-ONLY and localhost by default, so there is no auth dance here
// and no action buttons to fail closed - the whole surface is GETs.
//
// ONE RULE THIS FILE FOLLOWS THROUGHOUT: never invent a reading. An empty stage
// list is rendered as "the room is empty", not as a spinner. A run whose
// manifest went quiet is "stalled", not still running. A status this viewer does
// not recognise is rendered as itself with a hollow marker, not silently bucketed
// into "pending". The dashboard is a claim about reality; the only failure that
// really matters is being confidently wrong.

const $ = (id) => document.getElementById(id);

let TIMER = null;
let REFRESH_MS = 5000;

// Which cards the operator collapsed, by key, so a refresh every 5s does not
// keep re-opening what they just shut. In memory only - no storage APIs.
const COLLAPSED = new Set();

// -- (1) DO NOT REWRITE THE DOM WHEN NOTHING STRUCTURAL CHANGED --------------
//
// THE ANNOYANCE. `load()` replaced #stages-body wholesale every five seconds.
// Collapse state survived that (COLLAPSED, above) because somebody had already
// hit this problem once; SCROLL POSITION did not. So you could be halfway down
// the playbill reviewing a config and - BOOP - back to the top, every five
// seconds, forever.
//
// THE SIZE OF IT, beyond the annoyance: the manifest is written on STAGE
// TRANSITIONS, and a fine-tune stage runs about 58 minutes. Across one of them
// roughly 700 consecutive polls carry a board that is identical in every way
// that has a pixel on screen, and all 700 rebuilt the page.
//
// THE WRINKLE THAT MAKES THE NAIVE VERSION DO NOTHING. Several fields are
// computed against the server's clock and therefore differ on EVERY poll, so
// `JSON.stringify(a) === JSON.stringify(b)` never once matches and you have
// built an elaborate no-op. Hence a SIGNATURE: the payload with the
// clock-derived fields taken out.
//
// THE LIST BELOW WAS DERIVED BY READING sources.py AND manifest.py FOR WHAT IS
// ACTUALLY COMPUTED FROM `now`, not by guessing from field names:
//
//   generated, took_ms   app.state()             the envelope's own clock
//   stalled_for          sources.parse_run_log   now - the log's mtime
//   since                sources._file_activity  now - that file's mtime
//   since_activity       sources.read_launch     now - the newest mtime
//   quiet_for            sources.quiet_reading   now - each file's mtime
//   manifest_quiet_for   sources.quiet_reading   now - manifest `updated`
//   newest_activity_for  sources.quiet_reading   the smallest of those
//   elapsed              manifest.Stage.elapsed  now - started, while running
//
// ADD TO THIS SET WHEN A NEW CLOCK-DERIVED FIELD IS ADDED SERVER-SIDE, and be
// aware that forgetting to is INVISIBLE: the signature simply never matches
// again, the page goes quietly back to re-rendering every five seconds, and
// nothing anywhere says so. If the scroll starts jumping again, look here
// first, and diff a payload against itself two seconds apart.
//
// DELIBERATELY NOT EXCLUDED: `stale` and `state`, which manifest.py also
// derives from the clock. They only CHANGE when the run's story changes, and
// that change is exactly the re-render this must not miss.
const CLOCK_DERIVED = new Set([
    'generated', 'took_ms',
    'stalled_for',
    'since', 'since_activity',
    'quiet_for', 'manifest_quiet_for', 'newest_activity_for',
    'elapsed',
]);

let LAST_SIGNATURE = null;

// The payload, minus the clock, as a stable string.
//
// Keys are SORTED rather than taken in payload order, so a server refactor that
// reorders a dict - a thing python does for free and nobody notices - does not
// read as a structural change and flush the page.
//
// TWO CLOCK-DERIVED VALUES GATE WHETHER A BLOCK EXISTS AT ALL rather than what
// it says, so dropping them outright would let a panel appear or vanish with no
// re-render behind it. They come back as the CROSSING and not the number, which
// is stable between crossings and moves exactly once, when it matters.
function structuralSignature(payload) {
    const walk = (v) => {
        if (Array.isArray(v)) return v.map(walk);
        if (v && typeof v === 'object') {
            const out = {};
            Object.keys(v).sort().forEach((k) => {
                if (!CLOCK_DERIVED.has(k)) out[k] = walk(v[k]);
            });
            // renderQuiet draws nothing at all until the manifest has been
            // quiet for longer than after_seconds.
            if (typeof v.manifest_quiet_for === 'number'
                && typeof v.after_seconds === 'number') {
                out['~quiet'] = v.manifest_quiet_for > v.after_seconds;
            }
            // renderLog draws its "no new output" line only for a positive
            // duration - which is all but always, and "all but" is the point.
            if ('stalled_for' in v) out['~stalled'] = !!v.stalled_for;
            return out;
        }
        return v;
    };
    return JSON.stringify(walk(payload));
}

// -- text that ticks, without a re-render -----------------------------------
//
// Every elapsed-time string on the page registers itself under a stable key as
// it is rendered. On a poll that changed nothing structural, the SAME renderers
// run again into a string that is thrown away, and only the registered text is
// written back into the live nodes.
//
// RE-RUNNING THE RENDERERS IS THE POINT, not a shortcut. A second function that
// recomputed these values would be a second implementation of every "how long
// ago" on the page, and the two would drift - quietly, in a number, which is
// the worst place in this codebase for anything to drift. Building a string and
// discarding it costs microseconds; the expense being avoided is the DOM.
let TICKS = null;

// Marks the element whose ENTIRE text content is `text`. Returns an attribute,
// so it can be dropped into an element that already has a class.
function tickData(key, text) {
    if (TICKS) TICKS.set(key, text);
    return ` data-tick="${escapeHtml(key)}"`;
}

// The same thing as a self-contained span, for a value inside a sentence.
function tick(key, text) {
    return `<span${tickData(key, text)}>${escapeHtml(text)}</span>`;
}

function collectTicks(state) {
    const prev = TICKS;
    TICKS = new Map();
    try {
        stagesHtml(state);
        logsHtml(state);
        return TICKS;
    } finally {
        TICKS = prev;
    }
}

function retick(state) {
    const values = collectTicks(state);
    document.querySelectorAll('[data-tick]').forEach((el) => {
        const v = values.get(el.getAttribute('data-tick'));
        // undefined means this node's key is gone from the payload, which
        // cannot happen without a structural change - so leave the node alone
        // rather than blanking it on a reading we do not have.
        if (v !== undefined && el.textContent !== v) el.textContent = v;
    });
}

// -- (2) THE PLAYBILL NEVER RE-RENDERS --------------------------------------
//
// `resolved` is stamped ONCE at build start and is immutable for the life of
// the run - `build_id` is its digest, and that is what makes this true by
// construction rather than by heuristic. So even when (1) decides something
// real changed, the playbill is not rebuilt unless build_id changed: the live
// <aside> is lifted out before innerHTML destroys it and put straight back,
// with .pb-body's scrollTop restored. Cheapest correct fix for "you could be
// mid review near the bottom then BOOP, up to the top you go."
//
// Keyed by rung path AND build_id, not build_id alone: two stages can be
// running the same recipe, and one panel being adopted into the other's slot
// would leave the second empty.
let KEPT_PLAYBILLS = new Set();

function showError(html) { $('error-slot').innerHTML = `<div class="err">${html}</div>`; }
function clearError() { $('error-slot').innerHTML = ''; }

// -- formatters -------------------------------------------------------------

function fmtDur(s) {
    if (s == null) return '-';
    s = Math.floor(s);
    if (s < 60) return `${s}s`;
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h) return `${h}h ${m}m`;
    return `${m}m ${s % 60}s`;
}

function fmtAge(epoch) {
    if (!epoch) return '-';
    return fmtDur(Date.now() / 1000 - epoch) + ' ago';
}
// The date a run was executed, in the reader's own locale. The sketch asks for
// it in the header and the manifest has carried `started` all along.
function fmtWhen(epoch) {
    if (!epoch) return '-';
    const d = new Date(epoch * 1000);
    if (isNaN(d.getTime())) return '-';
    return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
}


function fmtBytes(n) {
    if (n == null) return '-';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

// 'warned' joined the writer's vocabulary while the manifest contract test was
// blind. Without it here, a status the reader understands perfectly well paints
// as the hollow unknown marker. known_status from the server is still the gate:
// if this list ever runs ahead of the manifest reader, that flag catches it.
const KNOWN = ['pending', 'running', 'done', 'skipped', 'failed', 'refused',
               'warned'];

// Last path segment of a path from either kind of box.
const base = (p) => String(p).split(/[\\/]/).pop() || String(p);

// -- collapsible cards ------------------------------------------------------
// Delegated, so it survives the innerHTML replacement on every poll. Keyed by
// a stable card key rather than DOM position - a new run appearing at the top
// must not silently collapse the one you were reading.

function card(key, title, badge, sub, body, startCollapsed) {
    const collapsed = COLLAPSED.has(key) || (startCollapsed && !COLLAPSED.has('!' + key));
    return `<div class="card collapsible ${collapsed ? 'collapsed' : ''}" data-key="${escapeHtml(key)}">
        <h3><span class="twisty">▾</span>${title}${badge || ''}</h3>
        ${sub ? `<div class="sub">${sub}</div>` : ''}
        <div class="card-body">${body}</div>
    </div>`;
}

document.addEventListener('click', (e) => {
    // Both levels of collapsible share one delegated handler: the run
    // cards and the step cards inside them. One mechanism, not two - a
    // second listener with its own key rules is how the two would start
    // disagreeing about what is open.
    const head = e.target.closest(
        '.card.collapsible > h3, .step.collapsible > .step-head');
    if (!head) return;
    const el = head.parentElement;
    const key = el.getAttribute('data-key');
    if (el.classList.toggle('collapsed')) {
        COLLAPSED.add(key);
        COLLAPSED.delete('!' + key);
    } else {
        COLLAPSED.delete(key);
        COLLAPSED.add('!' + key);   // an explicit re-open beats a default
    }
});

// -- the stage ladder -------------------------------------------------------

// -- the steps -------------------------------------------------------------
//
// WAS A FLAT LADDER OF 17 ROWS, all expanded, one row per stage of a gauntlet.
// That is a wall you read rather than a thing you glance at, and the brief is
// tiered seats with the house lights down: you look, you learn where the run
// is, you look away.
//
// So each step is now a small collapsible card - collapsed to a single row,
// opening to whatever is actually recorded about it. Same mechanics as .card,
// one level down and quieter, rather than a second design system.
//
// The glyph on the right is the sketch's (✔ / ⏳ / …) and it carries the
// status colour, which used to live on a bullet at the left. One indicator for
// one fact. A status this viewer does not recognise gets '?' and the hollow
// treatment, never a glyph borrowed from a state we do know.
const GLYPH = {
    pending: '…', running: '⏳', done: '✔', skipped: '⊘',
    failed: '✖', refused: '⊗', warned: '⚠',
};

// The newest log in the STAGE directory, offered as detail on the RUNNING step
// only, and labelled with the file it came from. Attributing a stage-level
// tail to one step is an inference; an inference that says so is detail, and
// an unlabelled one is the viewer inventing a reading.
function renderStepLog(log) {
    if (!log) return '';
    const st = log.step || {};
    const bits = [
        log.phase && log.phase !== 'unknown' ? `phase ${log.phase}` : null,
        log.activity ? `busy with ${log.activity}` : null,
        log.subject ? `subject ${log.subject}` : null,
        st.step != null ? `step ${st.step}${st.total ? ' / ' + st.total : ''}` : null,
        st.loss != null ? `loss ${st.loss}` : null,
        st.rate || null,
        st.eta ? `eta ${st.eta}` : null,
    ].filter(Boolean).join(' · ');
    if (!bits) return '';
    return `<div class="det">${escapeHtml(bits)}
        <div class="hint">read from <code>${escapeHtml(log.name)}</code> — the
        newest log in this stage directory, not the manifest.</div></div>`;
}

function renderSteps(key, m, log) {
    const rows = m.stages.map((s) => {
        // known_status comes from the server. Trusting it rather than
        // re-deriving here keeps one implementation of "do we understand this
        // state" - two would eventually disagree, on screen.
        const known = !(s.known_status === false || !KNOWN.includes(s.status));
        const cls = known ? s.status : 'unknown';
        const glyph = known ? (GLYPH[s.status] || '?') : '?';
        const right = (s.status === 'running' || s.status === 'done')
            ? fmtDur(s.elapsed) : '';
        const detail = [
            s.note ? `<div class="note">${escapeHtml(s.note)}</div>` : '',
            s.artifact
                ? `<div class="det">artifact <code>${escapeHtml(s.artifact)}</code></div>`
                : '',
            s.started
                ? `<div class="det">started ${escapeHtml(fmtWhen(s.started))}${
                    s.ended ? ` · ended ${escapeHtml(fmtWhen(s.ended))}` : ''}${
                    s.elapsed != null
                        ? ` · ${tick(`step-elapsed:${key}:${s.id}`,
                                     fmtDur(s.elapsed))}` : ''
                  }</div>`
                : '',
            s.status === 'running' ? renderStepLog(log) : '',
        ].filter(Boolean).join('');
        // The running step opens and finished ones stay shut - the same habit
        // the rung cards already had, one level down. An explicit click still
        // beats the default, both ways, via the '!' key in COLLAPSED.
        const k = `step:${key}:${s.id}`;
        const open = s.status === 'running';
        const collapsed = COLLAPSED.has(k) || (!open && !COLLAPSED.has('!' + k));
        return `<div class="step collapsible ${cls} ${collapsed ? 'collapsed' : ''}"
                     data-key="${escapeHtml(k)}">
            <div class="step-head">
                <span class="twisty">▾</span>
                <span class="label">${escapeHtml(s.label)}</span>
                <span class="meta"${
                    tickData(`step-meta:${key}:${s.id}`, right)
                }>${escapeHtml(right)}</span>
                <span class="glyph" title="${escapeHtml(s.status)}">${glyph}</span>
            </div>
            <div class="step-body">${detail
                || '<div class="det hint">Nothing further recorded for this step.</div>'}
            </div>
        </div>`;
    }).join('');
    return `<div class="ladder">${rows}</div>`;
}

// ② STALENESS, REPORTED RATHER THAN CONCLUDED.
//
// The old block here said, in red, that a manifest quiet for 46m meant the
// process had most likely been killed. The manifest is written on STAGE
// TRANSITIONS
// and a fine-tune stage runs about 58 minutes, so that fired on every healthy
// fine-tune - eight consecutive hour-long false alarms on an 8-expert gauntlet,
// while it trained perfectly.
//
// The deeper fault was the wording. A dead process is a conclusion no single
// mtime can support. So: print the readings - manifest quiet 46m · log
// wrote 3s ago - and let the reader draw it. The server decided the state
// (sources.activity_state); this only says what the numbers were.
// (4) Grammar, not decoration. "log wrote 3s ago" reads fine; "artifacts wrote
// 3s ago" does not, because sources.rung_activity's reading is a DIRECTORY - a
// set of files, and often a set of one that is itself a subdirectory. A role
// not named here keeps the original phrasing, so a future evidence source shows
// up readable instead of requiring an edit here first.
const ACTIVITY_PHRASE = {
    artifacts: (age) => `artifacts written ${age} ago`,
};
const ACTIVITY_ABSENT = {
    artifacts: 'the run directory is not there to read',
};

function renderQuiet(r) {
    const q = r.quiet;
    if (!q || q.manifest_quiet_for == null) return '';
    if (q.manifest_quiet_for <= q.after_seconds) return '';
    const reads = [`manifest quiet ${fmtDur(q.manifest_quiet_for)}`].concat(
        (q.files || []).map((f) => (f.exists && f.quiet_for != null)
            ? (ACTIVITY_PHRASE[f.role]
                || ((age) => `${f.role} wrote ${age} ago`))(fmtDur(f.quiet_for))
            : (ACTIVITY_ABSENT[f.role] || `${f.role} file never appeared`)));
    // (1) The whole line is one tick node: every number in it is an age, and
    // none of them can move without the payload moving structurally.
    const readsText = reads.join(' · ');
    const line = `<div class="reads"${
        tickData(`quiet-reads:${r.path}`, readsText)}>${
        escapeHtml(readsText)}</div>`;
    if (q.recent_write) {
        return `<div class="quiet">The manifest has not been rewritten in
            ${tick(`quiet-manifest:${r.path}`, fmtDur(q.manifest_quiet_for))} — it
            is written on stage
            transitions, and a fine-tune stage runs for about an hour. Something
            in this directory is still being written.${line}</div>`;
    }
    // "not the log" alone stopped being the whole story the moment the rung
    // directory became evidence: on a hand-run build there IS no log, and the
    // sentence has to name what was actually looked at.
    return `<div class="err">Nothing here has been written for
        ${tick(`quiet-newest:${r.path}`, fmtDur(q.newest_activity_for))} — not the
        manifest, not the log, and nothing new in the run directory. That is a
        reading and not a verdict: a stat() cannot tell you
        whether the process is alive.${line}</div>`;
}

// ④ THE PLAYBILL - the recipe that was executed, defaults filled in.
//
// SOURCED FROM THE MANIFEST AND NOT FROM A RECIPE FILE ON DISK, deliberately.
// A recipe is mutable and can be edited after the run; `resolved` is the
// record of what was actually built.
//
// It is also NOT the whole recipe, and the panel says so in words rather than
// letting the reader assume. `resolved` is the FINGERPRINT: the values that
// decide what the build produces. The writer excludes identity and paths, the
// force/redo flags, throughput tuning that changes how fast the teacher runs
// but not what it emits, and the smoke-test settings that inspect an artifact
// rather than build one. Seventeen fields, curated on purpose. Presenting the
// other seventy-five as "the recipe" would be a lie of omission.
//
// GROUPED BY WHAT A FIELD DECIDES, not alphabetically: seventy-five sorted
// keys is a dump, not a review. The config's own names already cluster
// (abliterate_, reasoning_, router_, lora_, warmup_, vllm_, shared_expert_),
// so prefixes do most of the work and a short list of exacts catches the
// singletons. First match wins, so the order below is the grouping.
//
// ANYTHING UNMATCHED LANDS IN "other" AND IS RENDERED. The day the writer adds
// a field it appears there rather than vanishing - the same bargain `extra`
// makes one layer down, for the same reason.
const PLAYBILL_GROUPS = [
    ['model', ['base_'], ['size', 'base', 'tier', 'seed', 'dryrun']],
    ['experts', [], ['expert_names', 'synth_experts', 'reasoning_experts',
                     'tools_expert_name', 'reasoning_expert_name']],
    ['abliterate', ['abliterate_'], []],
    ['reasoning', ['reasoning_'], ['reasoning']],
    ['corpus', ['code_prompt_'], ['num_code_samples', 'collect_token_target',
        'chars_per_token_est', 'min_samples_per_expert', 'max_shards',
        'num_agent_samples', 'per_repo_cap', 'expert_token_budget']],
    ['teacher', ['teacher_', 'vllm_'], ['use_vllm']],
    ['training', ['lora_', 'warmup_'], ['max_seq_length', 'load_in_4bit',
        'optim', 'gradient_checkpointing', 'packing_strategy', 'target_modules',
        'attn_impl', 'per_device_batch', 'grad_accum', 'lr_lora',
        'specialist_save_steps', 'use_unsloth', 'target_steps']],
    ['router', ['router_'], ['lr_router', 'agent_mix_fraction']],
    ['moe', ['shared_expert_'], ['experts_per_tok', 'norm_topk_prob',
                                 'mlp_only_layers']],
    ['eval', ['eval_'], []],
];

// (3) WRAP AT THE SEPARATORS, NOT ANYWHERE.
//
// The reported symptom was a defaults path rendering as
//
//     /mnt/nvme/msMoEMake
//     r/lib/python3.12/site-
//     packages/ms_moe_make
//     r/assets/defaults.yaml
//
// Two faults stacked. It sat in a 42%-wide value column, AND `overflow-wrap:
// anywhere` broke it MID-TOKEN, which is what makes it look broken rather than
// merely long. To a beginner that reads as a bug in the tool.
//
// `<wbr>` gives the wrapper legal places to break, so it takes those instead of
// inventing illegal ones. `overflow-wrap: anywhere` stays in the CSS as the
// backstop for a single segment wider than its column - the difference is that
// it is now the last resort rather than the first.
//
// ESCAPE FIRST, THEN INSERT. The other order feeds the markup through
// escapeHtml and renders `<wbr>` as visible text - and this string is a
// filesystem path out of a manifest somebody else wrote, which is what makes
// the escaping the part that is not negotiable. Safe in this order because no
// entity escapeHtml produces contains `/` or a backslash.
function breakablePath(p) {
    return escapeHtml(String(p))
        .replace(/\//g, '/<wbr>')
        .replace(/\\/g, '\\<wbr>');
}

function pbValue(v) {
    if (v === null || v === undefined) return '<span class="hint">null</span>';
    if (Array.isArray(v)) {
        // Joined with ', ' already, so the spaces are break opportunities and
        // expert_names / target_modules wrap fine as they are. Left alone on
        // purpose: reformatting a value that reads correctly is churn.
        return v.length ? escapeHtml(v.join(', '))
                        : '<span class="hint">(empty)</span>';
    }
    if (typeof v === 'object') return `<code>${escapeHtml(JSON.stringify(v))}</code>`;
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (v === '') return '<span class="hint">(blank)</span>';
    // The same problem one column over and the same fix: `base` is a model id
    // like Qwen/Qwen2.5-Coder-14B-Instruct, and every base_* and path-shaped
    // value has separators worth breaking at. A value with none is untouched.
    const s = String(v);
    if (s.includes('/') || s.includes('\\')) return breakablePath(s);
    return escapeHtml(s);
}

function pbBlock(label, keys, res) {
    return `<div class="pb-group"><h5>${escapeHtml(label)}</h5><dl class="kv">`
        + keys.map((k) => `<dt>${escapeHtml(k)}</dt><dd>${pbValue(res[k])}</dd>`)
              .join('')
        + `</dl></div>`;
}

function renderPlaybill(m, rungPath) {
    // (2) The panel is immutable for the life of a build. `key` is what decides
    // whether the live node gets adopted instead of rebuilt - see renderStages.
    const key = m.build_id ? `${rungPath} ${m.build_id}` : '';
    if (key && KEPT_PLAYBILLS.has(key)) {
        // A placeholder. renderStages swaps the existing <aside> - and its
        // scroll position - back into this slot.
        return `<aside class="playbill" data-playbill="${escapeHtml(key)}"></aside>`;
    }
    // Nothing in this panel is clock-derived, so the tick-collecting pass has
    // no reason to build it at all. See collectTicks.
    if (TICKS) return '';
    const res = m.resolved || {};
    const keys = Object.keys(res);
    const files = m.defaults_files || {};
    const fkeys = Object.keys(files);
    if (!keys.length && !fkeys.length && !m.build_id) {
        // A true reading, not an error: an older writer, or a directory
        // somebody redirected a log into with no pipeline cooperating at all.
        return `<aside class="playbill" data-playbill=""><h4>Playbill</h4>
            <div class="empty">This run's manifest carries no resolved config,
            so there is nothing to show here. That is a reading, not a
            failure — the run is watchable either way.</div></aside>`;
    }
    const taken = new Set();
    const blocks = PLAYBILL_GROUPS.map(([label, prefixes, exacts]) => {
        const mine = keys.filter((k) => !taken.has(k)
            && (exacts.includes(k) || prefixes.some((p) => k.startsWith(p))));
        mine.forEach((k) => taken.add(k));
        return mine.length ? pbBlock(label, mine, res) : '';
    }).join('');
    const rest = keys.filter((k) => !taken.has(k));
    const other = rest.length ? pbBlock('other', rest, res) : '';
    // The short hash beside each defaults file is the point of showing them at
    // all: it says WHICH version of that file this run inherited, which the
    // path alone cannot, because the file has probably been edited since.
    // (3) A FULL-WIDTH ROW, NOT A KEY/VALUE PAIR.
    //
    // A defaults path is not a short key beside a short value; it is one long
    // string that needs the whole panel. In the 42%/58% kv grid it had nowhere
    // to go but down, four characters at a time.
    //
    // THE PATH, not the basename. Two defaults files in different directories
    // are both called defaults.yaml, and showing only the name rendered them as
    // the same file with two different hashes - which reads as a contradiction
    // rather than as two files. So the FILENAME leads, because that is the
    // readable thing, and the full path sits under it as the context that tells
    // the two apart.
    //
    // AND THE HASH IS LABELLED. `3f0e3b6c1432` on its own reads like an id. It
    // is the first 12 hex of the sha256 of that FILE'S CONTENTS, and the
    // writer's own docstring says why that is worth conveying: "a build id says
    // two runs differ; this says WHICH file on which box was different, which
    // is the question somebody actually has at 2am when their run and yours
    // disagree." A bare hex blob answers none of that.
    const defaults = fkeys.length
        ? `<div class="pb-group pb-defaults"><h5>defaults inherited</h5>`
          + fkeys.map((f) => `<div class="pb-file">
                <div class="pb-file-name">${escapeHtml(base(f))}</div>
                <div class="pb-file-path">${breakablePath(f)}</div>
                <div class="pb-file-hash"><span class="hint">sha256 · first 12</span>
                    <code title="sha256 of this file's contents, first 12 hex — which version of it this run inherited">${
                        escapeHtml(String(files[f]))}</code></div>
            </div>`).join('')
          + `</div>`
        : '';
    return `<aside class="playbill" data-playbill="${escapeHtml(key)}">
        <h4>Playbill${m.build_id
            ? ` <code title="digest of the resolved config">${
                escapeHtml(m.build_id)}</code>` : ''}</h4>
        <div class="pb-note">The <b>fingerprinted</b> config: the ${keys.length}
        resolved values that decide what this build produces, defaults already
        filled in. Not the recipe verbatim — paths, redo flags, throughput
        tuning and smoke-test settings are excluded by the writer because none
        of them change the artifact. Read off the manifest rather than a recipe
        file, because a recipe can be edited after the run and this cannot.</div>
        <div class="pb-body">${defaults}${blocks}${other}</div>
    </aside>`;
}

function renderRefusals(m) {
    if (!m.refusals || !m.refusals.length) return '';
    const items = m.refusals.map((r) => `<li>${escapeHtml(r)}</li>`).join('');
    return `<div class="refusals">
        <h4>${m.refusals.length} recipe field(s) not honoured</h4>
        <ul>${items}</ul>
    </div>`;
}

// THE SMOKE TEST HAS THREE STATES AND ONLY ONE OF THEM IS A PASS.
//
// This line used to read `r.smoketested`, which was the existence of
// `.smoketest.txt` - the LOG, which the writer opens BEFORE the checks that can
// fail. So the room printed "smoke-tested" over a GGUF that flunked its
// degenerate-output check and emits one token forever. The proof is
// `.smokepass.txt`, written only after every check passes.
//
// The middle state is the one that was being rounded to success, so it is the
// loud one here. It is called "failed or unproven" rather than "failed" because
// disk genuinely cannot tell a failure from a run that predates the proof file
// - both are "not proven", neither is a pass, and inventing the difference
// would just be the old bug pointed the other way.
function renderSmoke(r) {
    const s = r.smoke;
    if (!s) return '';
    if (s.state === 'passed') {
        return ' · <span class="smoke pass" title="A .smokepass.txt is on disk:'
             + ' the writer only writes it after every check passes.">smoke'
             + ' passed</span>';
    }
    if (s.state === 'not run') {
        return ' · <b class="smoke none" title="Neither a smoke-test log nor a'
             + ' pass proof is on disk.">not smoke-tested</b>';
    }
    return ' · <b class="smoke fail" title="A smoke-test LOG is here with no'
         + ' .smokepass.txt beside it. Either the test ran and failed, or this'
         + ' run predates the proof file. Read the log next to the GGUF.">smoke'
         + ' test ran, did not pass</b>';
}

// -- the stagehand run marker ----------------------------------------------
//
// `.stagehand-run.json` says a build was LAUNCHED from this stage directory:
// the command line, the recipe, the pid, when, and whether the child survived
// being started. It says NOTHING about progress and this panel must not imply
// it - the manifest on each rung card keeps that job, and two opinions about
// what a run is doing is how a dashboard starts disagreeing with itself.
//
// It sits above the rung cards rather than inside one because the marker is a
// property of the stage directory, and because a launch that died on arrival
// never creates a rung at all: inside a rung card is precisely where it would
// be invisible in the case that matters most.
function renderLaunch(s) {
    if (s.launch_error) {
        return `<div class="card launch"><h3>${escapeHtml(s.name)} · stagehand
            <span class="badge failed">marker unreadable</span></h3>
            <div class="card-body"><div class="err">A stagehand run marker is
            present in this directory but could not be read:
            ${escapeHtml(s.launch_error)}. Everything below is from the
            manifests and the disk and is unaffected.</div></div></div>`;
    }
    const L = s.launch;
    if (!L) return '';

    const died = L.launched === false;
    // 'finished' IS NOT 'started' AND MUST NOT BE PAINTED AS A LIVE RUN. The
    // child was already gone when stagehand looked, having exited 0 inside the
    // liveness window - `ms-moe-maker build --plan` resolves the config, prints
    // its stages and is done in about a third of a second. That is a success
    // and a common one, but showing it as "launched" would leave somebody
    // watching for progress from a pid that ended before the page loaded.
    const finished = String(L.launch_state).toLowerCase() === 'finished';
    const badge = died
        ? '<span class="badge failed">launch failed</span>'
        : (finished
            ? '<span class="badge finished">ran and finished</span>'
            : (L.launched === true
                ? '<span class="badge idle">launched</span>'
                : `<span class="badge unknown">${escapeHtml(
                    L.launch_state || 'outcome not recorded')}</span>`));

    // A launch that died on arrival leaves nothing else behind - no rung, no
    // manifest, often not even a log - so this is the one thing on the page
    // that gets to shout. The tail is the child's dying words, and it is the
    // reason the panel is worth having rather than just a badge.
    const dead = died
        ? `<div class="err"><b>This build did not survive being started.</b>
           ${escapeHtml(L.launch_detail
               || 'The marker records the launch failing.')}${
           L.exit_code != null
               ? ` (exit code ${escapeHtml(String(L.exit_code))})` : ''}</div>`
          + (L.log_tail ? `<pre class="tail">${escapeHtml(L.log_tail)}</pre>` : '')
        : '';

    // LAST ACTIVITY IS A MTIME, NOT A HEARTBEAT, and it is never rendered as
    // "dead". A healthy run is silent for whole minutes - a 25-minute weight
    // load writes nothing at all - so this says when the file last grew and
    // stops there. A nine-hour run that stopped writing four hours ago is the
    // thing a person most needs to see; the conclusion is theirs to draw.
    const acts = (L.files || []).map((f) => f.exists
        ? `${f.role} last wrote ${fmtAge(f.mtime)} · ${fmtBytes(f.size)}`
        : `${f.role} file never appeared`).join(' · ');

    // Joined UNESCAPED and escaped once at the end, because the tick registry
    // holds the text a node will later be handed via textContent - giving it an
    // already-escaped string would put a literal &amp; on the page five seconds
    // after the correct character. Identical output either way: the separator
    // has nothing escapable in it.
    const rows = [
        L.started ? `launched ${fmtAge(L.started)}` : null,
        L.pid != null ? `pid ${L.pid}` : null,
        L.recipe ? `recipe ${base(L.recipe)}` : null,
    ].filter(Boolean).join(' · ');

    // An exit code is recorded whenever the child was already gone - which
    // includes the good case, where it is 0. So it gets said in words here
    // rather than left to look like the failure block's twin.
    const ended = finished
        ? `<div class="launch-row">The child had already exited${
            L.exit_code != null
                ? ` with code ${escapeHtml(String(L.exit_code))}` : ''
          } when stagehand looked — a run legitimately shorter than the
          liveness window, which <code>--plan</code> and anything else that
          does its whole job fast will be. Not a failure, and not still
          running.</div>`
        : '';

    // Say which schema we met rather than pretending to have read all of it.
    const schema = (L.schema_version != null
                    && L.schema_version !== L.understands_schema)
        ? `<div class="launch-row hint">This marker is schema
           ${escapeHtml(String(L.schema_version))}; this viewer reads schema
           ${escapeHtml(String(L.understands_schema))}. Anything the two do not
           share is simply absent above.</div>` : '';
    // Absent is not the same as fine. Saying so is the entire house style.
    const unsure = (L.launched === null)
        ? `<div class="launch-row hint">This marker does not record whether the
           launch survived — which is not the same as recording that it
           did.</div>` : '';

    // Named for the STAGE it belongs to. When rungs exist this panel sits above
    // cards titled by rung, and an unlabelled "stagehand" heading in that stack
    // does not say which directory it is a reading of.
    return `<div class="card launch"><h3>${escapeHtml(s.name)} · stagehand${badge}</h3>
        ${L.command_line
            ? `<div class="sub"><code>${escapeHtml(L.command_line)}</code></div>`
            : ''}
        <div class="card-body">${dead}
            ${rows ? `<div class="launch-row"${
                tickData(`launch-rows:${s.name}`, rows)}>${
                escapeHtml(rows)}</div>` : ''}${ended}
            ${acts ? `<div class="launch-row"${
                tickData(`launch-acts:${s.name}`, acts)}>${
                escapeHtml(acts)}</div>` : ''}
            ${unsure}${schema}
        </div></div>`;
}

function renderRung(r, log) {
    const m = r.manifest;
    if (m) {
        // The state the SERVER decided, which may have had the log's vote -
        // see sources.activity_state. Falling back to the manifest-only word
        // when a payload has no rung-level `state`, so an older server or a
        // non-current run still renders.
        const state = r.state || m.state;
        const badge = `<span class="badge ${state}">${escapeHtml(state)}</span>`;
        // Recipe name · date executed · status, per the sketch. The directory
        // name stays on the line because two runs of one recipe are told apart
        // by nothing else.
        const sub = [
            m.started ? `executed ${escapeHtml(fmtWhen(m.started))}` : null,
            m.size && escapeHtml(m.size),
            `${m.done_count}/${m.stage_count} steps`,
            `<code>${escapeHtml(r.name)}</code>`,
            // Say which readings decided the badge, whenever it was not the
            // manifest alone. Same honesty as `source` one field over.
            (r.state_source && r.state_source !== 'manifest')
                ? `<span class="hint" title="the manifest's own word was '${
                    escapeHtml(m.state)}'">${escapeHtml(r.state_source)}</span>`
                : null,
        ].filter(Boolean).join(' · ');
        // Finished runs start collapsed: the one you want open is the one
        // still moving.
        const done = state === 'finished';
        const body = `<div class="run"><div class="run-main">${renderQuiet(r)}${
            renderSteps(r.path, m, log)}${renderRefusals(m)}</div>${
            renderPlaybill(m, r.path)}</div>`;
        return card(r.path, escapeHtml(m.name || r.name), badge, sub, body, done);
    }

    // Fallback reading: no manifest, so this is what is ON DISK. Say so.
    const bits = [];
    if (r.specialists.length) {
        bits.push(`${r.specialists.length} specialist(s): ` +
            r.specialists.map(escapeHtml).join(', '));
    }
    if (r.skeleton) bits.push('skeleton stitched');
    if (r.final) bits.push('router trained');
    if (r.gguf) {
        // Converted is not proven, and neither is tested. See renderSmoke: the
        // old ternary here read a LOG that exists for failed smoke tests too.
        bits.push(`GGUF ${escapeHtml(r.gguf.name)} (${r.gguf.gb} GB)`
            + renderSmoke(r));
    }
    const err = r.manifest_error
        ? `<div class="err">A manifest is present but could not be read:
           ${escapeHtml(r.manifest_error)}. Showing what is on disk instead.</div>`
        : '';
    const body = err + (bits.length
        ? `<div class="hint">${bits.join(' · ')}</div>`
        : `<div class="empty">Nothing built here yet.</div>`);
    return card(r.name, escapeHtml(r.name),
                `<span class="badge disk">${escapeHtml(r.source)}</span>`,
                '', body, false);
}

// PURE STRING BUILDERS, and the split is load-bearing rather than tidying:
// collectTicks re-runs these to recompute the elapsed-time text without going
// anywhere near the DOM. A renderer that wrote to the page could not be used
// that way, and a second copy of the calculations would drift.
function stagesHtml(state) {
    if (!state.stages.length) {
        return `<div class="empty">
            <b>The room is empty.</b><br>
            No stages configured — which is a true reading, not an error.<br>
            Set <code>SEREN_THEATRE_STAGE=/path/to/lab</code>, or add a
            <code>stages:</code> block to your config.
        </div>`;
    }
    return state.stages.map((s) => {
        if (!s.exists) {
            return `<div class="card"><h3>${escapeHtml(s.name)}</h3>
                <div class="card-body"><div class="err">Directory not found:
                <code>${escapeHtml(s.path)}</code></div></div></div>`;
        }
        // The launch panel goes ABOVE the rung cards, and it is rendered
        // even when there are no rungs - that is exactly the shape a build
        // that died on arrival leaves behind, and "No runs here yet" on its
        // own is a true sentence that tells you nothing about why.
        const launch = renderLaunch(s);
        if (!s.rungs.length) {
            return launch + `<div class="card"><h3>${escapeHtml(s.name)}</h3>
                <div class="sub"><code>${escapeHtml(s.path)}</code></div>
                <div class="card-body"><div class="empty">No runs here yet.</div>
                </div></div>`;
        }
        // ONE RUN ON STAGE: the current one, or the most recent. The
        // server ordered them and counted the rest (sources.order_rungs),
        // so the page and /api/state agree about which is current.
        //
        // The earlier runs are still in the payload and the count is SHOWN.
        // A viewer that silently drops data is the thing this codebase
        // keeps fixing - and the count is the seam a Previous Shows
        // section lands on later.
        const earlier = s.earlier || 0;
        const seam = earlier
            ? `<div class="earlier">${earlier} earlier run${
                earlier === 1 ? '' : 's'} in <code>${escapeHtml(s.path)}</code>
               — not shown here, and still on <code>/api/state</code>.</div>`
            : '';
        return launch + renderRung(s.rungs[0], (s.logs || [])[0]) + seam;
    }).join('');
}

// (2) Lift the playbills out, rebuild everything else, put them back.
//
// The rescue has to happen BEFORE innerHTML, and the scrollTop has to be read
// while the node still has a box - a detached element reports 0 and forgets
// where it was. So: measure, replace, re-insert, restore.
function renderStages(state) {
    const host = $('stages-body');
    const kept = new Map();
    host.querySelectorAll('aside.playbill[data-playbill]').forEach((el) => {
        const key = el.getAttribute('data-playbill');
        if (!key) return;               // no build_id: nothing to key on
        const body = el.querySelector('.pb-body');
        kept.set(key, { el, scroll: body ? body.scrollTop : 0 });
    });
    KEPT_PLAYBILLS = new Set(kept.keys());
    let html;
    try {
        html = stagesHtml(state);
    } finally {
        // Cleared unconditionally: leaving it set would make the NEXT caller -
        // collectTicks, say - emit placeholders nobody is going to fill in.
        KEPT_PLAYBILLS = new Set();
    }
    host.innerHTML = html;
    host.querySelectorAll('aside.playbill[data-playbill]').forEach((el) => {
        const old = kept.get(el.getAttribute('data-playbill'));
        if (!old) return;               // a new build_id: the fresh one stands
        el.replaceWith(old.el);
        const body = old.el.querySelector('.pb-body');
        if (body) body.scrollTop = old.scroll;
    });
}

// -- logs -------------------------------------------------------------------

function renderLog(l) {
    const st = l.step || {};
    const pct = (st.step != null && st.total)
        ? Math.min(100, Math.round(100 * st.step / st.total)) : null;
    const bar = pct == null ? '' : `<div class="bar"><i style="width:${pct}%"></i></div>`;
    const kv = [
        ['phase', l.phase],
        ['activity', l.activity],
        ['subject', l.subject],
        ['step', st.step != null ? `${st.step}${st.total ? ' / ' + st.total : ''}` : null],
        ['loss', st.loss],
        ['grad_norm', st.grad_norm],
        ['rate', st.rate],
        ['eta', st.eta],
        ['size', fmtBytes(l.size)],
        // The third slot is a tick key: this row is an AGE, and it is the only
        // thing in this card that moves between two identical polls.
        ['modified', fmtAge(l.mtime), `log-modified:${l.path}`],
    ].filter(([, v]) => v != null && v !== '')
        .map(([k, v, tk]) => `<dt>${k}</dt><dd${
            tk ? tickData(tk, String(v)) : ''}>${escapeHtml(String(v))}</dd>`)
        .join('');

    const stalled = l.stalled_for
        ? `<div class="err">No new output for
           ${tick(`log-stalled:${l.path}`, fmtDur(l.stalled_for))}.</div>` : '';
    const warns = (l.warnings || []).length
        ? `<ul class="warnlist">${l.warnings.map(
            (w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>` : '';
    const miles = (l.milestones || []).length
        ? `<div class="mile">✓ ${l.milestones.map(escapeHtml).join(' · ')}</div>` : '';

    return card('log:' + l.path, escapeHtml(l.name), '',
                `<code>${escapeHtml(l.path)}</code>`,
                stalled + bar + `<dl class="kv">${kv}</dl>` + miles + warns,
                false);
}

function logsHtml(state) {
    const logs = state.stages.flatMap((s) => s.logs || []);
    if (!logs.length) {
        // Say WHERE it looked and WHAT for. "No logs found" on its own sends
        // you to check whether the tab is broken; naming the directory and the
        // pattern sends you to check the thing that is actually wrong.
        const where = state.stages.map(
            (s) => `<li><code>${escapeHtml(s.path)}</code></li>`).join('');
        return `<div class="empty">
            <b>No logs found.</b><br>
            Looked in:<ul style="list-style:none;padding:0">${where || '<li>(no stages)</li>'}</ul>
            for the <code>logs:</code> globs in your config (default
            <code>*.log</code>).<br>
            Redirect a run into a watched directory and it appears here —
            nothing needs instrumenting.
        </div>`;
    }
    return logs.map(renderLog).join('');
}

function renderLogs(state) {
    $('logs-body').innerHTML = logsHtml(state);
}

// -- the loop ---------------------------------------------------------------

async function load() {
    try {
        const state = await api('/api/state');
        clearError();

        const rungs = state.stages.flatMap((s) => s.rungs || []);
        const logs = state.stages.flatMap((s) => s.logs || []);
        const running = rungs.filter(
            (r) => r.manifest && r.manifest.state === 'running').length;

        // The pill answers a question you actually have: how many runs are
        // here, and is anything moving?
        $('runs-pill').textContent = running
            ? `${rungs.length} runs · ${running} live`
            : `${rungs.length} run${rungs.length === 1 ? '' : 's'}`;
        $('age-pill').textContent = `${state.took_ms} ms`;
        $('age-pill').title = `server read the disk in ${state.took_ms} ms`;

        // Counts on the tabs, so an empty tab is distinguishable from a broken
        // one WITHOUT clicking it.
        $('count-stages').textContent = rungs.length ? `(${rungs.length})` : '';
        $('count-logs').textContent = logs.length ? `(${logs.length})` : '(0)';

        // (1) The whole point. During a 58-minute fine-tune the manifest is not
        // rewritten once, so ~700 consecutive polls carry a structurally
        // identical board - and all 700 used to rebuild the page and throw the
        // reader back to the top of whatever they were reading.
        const signature = structuralSignature(state);
        if (signature === LAST_SIGNATURE) {
            retick(state);
        } else {
            renderStages(state);
            renderLogs(state);
            // Set AFTER the render, so a renderer that threw leaves the next
            // poll trying again rather than believing the page is current.
            LAST_SIGNATURE = signature;
        }

        if (state.refresh_seconds) REFRESH_MS = state.refresh_seconds * 1000;
    } catch (e) {
        showError(`⚠ Could not read <code>/api/state</code>: ${escapeHtml(
            (e && e.message) || String(e))}`);
    }
}

function reload() { load(); }

function schedule() {
    if (TIMER) clearInterval(TIMER);
    // Poll only while the tab is visible. A dashboard left open on a second
    // monitor overnight should not be re-reading log tails every five seconds
    // on the box that is training - the room must never be the reason the
    // machine is busy.
    TIMER = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
}

document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });

load().then(schedule);

// -- backstage --------------------------------------------------------------
// Present in the pack always, ENABLED only when GET / says the router is
// mounted. The tab being hidden is cosmetic; the guarantee is that on a base
// install the routes below do not exist to be called.

let BACKSTAGE = null;

async function loadBackstage() {
    try {
        const root = await api('/');
        const tab = $('tab-backstage');
        if (!root.backstage) {
            if (tab) tab.hidden = true;
            return;
        }
        if (tab) tab.hidden = false;
        BACKSTAGE = await api('/api/backstage');

        $('bs-where').textContent = `recipes · ${BACKSTAGE.recipes_dir}`;
        const list = $('bs-list');
        list.innerHTML = '<option value="">— new recipe —</option>'
            + BACKSTAGE.recipes.map(
                (r) => `<option value="${escapeHtml(r.name)}">${escapeHtml(r.name)}</option>`
            ).join('');

        // The form is built from the LIVE registries, so a kind or validator
        // added by a plugin shows up here without this file having heard of
        // it. A hardcoded list would make "extensible" true only for us.
        const kinds = (BACKSTAGE.kinds || []).map(
            (k) => `<li><code>${escapeHtml(k.name)}</code> — ${escapeHtml(k.summary)}`
                 + (k.requires.length ? ` <span class="hint">(needs ${
                     k.requires.map(escapeHtml).join(', ')})</span>` : '')
                 + `</li>`).join('');
        const vals = (BACKSTAGE.validators || []).map(
            (v) => `<li><code>${escapeHtml(v.name)}</code> — ${escapeHtml(v.summary)}</li>`
        ).join('');
        // The tag table is the third registry, and the one people are told to
        // extend: a family shipping a new delimiter is meant to be answerable
        // with a yaml on the box, not a release. Showing the PACKAGED table to
        // somebody who already dropped ~/.msmoe/reasoning.yaml would be worse
        // than showing nothing, because it would look complete. The delimiters
        // are printed because that is what a wrong entry costs you: the
        // splitter finds nothing, eval says "did not reason", and the think
        // block gets scored as the answer.
        const rz = BACKSTAGE.reasoning || {};
        const styleOf = {};
        (rz.styles || []).forEach((s) => { styleOf[s.key] = s; });
        const fams = (rz.families || []).map((f) => {
            const s = styleOf[f.style];
            const tags = s
                ? `<code>${escapeHtml(s.open)}</code>…<code>${escapeHtml(s.close)}</code>`
                  + (s.interwoven ? ' <span class="hint">(interwoven)</span>' : '')
                : `<span class="hint">unknown style ${escapeHtml(f.style)}</span>`;
            return `<li><code>${escapeHtml(f.key)}</code> — ${escapeHtml(f.name)} `
                 + `→ ${tags}</li>`;
        }).join('');

        // WHAT THE BOX SAID ABOUT ITSELF. Read off whatever `describe`
        // returned rather than listed here, so a key the CLI learns to report
        // shows up with no edit in this file - the same reason the registries
        // above are not a hardcoded list.
        const box = BACKSTAGE.box || {};
        const boxRow = (k, v) => `<li><code>${escapeHtml(k)}</code> — ${
            escapeHtml(Array.isArray(v) ? v.join(', ') : String(v))}</li>`;
        const boxRows = Object.keys(box).map((k) => boxRow(k, box[k])).join('');

        // ERRORS WERE NEVER PAINTED. A corpus kind or validator whose entry
        // point failed to import is simply absent from the lists above, which
        // looks exactly like one that was never installed - so the panel read
        // as a healthy box either way. A registry that could not load has to
        // say so on the screen, for the same reason eval keeps `unmeasurable`
        // apart from `fail` all the way to the front.
        const errs = (BACKSTAGE.errors || []).map(
            (e) => `<li>${escapeHtml(e)}</li>`).join('');

        $('bs-help').innerHTML =
            (errs ? `<b class="warn">the box could not answer everything</b>`
                  + `<ul>${errs}</ul>` : '')
            + `<b>source kinds on this box</b><ul>${kinds}</ul>`
            + `<b>validators</b><ul>${vals}</ul>`
            + (fams
                ? `<b>reasoning families on this box</b><ul>${fams}</ul>`
                : '')
            + (boxRows ? `<b>this install</b><ul>${boxRows}</ul>` : '');
    } catch (e) {
        const tab = $('tab-backstage');
        if (tab) tab.hidden = true;
    }
}

function bsShow(ok, text) {
    $('bs-out').innerHTML =
        `<pre class="${ok ? 'ok' : 'bad'}">${escapeHtml(text || '(no output)')}</pre>`;
}

async function bsPost(path, body) {
    const r = await api(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return r;
}

document.addEventListener('click', async (e) => {
    const id = e.target && e.target.id;
    if (!id || !id.startsWith('bs-')) return;
    const name = $('bs-name').value.trim() || $('bs-list').value;
    const text = $('bs-text').value;

    try {
        if (id === 'bs-validate') {
            const out = await bsPost('/api/backstage/validate', { name: name || 'draft', text });
            bsShow(out.ok, out.output);
        } else if (id === 'bs-save') {
            if (!name) return bsShow(false, 'give it a name first');
            const out = await bsPost('/api/backstage/recipes', { name, text });
            // The validation result is shown even on a successful save. Saving
            // an invalid recipe is allowed - a draft is a legitimate thing to
            // keep - but it must never LOOK clean.
            bsShow(out.validation.ok,
                   `saved ${out.saved}\n\n${out.validation.output}`);
            await loadBackstage();
        } else if (id === 'bs-run') {
            if (!name) return bsShow(false, 'save it first, then run it');
            const out = await bsPost('/api/backstage/run',
                                     { name, dryrun: $('bs-dryrun').checked });
            // No live channel back. The run is watched through the manifest
            // and the log exactly like one started by hand in a terminal -
            // a second way to know what is happening is a second opinion.
            bsShow(true, `started pid ${out.pid} in stage ${out.stage}\n\n`
                       + `${out.command_line}\n\n`
                       + `Watch it on the Stages tab. It survives this viewer `
                       + `restarting.`);
            load();
        }
    } catch (err) {
        bsShow(false, (err && err.message) || String(err));
    }
});

document.addEventListener('change', async (e) => {
    if (e.target && e.target.id === 'bs-list' && e.target.value) {
        const r = await api('/api/backstage/recipes/' + encodeURIComponent(e.target.value));
        $('bs-text').value = r.text;
        $('bs-name').value = r.name;
    }
});

loadBackstage();
