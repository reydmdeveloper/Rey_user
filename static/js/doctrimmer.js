"use strict";

const state = {
  files: [],      // {name, size, pages}
  jobId: null,
  pollTimer: null,
  lastLogCount: 0,
  blobs: {},      // name -> {blob, size} cached as soon as a job completes
};

const $ = (id) => document.getElementById(id);

const fileInput = $("fileInput");
const dropzone = $("dropzone");
const fileList = $("fileList");
const logConsole = $("logConsole");
const previewBox = $("previewBox");
const previewModal = $("previewModal");
const modalImg = $("modalImg");
const modalBox = $("modalBox");
const modalTitle = $("modalTitle");

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function appendLog(level, message) {
  const line = document.createElement("div");
  line.className = "log-line " + level;
  const ts = new Date().toLocaleTimeString();
  line.textContent = `[${ts}] [${level.toUpperCase()}] ${message}`;
  logConsole.appendChild(line);
  logConsole.scrollTop = logConsole.scrollHeight;
  while (logConsole.childNodes.length > 400) logConsole.removeChild(logConsole.firstChild);
}

function setProgress(completed, total) {
  const pct = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  $("progressFill").style.width = pct + "%";
  $("progressLabel").textContent = `${completed} / ${total}`;
}

function setStatus(text, kind) {
  const el = $("statusText");
  el.textContent = text;
  el.className = "status " + (kind || "info");
}

/* ---------------- File upload / list ---------------- */

async function uploadFiles(fileInputList) {
  const formData = new FormData();
  for (const f of fileInputList) formData.append("files", f);
  try {
    const data = await api("/api/upload", { method: "POST", body: formData });
    for (const f of data.files) {
      if (!state.files.some((x) => x.name === f.name)) {
        state.files.push({ name: f.name, size: f.size, pages: null });
      }
    }
    // Auto-select overwrite and auto-clear storage options on document upload
    if ($("overwriteCheck")) $("overwriteCheck").checked = true;
    if ($("clearServerStorageCheck")) $("clearServerStorageCheck").checked = true;

    renderFiles();
    schedulePreview();
  } catch (e) {
    appendLog("error", "Upload failed: " + e.message);
  }
}

function renderFiles() {
  fileList.innerHTML = "";
  if (state.files.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No documents uploaded.";
    fileList.appendChild(li);
    return;
  }
  state.files.forEach((f, i) => {
    const li = document.createElement("li");
    li.className = "file-row";

    const info = document.createElement("div");
    info.className = "file-info";
    const nameEl = document.createElement("span");
    nameEl.className = "file-name";
    nameEl.title = f.name;
    nameEl.textContent = f.name;
    const meta = document.createElement("span");
    meta.className = "muted";
    const sizeText = fmtSize(f.size);
    meta.textContent = " · " + sizeText + (f.pages != null ? " · " + f.pages + " pages" : "");
    info.append(nameEl, meta);

    const actions = document.createElement("div");
    const previewBtn = document.createElement("button");
    previewBtn.className = "chip";
    previewBtn.textContent = "Preview";
    previewBtn.onclick = () => showPreview(f);
    const inspectBtn = document.createElement("button");
    inspectBtn.className = "chip";
    inspectBtn.textContent = "Inspect";
    inspectBtn.onclick = () => inspectFile(f, inspectBtn);
    const removeBtn = document.createElement("button");
    removeBtn.className = "chip danger";
    removeBtn.textContent = "Remove";
    removeBtn.onclick = () => {
      if (state.preview && state.preview.name === f.name) hidePreview();
      state.files.splice(i, 1);
      renderFiles();
      schedulePreview();
    };
    actions.append(previewBtn, inspectBtn, removeBtn);

    li.append(info, actions);
    fileList.appendChild(li);
  });
}

async function inspectFile(f, btn) {
  btn.disabled = true;
  btn.textContent = "…";
  try {
    const info = await api("/api/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: f.name }),
    });
    f.pages = info.page_count;
    appendLog("success", `Inspected ${f.name}: ${info.page_count} page(s), ${info.section_count} section(s)`);
  } catch (e) {
    appendLog("error", `Inspect failed for ${f.name}: ${e.message}`);
  }
  btn.disabled = false;
  btn.textContent = "Inspect";
  renderFiles();
}

