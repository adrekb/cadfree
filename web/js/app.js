let currentTab = 'dashboard';
let currentProjectId = null;
let coderMode = 'agent';
let monacoEditor = null;
let catalog = null;
let paramTimer = null;
let surveyPending = false;
let agentStreaming = false;
let pendingAttachments = [];
let currentVision = true;
let motionFrames = [];
let motionTimer = null;
let selectedFeatureId = null;
let featureCache = [];
let featureDecorations = [];

function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str == null ? '' : String(str);
    return d.innerHTML;
}

async function api(path, options = {}) {
    const resp = await fetch(path, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    if (!resp.ok) {
        let detail = resp.statusText;
        try { detail = (await resp.json()).detail || detail; } catch (_) {}
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return resp.json();
}

function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const el = document.getElementById('view-' + tab);
    if (el) el.classList.add('active');
    document.querySelectorAll('.app-nav .nav-item').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tab);
    });
    const loaders = {
        dashboard: loadDashboard,
        workshop: loadWorkshop,
        projects: loadProjects,
        settings: loadSettings,
        studio: () => { if (currentProjectId) loadStudio(currentProjectId); },
        help: () => {},
    };
    if (loaders[tab]) loaders[tab]();
}

function setCoderMode(mode) {
    coderMode = mode === 'plan' ? 'plan' : 'agent';
    document.getElementById('mode-plan').classList.toggle('on', coderMode === 'plan');
    document.getElementById('mode-act').classList.toggle('on', coderMode === 'agent');
    document.getElementById('mode-hint').textContent = coderMode === 'plan'
        ? 'Read-only CAD. The agent can still survey you and search standards.'
        : 'Agent can write CadQuery and run MATLAB. It will survey you instead of guessing.';
}

function toggleAgentSide() {
    document.getElementById('cad-studio').classList.toggle('agent-collapsed');
}

async function loadDashboard() {
    try {
        const [health, shop, projects] = await Promise.all([
            api('/api/health'), api('/api/workshop'), api('/api/projects'),
        ]);
        document.getElementById('kpi-machines').textContent = shop.capabilities.length;
        document.getElementById('kpi-projects').textContent = projects.projects.length;
        document.getElementById('kpi-cad').textContent = health.cadquery.available ? 'on' : 'off';
        document.getElementById('kpi-matlab').textContent = health.matlab.available ? health.matlab.engine : 'off';
        const dot = document.getElementById('engine-dot');
        const label = document.getElementById('engine-label');
        const keyOn = health.llm && health.llm.api_key;
        dot.className = 'dot ' + (keyOn ? 'ok' : 'warn');
        const think = (health.llm && health.llm.thinking) || 'high';
        syncThinkSelect(think);
        currentVision = !!(health.llm && health.llm.vision);
        label.textContent = keyOn
            ? (health.llm.provider + ' · ' + health.llm.model + ' · think ' + think + (currentVision ? ' · vision' : ''))
            : 'No API key';
    } catch (e) {
        document.getElementById('engine-dot').className = 'dot err';
        document.getElementById('engine-label').textContent = e.message;
    }
}

async function loadWorkshop() {
    if (!catalog) catalog = await api('/api/catalog');
    const grid = document.getElementById('preset-grid');
    grid.innerHTML = catalog.machine_presets.map(p =>
        `<button class="preset-chip" onclick="addPreset('${p.id}')">${escHtml(p.name)}</button>`
    ).join('');
    const shop = await api('/api/workshop');
    const host = document.getElementById('shop-grid');
    if (!shop.capabilities.length) {
        host.innerHTML = '<p class="muted">No machines yet. Add the printer / mill / laser you actually have.</p>';
        return;
    }
    host.innerHTML = shop.capabilities.map(c => `
      <div class="settings-card machine-card">
        <h3>${escHtml(c.name)}</h3>
        <p class="muted">${escHtml(c.kind)} · ${(c.materials || []).join(', ') || 'no stock listed'}</p>
        <p class="muted small">${escHtml(JSON.stringify(c.params))}</p>
        <button class="btn btn-ghost" onclick="deleteMachine('${c.id}')">Remove</button>
      </div>`).join('');
}

async function addPreset(id) {
    const preset = catalog.machine_presets.find(p => p.id === id);
    await api('/api/workshop', {
        method: 'POST',
        body: JSON.stringify({
            kind: preset.kind,
            name: preset.name,
            preset_id: preset.id,
            params: preset.params,
            materials: preset.suggested_materials || [],
        }),
    });
    loadWorkshop();
}

async function deleteMachine(id) {
    await api('/api/workshop/' + id, { method: 'DELETE' });
    loadWorkshop();
}

async function loadProjects() {
    const [shop, list] = await Promise.all([api('/api/workshop'), api('/api/projects')]);
    const caps = document.getElementById('np-caps');
    caps.innerHTML = shop.capabilities.map(c =>
        `<label class="toggle"><input type="checkbox" class="np-cap" value="${c.id}" checked> ${escHtml(c.name)}</label>`
    ).join(' ') || '<span class="muted">Add a machine in Workshop first.</span>';
    const host = document.getElementById('project-list');
    host.innerHTML = list.projects.map(p =>
        `<div class="settings-card" style="margin-top:10px;cursor:pointer" onclick="openStudio('${p.id}')">
           <strong>${escHtml(p.name)}</strong>
           <span class="muted"> · ${escHtml(p.status)}</span>
           <p class="muted">${escHtml(p.spec_text || '')}</p>
         </div>`
    ).join('') || '<p class="muted">No projects yet.</p>';
}

async function createProject() {
    const ids = [...document.querySelectorAll('.np-cap:checked')].map(el => el.value);
    const body = {
        name: document.getElementById('np-name').value.trim() || 'Untitled part',
        spec_text: document.getElementById('np-spec').value.trim(),
        capability_ids: ids,
        constraints: {
            load_lbf: Number(document.getElementById('np-load').value) || null,
            max_mass_g: Number(document.getElementById('np-mass').value) || null,
            safety_factor: Number(document.getElementById('np-sf').value) || 2,
        },
    };
    const created = await api('/api/projects', { method: 'POST', body: JSON.stringify(body) });
    openStudio(created.id);
}

function openStudio(id) {
    currentProjectId = id;
    switchTab('studio');
}

