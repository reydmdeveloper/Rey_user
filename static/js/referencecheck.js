(() => {
  "use strict";

  const CONCURRENCY = 3;

  const drop     = document.getElementById("rc-drop");
  const fileIn   = document.getElementById("rc-file");
  const queueBox = document.getElementById("rc-queue");
  const queueLst = document.getElementById("rc-queue-list");
  const queueSum = document.getElementById("rc-queue-summary");
  const scanBtn  = document.getElementById("rc-scan-btn");
  const clearBtn = document.getElementById("rc-clear-btn");
  const progBox  = document.getElementById("rc-progress");
  const progFill = document.getElementById("rc-progress-fill");
  const progLbl  = document.getElementById("rc-progress-label");
  const resPanel = document.getElementById("rc-results-panel");
  const resBody  = document.getElementById("rc-results-body");
  const resStats = document.getElementById("rc-results-stats");
  const searchIn = document.getElementById("rc-search");
  const exportBtn = document.getElementById("rc-export-btn");
  const emptyNote = document.getElementById("rc-empty");
  const headers   = document.querySelectorAll("#rc-table thead th");

  let queue = [];
  let results = [];
  let sortKey = null;
  let sortDir = 1;
  let scanning = false;

  function addFiles(fileList) {
    const incoming = Array.from(fileList).filter(f =>
      f.name.toLowerCase().endsWith(".pdf") || f.type === "application/pdf");
    const skipped = fileList.length - incoming.length;
    for (const f of incoming) {
      if (!queue.some(q => q.name === f.name && q.size === f.size)) queue.push(f);
    }
    renderQueue();
    if (skipped > 0) {
      queueSum.textContent += ` — ${skipped} non-PDF ignored`;
    }
  }

  function renderQueue() {
    queueBox.hidden = queue.length === 0;
    queueLst.innerHTML = "";
    for (const f of queue) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "rc-queue-file";
      name.textContent = f.name;
      const size = document.createElement("span");
      size.className = "rc-queue-size";
      size.textContent = prettySize(f.size);
      li.append(name, size);
      queueLst.appendChild(li);
    }
    queueSum.textContent =
      `${queue.length} file${queue.length === 1 ? "" : "s"} ready ` +
      `(${prettySize(queue.reduce((s, f) => s + f.size, 0))})`;
    scanBtn.disabled = queue.length === 0 || scanning;
  }

  function prettySize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 ** 2).toFixed(1) + " MB";
  }

  drop.addEventListener("click", () => fileIn.click());
  drop.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileIn.click(); }
  });
  fileIn.addEventListener("change", () => { addFiles(fileIn.files); fileIn.value = ""; });

  ["dragenter", "dragover"].forEach(ev =>
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev =>
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => {
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });

  clearBtn.addEventListener("click", () => { queue = []; renderQueue(); });

  scanBtn.addEventListener("click", async () => {
    if (!queue.length || scanning) return;
    scanning = true;
    scanBtn.disabled = true;
    clearBtn.disabled = true;

    const files = queue.slice();
    queue = [];
    renderQueue();
    queueBox.hidden = true;

    progBox.hidden = false;
    let done = 0;
    updateProgress(done, files.length, "");

    let next = 0;
    async function worker() {
      while (next < files.length) {
        const file = files[next++];
        updateProgress(done, files.length, file.name);
        const res = await scanOne(file);
        results.push(res);
        done++;
        updateProgress(done, files.length, file.name);
        renderResults();
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, files.length) }, worker));

    progLbl.textContent = `Done — ${files.length} file${files.length === 1 ? "" : "s"} scanned.`;
    setTimeout(() => { progBox.hidden = true; }, 1500);

    scanning = false;
    clearBtn.disabled = false;
    renderQueue();
  });

  async function scanOne(file) {
    const form = new FormData();
    form.append("files", file, file.name);
    try {
      const resp = await fetch("/referencecheck/scan", { method: "POST", body: form });
      if (!resp.ok) throw new Error(`Server responded ${resp.status}`);
      const data = await resp.json();
      return data.results[0];
    } catch (err) {
      return {
        file_name: file.name, total_pages: 0, occurrences: 0, pages: [],
        status: "Error", message: "Upload or server error: " + err.message,
      };
    }
  }

  function updateProgress(done, total, current) {
    const pct = total ? Math.round((done / total) * 100) : 0;
    progFill.style.width = pct + "%";
    progLbl.textContent = done < total
      ? `Scanning ${done + 1} of ${total} — ${current}`
      : "Finishing up...";
  }

  function decorated(row) {
    return { ...row, pages_str: row.pages && row.pages.length ? row.pages.join(", ") : "-" };
  }

  function renderResults() {
    resPanel.hidden = results.length === 0;
    if (!results.length) return;

    const found = results.filter(r => r.status === "Found").length;
    const clean = results.filter(r => r.status === "Not Found").length;
    const errored = results.filter(r => r.status === "Error").length;
    const totalOcc = results.reduce((s, r) => s + (r.occurrences || 0), 0);
    resStats.innerHTML = "";
    addChip(`${results.length} files`, "");
    addChip(`${found} found · ${totalOcc} occurrence${totalOcc === 1 ? "" : "s"}`, "found");
    addChip(`${clean} clean`, "clean");
    if (errored) addChip(`${errored} error${errored === 1 ? "" : "s"}`, "errored");

    const term = searchIn.value.trim().toLowerCase();
    let rows = results.map(decorated).filter(r =>
      !term ||
      r.file_name.toLowerCase().includes(term) ||
      r.status.toLowerCase().includes(term) ||
      r.pages_str.includes(term));

    if (sortKey) {
      rows.sort((a, b) => {
        const av = a[sortKey], bv = b[sortKey];
        const cmp = typeof av === "number"
          ? av - bv
          : String(av).localeCompare(String(bv), undefined, { numeric: true });
        return cmp * sortDir;
      });
    }

    resBody.innerHTML = "";
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.append(td(r.file_name, "cell-file"), td(String(r.total_pages), "cell-num"),
                td(String(r.occurrences), "cell-num"), td(r.pages_str, "cell-pages"));

      const tdStatus = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "rc-badge " + (
        r.status === "Found" ? "found" :
        r.status === "Not Found" ? "not-found" : "error");
      badge.textContent = r.status;
      tdStatus.appendChild(badge);
      if (r.status === "Error" && r.message) {
        const msg = document.createElement("span");
        msg.className = "rc-error-msg";
        msg.textContent = r.message;
        tdStatus.appendChild(msg);
      }
      tr.appendChild(tdStatus);
      resBody.appendChild(tr);
    }
    emptyNote.hidden = rows.length !== 0;
  }

  function td(text, cls) {
    const el = document.createElement("td");
    el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }

  function addChip(text, cls) {
    const chip = document.createElement("span");
    chip.className = "rc-stat-chip " + cls;
    chip.textContent = text;
    resStats.appendChild(chip);
  }

  headers.forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (sortKey === key) sortDir *= -1;
      else { sortKey = key; sortDir = 1; }
      headers.forEach(h => {
        const arrow = h.querySelector(".rc-sort-arrow");
        arrow.textContent = h.dataset.key === sortKey ? (sortDir === 1 ? "\u25B2" : "\u25BC") : "";
        h.setAttribute("aria-sort",
          h.dataset.key === sortKey ? (sortDir === 1 ? "ascending" : "descending") : "none");
      });
      renderResults();
    });
  });

  searchIn.addEventListener("input", renderResults);

  exportBtn.addEventListener("click", async () => {
    if (!results.length) return;
    exportBtn.disabled = true;
    exportBtn.textContent = "Preparing...";
    try {
      const resp = await fetch("/referencecheck/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ results }),
      });
      if (!resp.ok) throw new Error("Export failed (" + resp.status + ")");
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const disposition = resp.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^";]+)"?/);
      a.href = url;
      a.download = match ? match[1] : "reference_check.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert("Could not export: " + err.message);
    } finally {
      exportBtn.disabled = false;
      exportBtn.textContent = "Export to Excel";
    }
  });
})();