/* ---------------- Document preview ---------------- */

function showPreview(f) {
  state.preview = { name: f.name, url: "/api/preview/" + encodeURIComponent(f.name), page: 1, total: null };
  previewBox.classList.remove("muted");
  previewBox.textContent = "Loading preview...";
  loadPreview();
}

function showOutputPreview(jobId, name) {
  state.preview = { name: name, url: "/api/output-preview/" + jobId + "/" + encodeURIComponent(name), page: 1, total: null };
  previewBox.classList.remove("muted");
  previewBox.textContent = "Loading preview...";
  loadPreview();
}

function hidePreview() {
  state.preview = null;
  previewBox.classList.add("muted");
  previewBox.innerHTML = "Select a document and click Preview to see it here.";
  updatePreviewNav();
}

async function loadPreview() {
  if (!state.preview) return;
  previewBox.textContent = "Loading preview... (first Word preview may take a few seconds)";
  try {
    const result = await fetchPreviewImage(state.preview.url, 1000);
    if (!result) return;
    state.preview.total = result.total > 0 ? result.total : null;
    previewBox.classList.remove("muted");
    previewBox.innerHTML = "";
    const img = document.createElement("img");
    img.src = result.urlObj;
    img.alt = "Preview of " + state.preview.name;
    img.className = "preview-img";
    img.title = "Click for fullscreen";
    img.onclick = () => openModal();
    previewBox.appendChild(img);
    updatePreviewNav();
  } catch (e) {
    previewBox.classList.add("muted");
    previewBox.textContent = "Preview error: " + e.message;
  }
}

const previewCache = new Map();
let previewAbort = null;

async function fetchPreviewImage(url, width, allowPoll = true, attempts = 180) {
  const key = url + "@" + width + "@" + state.preview.page;
  if (previewCache.has(key)) return previewCache.get(key);

  if (previewAbort) previewAbort.abort();
  const controller = new AbortController();
  previewAbort = controller;
  try {
    let resp = await fetch(url + "?page=" + state.preview.page + "&w=" + width, { signal: controller.signal });
    if (resp.status === 202 && allowPoll) {
      if (attempts <= 0) throw new Error("Preview generation timed out");
      await new Promise((r) => setTimeout(r, 1000));
      return fetchPreviewImage(url, width, allowPoll, attempts - 1);
    }
    if (!resp.ok) {
      let detail = "HTTP " + resp.status;
      try {
        const data = await resp.json();
        if (data && data.error) detail = data.error;
      } catch (_) {}
      throw new Error(detail);
    }
    const total = parseInt(resp.headers.get("X-Total-Pages") || "0", 10);
    const blob = await resp.blob();
    const urlObj = URL.createObjectURL(blob);
    previewCache.set(key, { urlObj, total });
    if (previewCache.size > 60) previewCache.clear();
    return { urlObj, total };
  } catch (e) {
    if (e.name === "AbortError") return null;
    throw e;
  }
}

function updatePreviewNav() {
  if (!state.preview) {
    $("prevPageBtn").disabled = true;
    $("nextPageBtn").disabled = true;
    $("pageLabel").textContent = "Page - / -";
    return;
  }
  $("prevPageBtn").disabled = state.preview.page <= 1;
  $("nextPageBtn").disabled = state.preview.total ? state.preview.page >= state.preview.total : false;
  $("pageLabel").textContent = "Page " + state.preview.page + (state.preview.total ? " / " + state.preview.total : "");
}

/* ---------------- Fullscreen preview modal ---------------- */

function openModal() {
  if (!state.preview) return;
  modalTitle.textContent = state.preview.name;
  previewModal.classList.remove("hidden");
  updateModalNav();
  loadModalImage();
}

function closeModal() {
  previewModal.classList.add("hidden");
  modalImg.src = "";
}

function updateModalNav() {
  if (!state.preview) {
    $("modalPrevBtn").disabled = true;
    $("modalNextBtn").disabled = true;
    $("modalPageLabel").textContent = "Page - / -";
    return;
  }
  $("modalPrevBtn").disabled = state.preview.page <= 1;
  $("modalNextBtn").disabled = state.preview.total ? state.preview.page >= state.preview.total : false;
  $("modalPageLabel").textContent = "Page " + state.preview.page + (state.preview.total ? " / " + state.preview.total : "");
}