function ensureMonaco() {
    if (monacoEditor) return;
    const host = document.getElementById('code-editor-host');
    if (window.monaco) {
        const monaco = window.monaco;
        monaco.editor.defineTheme('carrot-dark', {
            base: 'vs-dark', inherit: true, rules: [],
            colors: { 'editor.background': '#16181e', 'editor.foreground': '#eceef4' },
        });
        monacoEditor = monaco.editor.create(host, {
            value: '',
            language: 'python',
            theme: 'carrot-dark',
            automaticLayout: true,
            fontSize: 13,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
        });
        monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveSource());
        return;
    }
    const ta = document.createElement('textarea');
    ta.style.cssText = 'width:100%;height:100%;font-family:ui-monospace,monospace;background:#16181e;color:#eceef4;border:0;padding:12px';
    host.appendChild(ta);
    monacoEditor = {
        getValue: () => ta.value,
        setValue: (v) => { ta.value = v; },
        revealLineInCenter: () => {},
        setSelection: () => {},
        deltaDecorations: () => [],
        focus: () => ta.focus(),
    };
}

async function loadStudio(id) {
    const p = await api('/api/projects/' + id);
    ensureMonaco();
    if (monacoEditor) monacoEditor.setValue(p.cadquery_source || '');
    const activePart = ((p.assembly && p.assembly.parts) || []).find(x => x.id === p.active_part_id) || {};
    renderParams(p.params || {}, activePart);
    renderFeatureTree(p.features || [], {
        note: p.feature_note,
        parse_error: p.feature_parse_error,
        imported: activePart.kind === 'imported',
    });
    renderParts(p.assembly || {});
    renderJoints(p.joints || []);
    renderFeasibility(p.feasibility || {});
    renderMessages(p.messages || []);
    (p.pending_surveys || []).forEach(s => renderSurvey({
        survey_id: s.survey_id || s.id,
        title: s.title,
        questions: s.questions,
    }));
    document.getElementById('stl-download').href = '/api/projects/' + id + '/stl';
    const urdf = document.getElementById('urdf-download');
    if (urdf) urdf.href = '/api/projects/' + id + '/urdf';
    const dxf = document.getElementById('dxf-download');
    if (dxf) dxf.href = '/api/projects/' + id + '/dxf';
    refreshViewer(id);
    loadMotion(id);
    if (window.cadfreeViewer) {
        window.cadfreeViewer.onPick = (pickIndex) => {
            if (!pickIndex) return;
            const feat = featureCache.find(f => Number(f.pick_index) === Number(pickIndex));
            if (feat) {
                selectFeature(feat.id);
                return;
            }
            const handle = '@cad[face:' + pickIndex + ']';
            const refEl = document.getElementById('feature-cad-ref');
            if (refEl) {
                refEl.hidden = false;
                refEl.textContent = handle;
            }
        };
    }
}

function renderParts(assembly) {
    const host = document.getElementById('parts-strip');
    if (!host) return;
    const parts = (assembly && assembly.parts) || [];
    const active = assembly && assembly.active_part_id;
    const bom = (assembly && assembly.bom) || [];
    const n = assembly && assembly.expanded_count;
    if (!parts.length) {
        host.innerHTML = '<span class="muted">One unique part so far. Assemblies are extra parts + patterns, not one giant script.</span>';
        return;
    }
    const chips = parts.map(p =>
        `<button class="part-chip ${p.id === active ? 'on' : ''}" onclick="selectPart('${p.id}')" title="${escHtml(p.kind || 'part')}">${escHtml(p.name)}${p.kind === 'imported' ? (String(p.name || '').startsWith('gen') ? ' · gen' : ' · import') : ''}</button>`
    ).join('');
    const bomTxt = n > 1
        ? `<span class="muted small">${n} instances · ${assembly.unique_parts || parts.length} unique · ${bom.map(b => b.qty + '× ' + b.name).join(', ')}</span>`
        : '';
    host.innerHTML = chips + bomTxt;
}

function renderJoints(joints) {
    const host = document.getElementById('joints-chips');
    if (!host) return;
    const list = joints || [];
    if (!list.length) {
        host.innerHTML = '<span class="muted small">No joints — agent define_joint, then Check.</span>';
        return;
    }
    host.innerHTML = list.map(j =>
        `<span class="joint-chip" title="${escHtml(j.kind)}">${escHtml(j.name || j.kind)}${j.driven ? ' · drive' : ''}</span>`
    ).join('');
}

async function selectPart(id) {
    if (!currentProjectId) return;
    const res = await api('/api/projects/' + currentProjectId + '/parts/' + id + '/activate', { method: 'POST' });
    if (monacoEditor) monacoEditor.setValue((res.part && res.part.cadquery_source) || '');
    renderParams(res.params || {}, res.part || {});
    const p = await api('/api/projects/' + currentProjectId);
    renderParts(p.assembly || {});
    renderFeatureTree(p.features || [], {
        note: p.feature_note,
        imported: res.part && res.part.kind === 'imported',
    });
}

function renderAttachPreview() {
    const host = document.getElementById('attach-preview');
    if (!host) return;
    host.innerHTML = pendingAttachments.map(a =>
        `<span class="attach-chip">${escHtml(a.filename)}</span>`
    ).join('');
}

const CAD_FILE = /\.(step|stp|iges|igs|brep|brp|stl|obj|3mf|ply|gltf|glb|zip|sldprt|sldasm|f3d|f3z|ipt|iam|x_t|fcstd)$/i;

async function importCadFiles(event) {
    if (!currentProjectId) {
        alert('Create a project first.');
        event.target.value = '';
        return;
    }
    const files = [...(event.target.files || [])];
    for (const file of files) {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch('/api/projects/' + currentProjectId + '/import', { method: 'POST', body: fd });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            appendMsg('import', (body.detail || body.error || ('Could not import ' + file.name)), 'tool');
            continue;
        }
        const names = (body.parts || []).map(p => p.name).join(', ');
        appendMsg('import', 'Imported ' + (names || file.name) + ((body.notes || []).length ? ' — ' + body.notes.join(' ') : ''), 'tool');
        renderParts(body.assembly || {});
        refreshViewer(currentProjectId);
        if (body.parts && body.parts[0]) selectPart(body.parts[0].id);
    }
    if (event.target && typeof event.target.value === 'string') event.target.value = '';
}

