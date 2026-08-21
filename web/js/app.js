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
    };
}

async function loadStudio(id) {
    const p = await api('/api/projects/' + id);
    ensureMonaco();
    if (monacoEditor) monacoEditor.setValue(p.cadquery_source || '');
    renderParams(p.params || {});
    renderParts(p.assembly || {});
    renderFeasibility(p.feasibility || {});
    renderMessages(p.messages || []);
    (p.pending_surveys || []).forEach(s => renderSurvey({
        survey_id: s.survey_id || s.id,
        title: s.title,
        questions: s.questions,
    }));
    document.getElementById('stl-download').href = '/api/projects/' + id + '/stl';
    if (window.cadfreeViewer) window.cadfreeViewer.load('/api/projects/' + id + '/stl?t=' + Date.now());
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
        `<button class="part-chip ${p.id === active ? 'on' : ''}" onclick="selectPart('${p.id}')">${escHtml(p.name)}</button>`
    ).join('');
    const bomTxt = n > 1 ? `<span class="muted small">${n} instances · ${bom.map(b => b.qty + '× ' + b.name).join(', ')}</span>` : '';
    host.innerHTML = chips + bomTxt;
}

async function selectPart(id) {
    if (!currentProjectId) return;
    const res = await api('/api/projects/' + currentProjectId + '/parts/' + id + '/activate', { method: 'POST' });
    if (monacoEditor) monacoEditor.setValue((res.part && res.part.cadquery_source) || '');
    renderParams(res.params || {});
    const p = await api('/api/projects/' + currentProjectId);
    renderParts(p.assembly || {});
}

function renderAttachPreview() {
    const host = document.getElementById('attach-preview');
    if (!host) return;
    host.innerHTML = pendingAttachments.map(a =>
        `<span class="attach-chip">${escHtml(a.filename)}</span>`
    ).join('');
}

async function uploadChatFiles(event) {
    if (!currentProjectId) {
        alert('Create a project first.');
        event.target.value = '';
        return;
    }
    const files = [...(event.target.files || [])];
    for (const file of files) {
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
    const host = document.getElementById('params-strip');
    const keys = Object.keys(params);
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
    document.getElementById('stl-download').href = built.stl_url || ('/api/projects/' + currentProjectId + '/stl');
    if (window.cadfreeViewer) window.cadfreeViewer.load(built.stl_url);
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

function thinkHint(level) {
    return {
        off: 'Thinking off. Fastest; weaker CadQuery on hard parts.',
        low: 'Think low — short chain-of-thought.',
        high: 'Think high — DeepSeek default for agent work (standards + CadQuery).',
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
    };
    if (!model.value.trim() && defaults[p]) model.value = defaults[p];
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
                attachment_ids: pendingAttachments.map(a => a.id),
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
                    if (window.cadfreeViewer) {
                        window.cadfreeViewer.load('/api/projects/' + currentProjectId + '/stl?t=' + Date.now());
                    }
                } else if (ev.type === 'tool_result' && ev.name === 'check_feasibility') {
                    renderFeasibility(ev.result || {});
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
    if (typeof renderThemePicker === 'function') renderThemePicker();
}

async function saveSettings() {
    const body = {
        llm_provider: document.getElementById('llm-provider').value,
        llm_model: document.getElementById('llm-model').value,
        llm_base_url: document.getElementById('llm-base').value,
        llm_thinking: document.getElementById('llm-thinking').value,
        search_provider: document.getElementById('search-provider').value,
    };
    const key = document.getElementById('llm-key').value.trim();
    if (key) body.llm_api_key = key;
    const skey = document.getElementById('search-key').value.trim();
    if (skey) body.search_api_key = skey;
    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
    document.getElementById('llm-key').value = '';
    document.getElementById('search-key').value = '';
    loadSettings();
    loadDashboard();
}

document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) sendChat();
});

loadDashboard();
