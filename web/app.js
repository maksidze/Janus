// ═══════════════════════════════════════════════════════════════════════════
// Janus — SD Card Mass Flasher · Frontend Application
// ═══════════════════════════════════════════════════════════════════════════
"use strict";

// ── State ────────────────────────────────────────────────────────────────────
const S = {
    layout: null,          // LayoutConfig from server
    drives: [],            // DriveInfo[]
    images: [],            // ImageInfo[]
    ports: [],             // port list (flat)
    physicalPorts: [],     // PhysicalPort[] — enriched, deduplicated
    jobs: {},              // job_id → JobInfo
    jobByCellId: {},       // cell_id → JobInfo (latest)
    selectedCells: new Set(),
    lastSelectedIdx: -1,
    sseConnected: false,
};

// ── API helpers ──────────────────────────────────────────────────────────────

async function api(path, opts = {}) {
    const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
    }
    if (res.status === 204) return null;
    return res.json();
}

// ── Toast ────────────────────────────────────────────────────────────────────

function toast(msg, type = '') {
    const c = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = 'j-toast' + (type ? ' ' + type : '');
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => { el.remove(); }, 4000);
}

// ── Modal helpers ────────────────────────────────────────────────────────────

function openModal(id) { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }
// Make closeModal globally accessible for onclick handlers
window.closeModal = closeModal;

// ── Escape HTML ──────────────────────────────────────────────────────────────