async function uploadChatFiles(event) {
    if (!currentProjectId) {
        alert('Create a project first.');
        event.target.value = '';
        return;
    }
    const files = [...(event.target.files || [])];
    const images = [];
    const cad = [];
    for (const file of files) {
        if (CAD_FILE.test(file.name)) cad.push(file);
        else images.push(file);
    }
    if (cad.length) {
        const fake = { target: { files: cad, value: '' } };
        await importCadFiles(fake);
    }
    for (const file of images) {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch('/api/projects/' + currentProjectId + '/attachments', { method: 'POST', body: fd });
        if (!resp.ok) {
            alert('Could not attach ' + file.name);
            continue;
        }
        pendingAttachments.push(await resp.json());
    }
    event.target.value = '';
    renderAttachPreview();
    if (!currentVision && pendingAttachments.length) {
        appendMsg('note', 'This model is not vision-native. Switch to OpenAI, Anthropic, Gemini, or an OpenRouter vision model to read the drawing.', 'tool');
    }
}

function renderParams(params, part) {
    const host = document.getElementById('params-strip');
    if (part && part.kind === 'imported') {
        host.innerHTML = '<span class="muted">Imported mesh — PARAMS do not apply. Edit it in the original program and Import CAD again.</span>';
        return;
    }
    const keys = Object.keys(params || {});
    if (!keys.length) {
        host.innerHTML = '<span class="muted">No PARAMS dict in this script — add one so you can tweak without the agent.</span>';
        return;
    }
    host.innerHTML = keys.map(k => {
        const v = params[k];
        if (typeof v !== 'number') return '';
        return `<div class="param-field"><label>${escHtml(k)}</label>
          <input type="number" step="0.1" data-param="${escHtml(k)}" value="${v}" oninput="queueParamChange()"></div>`;
    }).join('');
}

async function refreshFeatureTree() {
    if (!currentProjectId) return;
    try {
        const tree = await api('/api/projects/' + currentProjectId + '/features');
        renderFeatureTree(tree.features || [], tree);
    } catch (e) {
        renderFeatureTree([], { note: e.message || 'Could not read features.' });
    }
}

function renderFeatureTree(features, meta) {
    const list = document.getElementById('feature-tree-list');
    const note = document.getElementById('feature-tree-note');
    const inspector = document.getElementById('feature-inspector');
    if (!list) return;
    featureCache = features || [];
    meta = meta || {};
    if (note) {
        note.textContent = meta.parse_error
            ? ('Parse error: ' + meta.parse_error)
            : (meta.note || meta.honest || '');
    }
    if (meta.imported) {
        list.innerHTML = '<p class="muted small" style="padding:8px 12px">Imported mesh — no CadQuery tree.</p>';
        if (inspector) inspector.hidden = true;
        return;
    }
    if (meta.parse_error) {
        list.innerHTML = '<p class="muted small" style="padding:8px 12px">Fix the script to see features.</p>';
        if (inspector) inspector.hidden = true;
        return;
    }
    if (!featureCache.length) {
        list.innerHTML = '<p class="muted small" style="padding:8px 12px">No CadQuery operations yet. Write a script or let the agent.</p>';
        if (inspector) inspector.hidden = true;
        return;
    }
    const groups = [];
    const byBody = new Map();
    for (const feat of featureCache) {
        const body = feat.body || 'script';
        if (!byBody.has(body)) {
            byBody.set(body, []);
            groups.push(body);
        }
        byBody.get(body).push(feat);
    }
    list.innerHTML = groups.map(body => {
        const rows = byBody.get(body).map(feat => {
            const sel = (feat.selectors || []).slice(-1)[0];
            const on = feat.id === selectedFeatureId ? ' on' : '';
            const live = feat.live && feat.pick_index ? '<span class="sel">3D pick</span>' : '';
            const href = feat.face_ref || feat.cad_ref || '';
            const ref = href ? `<span class="sel cad-ref">${escHtml(href)}</span>` : '';
            return `<button type="button" class="feature-row${on}" data-feature="${escHtml(feat.id)}">
              <span class="feature-kind">${escHtml(feat.kind)}</span>
              <span class="lbl">${escHtml(feat.label)}${sel ? `<span class="sel">${escHtml(sel)}</span>` : ''}${live}${ref}</span>
            </button>`;
        }).join('');
        return `<div class="feature-group"><div class="feature-group-name">${escHtml(body)}</div>${rows}</div>`;
    }).join('');
    list.querySelectorAll('[data-feature]').forEach(el => {
        el.onclick = () => selectFeature(el.dataset.feature);
    });
    const still = featureCache.find(f => f.id === selectedFeatureId);
    if (still) showFeatureInspector(still);
    else if (inspector) inspector.hidden = true;
}

function selectFeature(id) {
    selectedFeatureId = id;
    const feat = featureCache.find(f => f.id === id);
    document.querySelectorAll('.feature-row').forEach(el => {
        el.classList.toggle('on', el.dataset.feature === id);
    });
    if (!feat) return;
    showFeatureInspector(feat);
    revealFeatureInEditor(feat);
    if (window.cadfreeViewer && typeof window.cadfreeViewer.highlight === 'function') {
        window.cadfreeViewer.highlight(feat.pick_index || 0);
    }
    const refEl = document.getElementById('feature-cad-ref');
    if (refEl) {
        const handle = feat.face_ref || feat.cad_ref;
        if (handle) {
            refEl.hidden = false;
            refEl.textContent = handle;
            refEl.title = 'Click to copy';
            refEl.style.cursor = 'pointer';
            refEl.onclick = () => navigator.clipboard && navigator.clipboard.writeText(handle);
        } else {
            refEl.hidden = true;
        }
    }
}

function showFeatureInspector(feat) {
    const inspector = document.getElementById('feature-inspector');
    const input = document.getElementById('feature-value');
    const label = document.getElementById('feature-value-label');
    const hint = document.getElementById('feature-inspect-hint');
    const refEl = document.getElementById('feature-cad-ref');
    if (refEl) {
        const handle = feat.face_ref || feat.cad_ref;
        if (handle) {
            refEl.hidden = false;
            refEl.textContent = handle;
            refEl.title = 'Click to copy';
            refEl.style.cursor = 'pointer';
            refEl.onclick = () => navigator.clipboard && navigator.clipboard.writeText(handle);
        } else {
            refEl.hidden = true;
        }
    }
    if (!inspector || !input) return;
    const primary = feat.primary || (feat.args || []).find(a => a.editable);
    if (!primary || primary.value == null) {
        inspector.hidden = false;
        input.disabled = true;
        input.value = '';
        if (label) label.textContent = feat.kind;
        if (hint) hint.textContent = 'No single number on this op — edit the highlighted line.';
        return;
    }
    inspector.hidden = false;
    input.disabled = false;
    input.value = primary.value;
    input.dataset.argIndex = String(primary.index || 0);
    if (label) label.textContent = (primary.name || 'value') + (primary.key ? ' · ' + primary.key : '');
    if (hint) {
        hint.textContent = primary.kind === 'params'
            ? 'Apply changes that call only. Shared PARAMS keys are isolated.'
            : 'Apply rewrites this call only, then rebuilds.';
    }
    input.focus();
    input.select();
}

