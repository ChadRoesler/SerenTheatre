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

function fmtBytes(n) {
    if (n == null) return '-';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

const KNOWN = ['pending', 'running', 'done', 'skipped', 'failed', 'refused'];

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
    const head = e.target.closest('.card.collapsible > h3');
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

function renderLadder(m) {
    const rows = m.stages.map((s) => {
        // known_status comes from the server. Trusting it rather than
        // re-deriving here keeps one implementation of "do we understand this
        // state" - two would eventually disagree, on screen.
        const cls = s.known_status === false || !KNOWN.includes(s.status)
            ? 'unknown' : s.status;
        const right = (s.status === 'running' || s.status === 'done')
            ? fmtDur(s.elapsed) : '';
        const note = s.note ? `<div class="note">${escapeHtml(s.note)}</div>` : '';
        const art = s.artifact ? ` <code>${escapeHtml(s.artifact)}</code>` : '';
        return `<div class="step ${cls}">
            <span class="dot"></span>
            <span class="label">${escapeHtml(s.label)}${art}</span>
            <span class="meta">${escapeHtml(right)}</span>
            ${note}
        </div>`;
    }).join('');
    return `<div class="ladder">${rows}</div>`;
}

function renderRefusals(m) {
    if (!m.refusals || !m.refusals.length) return '';
    const items = m.refusals.map((r) => `<li>${escapeHtml(r)}</li>`).join('');
    return `<div class="refusals">
        <h4>${m.refusals.length} recipe field(s) not honoured</h4>
        <ul>${items}</ul>
    </div>`;
}

function renderRung(r) {
    const m = r.manifest;
    if (m) {
        const badge = `<span class="badge ${m.state}">${m.state}</span>`;
        const sub = [
            m.name && escapeHtml(m.name),
            m.size && escapeHtml(m.size),
            m.recipe_id && `<code>${escapeHtml(m.recipe_id)}</code>`,
            `${m.done_count}/${m.stage_count} stages`,
            `updated ${fmtAge(m.updated)}`,
        ].filter(Boolean).join(' · ');
        const stalled = m.stale
            ? `<div class="err">This run's manifest says a stage is still
               running, but it has not been updated in
               ${escapeHtml(fmtDur(Date.now() / 1000 - m.updated))}. The process
               was probably killed - a stage that dies never gets to write a
               final status.</div>` : '';
        // Finished runs start collapsed: on a ladder you accumulate one card
        // per rung, and the one you want open is the one still moving.
        const done = m.state === 'finished';
        return card(r.name, escapeHtml(r.name), badge, sub,
                    stalled + renderLadder(m) + renderRefusals(m), done);
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
        bits.push(`GGUF ${escapeHtml(r.gguf.name)} (${r.gguf.gb} GB)` +
            // Converted is not proven. The pipeline learned this the hard way.
            (r.smoketested ? ' · smoke-tested' : ' · <b>not smoke-tested</b>'));
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

function renderStages(state) {
    const host = $('stages-body');
    if (!state.stages.length) {
        host.innerHTML = `<div class="empty">
            <b>The room is empty.</b><br>
            No stages configured — which is a true reading, not an error.<br>
            Set <code>SEREN_THEATRE_STAGE=/path/to/lab</code>, or add a
            <code>stages:</code> block to your config.
        </div>`;
        return;
    }
    host.innerHTML = state.stages.map((s) => {
        if (!s.exists) {
            return `<div class="card"><h3>${escapeHtml(s.name)}</h3>
                <div class="card-body"><div class="err">Directory not found:
                <code>${escapeHtml(s.path)}</code></div></div></div>`;
        }
        if (!s.rungs.length) {
            return `<div class="card"><h3>${escapeHtml(s.name)}</h3>
                <div class="sub"><code>${escapeHtml(s.path)}</code></div>
                <div class="card-body"><div class="empty">No runs here yet.</div>
                </div></div>`;
        }
        return s.rungs.map(renderRung).join('');
    }).join('');
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
        ['modified', fmtAge(l.mtime)],
    ].filter(([, v]) => v != null && v !== '')
        .map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(String(v))}</dd>`)
        .join('');

    const stalled = l.stalled_for
        ? `<div class="err">No new output for
           ${escapeHtml(fmtDur(l.stalled_for))}.</div>` : '';
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

function renderLogs(state) {
    const logs = state.stages.flatMap((s) => s.logs || []);
    const host = $('logs-body');
    if (!logs.length) {
        // Say WHERE it looked and WHAT for. "No logs found" on its own sends
        // you to check whether the tab is broken; naming the directory and the
        // pattern sends you to check the thing that is actually wrong.
        const where = state.stages.map(
            (s) => `<li><code>${escapeHtml(s.path)}</code></li>`).join('');
        host.innerHTML = `<div class="empty">
            <b>No logs found.</b><br>
            Looked in:<ul style="list-style:none;padding:0">${where || '<li>(no stages)</li>'}</ul>
            for the <code>logs:</code> globs in your config (default
            <code>*.log</code>).<br>
            Redirect a run into a watched directory and it appears here —
            nothing needs instrumenting.
        </div>`;
        return;
    }
    host.innerHTML = logs.map(renderLog).join('');
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

        renderStages(state);
        renderLogs(state);

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
        $('bs-help').innerHTML =
            `<b>source kinds on this box</b><ul>${kinds}</ul>`
            + `<b>validators</b><ul>${vals}</ul>`;
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
