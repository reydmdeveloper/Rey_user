/* ═══════════════════════════════════════════════════════════════════
   REYDM – Night Shift Attendance JS (MySQL-backed)
   ═══════════════════════════════════════════════════════════════════ */

const WD=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const MN=['January','February','March','April','May','June','July','August','September','October','November','December'];
const MNS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const COLORS=['#1f6feb','#3fb950','#d29922','#f78166','#a371f7','#39d353','#58a6ff','#ff7b72','#79c0ff','#ffa657'];

function dim(y,m){return new Date(y,m+1,0).getDate();}
function isWknd(y,m,d){const w=new Date(y,m,d).getDay();return w===0||w===6;}
function getWD(y,m,d){return WD[new Date(y,m,d).getDay()];}
function initials(n){return n.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);}

// ── STATE ──
let nsEmployees = [];
let nsAttendance = {}; // { "E001": [1, 5, 7], ... }
let nsYearData = {}; // { "E001": { 1: 5, 2: 10, ... }, ... }
let nsCurYear = new Date().getFullYear();
let nsCurMonth = new Date().getMonth();
let nsSelectedEmp = null;
let nsFilter = 'all';
let nsSearch = '';
let nsEmpSearch = '';
let nsCurPage = 'attendance';

// ── API HELPERS ──
async function apiFetch(url, opts) {
    try {
        const res = await fetch(url, opts);
        const text = await res.text();
        if (!text) return { success: false, message: 'Empty response' };
        try { return JSON.parse(text); }
        catch (e) {
            console.error('Non-JSON response from', url, text.slice(0, 200));
            return { success: false, message: 'Server error (' + res.status + ')' };
        }
    } catch (err) {
        console.error('Network error:', err);
        return { success: false, message: 'Network error: ' + (err.message || 'unknown') };
    }
}

async function loadEmployees() {
    const data = await apiFetch('/api/ns/employees');
    nsEmployees = Array.isArray(data) ? data : [];
}

async function loadAttendance() {
    const data = await apiFetch(`/api/ns/attendance/${nsCurYear}/${nsCurMonth + 1}`);
    nsAttendance = (data && typeof data === 'object' && !Array.isArray(data) && !data.success === false) ? data : {};
    if (data && data.success === false) nsAttendance = {};
}

async function loadYearData() {
    const data = await apiFetch(`/api/ns/attendance/year/${nsCurYear}`);
    nsYearData = (data && typeof data === 'object' && !Array.isArray(data) && !(data.success === false)) ? data : {};
}