function revealFeatureInEditor(feat) {
    if (!monacoEditor || !feat) return;
    const line = feat.line || 1;
    const end = feat.end_line || line;
    if (typeof monacoEditor.revealLineInCenter === 'function') {
        monacoEditor.revealLineInCenter(line);
    }
    if (window.monaco && typeof monacoEditor.setSelection === 'function') {
        const range = new window.monaco.Range(line, 1, end, (feat.end_col || 120) + 1);
        monacoEditor.setSelection(range);
        if (typeof monacoEditor.deltaDecorations === 'function') {
            featureDecorations = monacoEditor.deltaDecorations(featureDecorations, [{
                range,
                options: { isWholeLine: true, className: 'feature-line-hi' },
            }]);
        }
        monacoEditor.focus();
    }
}

async function applyFeatureEdit() {
    if (!currentProjectId || !selectedFeatureId) return;
    const input = document.getElementById('feature-value');
    if (!input || input.disabled) return;
    const value = Number(input.value);
    if (Number.isNaN(value)) return;
    const argIndex = Number(input.dataset.argIndex || 0);
    await saveSource();
    let res;
    try {
        res = await api('/api/projects/' + currentProjectId + '/features/patch', {
            method: 'POST',
            body: JSON.stringify({
                feature_id: selectedFeatureId,
                value,
                arg_index: argIndex,
            }),
        });
    } catch (e) {
        renderFeasibility({ summary: e.message, checks: [{ id: 'feature', status: 'fail', title: 'Feature', message: e.message }] });
        return;
    }
    if (monacoEditor && res.source) monacoEditor.setValue(res.source);
    selectedFeatureId = res.feature_id || selectedFeatureId;
    renderFeatureTree(res.features || [], res);
    if (res.params) renderParams(res.params);
    rebuildNow();
}

function queueParamChange() {
    clearTimeout(paramTimer);
    paramTimer = setTimeout(applyParamEdits, 400);
}

async function applyParamEdits() {
    if (!currentProjectId) return;
    const params = {};
    document.querySelectorAll('#params-strip [data-param]').forEach(el => {
        params[el.dataset.param] = Number(el.value);
    });
    const res = await api('/api/projects/' + currentProjectId + '/params', {
        method: 'PUT', body: JSON.stringify({ params }),
    });
    if (monacoEditor && res.source) monacoEditor.setValue(res.source);
    rebuildNow();
}

async function saveSource() {
    if (!currentProjectId || !monacoEditor) return;
    await api('/api/projects/' + currentProjectId + '/source', {
        method: 'PUT', body: JSON.stringify({ source: monacoEditor.getValue() }),
    });
}

async function rebuildNow() {
    if (!currentProjectId) return;
    await saveSource();
    const built = await api('/api/projects/' + currentProjectId + '/build', { method: 'POST' });
    if (!built.ok) {
        renderFeasibility({ summary: built.error, checks: [{ id: 'build', status: 'fail', title: 'Build', message: built.error }] });
        return;
    }
    renderParams(built.params || {});
    renderFeasibility(built.feasibility || {});
    renderParts(built.assembly || {});
    await refreshFeatureTree();
    document.getElementById('stl-download').href = '/api/projects/' + currentProjectId + '/stl';
    const urdf = document.getElementById('urdf-download');
    if (urdf) urdf.href = '/api/projects/' + currentProjectId + '/urdf';
    const dxf = document.getElementById('dxf-download');
    if (dxf) dxf.href = '/api/projects/' + currentProjectId + '/dxf';
    refreshViewer(currentProjectId);
}