function esc(s) {
    if (s == null) return '—';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ═══════════════════════════════════════════════════════════════════════════
// Data loading
// ═══════════════════════════════════════════════════════════════════════════

async function loadLayout() {
    try {
        S.layout = await api('/api/layout');
        renderGrid();
    } catch (e) {
        toast('Layout load error: ' + e.message, 'error');
    }
}

async function loadDrives() {
    try {
        const removable = document.getElementById('opt-removable').checked ? 1 : 0;
        S.drives = await api('/api/drives?removable=' + removable);
    } catch (e) {
        console.warn('drives error', e);
        S.drives = [];
    }
    refreshCellDriveInfo();
}

async function loadImages() {
    try {
        S.images = await api('/api/images');
        const sel = document.getElementById('sel-image');
        const cur = sel.value;
        sel.innerHTML = '<option value="">— выберите образ —</option>';
        S.images.forEach(img => {
            const o = document.createElement('option');
            o.value = img.name;
            o.textContent = `${img.name}  (${img.size_human})`;
            sel.appendChild(o);
        });
        if (cur) sel.value = cur;
    } catch (e) {
        toast('Images load error: ' + e.message, 'error');
    }
}

async function loadPorts() {
    try {
        S.ports = await api('/api/ports');
    } catch (e) {
        S.ports = [];
    }
}

async function loadPhysicalPorts() {
    try {
        S.physicalPorts = await api('/api/ports/physical');
    } catch (e) {
        console.warn('physical ports error', e);
        S.physicalPorts = [];
    }
}

async function loadJobs() {
    try {
        const jobs = await api('/api/jobs');
        jobs.forEach(j => {
            S.jobs[j.job_id] = j;
            S.jobByCellId[j.cell_id] = j;
        });
        updateCounters();
        refreshCellStates();
    } catch (e) {
        console.warn('jobs poll error', e);
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE
// ═══════════════════════════════════════════════════════════════════════════

let sseSource = null;
let sseReconnectTimer = null;

function connectSSE() {
    if (sseSource) { sseSource.close(); }

    sseSource = new EventSource('/api/events');
    sseSource.onopen = () => {
        S.sseConnected = true;
        console.log('SSE connected');
    };
    sseSource.addEventListener('job_update', (e) => {
        try {
            const job = JSON.parse(e.data);
            S.jobs[job.job_id] = job;
            S.jobByCellId[job.cell_id] = job;
            updateCell(job.cell_id);
            updateCounters();
        } catch (err) { console.warn('SSE parse error', err); }
    });
    sseSource.onerror = () => {
        S.sseConnected = false;
        sseSource.close();
        // Reconnect after 3s
        clearTimeout(sseReconnectTimer);
        sseReconnectTimer = setTimeout(connectSSE, 3000);
    };
}

// Fallback polling
setInterval(() => {
    if (!S.sseConnected) {
        loadJobs();
    }
}, 2000);

// ═══════════════════════════════════════════════════════════════════════════
// Grid rendering
// ═══════════════════════════════════════════════════════════════════════════

function renderGrid() {
    if (!S.layout) return;
    const grid = document.getElementById('port-grid');
    const cols = S.layout.cols || 4;
    const compact = S.layout.cell_size === 'compact';
    grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    grid.innerHTML = '';

    (S.layout.cells || []).forEach((cell, idx) => {
        const div = document.createElement('div');
        div.className = 'port-cell' + (compact ? ' compact' : '')
                      + (cell.enabled ? '' : ' disabled')
                      + (S.selectedCells.has(cell.cell_id) ? ' selected' : '');
        div.dataset.cellId = cell.cell_id;
        div.dataset.idx = idx;
        div.id = 'cell-' + cell.cell_id;

        const usbCls = cell.usb_hint === '3.0' ? 'usb3'
                      : cell.usb_hint === '2.0' ? 'usb2' : 'unknown';

        // Find physical port for alias
        const physPort = S.physicalPorts.find(p => p.port_path === cell.port_id);
        const portAlias = physPort ? physPort.alias : (cell.port_id || '—');
        const portTitle = cell.port_id || '—';

        div.innerHTML = `
            <div class="cell-top">
                <span class="cell-label">${esc(cell.label || cell.cell_id)}</span>
                <span class="cell-usb ${usbCls}">${esc(cell.usb_hint)}</span>
            </div>
            <div class="cell-device" title="${esc(portTitle)}">
                <code>${esc(portAlias)}</code>
            </div>
            <div class="cell-drive-info" id="drive-${cell.cell_id}"></div>
            <div class="cell-status status-idle" id="status-${cell.cell_id}">Idle</div>
            <div class="cell-progress-wrap">
                <div class="cell-progress-bar" id="progress-${cell.cell_id}" style="width:0%"></div>
            </div>
            <div class="cell-stats" id="stats-${cell.cell_id}"></div>
            <div class="cell-actions" id="actions-${cell.cell_id}"></div>
        `;

        // Click to select
        div.addEventListener('click', (e) => {
            if (e.target.closest('.cell-actions button')) return;
            handleCellClick(cell.cell_id, idx, e);
        });

        grid.appendChild(div);
    });

    refreshCellDriveInfo();
    refreshCellStates();
}

// ── Drive info on cells ──────────────────────────────────────────────────────

function findDriveForCell(cell) {
    if (!cell.port_id) return null;
    return S.drives.find(d =>
        d.device_path === cell.port_id ||
        d.by_path === cell.port_id
    ) || null;
}

function refreshCellDriveInfo() {
    if (!S.layout) return;
    S.layout.cells.forEach(cell => {
        const el = document.getElementById('drive-' + cell.cell_id);
        if (!el) return;
        const drive = findDriveForCell(cell);
        const job = S.jobByCellId[cell.cell_id];

        // States that must "stick" — do not let drive-refresh overwrite them
        const stickyStates = new Set(['FAILED', 'CANCELLED', 'WRITING', 'VERIFYING',
                                      'EXPANDING', 'RESIZING', 'QUEUED']);
        if (job && stickyStates.has(job.state)) {
            // Still update drive info line, but leave status badge alone
            if (drive) {
                const parts = [drive.device_path, drive.size_human];
                if (drive.model) parts.push(drive.model);
                if (drive.serial) parts.push('S/N:' + drive.serial);
                el.innerHTML = `<span style="font-size:.72rem;color:var(--j-text-muted)">${esc(parts.join(' · '))}</span>`;
                if (drive.is_system)
                    el.innerHTML += ' <span style="color:var(--j-danger);font-size:.7rem">⚠ SYSTEM</span>';
                if (drive.mounted)
                    el.innerHTML += ' <span style="color:var(--j-warning);font-size:.7rem">● mounted</span>';
            } else {
                el.innerHTML = '';
            }
            return;
        }

        if (!drive) {
            el.innerHTML = '';
            // Only idle/done jobs: show "no card"
            if (!job || job.state === 'DONE') {
                setStatus(cell.cell_id, 'no-card', 'Нет карты');
            }
            return;
        }

        const parts = [drive.device_path, drive.size_human];
        if (drive.model) parts.push(drive.model);
        if (drive.serial) parts.push('S/N:' + drive.serial);
        el.innerHTML = `<span style="font-size:.72rem;color:var(--j-text-muted)">${esc(parts.join(' · '))}</span>`;
        if (drive.is_system) {
            el.innerHTML += ' <span style="color:var(--j-danger);font-size:.7rem">⚠ SYSTEM</span>';
        }
        if (drive.mounted) {
            el.innerHTML += ' <span style="color:var(--j-warning);font-size:.7rem">● mounted</span>';
        }

        // If no active/terminal job, show "connected"
        if (!job || job.state === 'DONE') {
            if (drive.is_system || !drive.removable) {
                setStatus(cell.cell_id, 'failed', 'BLOCKED');
            } else {
                setStatus(cell.cell_id, 'connected', 'Подключена');
            }
        }
    });
}

// ── Cell status helpers ──────────────────────────────────────────────────────

function setStatus(cellId, cls, text) {
    const el = document.getElementById('status-' + cellId);
    if (!el) return;
    el.className = 'cell-status status-' + cls;
    el.textContent = text;
}

function refreshCellStates() {
    if (!S.layout) return;
    S.layout.cells.forEach(cell => {
        const job = S.jobByCellId[cell.cell_id];
        if (job) updateCell(cell.cell_id);
    });
}

function updateCell(cellId) {
    const job = S.jobByCellId[cellId];
    if (!job) return;

    const cellEl = document.getElementById('cell-' + cellId);
    if (!cellEl) return;

    // Remove old state classes
    cellEl.classList.remove('state-done','state-failed','state-cancelled',
                            'state-writing','state-verifying','state-expanding','state-resizing');

    const state = job.state;
    const stateMap = {
        'QUEUED':    ['queued',    'В очереди'],
        'WRITING':   ['writing',   `Запись ${(job.progress * 100).toFixed(1)}%`],
        'VERIFYING': ['verifying', `Проверка ${(job.progress * 100).toFixed(1)}%`],
        'EXPANDING': ['expanding', 'Expand…'],
        'RESIZING':  ['resizing',  'Resize…'],
        'DONE':      ['done',      'Готово ✓'],
        'FAILED':    ['failed',    'Ошибка ✗'],
        'CANCELLED': ['cancelled', 'Отменено ✕'],
    };
    const [cls, label] = stateMap[state] || ['idle', state];
    setStatus(cellId, cls, label);

    // Add state class to cell for coloring
    if (['DONE','FAILED','CANCELLED','WRITING','VERIFYING','EXPANDING','RESIZING'].includes(state)) {
        cellEl.classList.add('state-' + state.toLowerCase());
    }

    // Progress bar
    const bar = document.getElementById('progress-' + cellId);
    if (bar) {
        const pct = Math.min(100, Math.max(0, job.progress * 100));
        bar.style.width = pct + '%';
        bar.className = 'cell-progress-bar'
            + (state === 'DONE' ? ' done' : '')
            + (state === 'FAILED' ? ' failed' : '')
            + (state === 'VERIFYING' ? ' verify' : '');
    }

    // Stats
    const statsEl = document.getElementById('stats-' + cellId);
    if (statsEl) {
        if (['WRITING','VERIFYING','EXPANDING','RESIZING'].includes(state)) {
            statsEl.innerHTML = `
                <span>${(job.progress * 100).toFixed(1)}%</span>
                <span>⚡ <strong>${esc(job.speed_human || '—')}</strong></span>
                <span>ETA: <strong>${esc(job.eta_human || '--:--')}</strong></span>
                <span>Stage: <strong>${esc(job.stage)}</strong></span>
            `;
        } else if (state === 'DONE') {
            statsEl.innerHTML = '<span style="color:var(--j-success)">✓ Complete</span>';
        } else if (state === 'FAILED') {
            const errText = esc(job.error || 'Unknown error');
            statsEl.innerHTML = `<span style="color:var(--j-danger);word-break:break-word">⚠ ${errText}</span>`;
        } else if (state === 'CANCELLED') {
            const pct = (job.progress * 100).toFixed(1);
            statsEl.innerHTML = `<span style="color:var(--j-text-muted)">Прервано на ${pct}%</span>`;
        } else {
            statsEl.innerHTML = '';
        }
    }

    // Actions
    const actEl = document.getElementById('actions-' + cellId);
    if (actEl) {
        let btns = '';
        btns += `<button class="btn" onclick="showJobDetails('${job.job_id}')">Details</button>`;
        if (['QUEUED','WRITING','VERIFYING','EXPANDING','RESIZING'].includes(state)) {
            btns += `<button class="btn btn-error" onclick="cancelJob('${job.job_id}')">Cancel</button>`;
        }
        if (state === 'FAILED' || state === 'CANCELLED') {
            btns += `<button class="btn" onclick="retryJob('${job.job_id}')">Retry</button>`;
        }
        if (state === 'DONE') {
            btns += `<button class="btn" onclick="ejectCell('${cellId}')">⏏ Eject</button>`;
        }
        actEl.innerHTML = btns;
    }
}

// ── Counters ─────────────────────────────────────────────────────────────────

function updateCounters() {
    let active = 0, queued = 0, done = 0, failed = 0;
    Object.values(S.jobByCellId).forEach(j => {
        if (['WRITING','VERIFYING','EXPANDING','RESIZING'].includes(j.state)) active++;
        else if (j.state === 'QUEUED') queued++;
        else if (j.state === 'DONE') done++;
        else if (j.state === 'FAILED') failed++;
    });
    document.getElementById('cnt-active').textContent = active;
    document.getElementById('cnt-queued').textContent = queued;
    document.getElementById('cnt-done').textContent = done;
    document.getElementById('cnt-failed').textContent = failed;
}

// ═══════════════════════════════════════════════════════════════════════════
// Cell selection
// ═══════════════════════════════════════════════════════════════════════════

function handleCellClick(cellId, idx, event) {
    const cells = S.layout.cells;

    if (event.shiftKey && S.lastSelectedIdx >= 0) {
        // Range select
        const start = Math.min(S.lastSelectedIdx, idx);
        const end = Math.max(S.lastSelectedIdx, idx);
        for (let i = start; i <= end; i++) {
            if (cells[i].enabled) S.selectedCells.add(cells[i].cell_id);
        }
    } else {
        // Toggle
        if (S.selectedCells.has(cellId)) {
            S.selectedCells.delete(cellId);
        } else {
            S.selectedCells.add(cellId);
        }
    }
    S.lastSelectedIdx = idx;
    updateSelectionUI();
}

function updateSelectionUI() {
    document.querySelectorAll('.port-cell').forEach(el => {
        el.classList.toggle('selected', S.selectedCells.has(el.dataset.cellId));
    });
}

document.getElementById('btn-select-all').addEventListener('click', () => {
    if (!S.layout) return;
    S.layout.cells.forEach(cell => {
        if (!cell.enabled) return;
        const drive = findDriveForCell(cell);
        if (drive && drive.removable && !drive.is_system) {
            S.selectedCells.add(cell.cell_id);
        }
    });
    updateSelectionUI();
});

document.getElementById('btn-deselect-all').addEventListener('click', () => {
    S.selectedCells.clear();
    updateSelectionUI();
});

// ═══════════════════════════════════════════════════════════════════════════
// Batch actions
// ═══════════════════════════════════════════════════════════════════════════

document.getElementById('btn-start').addEventListener('click', async () => {
    const imageName = document.getElementById('sel-image').value;
    if (!imageName) { toast('Выберите образ!', 'warning'); return; }
    if (S.selectedCells.size === 0) { toast('Выберите ячейки!', 'warning'); return; }

    const body = {
        image_name: imageName,
        cell_ids: [...S.selectedCells],
        concurrency: parseInt(document.getElementById('inp-concurrency').value) || 2,
        options: {
            verify: document.getElementById('opt-verify').checked,
            expand_partition: document.getElementById('opt-expand').checked,
            resize_filesystem: document.getElementById('opt-resize').checked,
            eject_after_done: document.getElementById('opt-eject').checked,
        },
    };

    try {
        const jobs = await api('/api/batch/start', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        jobs.forEach(j => {
            S.jobs[j.job_id] = j;
            S.jobByCellId[j.cell_id] = j;
            updateCell(j.cell_id);
        });
        updateCounters();
        toast(`Запущено ${jobs.length} задач`, 'success');
    } catch (e) {
        toast('Start error: ' + e.message, 'error');
    }
});

document.getElementById('btn-stop-all').addEventListener('click', async () => {
    const active = Object.values(S.jobs).filter(j =>
        ['QUEUED','WRITING','VERIFYING','EXPANDING','RESIZING'].includes(j.state)
    ).length;
    if (active === 0) { toast('Нет активных задач', 'warning'); return; }
    if (!confirm(`Остановить все задачи (${active} активных)?\n\nВсе текущие записи будут прерваны, карты могут оказаться неработоспособными.`)) return;
    try {
        await api('/api/batch/cancel', { method: 'POST' });
        toast('All jobs cancelled', 'warning');
        await loadJobs();
        refreshCellStates();
    } catch (e) {
        toast('Stop error: ' + e.message, 'error');
    }
});

document.getElementById('btn-retry-failed').addEventListener('click', async () => {
    try {
        const jobs = await api('/api/batch/retry', { method: 'POST' });
        jobs.forEach(j => {
            S.jobs[j.job_id] = j;
            S.jobByCellId[j.cell_id] = j;
            updateCell(j.cell_id);
        });
        updateCounters();
        toast(`Retrying ${jobs.length} jobs`, 'success');
    } catch (e) {
        toast('Retry error: ' + e.message, 'error');
    }
});

// ── Individual job actions ───────────────────────────────────────────────────

window.cancelJob = async function(jobId) {
    const job = S.jobs[jobId];
    const label = job ? `ячейки ${job.cell_id}` : jobId;
    if (!confirm(`Прервать запись для ${label}?\n\nПроцесс будет остановлен, карта может оказаться неработоспособной.`)) return;
    try {
        await api(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
        toast('Job cancelled');
    } catch (e) {
        toast('Cancel error: ' + e.message, 'error');
    }
};

window.retryJob = async function(jobId) {
    try {
        const job = await api(`/api/jobs/${jobId}/retry`, { method: 'POST' });
        S.jobs[job.job_id] = job;
        S.jobByCellId[job.cell_id] = job;
        updateCell(job.cell_id);
        updateCounters();
        toast('Job retried');
    } catch (e) {
        toast('Retry error: ' + e.message, 'error');
    }
};

window.ejectCell = async function(cellId) {
    try {
        await api(`/api/cells/${cellId}/eject`, { method: 'POST' });
        toast('Ejected ✓', 'success');
        await loadDrives();
    } catch (e) {
        toast('Eject error: ' + e.message, 'error');
    }
};

// ── Job details modal ────────────────────────────────────────────────────────

window.showJobDetails = async function(jobId) {
    try {
        const job = await api(`/api/jobs/${jobId}`);
        document.getElementById('detail-title').textContent = `Job — ${job.cell_id}`;
        document.getElementById('detail-info').innerHTML = `
            <div>Device: <strong>${esc(job.device_path)}</strong></div>
            <div>Image: <strong>${esc(job.image_name)}</strong></div>
            <div>State: <strong>${esc(job.state)}</strong> · Stage: <strong>${esc(job.stage)}</strong></div>
            <div>Progress: <strong>${(job.progress * 100).toFixed(1)}%</strong></div>
            ${job.error ? `<div style="color:var(--j-danger)">Error: ${esc(job.error)}</div>` : ''}
            ${job.warning ? `<div style="color:var(--j-warning)">Warning: ${esc(job.warning)}</div>` : ''}
        `;
        const logEl = document.getElementById('detail-log');
        logEl.textContent = (job.log_tail || []).join('\n') || '(no log)';
        logEl.scrollTop = logEl.scrollHeight;
        openModal('modal-details');
    } catch (e) {
        toast('Details error: ' + e.message, 'error');
    }
};

// ═══════════════════════════════════════════════════════════════════════════
// Layout editor
// ═══════════════════════════════════════════════════════════════════════════

document.getElementById('btn-edit-layout').addEventListener('click', () => {
    openLayoutEditor();
});

async function openLayoutEditor() {
    if (!S.layout) return;
    document.getElementById('le-rows').value = S.layout.rows;
    document.getElementById('le-cols').value = S.layout.cols;
    document.getElementById('le-size').value = S.layout.cell_size || 'normal';
    // Always refresh physical ports when opening the editor
    await loadPhysicalPorts();
    renderPortsPanel();
    renderLayoutEditorGrid();
    openModal('modal-layout');
}

// ── Port option label builder ─────────────────────────────────────────────────

function portOptionLabel(p) {
    // e.g. "USB 0:3  ● /dev/sdb  SanDisk  14.8 GB"
    let label = p.alias || p.port_path;
    if (p.usb_speed && p.usb_speed !== 'unknown') label += `  [USB ${p.usb_speed}]`;
    if (p.occupied) {
        label += `  ● ${p.device_path}`;
        if (p.device_model) label += `  ${p.device_model}`;
        if (p.device_size)  label += `  ${p.device_size}`;
    } else {
        label += '  ○ пусто';
    }
    return label;
}

function portShortLabel(p) {
    if (!p) return '—';
    let s = p.alias;
    if (p.occupied) s += ' ●';
    return s;
}

// ── Render layout editor table ───────────────────────────────────────────────

function renderLayoutEditorGrid() {
    const grid = document.getElementById('le-grid');

    // Headers
    grid.innerHTML = `
        <div class="le-header">Cell</div>
        <div class="le-header">Label</div>
        <div class="le-header">Физический порт</div>
        <div class="le-header">USB</div>
        <div class="le-header">On</div>
    `;

    // Build port options HTML from physicalPorts
    const portOptHTML = S.physicalPorts.map(p => {
        const occupied = p.occupied
            ? `● ${esc(p.device_path)}${p.device_model ? ' · ' + esc(p.device_model) : ''}${p.device_size ? ' · ' + esc(p.device_size) : ''}`
            : '○ пусто';
        const speed = (p.usb_speed && p.usb_speed !== 'unknown') ? ` [USB ${esc(p.usb_speed)}]` : '';
        return `<option value="${esc(p.port_path)}">${esc(p.alias)}${speed}  ${occupied}</option>`;
    }).join('');

    (S.layout.cells || []).forEach((cell, i) => {
        // Find currently assigned port info
        const assignedPort = S.physicalPorts.find(p => p.port_path === cell.port_id) || null;

        // USB hint: auto-detect from port if available, else keep manual
        const autoUsb = assignedPort ? assignedPort.usb_speed : 'unknown';
        const usbVal = cell.usb_hint !== 'unknown' ? cell.usb_hint : autoUsb;

        grid.innerHTML += `
            <div class="le-cell-id">${esc(cell.cell_id)}</div>
            <input class="le-input" data-le-idx="${i}" data-le-field="label"
                   value="${esc(cell.label || '')}" placeholder="Метка" />
            <div class="le-port-selector">
                <select data-le-idx="${i}" data-le-field="port_id" class="le-port-select">
                    <option value="">— не привязано —</option>
                    ${portOptHTML}
                </select>
                <div class="le-port-preview" id="le-preview-${i}"></div>
            </div>
            <select data-le-idx="${i}" data-le-field="usb_hint" class="le-usb-select">
                <option value="unknown" ${usbVal==='unknown'?'selected':''}>?</option>
                <option value="2.0" ${usbVal==='2.0'?'selected':''}>2.0</option>
                <option value="3.0" ${usbVal==='3.0'?'selected':''}>3.0</option>
            </select>
            <input type="checkbox" data-le-idx="${i}" data-le-field="enabled"
                   ${cell.enabled ? 'checked' : ''} />
        `;
    });

    // Set current values and hook change events
    setTimeout(() => {
        grid.querySelectorAll('select[data-le-field="port_id"]').forEach(sel => {
            const idx = parseInt(sel.dataset.leIdx);
            const cell = S.layout.cells[idx];
            if (cell && cell.port_id) {
                // Try to set value; if not in list, add it
                sel.value = cell.port_id;
                if (sel.value !== cell.port_id) {
                    const o = document.createElement('option');
                    o.value = cell.port_id;
                    o.textContent = `${cell.port_id} (сохранённый)`;
                    sel.appendChild(o);
                    sel.value = cell.port_id;
                }
            }
            // Update preview and auto USB hint on change
            sel.addEventListener('change', () => {
                updateLePreview(idx);
                autoFillUsbHint(idx);
            });
            updateLePreview(idx);
        });
    }, 0);
}

// ── Update port preview row ───────────────────────────────────────────────────

function updateLePreview(idx) {
    const sel = document.querySelector(`select[data-le-idx="${idx}"][data-le-field="port_id"]`);
    const preview = document.getElementById(`le-preview-${idx}`);
    if (!sel || !preview) return;

    const portPath = sel.value;
    if (!portPath) {
        preview.innerHTML = '';
        return;
    }

    const port = S.physicalPorts.find(p => p.port_path === portPath);
    if (!port) {
        preview.innerHTML = `<span class="le-preview-path">${esc(portPath)}</span>`;
        return;
    }

    const speedCls = port.usb_speed === '3.0' || port.usb_speed === '3.2' ? 'usb3'
                   : port.usb_speed === '2.0' ? 'usb2' : 'usb-unknown';
    const speedBadge = `<span class="le-usb-badge ${speedCls}">USB ${esc(port.usb_speed)}</span>`;

    let deviceInfo = '';
    if (port.occupied) {
        const safe = port.is_system ? '<span class="le-danger">⚠ SYSTEM</span>' : '';
        const rm = port.removable ? '' : '<span class="le-warn">non-removable</span>';
        deviceInfo = `
            <span class="le-device-dot occupied">●</span>
            <span class="le-device-name">${esc(port.device_path)}</span>
            <span class="le-device-meta">${esc(port.device_model || '')} ${esc(port.device_size || '')}</span>
            ${safe}${rm}
        `;
    } else {
        deviceInfo = `<span class="le-device-dot empty">○</span><span class="le-device-empty">Нет устройства</span>`;
    }

    preview.innerHTML = `
        <div class="le-preview-inner">
            ${speedBadge}
            <span class="le-preview-path" title="${esc(portPath)}">${esc(port.alias)}</span>
            <span class="le-preview-device">${deviceInfo}</span>
        </div>
    `;
}

// ── Auto-fill USB hint from physical port ─────────────────────────────────────

function autoFillUsbHint(idx) {
    const portSel = document.querySelector(`select[data-le-idx="${idx}"][data-le-field="port_id"]`);
    const usbSel  = document.querySelector(`select[data-le-idx="${idx}"][data-le-field="usb_hint"]`);
    if (!portSel || !usbSel) return;
    const port = S.physicalPorts.find(p => p.port_path === portSel.value);
    if (!port) return;
    const speed = port.usb_speed;
    if (speed === '2.0' || speed === '3.0' || speed === '3.2') {
        usbSel.value = speed.startsWith('3') ? '3.0' : '2.0';
    }
}

// ── Auto-assign: assign ports to cells in order ───────────────────────────────

document.getElementById('le-auto-assign').addEventListener('click', () => {
    // Get all physical ports that have a real USB topology path
    const usablePorts = S.physicalPorts.filter(p => p.port_path);
    if (usablePorts.length === 0) {
        toast('Нет доступных физических портов', 'warning');
        return;
    }

    const cells = S.layout.cells || [];
    usablePorts.forEach((port, i) => {
        if (i >= cells.length) return;
        cells[i].port_id = port.port_path;
        // Auto USB hint
        if (port.usb_speed === '2.0') cells[i].usb_hint = '2.0';
        else if (port.usb_speed === '3.0' || port.usb_speed === '3.2') cells[i].usb_hint = '3.0';
    });

    // Re-render editor to reflect changes
    renderLayoutEditorGrid();
    toast(`Назначено ${Math.min(usablePorts.length, cells.length)} ячеек`, 'success');
});

document.getElementById('le-refresh-ports').addEventListener('click', async () => {
    await loadPhysicalPorts();
    renderPortsPanel();
    renderLayoutEditorGrid();
    toast('Порты обновлены');
});

// ── Render physical ports legend panel ───────────────────────────────────────

function renderPortsPanel() {
    const list = document.getElementById('le-ports-list');
    if (!list) return;
    if (S.physicalPorts.length === 0) {
        list.innerHTML = '<span style="color:var(--j-text-muted);font-size:.8rem">Нет данных</span>';
        return;
    }
    list.innerHTML = S.physicalPorts.map((p, idx) => {
        const speedCls = p.usb_speed === '3.0' || p.usb_speed === '3.2' ? 'usb3'
                       : p.usb_speed === '2.0' ? 'usb2' : 'usb-unknown';
        const devLine = p.occupied
            ? `<span class="lpp-dev">${esc(p.device_path)} <span class="lpp-meta">${esc(p.device_model || '')} ${esc(p.device_size || '')}</span></span>`
            : `<span class="lpp-empty">пусто</span>`;
        const sysWarn = p.is_system ? ' <span class="le-danger">⚠SYSTEM</span>' : '';
        const rmWarn  = p.occupied && !p.removable ? ' <span class="le-warn">non-removable</span>' : '';
        return `
            <div class="lpp-item${p.occupied ? ' occupied' : ''}">
                <span class="lpp-num">${idx + 1}</span>
                <span class="le-usb-badge ${speedCls}">USB ${esc(p.usb_speed)}</span>
                <span class="lpp-alias">${esc(p.alias)}</span>
                ${devLine}${sysWarn}${rmWarn}
            </div>
        `;
    }).join('');
}

document.getElementById('le-apply-rc').addEventListener('click', () => {
    const rows = parseInt(document.getElementById('le-rows').value) || 2;
    const cols = parseInt(document.getElementById('le-cols').value) || 4;
    const total = rows * cols;
    const cells = [];
    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            const label = String.fromCharCode(65 + r) + (c + 1);
            // Reuse existing cell if available
            const existing = (S.layout.cells || []).find(x => x.cell_id === label);
            cells.push(existing || {
                cell_id: label,
                label: label,
                port_id: '',
                usb_hint: 'unknown',
                enabled: true,
            });
        }
    }
    S.layout.rows = rows;
    S.layout.cols = cols;
    S.layout.cells = cells;
    renderPortsPanel();
    renderLayoutEditorGrid();
});

document.getElementById('le-save').addEventListener('click', async () => {
    // Collect edited values
    const grid = document.getElementById('le-grid');
    grid.querySelectorAll('[data-le-idx]').forEach(el => {
        const idx = parseInt(el.dataset.leIdx);
        const field = el.dataset.leField;
        const cell = S.layout.cells[idx];
        if (!cell) return;
        if (field === 'enabled') {
            cell.enabled = el.checked;
        } else {
            cell[field] = el.value;
        }
    });
    S.layout.cell_size = document.getElementById('le-size').value;
    S.layout.rows = parseInt(document.getElementById('le-rows').value) || S.layout.rows;
    S.layout.cols = parseInt(document.getElementById('le-cols').value) || S.layout.cols;

    try {
        await api('/api/layout', {
            method: 'PUT',
            body: JSON.stringify(S.layout),
        });
        toast('Layout saved ✓', 'success');
        closeModal('modal-layout');
        renderGrid();
    } catch (e) {
        toast('Save error: ' + e.message, 'error');
    }
});

document.getElementById('le-export').addEventListener('click', () => {
    window.location.href = '/api/layout/export';
});

document.getElementById('le-import').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
        const layout = await fetch('/api/layout/import', { method: 'POST', body: fd })
            .then(r => { if (!r.ok) throw new Error('Import failed'); return r.json(); });
        S.layout = layout;
        toast('Layout imported ✓', 'success');
        renderLayoutEditorGrid();
        renderGrid();
    } catch (err) {
        toast('Import error: ' + err.message, 'error');
    }
});

// ═══════════════════════════════════════════════════════════════════════════
// Refresh drives button
// ═══════════════════════════════════════════════════════════════════════════

document.getElementById('btn-refresh-drives').addEventListener('click', async () => {
    await loadDrives();
    toast('Drives refreshed');
});

document.getElementById('opt-removable').addEventListener('change', () => {
    loadDrives();
});

// ═══════════════════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════════════════

(async () => {
    await Promise.all([loadLayout(), loadImages(), loadPorts(), loadPhysicalPorts()]);
    await loadDrives();
    await loadJobs();
    connectSSE();
    // Periodic drive refresh
    setInterval(loadDrives, 5000);
})();