// ── TOAST ──
let nsToastTimer = null;
function nsToast(msg, type='ok') {
    const t = document.getElementById('ns-toast');
    t.textContent = msg;
    t.className = 'ns-toast show ' + (type === 'err' ? 'err' : 'ok');
    clearTimeout(nsToastTimer);
    nsToastTimer = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── MODALS ──
function nsOpenModal(id) { document.getElementById(id).classList.add('open'); }
function nsCloseModal(id) { document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('.ns-modal-overlay').forEach(o => {
    o.addEventListener('click', e => { if (e.target === o) o.classList.remove('open'); });
});

// ── PAGE SWITCHING ──
function nsSwitchPage(p) {
    nsCurPage = p;
    document.querySelectorAll('.ns-page').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.ns-tab').forEach(el => el.classList.remove('active'));
    document.getElementById('ns-page-' + p).style.display = 'block';
    document.querySelectorAll('.ns-tab').forEach(el => {
        if (el.textContent.toLowerCase().includes(p.slice(0, 4))) el.classList.add('active');
    });
    document.getElementById('ns-month-nav').style.display = p === 'attendance' ? 'flex' : 'none';
    if (p === 'dashboard') nsRenderDashboard();
    if (p === 'employees') nsRenderEmpTable();
}

// ── YEAR SELECT ──
function nsRenderYrSel() {
    const sel = document.getElementById('ns-yr-sel');
    sel.innerHTML = '';
    const cy = new Date().getFullYear();
    for (let y = cy - 3; y <= cy + 3; y++) {
        const o = document.createElement('option');
        o.value = y; o.textContent = y;
        if (y === nsCurYear) o.selected = true;
        sel.appendChild(o);
    }
    sel.onchange = async () => { nsCurYear = +sel.value; await loadAttendance(); nsRender(); };
}

// ── FILTER ──
function nsSetFilter(f) {
    nsFilter = f;
    ['all', 'active', 'resigned'].forEach(x => {
        document.getElementById('ns-fc-' + x).classList.toggle('on', x === f);
    });
    nsRenderBody();
    nsRenderStats();
}

document.getElementById('ns-search-inp').addEventListener('input', e => {
    nsSearch = e.target.value.toLowerCase();
    nsRenderBody(); nsRenderStats();
});
document.getElementById('ns-emp-search').addEventListener('input', e => {
    nsEmpSearch = e.target.value.toLowerCase();
    nsRenderEmpTable();
});

function nsFilteredEmployees() {
    return nsEmployees.filter(emp => {
        const matchFilter = nsFilter === 'all' || (emp.status || 'active') === nsFilter;
        const matchSearch = !nsSearch ||
            emp.name.toLowerCase().includes(nsSearch) ||
            emp.emp_id.toLowerCase().includes(nsSearch) ||
            (emp.dept || '').toLowerCase().includes(nsSearch);
        return matchFilter && matchSearch;
    });
}

// ── ATTENDANCE HEADER ──
function nsRenderHeader() {
    const y = nsCurYear, m = nsCurMonth, days = dim(y, m);
    document.getElementById('ns-m-disp').textContent = MN[m] + ' ' + y;
    document.getElementById('ns-sub-lbl').textContent = 'Click cell to mark present · Weekends shaded';
    let r1 = `<tr><th class="nc">${MN[m]} ${y}</th><th class="ic">Emp ID</th><th class="sc">Status</th>`;
    for (let d = 1; d <= days; d++) r1 += `<th style="width:28px;min-width:28px;max-width:28px;font-family:var(--mono);font-size:11px">${d}</th>`;
    r1 += `<th class="th-total">Total</th></tr>`;
    let r2 = `<tr><th class="nc" style="text-transform:none;letter-spacing:0"></th><th class="ic" style="text-transform:none"></th><th class="sc"></th>`;
    for (let d = 1; d <= days; d++) { const wk = isWknd(y, m, d); r2 += `<th class="wday${wk ? ' wknd' : ''}">${getWD(y, m, d)}</th>`; }
    r2 += `<th class="th-total"></th></tr>`;
    document.getElementById('ns-thead').innerHTML = r1 + r2;
}

// ── ATTENDANCE BODY ──
function nsRenderBody() {
    const y = nsCurYear, m = nsCurMonth, days = dim(y, m);
    const tbody = document.getElementById('ns-tbody');
    tbody.innerHTML = '';
    const emps = nsFilteredEmployees();
    if (!emps.length) {
        tbody.innerHTML = `<tr class="empty-r"><td colspan="999">No employees match the current filter.</td></tr>`;
        return;
    }
    emps.forEach(emp => {
        const isResigned = (emp.status || 'active') === 'resigned';
        const tr = document.createElement('tr');
        if (isResigned) tr.classList.add('resigned-row');
        const presentDays = nsAttendance[emp.emp_id] || [];
        let total = 0;
        const statusBadge = isResigned
            ? `<span class="status-badge-ns status-resigned-ns" onclick="nsToggleStatus('${emp.emp_id}')" title="Click to reactivate">✕ Resigned</span>`
            : `<span class="status-badge-ns status-active-ns" onclick="nsToggleStatus('${emp.emp_id}')" title="Click to mark resigned">✓ Active</span>`;
        let html = `<td class="nc"><span class="emp-name">${emp.name}</span>${emp.dept ? `<span style="font-size:10px;color:var(--muted);margin-left:5px">${emp.dept}</span>` : ''}</td>`
            + `<td class="ic">${emp.emp_id}</td>`
            + `<td class="sc">${statusBadge}</td>`;
        for (let d = 1; d <= days; d++) {
            const present = presentDays.includes(d);
            const wk = isWknd(y, m, d);
            if (present) total++;
            html += `<td class="dc${present ? ' present' : ''}${wk ? ' wknd' : ''}${isResigned ? ' disabled' : ''}" `
                + `data-eid="${emp.emp_id}" data-d="${d}" title="${emp.name} — ${MN[m]} ${d}, ${getWD(y, m, d)}${isResigned ? ' (Resigned)' : ''}"></td>`;
        }
        html += `<td class="tc">${total}</td>`;
        tr.innerHTML = html;
        tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.dc:not(.disabled)').forEach(c => {
        c.addEventListener('click', async () => {
            const eid = c.dataset.eid;
            const d = +c.dataset.d;
            const res = await apiFetch('/api/ns/attendance/toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ emp_id: eid, year: nsCurYear, month: nsCurMonth + 1, day: d })
            });
            if (res.success) {
                if (!nsAttendance[eid]) nsAttendance[eid] = [];
                if (res.present) {
                    nsAttendance[eid].push(d);
                } else {
                    nsAttendance[eid] = nsAttendance[eid].filter(x => x !== d);
                }
                nsRenderBody();
                nsRenderStats();
            }
        });
    });
}

// ── TOGGLE STATUS ──
async function nsToggleStatus(eid) {
    const emp = nsEmployees.find(e => e.emp_id === eid);
    if (!emp) return;
    const isResigned = (emp.status || 'active') === 'resigned';
    const newStatus = isResigned ? 'active' : 'resigned';
    if (!isResigned && !confirm(`Mark ${emp.name} as resigned?`)) return;
    await apiFetch(`/api/ns/employees/${eid}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emp_id: eid, name: emp.name, dept: emp.dept, status: newStatus })
    });
    emp.status = newStatus;
    nsToast(`${emp.name} ${newStatus === 'active' ? 'reactivated' : 'marked resigned'}`, newStatus === 'active' ? 'ok' : 'err');
    nsRenderBody(); nsRenderStats();
    if (nsCurPage === 'employees') nsRenderEmpTable();
}

// ── STATS ──
function nsRenderStats() {
    const y = nsCurYear, m = nsCurMonth, days = dim(y, m);
    const active = nsEmployees.filter(e => (e.status || 'active') === 'active');
    const resigned = nsEmployees.filter(e => (e.status || 'active') === 'resigned');
    let total = 0, top = { name: '-', count: 0 };
    active.forEach(emp => {
        const c = (nsAttendance[emp.emp_id] || []).length;
        total += c;
        if (c > top.count) top = { name: emp.name, count: c };
    });
    const slots = active.length * days;
    const pct = slots > 0 ? Math.round(total / slots * 100) : 0;
    document.getElementById('ns-stats-row').innerHTML = `
        <div class="ns-stat"><div class="lbl">Total Employees</div><div class="val blue">${nsEmployees.length}</div></div>
        <div class="ns-stat"><div class="lbl">Active</div><div class="val green">${active.length}</div></div>
        <div class="ns-stat"><div class="lbl">Resigned</div><div class="val${resigned.length ? ' red' : ''}">${resigned.length}</div></div>
        <div class="ns-stat"><div class="lbl">Days in Month</div><div class="val">${days}</div></div>
        <div class="ns-stat"><div class="lbl">Total Present Days</div><div class="val green">${total}</div></div>
        <div class="ns-stat"><div class="lbl">Attendance %</div><div class="val${pct >= 75 ? ' green' : ''}">${pct}%</div></div>
        <div class="ns-stat"><div class="lbl">Top Attendance</div><div class="val sm">${top.name}</div></div>`;
}

// ── EMPLOYEE TABLE ──
function nsRenderEmpTable() {
    const tbody = document.getElementById('ns-emp-tbody');
    const filtered = nsEmployees.filter(e => {
        if (!nsEmpSearch) return true;
        return e.name.toLowerCase().includes(nsEmpSearch) || e.emp_id.toLowerCase().includes(nsEmpSearch) || (e.dept || '').toLowerCase().includes(nsEmpSearch);
    });
    if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted)">No employees found.</td></tr>`;
        return;
    }
    tbody.innerHTML = filtered.map(emp => {
        const isResigned = (emp.status || 'active') === 'resigned';
        const statusBadge = isResigned
            ? `<span class="status-badge-ns status-resigned-ns">✕ Resigned</span>`
            : `<span class="status-badge-ns status-active-ns">✓ Active</span>`;
        return `<tr>
            <td style="font-weight:500${isResigned ? ';text-decoration:line-through;opacity:.6' : ''}">${emp.name}</td>
            <td class="mono">${emp.emp_id}</td>
            <td style="color:var(--muted);font-size:12px">${emp.dept || '—'}</td>
            <td>${statusBadge}</td>
            <td><div class="actions">
                <button class="icon-btn-ns" onclick="nsOpenEditModal('${emp.emp_id}')" title="Edit">✏️</button>
                <button class="icon-btn-ns" onclick="nsToggleStatus('${emp.emp_id}')" title="${isResigned ? 'Reactivate' : 'Mark resigned'}">${isResigned ? '↩' : '🚫'}</button>
                <button class="icon-btn-ns red" onclick="nsDeleteEmployee('${emp.emp_id}')" title="Delete">🗑</button>
            </div></td>
        </tr>`;
    }).join('');
}

// ── ADD EMPLOYEE ──
function nsOpenAddModal() {
    document.getElementById('ns-add-id').value = '';
    document.getElementById('ns-add-name').value = '';
    document.getElementById('ns-add-dept').value = '';
    document.getElementById('ns-add-status').value = 'active';
    nsOpenModal('ns-modal-add');
    setTimeout(() => document.getElementById('ns-add-id').focus(), 80);
}

async function nsAddEmployee() {
    const emp_id = document.getElementById('ns-add-id').value.trim();
    const name = document.getElementById('ns-add-name').value.trim();
    const dept = document.getElementById('ns-add-dept').value.trim();
    const status = document.getElementById('ns-add-status').value;
    if (!emp_id || !name) { nsToast('ID and Name are required', 'err'); return; }
    if (emp_id.length > 20) { nsToast('Employee ID too long (max 20 chars)', 'err'); return; }
    if (name.length > 100) { nsToast('Name too long (max 100 chars)', 'err'); return; }
    if (!/^[A-Za-z0-9_\-]+$/.test(emp_id)) {
        nsToast('Employee ID can only contain letters, digits, underscore and hyphen', 'err');
        return;
    }
    if (nsEmployees.some(e => e.emp_id.toLowerCase() === emp_id.toLowerCase())) {
        nsToast(`Employee ID "${emp_id}" already exists`, 'err'); return;
    }
    const res = await apiFetch('/api/ns/employees', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emp_id, name, dept, status })
    });
    if (!res.success) { nsToast(res.message || 'Error adding employee', 'err'); return; }
    await loadEmployees();
    nsRender(); nsRenderEmpTable();
    nsCloseModal('ns-modal-add');
    nsToast(`${name} added successfully`);
}

