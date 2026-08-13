// ── SerenTheatre - leaf logic on the SerenMeninges shell ────────────────────
// The shell provides api() (same-origin, attaches the saved bearer),
// escapeHtml(), showTab() and the 🔑 token modal. We call them.
//
// Theatre is READ-ONLY and localhost by default, so there is no auth dance
// here and no action buttons to fail closed - the whole surface is GETs.
//
// ONE RULE THIS FILE FOLLOWS THROUGHOUT: never invent a reading. An empty
// stage list is rendered as "the room is empty", not as a spinner. A run whose
// manifest went quiet is rendered as "stalled", not as still running. A status
// this viewer does not recognise is rendered as itself with a hollow marker,
// not silently bucketed into "pending". The dashboard is a claim about
// reality; the only failure that really matters is being confidently wrong.

const $ = (id) => document.getElementById(id);

let TIMER = null;
let REFRESH_MS = 5000;

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

// -- the stage ladder -------------------------------------------------------

function renderLadder(m) {
    const rows = m.stages.map((s) => {
        // known_status comes from the server. Trusting it rather than
        // re-deriving here keeps one implementation of "do we understand this
        // state" - two would eventually disagree, on screen.
        const cls = s.known_status === false || !KNOWN.includes(s.status)
            ? 'unknown' : s.status;
        const right = s.status === 'running' ? fmtDur(s.elapsed)
            : (s.status === 'done' ? fmtDur(s.elapsed) : '');
        const note = s.note
            ? `<div class="note">${escapeHtml(s.note)}</div>` : '';
        const art = s.artifact
            ? ` <code>${escapeHtml(s.artifact)}</code>` : '';
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
        const counts = `${m.done_count}/${m.stage_count} stages`;
        const sub = [
            m.name && escapeHtml(m.name),
            m.size && escapeHtml(m.size),
            m.recipe_id && `<code>${escapeHtml(m.recipe_id)}</code>`,
            counts,
            `updated ${fmtAge(m.updated)}`,
        ].filter(Boolean).join(' · ');
        const stalled = m.stale
            ? `<div class="err">This run's manifest says a stage is still
               running, but it has not been updated in
               ${escapeHtml(fmtDur(Date.now() / 1000 - m.updated))}. The process
               was probably killed - a stage that dies never gets to write a
               final status.</div>` : '';
        return `<div class="card">
            <h3>${escapeHtml(r.name)} ${badge}</h3>
            <div class="sub">${sub}</div>
            ${stalled}
            ${renderLadder(m)}
            ${renderRefusals(m)}
        </div>`;
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
    return `<div class="card">
        <h3>${escapeHtml(r.name)} <span class="badge disk">${escapeHtml(r.source)}</span></h3>
        ${err}
        ${bits.length
            ? `<div class="sub">${bits.join(' · ')}</div>`
            : `<div class="empty">Nothing built here yet.</div>`}
    </div>`;
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
            return `<div class="card">
                <h3>${escapeHtml(s.name)}</h3>
                <div class="err">Directory not found:
                <code>${escapeHtml(s.path)}</code></div>
            </div>`;
        }
        if (!s.rungs.length) {
            return `<div class="card">
                <h3>${escapeHtml(s.name)}</h3>
                <div class="sub"><code>${escapeHtml(s.path)}</code></div>
                <div class="empty">No runs here yet.</div>
            </div>`;
        }
        return s.rungs.map(renderRung).join('');
    }).join('');
}

// -- logs -------------------------------------------------------------------

function renderLog(l) {
    const st = l.step || {};
    const pct = (st.step != null && st.total)
        ? Math.min(100, Math.round(100 * st.step / st.total)) : null;
    const bar = pct == null ? '' :
        `<div class="bar"><i style="width:${pct}%"></i></div>`;
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
        ? `<div class="mile">✓ ${l.milestones.map(escapeHtml).join(' · ')}</div>`
        : '';

    return `<div class="card">
        <h3>${escapeHtml(l.name)}</h3>
        <div class="sub"><code>${escapeHtml(l.path)}</code></div>
        ${stalled}${bar}
        <dl class="kv">${kv}</dl>
        ${miles}${warns}
    </div>`;
}

function renderLogs(state) {
    const logs = state.stages.flatMap((s) => s.logs || []);
    const host = $('logs-body');
    if (!logs.length) {
        host.innerHTML = `<div class="empty">
            <b>No logs found.</b><br>
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

        const demo = state.demo === true;
        $('rehearsal-banner').style.display = demo ? 'block' : 'none';
        $('rehearsal-pill').style.display = demo ? '' : 'none';

        $('stage-pill').textContent =
            `${state.stages.length} stage${state.stages.length === 1 ? '' : 's'}`;
        $('age-pill').textContent = `${state.took_ms} ms`;
        $('age-pill').title = `read in ${state.took_ms} ms`;

        renderStages(state);
        renderLogs(state);

        if (state.refresh_seconds) {
            REFRESH_MS = state.refresh_seconds * 1000;
        }
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
    TIMER = setInterval(() => {
        if (!document.hidden) load();
    }, REFRESH_MS);
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) load();
});

load().then(schedule);