async function loadModalImage() {
  if (!state.preview) return;
  try {
    const result = await fetchPreviewImage(state.preview.url, 1600);
    if (!result) return;
    state.preview.total = result.total > 0 ? result.total : null;
    modalImg.src = result.urlObj;
    updateModalNav();
  } catch (e) { /* ignore */ }
}

function gotoPage(page) {
  if (!state.preview) return;
  page = parseInt(page, 10);
  if (isNaN(page)) return;
  if (page < 1) page = 1;
  if (state.preview.total && page > state.preview.total) page = state.preview.total;
  state.preview.page = page;
  loadPreview();
  if (!previewModal.classList.contains("hidden")) {
    updateModalNav();
    loadModalImage();
  }
  $("gotoPageInput").value = "";
  $("modalGotoPageInput").value = "";
}

/* ---------------- Naming preview ---------------- */

let previewTimer = null;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, 250);
}

async function updatePreview() {
  const pattern = $("namingInput").value.trim();
  const fmt = $("formatSelect").value;
  const sample = state.files.length ? state.files[0].name : "SampleReport.docx";
  try {
    const data = await api("/api/naming-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pattern, format: fmt, sample }),
    });
    $("namingPreview").textContent = data.preview;
  } catch (e) {
    $("namingPreview").textContent = "[Pattern Error]";
  }
}

/* ---------------- Export flow ---------------- */