// ── EDIT EMPLOYEE ──
function nsOpenEditModal(eid) {
    const emp = nsEmployees.find(e => e.emp_id === eid);
    if (!emp) return;
    document.getElementById('ns-edit-orig-id').value = emp.emp_id;
    document.getElementById('ns-edit-id').value = emp.emp_id;
    document.getElementById('ns-edit-name').value = emp.name;
    document.getElementById('ns-edit-dept').value = emp.dept || '';
    document.getElementById('ns-edit-status').value = emp.status || 'active';
    nsOpenModal('ns-modal-edit');
    setTimeout(() => document.getElementById('ns-edit-name').focus(), 80);
}

async function nsSaveEdit() {
    const origId = document.getElementById('ns-edit-orig-id').value;
    const emp_id = document.getElementById('ns-edit-id').value.trim();
    const name = document.getElementById('ns-edit-name').value.trim();
    const dept = document.getElementById('ns-edit-dept').value.trim();
    const status = document.getElementById('ns-edit-status').value;
    if (!emp_id || !name) { nsToast('ID and Name required', 'err'); return; }
    if (emp_id.length > 20) { nsToast('Employee ID too long (max 20 chars)', 'err'); return; }
    if (name.length > 100) { nsToast('Name too long (max 100 chars)', 'err'); return; }
    if (!/^[A-Za-z0-9_\-]+$/.test(emp_id)) {
        nsToast('Employee ID can only contain letters, digits, underscore and hyphen', 'err');
        return;
    }
    // Check for ID collision with other employees (excluding self)
    if (emp_id !== origId && nsEmployees.some(e => e.emp_id.toLowerCase() === emp_id.toLowerCase())) {
        nsToast(`Employee ID "${emp_id}" already in use`, 'err'); return;
    }
    const res = await apiFetch(`/api/ns/employees/${origId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emp_id, name, dept, status })
    });
    if (res && res.success === false) { nsToast(res.message || 'Update failed', 'err'); return; }
    await loadEmployees();
    await loadAttendance();
    nsRender(); nsRenderEmpTable();
    nsCloseModal('ns-modal-edit');
    nsToast(`${name} updated`);
}

// ── DELETE EMPLOYEE ──
async function nsDeleteEmployee(eid) {
    const emp = nsEmployees.find(e => e.emp_id === eid);
    if (!emp) return;
    if (!confirm(`Delete ${emp.name} (${eid})? All attendance records will be removed.`)) return;
    await apiFetch(`/api/ns/employees/${eid}`, { method: 'DELETE' });
    await loadEmployees();
    await loadAttendance();
    nsRender(); nsRenderEmpTable();
    nsToast(`${emp.name} removed`, 'err');
}

// ── EXCEL IMPORT ──
let nsImportedRows = [];
let nsImportColumns = [];

function nsOpenImportModal() {
    nsImportedRows = []; nsImportColumns = [];
    document.getElementById('ns-xl-file').value = '';
    document.getElementById('ns-import-preview').style.display = 'none';
    document.getElementById('ns-import-count').style.display = 'none';
    document.getElementById('ns-import-err').style.display = 'none';
    document.getElementById('ns-col-map-wrap').style.display = 'none';
    document.getElementById('ns-do-import-btn').disabled = true;
    nsOpenModal('ns-modal-import');
}

const nsDz = document.getElementById('ns-drop-zone');
if (nsDz) {
    nsDz.addEventListener('dragover', e => { e.preventDefault(); nsDz.classList.add('over'); });
    nsDz.addEventListener('dragleave', () => nsDz.classList.remove('over'));
    nsDz.addEventListener('drop', e => {
        e.preventDefault(); nsDz.classList.remove('over');
        if (e.dataTransfer.files[0]) nsProcessImportFile(e.dataTransfer.files[0]);
    });
}

function nsHandleFileSelect(inp) {
    if (inp.files[0]) nsProcessImportFile(inp.files[0]);
}

function nsProcessImportFile(file) {
    const errEl = document.getElementById('ns-import-err');
    errEl.style.display = 'none';
    if (!window.XLSX) { errEl.textContent = 'SheetJS not loaded.'; errEl.style.display = 'block'; return; }
    const reader = new FileReader();
    reader.onload = e => {
        try {
            const wb = XLSX.read(e.target.result, { type: 'array' });
            const ws = wb.Sheets[wb.SheetNames[0]];
            const json = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' });
            if (!json || json.length < 2) { errEl.textContent = 'File empty or headers only.'; errEl.style.display = 'block'; return; }
            const headers = json[0].map(String);
            nsImportColumns = headers;
            nsImportedRows = json.slice(1).filter(r => r.some(c => String(c).trim()));
            nsPopulateColMap(headers);
            nsShowImportPreview(headers, nsImportedRows.slice(0, 5));
            document.getElementById('ns-import-count').textContent = `${nsImportedRows.length} row(s) found.`;
            document.getElementById('ns-import-count').style.display = 'block';
            document.getElementById('ns-do-import-btn').disabled = false;
        } catch (err) { errEl.textContent = 'Read error: ' + err.message; errEl.style.display = 'block'; }
    };
    reader.readAsArrayBuffer(file);
}

function nsPopulateColMap(headers) {
    document.getElementById('ns-col-map-wrap').style.display = 'grid';
    ['ns-map-id', 'ns-map-name', 'ns-map-dept', 'ns-map-status'].forEach((selId, i) => {
        const sel = document.getElementById(selId);
        const prev = sel.innerHTML.startsWith('<option value="">') ? '<option value="">— none —</option>' : '';
        sel.innerHTML = (i < 2 ? '' : prev) + headers.map((h, hi) => `<option value="${hi}">${h}</option>`).join('');
    });
    const autoMap = {
        'ns-map-id': ['id', 'emp id', 'employee id', 'empid', 'staff id', 'code'],
        'ns-map-name': ['name', 'employee name', 'full name', 'emp name'],
        'ns-map-dept': ['dept', 'department', 'division'],
        'ns-map-status': ['status', 'employment status', 'active']
    };
    Object.entries(autoMap).forEach(([selId, keywords]) => {
        const sel = document.getElementById(selId);
        const match = headers.findIndex(h => keywords.some(k => h.toLowerCase().includes(k)));
        if (match >= 0) sel.value = String(match);
    });
}

function nsShowImportPreview(headers, rows) {
    const tbl = document.getElementById('ns-preview-tbl');
    tbl.innerHTML = `<tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr>`
        + rows.map(r => `<tr>${headers.map((_, i) => `<td>${r[i] || ''}</td>`).join('')}</tr>`).join('');
    document.getElementById('ns-import-preview').style.display = 'block';
}

async function nsDoImport() {
    const idCol = +document.getElementById('ns-map-id').value;
    const nameCol = +document.getElementById('ns-map-name').value;
    const deptColVal = document.getElementById('ns-map-dept').value;
    const statusColVal = document.getElementById('ns-map-status').value;
    const deptCol = deptColVal !== '' ? +deptColVal : null;
    const statusCol = statusColVal !== '' ? +statusColVal : null;

    const employees = [];
    nsImportedRows.forEach(row => {
        const id = String(row[idCol] || '').trim();
        const name = String(row[nameCol] || '').trim();
        if (!id || !name) return;
        let status = 'active';
        if (statusCol !== null) {
            const sv = String(row[statusCol] || '').toLowerCase();
            if (sv.includes('resign') || sv === 'inactive' || sv === '0' || sv === 'no') status = 'resigned';
        }
        employees.push({
            emp_id: id, name,
            dept: deptCol !== null ? String(row[deptCol] || '').trim() : '',
            status
        });
    });

    const res = await apiFetch('/api/ns/employees/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ employees })
    });

    await loadEmployees();
    nsRender(); nsRenderEmpTable();
    nsCloseModal('ns-modal-import');
    nsToast(`${res.added} imported${res.skipped ? `, ${res.skipped} skipped` : ''}`);
}

// ── EXPORT CSV ──
document.getElementById('ns-exp-btn').onclick = () => {
    const y = nsCurYear, m = nsCurMonth, days = dim(y, m);
    const hdr = Array.from({ length: days }, (_, i) => `${i + 1}-${getWD(y, m, i + 1)}`).join(',');
    let csv = `Emp ID,Employee Name,Department,Status,${hdr},Total\n`;
    nsEmployees.forEach(emp => {
        let total = 0;
        const presentDays = nsAttendance[emp.emp_id] || [];
        const row = Array.from({ length: days }, (_, i) => {
            if ((emp.status || 'active') === 'resigned') return '';
            const v = presentDays.includes(i + 1) ? 1 : 0; total += v; return v;
        }).join(',');
        csv += `${emp.emp_id},"${emp.name}","${emp.dept || ''}",${emp.status || 'active'},${row},${total}\n`;
    });
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `night_shift_${MN[m]}_${y}.csv`;
    a.click();
    nsToast('CSV exported');
};

// ── NAV ──
document.getElementById('ns-prev-m').onclick = async () => {
    nsCurMonth--;
    if (nsCurMonth < 0) { nsCurMonth = 11; nsCurYear--; }
    await loadAttendance();
    nsRender();
};
document.getElementById('ns-next-m').onclick = async () => {
    nsCurMonth++;
    if (nsCurMonth > 11) { nsCurMonth = 0; nsCurYear++; }
    await loadAttendance();
    nsRender();
};

// ── DASHBOARD ──
function getEmpMonthTotal(emp, m) {
    if ((emp.status || 'active') === 'resigned') return 0;
    const yd = nsYearData[emp.emp_id];
    return yd ? (yd[m + 1] || 0) : 0;
}

function getEmpYearTotal(emp) {
    let c = 0; for (let m = 0; m < 12; m++) c += getEmpMonthTotal(emp, m); return c;
}

function getMonthTotal(m) {
    let c = 0;
    nsEmployees.filter(e => (e.status || 'active') === 'active').forEach(emp => c += getEmpMonthTotal(emp, m));
    return c;
}

async function nsRenderDashboard() {
    await loadYearData();
    const y = nsCurYear;
    document.getElementById('ns-dash-year-title').textContent = `Year Summary — ${y}`;
    const active = nsEmployees.filter(e => (e.status || 'active') === 'active');
    let yearTotal = 0, maxPct = 0, maxName = '-';
    const totalPossibleSingle = Array.from({ length: 12 }, (_, m) => dim(y, m)).reduce((a, b) => a + b, 0);
    active.forEach(emp => {
        const t = getEmpYearTotal(emp); yearTotal += t;
        const pct = totalPossibleSingle > 0 ? Math.round(t / totalPossibleSingle * 100) : 0;
        if (pct > maxPct) { maxPct = pct; maxName = emp.name; }
    });
    const totalPossible = active.length * totalPossibleSingle;
    const yearPct = totalPossible > 0 ? Math.round(yearTotal / totalPossible * 100) : 0;
    document.getElementById('ns-dash-stats').innerHTML = `
        <div class="ns-stat"><div class="lbl">Year</div><div class="val blue">${y}</div></div>
        <div class="ns-stat"><div class="lbl">Active Employees</div><div class="val">${active.length}</div></div>
        <div class="ns-stat"><div class="lbl">Total Present Days</div><div class="val green">${yearTotal}</div></div>
        <div class="ns-stat"><div class="lbl">Avg Attendance %</div><div class="val${yearPct >= 75 ? ' green' : ''}">${yearPct}%</div></div>
        <div class="ns-stat"><div class="lbl">Best Attendance</div><div class="val sm">${maxName}</div></div>`;

    const maxVal = Math.max(...Array.from({ length: 12 }, (_, m) => getMonthTotal(m)), 1);
    let bars = '';
    for (let m = 0; m < 12; m++) {
        const v = getMonthTotal(m);
        const h = Math.max(4, Math.round((v / maxVal) * 90));
        const isCur = m === nsCurMonth;
        bars += `<div class="bar-wrap-ns"><div class="bar-val-ns">${v || ''}</div>
            <div class="bar-col-ns"><div class="bar-ns${isCur ? ' cur' : ''}" style="height:${h}px" title="${MN[m]}: ${v}"></div></div>
            <div class="bar-lbl-ns">${MNS[m]}</div></div>`;
    }
    document.getElementById('ns-bar-chart').innerHTML = bars;

    let hm = `<div></div>`;
    MNS.forEach(mn => hm += `<div class="hm-month-label">${mn}</div>`);
    nsEmployees.forEach(emp => {
        const isResigned = (emp.status || 'active') === 'resigned';
        hm += `<div class="hm-row-label" style="${isResigned ? 'opacity:.4' : ''}">${emp.name.split(' ')[0]}</div>`;
        for (let m = 0; m < 12; m++) {
            const total = getEmpMonthTotal(emp, m);
            const days = dim(y, m); const pct = days > 0 ? total / days : 0;
            const lvl = isResigned ? 0 : pct === 0 ? 0 : pct < 0.25 ? 1 : pct < 0.5 ? 2 : pct < 0.75 ? 3 : 4;
            hm += `<div class="hm-cell hm-${lvl}" style="${isResigned ? 'opacity:.3' : ''}" title="${emp.name} — ${MN[m]}: ${isResigned ? 'Resigned' : `${total}/${days} (${Math.round(pct * 100)}%)`}">${isResigned ? '' : total || ''}</div>`;
        }
    });
    document.getElementById('ns-heatmap').innerHTML = hm;
    nsRenderEmpCards();
}

function nsRenderEmpCards() {
    const y = nsCurYear;
    const totalDays = Array.from({ length: 12 }, (_, m) => dim(y, m)).reduce((a, b) => a + b, 0);
    let html = '';
    nsEmployees.forEach((emp, ei) => {
        const isResigned = (emp.status || 'active') === 'resigned';
        const t = getEmpYearTotal(emp);
        const pct = totalDays > 0 ? Math.round(t / totalDays * 100) : 0;
        const color = COLORS[ei % COLORS.length];
        const sel = nsSelectedEmp === emp.emp_id;
        html += `<div class="emp-card${sel ? ' selected' : ''}${isResigned ? ' resigned-card' : ''}" onclick="nsSelectEmp('${emp.emp_id}')">
            ${isResigned ? '<div class="resigned-tag">Resigned</div>' : ''}
            <div class="ec-top">
                <div class="ec-avatar" style="background:${color}22;color:${color}${isResigned ? ';opacity:.5' : ''}">${initials(emp.name)}</div>
                <div><div class="ec-name">${emp.name}</div><div class="ec-id">${emp.emp_id}${emp.dept ? ` · ${emp.dept}` : ''}</div></div>
            </div>
            <div class="ec-bar-wrap"><div class="ec-bar" style="width:${pct}%;background:${isResigned ? 'var(--muted)' : color}"></div></div>
            <div class="ec-stats"><span>${isResigned ? '—' : t + ' / ' + totalDays}</span><span class="ec-pct" style="color:${isResigned ? 'var(--muted)' : color}">${isResigned ? '—' : pct + '%'}</span></div>
        </div>`;
    });
    document.getElementById('ns-emp-grid').innerHTML = html || '<div style="color:var(--muted)">No employees yet.</div>';
    if (nsSelectedEmp) nsRenderDetail(nsSelectedEmp);
    else document.getElementById('ns-detail-panel').innerHTML = '';
}

function nsSelectEmp(id) { nsSelectedEmp = nsSelectedEmp === id ? null : id; nsRenderEmpCards(); }

function nsRenderDetail(id) {
    const emp = nsEmployees.find(e => e.emp_id === id);
    if (!emp) { document.getElementById('ns-detail-panel').innerHTML = ''; return; }
    const isResigned = (emp.status || 'active') === 'resigned';
    const y = nsCurYear;
    const ei = nsEmployees.indexOf(emp);
    const color = COLORS[ei % COLORS.length];
    const totalDays = Array.from({ length: 12 }, (_, m) => dim(y, m)).reduce((a, b) => a + b, 0);
    const yearTotal = getEmpYearTotal(emp);
    const yearPct = totalDays > 0 ? Math.round(yearTotal / totalDays * 100) : 0;
    const curTotal = getEmpMonthTotal(emp, nsCurMonth);
    const curDays = dim(y, nsCurMonth);
    let bestM = { name: '-', pct: 0 };
    if (!isResigned) {
        for (let m = 0; m < 12; m++) { const t = getEmpMonthTotal(emp, m); const p = dim(y, m) > 0 ? Math.round(t / dim(y, m) * 100) : 0; if (p > bestM.pct) bestM = { name: MNS[m], pct: p }; }
    }
    const maxV = Math.max(...Array.from({ length: 12 }, (_, m) => getEmpMonthTotal(emp, m)), 1);
    let mbars = '<div style="display:flex;align-items:flex-end;gap:5px;height:70px;margin-top:10px">';
    for (let m = 0; m < 12; m++) {
        const v = getEmpMonthTotal(emp, m); const h = Math.max(2, Math.round((v / maxV) * 60)); const isCur = m === nsCurMonth;
        mbars += `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px">
            <div style="width:100%;height:${h}px;border-radius:3px 3px 0 0;background:${isCur ? color : color + '66'}"></div>
            <div style="font-size:9px;color:var(--muted);font-family:var(--mono)">${MNS[m]}</div></div>`;
    }
    mbars += '</div>';
    document.getElementById('ns-detail-panel').innerHTML = `
        <div class="detail-panel-ns">
            <div class="dp-head">
                <div class="dp-avatar" style="background:${color}22;color:${color}">${initials(emp.name)}</div>
                <div><div class="dp-name">${emp.name}${isResigned ? ' <span class="badge badge-danger">Resigned</span>' : ''}</div>
                <div class="dp-meta">${emp.emp_id}${emp.dept ? ' · ' + emp.dept : ''} · ${y}</div></div>
            </div>
            <div class="dp-stats-ns">
                <div class="dp-stat"><div class="l">Year Total</div><div class="v" style="color:${isResigned ? 'var(--muted)' : color}">${isResigned ? '—' : yearTotal + ' days'}</div></div>
                <div class="dp-stat"><div class="l">Year %</div><div class="v" style="color:${isResigned ? 'var(--muted)' : color}">${isResigned ? '—' : yearPct + '%'}</div></div>
                <div class="dp-stat"><div class="l">${MNS[nsCurMonth]} Present</div><div class="v">${isResigned ? '—' : curTotal + '/' + curDays}</div></div>
                <div class="dp-stat"><div class="l">Best Month</div><div class="v" style="font-size:13px">${isResigned ? '—' : bestM.name + ' (' + bestM.pct + '%)'}</div></div>
            </div>
            ${isResigned ? '' : mbars}
        </div>`;
}

// ── RENDER ALL ──
function nsRender() {
    nsRenderYrSel();
    nsRenderHeader();
    nsRenderBody();
    nsRenderStats();
}

// ── INIT ──
(async function init() {
    await loadEmployees();
    await loadAttendance();
    nsRender();
})();