async function runGenerate() {
    if (!currentProjectId) return;
    const sel = document.getElementById('gen-volfrac');
    const raw = sel ? sel.value : '';
    const body = { assumed_load: true, design_space: 'part' };
    if (raw) body.volfrac = Number(raw);
    appendMsg('tool', 'generate_designs', 'tool');
    try {
        const result = await api('/api/projects/' + currentProjectId + '/generate', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        renderGenerate(result);
        const p = await api('/api/projects/' + currentProjectId);
        renderParts(p.assembly || {});
        refreshViewer(currentProjectId);
    } catch (e) {
        appendMsg('error', e.message || String(e));
    }
}

function renderGenerate(result) {
    const log = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = 'msg physics';
    const cands = result.candidates || [];
    const cards = cands.map(c => {
        const ok = c.ok ? 'ok' : 'no';
        const hist = (c.history || []).map(n => Number(n).toFixed(1)).join(' → ');
        return `<div class="solver-card gen-card"><span class="chip ${ok}">${escHtml(c.name || 'SIMP')}</span>` +
            (c.compliance != null ? `<span class="muted small">c = ${escHtml(String(c.compliance))}</span>` : '') +
            (c.volfrac_actual != null ? `<span class="muted small"> vol ${escHtml(String(Number(c.volfrac_actual).toFixed(3)))}</span>` : '') +
            (hist ? `<div class="prov">compliance ${escHtml(hist)}</div>` : '') +
            (c.reason ? `<p class="disclaimer">${escHtml(c.reason)}</p>` : '') +
            `</div>`;
    }).join('');
    const miss = result.ok ? '' : `<p class="disclaimer">${escHtml(result.reason || result.error || 'Generate failed.')}</p>`;
    div.innerHTML = '<div class="who">generate · SIMP</div>' + (cards || miss) +
        (result.disclaimer ? `<p class="disclaimer">${escHtml(result.disclaimer)}</p>` : '') +
        (result.assumed_unit_load ? '<p class="disclaimer">Unit load assumed — not a Fusion-grade load case.</p>' : '');
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function renderFeasibility(report) {
    const bar = document.getElementById('feasibility-bar');
    if (!report || !report.checks) {
        bar.innerHTML = '<span class="muted">' + escHtml(report.summary || 'Rebuild to score manufacturability.') + '</span>';
        return;
    }
    const chips = (report.checks || []).map(c =>
        `<span class="check-chip ${escHtml(c.status)}" title="${escHtml(c.message)}">${escHtml(c.title)}</span>`
    ).join('');
    const recs = (report.recommendations || []).map(r => escHtml(r.reason)).join(' ');
    bar.innerHTML = `<strong>${escHtml(report.verdict || '')}</strong>
      <span class="muted">${escHtml(report.summary || '')}</span>${chips}
      <span class="muted">${recs}</span>`;
}

function renderMessages(messages) {
    const log = document.getElementById('chat-log');
    log.innerHTML = messages.map(m =>
        `<div class="msg"><div class="who">${escHtml(m.role)}</div><div>${escHtml(m.content)}</div></div>`
    ).join('');
    log.scrollTop = log.scrollHeight;
}

function appendMsg(role, text, cls) {
    const log = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = 'msg ' + (cls || '');
    div.innerHTML = `<div class="who">${escHtml(role)}</div><div></div>`;
    div.lastChild.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
}

function refreshViewer(id) {
    const pid = id || currentProjectId;
    if (!pid) return;
    let n = 0;
    const apply = () => {
        if (!window.cadfreeViewer || !window.cadfreeViewer.loadScene) return false;
        fetch('/api/projects/' + pid + '/scene?t=' + Date.now())
            .then((r) => r.json())
            .then((scene) => window.cadfreeViewer.loadScene(scene))
            .catch(() => {});
        return true;
    };
    const tryLoad = () => {
        if (apply() || n++ > 20) return;
        setTimeout(tryLoad, 50);
    };
    tryLoad();
}

async function loadMotion(id) {
    const pid = id || currentProjectId;
    const strip = document.getElementById('motion-strip');
    if (!pid || !strip) return;
    strip.hidden = false;
    try {
        const data = await api('/api/projects/' + pid + '/motion?steps=24');
        if (!data.ok || !(data.frames || []).length) {
            motionFrames = [];
            const st = document.getElementById('motion-status');
            if (st) st.textContent = data.error || 'Joints + Play — not SolidWorks Motion.';
            return;
        }
        motionFrames = data.frames;
        const sl = document.getElementById('motion-slider');
        sl.min = 0;
        sl.max = Math.max(0, motionFrames.length - 1);
        sl.value = 0;
        document.getElementById('motion-status').textContent = data.summary || '';
        scrubMotion(0);
    } catch (_) {
        motionFrames = [];
    }
}

function scrubMotion(i) {
    const f = motionFrames[Number(i)];
    if (f && window.cadfreeViewer && window.cadfreeViewer.applyDraws) {
        window.cadfreeViewer.applyDraws(f.draws);
    }
    const status = document.getElementById('motion-status');
    if (status && f) {
        const mu = f.transmission_deg != null ? (' · μ ' + Number(f.transmission_deg).toFixed(0) + '°') : '';
        status.textContent = (f.locked ? 'lock-up @ ' : (f.hits && f.hits.length ? 'clash @ ' : '')) + f.deg + '°' + mu;
    }
}

function playMotion() {
    if (!motionFrames.length) return;
    let i = 0;
    clearInterval(motionTimer);
    motionTimer = setInterval(() => {
        const sl = document.getElementById('motion-slider');
        if (sl) sl.value = i;
        scrubMotion(i);
        i = (i + 1) % motionFrames.length;
    }, 90);
}

async function checkMotionNow() {
    if (!currentProjectId) return;
    try {
        const result = await api('/api/projects/' + currentProjectId + '/mechanism');
        renderMechanism(result);
        const st = document.getElementById('motion-status');
        if (st) st.textContent = result.for_model || result.summary || result.verdict || '';
        await loadMotion(currentProjectId);
    } catch (e) {
        appendMsg('error', e.message || String(e));
    }
}

function renderMechanism(result) {
    const log = document.getElementById('chat-log');
    if (!log) return;
    const div = document.createElement('div');
    div.className = 'msg physics';
    const v = result.verdict || (result.ok ? 'ok' : 'no');
    const chip = result.works ? 'ok' : (result.awkward ? 'no' : 'no');
    const c = result.comfort || {};
    div.innerHTML = '<div class="who">mechanism</div>' +
        `<div class="solver-card"><span class="chip ${chip}">${escHtml(v)}</span>` +
        `<span class="muted small">${escHtml(result.kind || '')}</span>` +
        (c.transmission_min_deg != null ? `<div class="prov">min transmission ${escHtml(String(Number(c.transmission_min_deg).toFixed(0)))}°</div>` : '') +
        (c.class ? `<div class="prov">${escHtml(c.class)}</div>` : '') +
        (result.loads && result.loads.max_pin_n != null ? `<div class="prov">max pin ${escHtml(String(Number(result.loads.max_pin_n).toFixed(1)))} N</div>` : '') +
        (result.loads && result.loads.T_hold_nm != null ? `<div class="prov">hold ${escHtml(String(Number(result.loads.T_hold_nm).toFixed(3)))} N·m</div>` : '') +
        `<p class="disclaimer">${escHtml(result.for_model || result.summary || '')}</p>` +
        (result.disclaimer ? `<p class="disclaimer">${escHtml(result.disclaimer)}</p>` : '') +
        '</div>';
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function thinkHint(level) {
    return {
        off: 'Thinking off. Fastest; weaker CadQuery on hard parts.',
        low: 'Think low — short chain-of-thought.',
        high: 'Think high — default for agent work (standards + CadQuery).',
        max: 'Think max — slowest and spendiest. Best shot at a hard parametric part.',
    }[level] || '';
}

function syncThinkSelect(level) {
    ['llm-thinking', 'think-level'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = level;
    });
    const hint = document.getElementById('think-status');
    if (hint) hint.textContent = thinkHint(level);
}

function onProviderChange() {
    const p = document.getElementById('llm-provider').value;
    const model = document.getElementById('llm-model');
    const defaults = {
        openai: 'gpt-4.1',
        anthropic: 'claude-sonnet-4-5',
        gemini: 'gemini-2.5-flash',
        deepseek: 'deepseek-v4-pro',
        openrouter: 'openai/gpt-4.1',
        ollama: 'llama3.2',
        custom: 'gpt-4.1',
    };
    if (defaults[p]) model.value = defaults[p];
}

async function saveThinkingLevel() {
    const level = document.getElementById('think-level').value;
    syncThinkSelect(level);
    await api('/api/settings', { method: 'POST', body: JSON.stringify({ llm_thinking: level }) });
    loadDashboard();
}

function appendThinking(text) {
    if (!text) return;
    const log = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = 'msg think';
    div.innerHTML = '<div class="who">thinking</div><details><summary>chain of thought</summary><pre></pre></details>';
    div.querySelector('pre').textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function setComposerLocked(locked) {
    const input = document.getElementById('chat-input');
    const btn = document.getElementById('chat-send');
    input.disabled = locked;
    btn.disabled = locked;
}

function surveyFieldHtml(q) {
    const id = escHtml(q.id);
    const prompt = escHtml(q.prompt) + (q.required === false ? '' : ' *');
    const help = q.help ? `<p class="muted small">${escHtml(q.help)}</p>` : '';
    const unit = q.unit ? `<span class="muted small">${escHtml(q.unit)}</span>` : '';
    const req = q.required === false ? '' : 'required';
    if (q.type === 'choice') {
        const opts = (q.options || []).map(o =>
            `<label class="survey-opt"><input type="radio" name="${id}" value="${escHtml(o)}" ${req}> ${escHtml(o)}</label>`
        ).join('');
        return `<fieldset class="survey-q" data-qid="${id}" data-qtype="choice"><legend>${prompt}</legend>${help}${opts}</fieldset>`;
    }
    if (q.type === 'multi') {
        const opts = (q.options || []).map(o =>
            `<label class="survey-opt"><input type="checkbox" name="${id}" value="${escHtml(o)}"> ${escHtml(o)}</label>`
        ).join('');
        return `<fieldset class="survey-q" data-qid="${id}" data-qtype="multi"><legend>${prompt}</legend>${help}${opts}</fieldset>`;
    }
    if (q.type === 'bool') {
        return `<fieldset class="survey-q" data-qid="${id}" data-qtype="bool"><legend>${prompt}</legend>${help}
          <label class="survey-opt"><input type="radio" name="${id}" value="yes" ${req}> Yes</label>
          <label class="survey-opt"><input type="radio" name="${id}" value="no"> No</label></fieldset>`;
    }
    if (q.type === 'number') {
        return `<fieldset class="survey-q" data-qid="${id}" data-qtype="number"><legend>${prompt}</legend>${help}
          <input type="number" step="any" ${req}> ${unit}</fieldset>`;
    }
    return `<fieldset class="survey-q" data-qid="${id}" data-qtype="text"><legend>${prompt}</legend>${help}
      <input type="text" ${req}></fieldset>`;
}

function renderSurvey(ev) {
    const sid = ev.survey_id || ev.id;
    if (!sid) return;
    if (document.querySelector(`.survey-wrap[data-survey-id="${sid}"]`)) return;
    surveyPending = true;
    setComposerLocked(true);
    const log = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = 'msg survey-wrap';
    div.dataset.surveyId = sid;
    const fields = (ev.questions || []).map(surveyFieldHtml).join('');
    div.innerHTML = `<div class="who">survey</div>
      <form class="survey-card" onsubmit="return submitSurveyForm(event, '${escHtml(sid)}')">
        <h4>${escHtml(ev.title || 'A few questions before designing')}</h4>
        ${fields || '<p class="muted">No questions.</p>'}
        <button class="btn btn-primary" type="submit">Submit answers</button>
      </form>`;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function collectSurveyAnswers(form) {
    const answers = {};
    form.querySelectorAll('.survey-q').forEach(fs => {
        const qid = fs.dataset.qid;
        const type = fs.dataset.qtype;
        if (type === 'multi') {
            answers[qid] = [...fs.querySelectorAll('input:checked')].map(i => i.value);
        } else if (type === 'choice') {
            const picked = fs.querySelector('input:checked');
            answers[qid] = picked ? picked.value : '';
        } else if (type === 'bool') {
            const picked = fs.querySelector('input:checked');
            answers[qid] = picked ? picked.value === 'yes' : null;
        } else if (type === 'number') {
            const inp = fs.querySelector('input');
            answers[qid] = inp && inp.value !== '' ? Number(inp.value) : null;
        } else {
            const inp = fs.querySelector('input, textarea');
            answers[qid] = inp ? inp.value : '';
        }
    });
    return answers;
}

async function submitSurveyForm(event, surveyId) {
    event.preventDefault();
    if (!currentProjectId) return false;
    const form = event.target;
    const answers = collectSurveyAnswers(form);
    await api('/api/projects/' + currentProjectId + '/survey/' + surveyId, {
        method: 'POST', body: JSON.stringify({ answers }),
    });
    const btn = form.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    const note = document.createElement('p');
    note.className = 'muted small';
    note.textContent = 'Saved. The agent will continue.';
    form.appendChild(note);
    surveyPending = false;
    if (!agentStreaming) {
        setComposerLocked(false);
        sendChatText('Survey submitted. Continue with those answers.');
    }
    return false;
}

function typesetLatex(tex, display) {
    if (window.katex) {
        try {
            return window.katex.renderToString(tex || '', { throwOnError: false, displayMode: !!display });
        } catch (_) { /* fall through */ }
    }
    const pre = document.createElement('code');
    pre.textContent = tex || '';
    return pre.outerHTML;
}

function renderWorksheet(ws) {
    if (!ws) return '';
    const steps = (ws.steps || []).map(s =>
        `<div class="math-step"><div class="ttl">${escHtml(s.title || '')}</div>${typesetLatex(s.latex || '', true)}</div>`
    ).join('');
    const prov = Object.entries(ws.provenance || {}).map(([k, v]) =>
        `<div class="prov"><code>${escHtml(k)}</code> ${escHtml(String(v))}</div>`
    ).join('');
    const extra = ws.ok
        ? `<div class="math-head">${escHtml(ws.title || ws.formula_id || '')}` +
          (ws.value != null ? ` = ${escHtml(String(ws.value))} ${escHtml(ws.unit || '')}` : '') +
          `</div>`
        : `<div class="math-head">${escHtml(ws.title || ws.formula_id || 'formula')} — ${escHtml(ws.error || 'needed inputs')}</div>`;
    return `<div class="math-card">${extra}${steps}${prov}` +
        (ws.maintain ? `<p class="maintain">${escHtml(ws.maintain)}</p>` : '') +
        (ws.disclaimer ? `<p class="disclaimer">${escHtml(ws.disclaimer)}</p>` : '') +
        `</div>`;
}

function renderPhysics(name, result) {
    const log = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = 'msg physics';
    if (name === 'lookup_formula') {
        const hits = result.formulas || [];
        div.innerHTML = '<div class="who">formula book</div>' + (hits.map(f =>
            `<div class="math-card"><div class="math-head">${escHtml(f.title || f.id)} · ${escHtml(f.domain || '')}</div>` +
            typesetLatex(f.latex || '', true) +
            `<p class="disclaimer">${escHtml(f.disclaimer || '')}</p></div>`
        ).join('') || `<div class="muted">${escHtml(result.note || 'No formulas.')}</div>`);
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
        return;
    }
    if (name === 'solve_formula') {
        div.innerHTML = '<div class="who">physics</div>' + renderWorksheet(result);
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
        return;
    }
    const blocks = (result.results || []).map(r => {
        const ok = r.ok ? 'ok' : 'no';
        const sheets = (r.worksheets || []).map(renderWorksheet).join('');
        const hints = (r.iterate || result.iterate || []).map(h =>
            `<div class="prov">iterate <code>${escHtml(h.param || '')}</code> ${escHtml(h.reason || h.note || '')}</div>`
        ).join('');
        return `<div class="solver-card"><span class="chip ${ok}">${escHtml(r.kind || r.solver || 'solver')}</span>` +
            (r.error ? `<span class="muted small">${escHtml(r.error)}</span>` : '') +
            sheets + hints +
            (r.disclaimer ? `<p class="disclaimer">${escHtml(r.disclaimer)}</p>` : '') +
            `</div>`;
    }).join('');
    div.innerHTML = '<div class="who">solvers</div>' + (blocks || `<div class="muted">${escHtml(result.disclaimer || '')}</div>`);
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function renderCitations(result) {
    const hits = (result && (result.citations || result.results)) || [];
    const log = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = 'msg cites';
    if (!hits.length) {
        div.innerHTML = `<div class="who">standards</div><div class="muted">${escHtml((result && result.note) || 'No standards hits.')}</div>`;
        log.appendChild(div);
        return;
    }
    div.innerHTML = '<div class="who">standards</div><ul class="cite-list">' +
        hits.map(h => {
            const ids = (h.standard_ids || []).join(', ');
            const badge = h.source || (h.official ? 'body' : 'web');
            return `<li><a href="${escHtml(h.url)}" target="_blank" rel="noopener">${escHtml(h.title || h.url)}</a>
              <span class="cite-badge ${escHtml(badge)}">${escHtml(badge)}</span>
              ${ids ? `<span class="muted small">${escHtml(ids)}</span>` : ''}</li>`;
        }).join('') + '</ul>';
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function renderVehicle(name, result) {
    const log = document.getElementById('chat-log');
    const div = document.createElement('div');
    div.className = 'msg vehicle';
    if (name === 'search_parts') {
        const cat = result.catalog || [];
        const vendors = result.vendors || result.citations || [];
        const rows = cat.map(p =>
            `<li><code>${escHtml(p.id)}</code> ${escHtml(p.name || '')} · $${escHtml(String(p.price_usd))}
             <span class="cite-badge vendor">${escHtml(p.role || '')}</span></li>`
        ).join('');
        const links = vendors.map(h =>
            `<li><a href="${escHtml(h.url || '')}" target="_blank" rel="noopener">${escHtml(h.title || h.url || '')}</a>
             <span class="cite-badge ${escHtml(h.source || 'web')}">${escHtml(h.source || 'web')}</span></li>`
        ).join('');
        div.innerHTML = '<div class="who">parts</div>' +
            (rows ? `<ul class="kit-bom">${rows}</ul>` : '<p class="muted">No catalog hits.</p>') +
            (links ? `<ul class="cite-list">${links}</ul>` : '') +
            `<p class="disclaimer">${escHtml(result.note || result.price_note || '')}</p>`;
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
        return;
    }
    const possible = result.possible;
    const chip = possible === true ? 'ok' : possible === false ? 'no' : '';
    const alts = (result.alternatives || []).map(a =>
        `<li>${escHtml(a.change || a.label || '')}</li>`
    ).join('');
    const kit = result.proposed_kit || result.kit;
    const bom = ((kit && kit.lines) || []).map(l =>
        `<li>${escHtml(String(l.qty))}× ${escHtml(l.name)} · $${escHtml(String(l.line_usd))}</li>`
    ).join('');
    div.innerHTML = `<div class="who">vehicle</div>
      <div class="vehicle-card ${chip}">
        <span class="chip ${chip}">${escHtml(result.verdict || name)}</span>
        <p>${escHtml(result.for_user || result.for_model || result.error || '')}</p>
        ${alts ? `<ul class="cite-list">${alts}</ul>` : ''}
        ${bom ? `<ul class="kit-bom">${bom}</ul>` : ''}
        <p class="disclaimer">${escHtml((kit && kit.price_note) || result.disclaimer || '')}</p>
      </div>`;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

async function sendChat() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    await sendChatText(text);
}

async function sendChatText(text) {
    if (!currentProjectId) {
        alert('Create a project first.');
        return;
    }
    if (agentStreaming) return;
    await saveSource();
    appendMsg('you', text);
    const attachmentIds = pendingAttachments.map(a => a.id);
    pendingAttachments = [];
    renderAttachPreview();
    agentStreaming = true;
    setComposerLocked(true);
    try {
        const resp = await fetch('/api/projects/' + currentProjectId + '/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: text,
                mode: coderMode,
                attachment_ids: attachmentIds,
            }),
        });
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        let assistant = null;
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const chunks = buf.split('\n\n');
            buf = chunks.pop();
            for (const chunk of chunks) {
                if (!chunk.startsWith('data: ')) continue;
                let ev;
                try { ev = JSON.parse(chunk.slice(6)); } catch (_) { continue; }
                if (ev.type === 'assistant' || ev.type === 'assistant_partial') {
                    if (!assistant) assistant = appendMsg('cadfree', '');
                    assistant.lastChild.textContent += ev.content || '';
                } else if (ev.type === 'thinking') {
                    appendThinking(ev.content || '');
                } else if (ev.type === 'survey') {
                    renderSurvey(ev);
                } else if (ev.type === 'survey_answered') {
                    surveyPending = false;
                } else if (ev.type === 'tool_call') {
                    appendMsg('tool', ev.name, 'tool');
                } else if (ev.type === 'tool_result' && ev.name === 'search_standards') {
                    renderCitations(ev.result || {});
                } else if (ev.type === 'tool_result' && ev.name === 'read_url' && ev.result) {
                    const r = ev.result;
                    const label = (r.ok ? 'read ' : 'could not read ') + (r.url || '');
                    appendMsg('search', label + (r.paywalled ? ' (paywalled)' : ''), 'tool');
                } else if (ev.type === 'tool_result' && ev.name === 'build_model' && ev.result && ev.result.ok) {
                    refreshViewer(currentProjectId);
                } else if (ev.type === 'tool_result' && (ev.name === 'place_instance' || ev.name === 'upsert_part')) {
                    refreshViewer(currentProjectId);
                } else if (ev.type === 'tool_result' && (ev.name === 'define_joint' || ev.name === 'sweep_mechanism' || ev.name === 'check_mesh' || ev.name === 'check_mechanism')) {
                    loadMotion(currentProjectId);
                    if (ev.name === 'check_mechanism' || ev.name === 'check_mesh') renderMechanism(ev.result || {});
                    else if (ev.result && ev.result.summary) appendMsg('motion', ev.result.summary, 'tool');
                    else if (ev.result && ev.result.gears) appendMsg('mesh', JSON.stringify(ev.result.gears), 'tool');
                    api('/api/projects/' + currentProjectId).then(p => renderJoints(p.joints || [])).catch(() => {});
                } else if (ev.type === 'tool_result' && (ev.name === 'solve_formula' || ev.name === 'lookup_formula' || ev.name === 'run_solvers')) {
                    renderPhysics(ev.name, ev.result || {});
                    if (ev.name === 'run_solvers' && ev.result && (ev.result.results || []).some(r => r.kind === 'topology' || r.engine === 'simp')) {
                        refreshViewer(currentProjectId);
                    }
                } else if (ev.type === 'tool_result' && ev.name === 'generate_designs') {
                    renderGenerate(ev.result || {});
                    refreshViewer(currentProjectId);
                    api('/api/projects/' + currentProjectId).then(p => renderParts(p.assembly || {})).catch(() => {});
                } else if (ev.type === 'tool_result' && (ev.name === 'search_parts' || ev.name === 'commit_cots_kit')) {
                    renderVehicle(ev.name, ev.result || {});
                    if (ev.name === 'commit_cots_kit' && ev.result && ev.result.ok) {
                        refreshViewer(currentProjectId);
                        api('/api/projects/' + currentProjectId).then(p => renderParts(p.assembly || {})).catch(() => {});
                    }
                } else if (ev.type === 'tool_result' && ev.name === 'check_feasibility') {
                    renderFeasibility(ev.result || {});
                    if (ev.result && ev.result.catalog_class) {
                        renderVehicle('check_feasibility', ev.result.catalog_class);
                    }
                    if (ev.result && ev.result.catalog_spring) {
                        renderVehicle('check_feasibility', ev.result.catalog_spring);
                    }
                    if (ev.result && ev.result.mechanism_loads) {
                        renderMechanism({ verdict: ev.result.verdict, loads: ev.result.mechanism_loads, for_model: ev.result.summary, kind: 'loads' });
                    }
                } else if (ev.type === 'error') {
                    appendMsg('error', ev.message);
                }
            }
        }
        if (monacoEditor && currentProjectId) {
            const p = await api('/api/projects/' + currentProjectId);
            monacoEditor.setValue(p.cadquery_source || '');
            renderParams(p.params || {});
            renderParts(p.assembly || {});
            renderFeatureTree(p.features || [], { note: p.feature_note });
        }
    } finally {
        agentStreaming = false;
        if (!surveyPending) setComposerLocked(false);
    }
}

async function loadSettings() {
    const cfg = await api('/api/config');
    document.getElementById('llm-provider').value = cfg.llm_provider || 'openai';
    document.getElementById('llm-model').value = cfg.llm_model || '';
    document.getElementById('llm-base').value = cfg.llm_base_url || '';
    document.getElementById('key-status').textContent = cfg.llm_api_key_set ? 'A key is saved on this machine.' : 'No key saved yet.';
    currentVision = !!cfg.vision;
    const vs = document.getElementById('think-status');
    if (vs) {
        vs.textContent = (thinkHint(cfg.llm_thinking || 'high') + ' ' +
            (cfg.vision
                ? 'This model can read drawings and photos attached in chat.'
                : 'Not vision-native — switch to OpenAI, Anthropic, Gemini, or OpenRouter to read images.'));
    }
    syncThinkSelect(cfg.llm_thinking || 'high');
    document.getElementById('search-provider').value = cfg.search_provider || 'auto';
    document.getElementById('search-status').textContent = cfg.search_api_key_set
        ? 'A Brave search key is saved on this machine.'
        : 'DuckDuckGo (no key). Paste a Brave key for a stronger standards search.';
    const cu = document.getElementById('cadcoder-url');
    const cm = document.getElementById('cadcoder-model');
    const cs = document.getElementById('cadcoder-status');
    if (cu) cu.value = cfg.cadcoder_base_url || '';
    if (cm) cm.value = cfg.cadcoder_model || 'cad-coder';
    if (cs) {
        cs.textContent = (cfg.cadcoder && cfg.cadcoder.available)
            ? 'CAD-Coder endpoint is set. draft_from_image will call it.'
            : 'No CAD-Coder URL — image→CadQuery is skipped with an install hint.';
    }
    if (typeof renderThemePicker === 'function') renderThemePicker();
}

async function saveSettings() {
    const body = {
        llm_provider: document.getElementById('llm-provider').value,
        llm_model: document.getElementById('llm-model').value,
        llm_base_url: document.getElementById('llm-base').value,
        llm_thinking: document.getElementById('llm-thinking').value,
        search_provider: document.getElementById('search-provider').value,
        cadcoder_base_url: (document.getElementById('cadcoder-url') || {}).value || '',
        cadcoder_model: (document.getElementById('cadcoder-model') || {}).value || '',
    };
    const key = document.getElementById('llm-key').value.trim();
    if (key) body.llm_api_key = key;
    const skey = document.getElementById('search-key').value.trim();
    if (skey) body.search_api_key = skey;
    const ckey = (document.getElementById('cadcoder-key') || {value: ''}).value.trim();
    if (ckey) body.cadcoder_api_key = ckey;
    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
    document.getElementById('llm-key').value = '';
    document.getElementById('search-key').value = '';
    const ck = document.getElementById('cadcoder-key');
    if (ck) ck.value = '';
    loadSettings();
    loadDashboard();
}

document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) sendChat();
});

loadDashboard();