async function startExport() {
  if (state.files.length === 0) { alert("Add at least one document first."); return; }
  if (!$("rangeInput").value.trim()) { alert("Enter a valid page range."); return; }

  const body = {
    files: state.files.map((f) => f.name),
    range: $("rangeInput").value.trim(),
    format: $("formatSelect").value,
    naming_pattern: $("namingInput").value.trim(),
    overwrite: $("overwriteCheck").checked,
    clear_storage_after_export: $("clearServerStorageCheck") ? $("clearServerStorageCheck").checked : false,
    engine_mode: $("engineSelect").value,
    visible: $("visibleCheck").checked,
    verify: $("quickModeCheck") ? !$("quickModeCheck").checked : false,
  };

  $("startBtn").disabled = true;
  $("cancelBtn").disabled = false;
  setProgress(0, 0);
  logConsole.innerHTML = "";
  appendLog("info", "Starting export job...");
  state.lastLogCount = 1;

  try {
    const data = await api("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.jobId = data.job_id;
    startPolling();
  } catch (e) {
    setStatus("Error starting job: " + e.message, "error");
    appendLog("error", "Error starting job: " + e.message);
    $("startBtn").disabled = false;
    $("cancelBtn").disabled = true;
  }
}

let pollRetries = 0;

function startPolling() {
  stopPolling();
  pollRetries = 0;
  state.pollTimer = setInterval(pollJob, 800);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollJob() {
  if (!state.jobId) return;
  let job;
  try {
    job = await api("/api/job/" + state.jobId);
    pollRetries = 0;
  } catch (e) {
    pollRetries++;
    if (pollRetries <= 4) {
      return;
    }
    stopPolling();
    setStatus("Job polling error: " + e.message, "error");
    $("startBtn").disabled = false;
    $("cancelBtn").disabled = true;
    return;
  }
  applyJob(job);

  if (["done", "error", "cancelled"].includes(job.status)) {
    stopPolling();
    $("startBtn").disabled = false;
    $("cancelBtn").disabled = true;
  }
}

function applyJob(job) {
  setProgress(job.completed, job.total);
  const pct = job.total > 0 ? Math.round((job.completed / job.total) * 100) : 0;
  setStatus(`${job.current_status} (${pct}%)`, "info");

  if (job.logs.length > state.lastLogCount) {
    for (let i = state.lastLogCount; i < job.logs.length; i++) {
      appendLog(job.logs[i].level.toLowerCase(), job.logs[i].message);
    }
    state.lastLogCount = job.logs.length;
  }

  if (["done", "error", "cancelled"].includes(job.status)) {
    renderResults(job);
    job.errors.forEach((e) => appendLog("error", e));
    if (job.status === "done") {
      setStatus(`Finished: ${job.success_count} succeeded, ${job.fail_count} failed.`, "success");
    } else if (job.status === "cancelled") {
      setStatus("Job cancelled.", "warn");
    } else {
      setStatus("Job failed to complete.", "error");
    }
    state.lastLogCount = 0;
  }
}

function renderResults(job) {
  const box = $("resultsBox");
  box.innerHTML = "";
  box.classList.remove("muted");
  if (job.outputs.length === 0) {
    box.textContent = "No output files were produced.";
    box.classList.add("muted");
    return;
  }

  const jid = job.job_id || state.jobId;
  const multi = job.outputs.length > 1;
  const singleName = job.outputs[0];

  const actionRow = document.createElement("div");
  actionRow.style.cssText = "display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;";

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn save-all";
  saveBtn.style.cssText = "background: linear-gradient(135deg, #059669, #10b981); color: white; flex: 1; font-weight: 600;";
  saveBtn.innerHTML = multi
    ? "💾 Save All Files (ZIP) via Save As…"
    : "💾 Save File via Save As…";
  saveBtn.title = multi
    ? "Opens the Save As dialog to choose where the ZIP archive is saved"
    : "Opens the Save As dialog to choose where this file is saved";
  saveBtn.onclick = () => saveResults(jid, multi, singleName);
  actionRow.appendChild(saveBtn);
  box.appendChild(actionRow);

  const list = document.createElement("ul");
  list.style.cssText = "list-style: none; padding: 0; margin: 0;";
  job.outputs.forEach((name) => {
    const row = document.createElement("li");
    row.className = "result-row";
    row.style.cssText = "display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.08);";
    const label = document.createElement("span");
    label.className = "file-name";
    label.style.cssText = "flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;";
    label.title = name;
    label.textContent = name;
    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "btn prev-out";
    prevBtn.textContent = "Preview";
    prevBtn.onclick = () => showOutputPreview(job.job_id || state.jobId, name);
    row.append(label, prevBtn);
    list.appendChild(row);
  });
  box.appendChild(list);

  const autoZipCheck = $("autoZipCheck");
  if (autoZipCheck && autoZipCheck.checked) {
    setTimeout(() => saveResults(jid, multi, singleName), 500);
  }
}

function isDesktopApp() {
  return false;
}

function fileExtFilter(name) {
  const ext = "." + (name.split(".").pop() || "docx").toLowerCase();
  const map = {
    ".docx": "Word Document (*.docx)",
    ".docm": "Macro-Enabled Word Document (*.docm)",
    ".doc": "Word 97-2003 Document (*.doc)",
    ".rtf": "RTF Document (*.rtf)",
    ".pdf": "PDF Document (*.pdf)",
    ".zip": "ZIP Archive (*.zip)",
  };
  const desc = map[ext] || "Document (" + ext + ")";
  return desc + "|*" + ext + "|All Files (*.*)|*.*";
}

async function pickAndSave(suggestedName, blob, description, mime, exts) {
  if (window.showSaveFilePicker) {
    try {
      const accept = exts
        ? { [mime]: exts }
        : { "application/octet-stream": ["." + (suggestedName.split(".").pop() || "bin")] };
      const handle = await window.showSaveFilePicker({
        suggestedName,
        excludeAcceptAllOption: false,
        types: [{ description, accept }]
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      appendLog("info", "Saved file to: " + handle.name);
      return;
    } catch (err) {
      if (err.name === "AbortError") return;
    }
  }
  const urlObj = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = urlObj;
  a.download = suggestedName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(urlObj), 10000);
}

async function saveResults(jobId, asZip, singleName) {
  if (asZip) {
    // Multiple files -> Save As a single ZIP archive.
    try {
      appendLog("info", "Packaging output files into ZIP archive...");
      const resp = await fetch("/api/download-zip/" + jobId);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const blob = await resp.blob();
      const cd = resp.headers.get("Content-Disposition") || "";
      let zipName = "Exported_Document_Pages.zip";
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
      if (m && m[1]) {
        try { zipName = decodeURIComponent(m[1]); } catch (_) { zipName = m[1]; }
      }
      await pickAndSave(zipName, blob, "ZIP Archive", "application/zip", [".zip"]);
    } catch (e) {
      appendLog("error", "ZIP packaging failed: " + e.message);
      alert("ZIP packaging failed: " + e.message);
    }
    return;
  }

  // Single file -> Save As that exact file (no ZIP wrapper).
  try {
    const resp = await fetch("/api/download/" + jobId + "/" + encodeURIComponent(singleName));
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const blob = await resp.blob();
    await pickAndSave(singleName, blob, "Exported Document", "application/octet-stream", null);
  } catch (e) {
    appendLog("error", "Save failed: " + e.message);
    alert("Save failed: " + e.message);
  }
}

/* ---------------- Session reset ---------------- */

async function resetSession() {
  stopPolling();
  state.files = [];
  state.jobId = null;
  state.lastLogCount = 0;
  state.preview = null;
  state.blobs = {};

  $("presetSelect").value = "all";
  $("rangeInput").value = "1-end";
  updateRangeAnalysis();
  $("namingInput").value = "{original_name}_pages_{start_page}-{end_page}";
  $("formatSelect").value = "docx";
  $("engineSelect").value = "trimming";
  if ($("overwriteCheck")) $("overwriteCheck").checked = true;
  if ($("clearServerStorageCheck")) $("clearServerStorageCheck").checked = true;
  $("visibleCheck").checked = false;

  renderFiles();
  hidePreview();
  logConsole.innerHTML = "";
  $("resultsBox").innerHTML = "No exports completed yet.";
  $("resultsBox").classList.add("muted");
  setProgress(0, 0);
  setStatus("Ready to export.", "info");
  $("startBtn").disabled = false;
  $("cancelBtn").disabled = true;
  schedulePreview();

  try {
    const res = await api("/api/clear-storage", { method: "POST" });
    appendLog("info", `Session cleared: ${res.file_count} file(s) (${res.reclaimed_mb} MB) purged from server storage.`);
  } catch (e) {
    appendLog("info", "Session cleared.");
  }
}

/* ---------------- Range Analysis & Dynamic Sync ---------------- */

function updateRangeAnalysis() {
  const spec = $("rangeInput").value.trim().toLowerCase();
  const textEl = $("rangeAnalysisText");
  if (!textEl) return;

  if (spec === "1-end" || spec === "all" || spec === "all pages") {
    textEl.textContent = "Full Document Cut (Pages 1 to End) into 1 single output file.";
  } else if (spec === "all-individual") {
    textEl.textContent = "Individual Page Cut: Every page (Page 1, Page 2, Page 3...) extracted into separate individual files.";
  } else if (spec === "even") {
    textEl.textContent = "Even Page Cut: Pages 2, 4, 6, 8... extracted into separate individual files.";
  } else if (spec === "odd") {
    textEl.textContent = "Odd Page Cut: Pages 1, 3, 5, 7... extracted into separate individual files.";
  } else if (/^\d+\s*-\s*(\d+|\bend\b)$/.test(spec)) {
    const parts = spec.split("-");
    const s = parts[0].trim();
    const e = parts[1].trim();
    if (s === e) {
      textEl.textContent = `Exact Single Page Cut: Page ${s} extracted into 1 standalone document.`;
    } else {
      textEl.textContent = `Exact Range Cut: Pages ${s} to ${e} extracted into 1 document.`;
    }
  } else if (/^\d+$/.test(spec)) {
    textEl.textContent = `Exact Single Page Cut: Page ${spec} extracted into 1 standalone document.`;
  } else if (spec.includes(",")) {
    textEl.textContent = `Custom Multi-Range Cut: Specified ranges (${spec}) extracted into separate files/documents.`;
  } else if (spec) {
    textEl.textContent = `Custom Range Cut: '${spec}' analyzed and processed.`;
  } else {
    textEl.textContent = "Enter a valid page range specification.";
  }
}

/* ---------------- Events ---------------- */

$("pickFilesBtn").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => {
  if (e.target.files.length) uploadFiles(e.target.files);
  fileInput.value = "";
});

["dragover", "dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => e.preventDefault()));
dropzone.addEventListener("dragover", () => dropzone.classList.add("drag-over"));
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
dropzone.addEventListener("drop", (e) => {
  dropzone.classList.remove("drag-over");
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});

$("presetSelect").addEventListener("change", () => {
  const v = $("presetSelect").value;
  if (v === "all" || v === "All Pages (Single Doc)") {
    $("rangeInput").value = "1-end";
    $("namingInput").value = "{original_name}_pages_{start_page}-{end_page}";
  } else if (v === "individual" || v === "Every Page as Separate Document") {
    $("rangeInput").value = "all-individual";
    $("namingInput").value = "{original_name}_page_{start_page}";
  } else if (v === "even" || v === "Even Pages Only") {
    $("rangeInput").value = "even";
    $("namingInput").value = "{original_name}_page_{start_page}";
  } else if (v === "odd" || v === "Odd Pages Only") {
    $("rangeInput").value = "odd";
    $("namingInput").value = "{original_name}_page_{start_page}";
  } else if (v === "custom" || v === "Custom Page Range") {
    $("rangeInput").focus();
  }
  updateRangeAnalysis();
  schedulePreview();
});

$("rangeInput").addEventListener("input", () => {
  const val = $("rangeInput").value.trim().toLowerCase();
  if (val === "1-end" || val === "all") {
    $("presetSelect").value = "all";
  } else if (val === "all-individual") {
    $("presetSelect").value = "individual";
  } else if (val === "even") {
    $("presetSelect").value = "even";
  } else if (val === "odd") {
    $("presetSelect").value = "odd";
  } else {
    $("presetSelect").value = "custom";
  }
  updateRangeAnalysis();
  schedulePreview();
});

$("namingInput").addEventListener("input", schedulePreview);
$("formatSelect").addEventListener("change", schedulePreview);

document.querySelectorAll(".chip[data-token]").forEach((btn) => {
  btn.addEventListener("click", () => {
    $("namingInput").value += btn.dataset.token;
    schedulePreview();
  });
});

$("startBtn").addEventListener("click", startExport);
$("cancelBtn").addEventListener("click", async () => {
  if (!state.jobId) return;
  try {
    await api("/api/job/" + state.jobId + "/cancel", { method: "POST" });
  } catch (e) { /* ignore */ }
});
$("clearLogsBtn").addEventListener("click", () => { logConsole.innerHTML = ""; });
$("resetBtn").addEventListener("click", resetSession);
$("closePreviewBtn").addEventListener("click", hidePreview);
$("prevPageBtn").addEventListener("click", () => {
  if (state.preview && state.preview.page > 1) {
    state.preview.page -= 1;
    loadPreview();
    if (!previewModal.classList.contains("hidden")) loadModalImage();
  }
});
$("nextPageBtn").addEventListener("click", () => {
  if (state.preview && (!state.preview.total || state.preview.page < state.preview.total)) {
    state.preview.page += 1;
    loadPreview();
    if (!previewModal.classList.contains("hidden")) loadModalImage();
  }
});
$("modalPrevBtn").addEventListener("click", () => {
  if (state.preview && state.preview.page > 1) {
    state.preview.page -= 1;
    updateModalNav();
    loadModalImage();
  }
});
$("modalNextBtn").addEventListener("click", () => {
  if (state.preview && (!state.preview.total || state.preview.page < state.preview.total)) {
    state.preview.page += 1;
    updateModalNav();
    loadModalImage();
  }
});
$("modalCloseBtn").addEventListener("click", closeModal);
previewModal.addEventListener("click", (e) => {
  if (e.target === modalBox || e.target === previewModal) closeModal();
});
$("gotoPageBtn").addEventListener("click", () => gotoPage($("gotoPageInput").value));
$("gotoPageInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") gotoPage(e.target.value);
});
$("modalGotoPageBtn").addEventListener("click", () => gotoPage($("modalGotoPageInput").value));
$("modalGotoPageInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") gotoPage(e.target.value);
});
document.addEventListener("keydown", (e) => {
  if (previewModal.classList.contains("hidden")) return;
  if (e.key === "Escape") closeModal();
  else if (e.key === "ArrowLeft") $("modalPrevBtn").click();
  else if (e.key === "ArrowRight") $("modalNextBtn").click();
});

setProgress(0, 0);
updatePreviewNav();
schedulePreview();
