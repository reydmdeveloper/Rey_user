"""Document Trimmer - Word & PDF Page Exporter tool.

Flask Blueprint that exposes the trimming engine (`engine/`) as a tool inside
the REYDM platform. Adapted from the standalone "Document Trimmer Pro" app:

  * Storage is scoped per-user under the OS temp directory, so one user's
    uploads / jobs / previews never collide with another's.
  * All API routes are protected: the user must be logged in and have the
    ``doctrimmer`` tool assigned (admins always have it).
  * Desktop-only features (native Save-As dialog, server git-pull/update,
    desktop shortcuts) are intentionally not exposed on the web platform.
"""

import os
import re
import shutil
import tempfile
import threading
import time
import uuid

from flask import Blueprint, jsonify, request, send_file, session, Response

# Runtime storage lives under the OS temp dir so the repository stays clean.
RUNTIME_ROOT = os.path.join(tempfile.gettempdir(), "reydm_doc_trimmer")
os.environ.setdefault("RUNTIME_DIR", RUNTIME_ROOT)
os.makedirs(RUNTIME_ROOT, exist_ok=True)

from engine import convert, naming, paginate, pdf_split, ranges, docx_trim, verify  # noqa: E402
from engine.renderer import Renderer  # noqa: E402

bp = Blueprint("doctrimmer", __name__)

TOOL_KEY = "doctrimmer"

_WORD_EXT = {".docx", ".doc", ".docm", ".dotx", ".dotm", ".rtf"}
_PDF_EXT = {".pdf"}

# In-memory export jobs. Each job records the owning user id so downloads,
# polling and cancellation are strictly scoped to that user.
JOBS = {}
JOBS_LOCK = threading.Lock()

_RENDERERS = {}
_RENDERERS_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# auth + per-user storage helpers
# --------------------------------------------------------------------------

@bp.before_request
def _require_auth():
    if "user_id" not in session:
        return jsonify({"error": "Authentication required."}), 401
    if session.get("role") != "admin" and TOOL_KEY not in session.get("allowed_tools", []):
        return jsonify({"error": "You don't have access to this tool."}), 403
    return None


def _user_id():
    return str(session.get("user_id") or 0)


def _user_root(user_id):
    d = os.path.join(RUNTIME_ROOT, user_id)
    for sub in ("uploads", "jobs", "renders", "tmp"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


def _uploads_dir(user_id):
    return os.path.join(_user_root(user_id), "uploads")


def _jobs_dir(user_id):
    return os.path.join(_user_root(user_id), "jobs")


def _renders_dir(user_id):
    return os.path.join(_user_root(user_id), "renders")


def _renderer(user_id):
    with _RENDERERS_LOCK:
        r = _RENDERERS.get(user_id)
        if r is None:
            r = Renderer(_renders_dir(user_id))
            _RENDERERS[user_id] = r
    return r


def _clean_name(name):
    name = os.path.basename((name or "").replace("\\", "/")).split("/")[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return name or "file"


def resolve_upload(user_id, name):
    """Resolve an uploaded file by its (basename) inside the user's uploads dir.

    Only the basename is ever used, so requests can never escape the user's
    own upload folder (no arbitrary server paths on the web edition).
    """
    clean = _clean_name(name)
    path = os.path.join(_uploads_dir(user_id), clean)
    if not os.path.exists(path) or not os.path.isfile(path):
        raise FileNotFoundError(f"File not found on server: {name}")
    return path


# --------------------------------------------------------------------------
# document inspection helpers
# --------------------------------------------------------------------------

def _word_namespace():
    return "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def count_sections(path):
    import zipfile

    from lxml import etree

    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            target = "word/document.xml"
            if target not in names:
                return 1
            root = etree.fromstring(z.read(target))
            ns = {"w": _word_namespace()}
            return len(root.findall(".//w:sectPr", ns)) or 1
    except Exception:
        return 1


def get_document_info(user_id, path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _PDF_EXT:
        return pdf_split.pdf_page_count(path), 1
    docx_path = path
    tmp = None
    if ext != ".docx":
        tmp = os.path.join(_user_root(user_id), "tmp", "inspect")
        os.makedirs(tmp, exist_ok=True)
        try:
            docx_path = convert.normalize_to_docx(path, tmp)
        except Exception:
            docx_path = path
    try:
        page_count, _ = paginate.paginate_docx(docx_path)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return page_count, count_sections(path)


# --------------------------------------------------------------------------
# upload / inspect / naming
# --------------------------------------------------------------------------

@bp.route("/api/upload", methods=["POST"])
def api_upload():
    files = request.files.getlist("files")
    saved = []
    uploads = _uploads_dir(_user_id())
    for f in files:
        if not f.filename:
            continue
        name = _clean_name(f.filename)
        dest = os.path.join(uploads, name)
        f.save(dest)
        saved.append({"name": name, "size": os.path.getsize(dest)})
    return jsonify({"files": saved})


@bp.route("/api/inspect", methods=["POST"])
def api_inspect():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    uid = _user_id()
    try:
        path = resolve_upload(uid, name)
        page_count, section_count = get_document_info(uid, path)
        return jsonify({"name": name, "page_count": page_count, "section_count": section_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/naming-preview", methods=["POST"])
def api_naming_preview():
    data = request.get_json(silent=True) or {}
    pattern = data.get("pattern", "{original_name}_pages_{start_page}-{end_page}")
    fmt = data.get("format", "docx")
    sample = data.get("sample", "SampleReport.docx")
    try:
        ext = "pdf" if os.path.splitext(sample)[1].lower() == ".pdf" else fmt
        preview = naming.apply_template(pattern, sample, 1, 2, ext)
        return jsonify({"preview": preview})
    except Exception as e:
        return jsonify({"preview": "[Pattern Error]", "error": str(e)})


# --------------------------------------------------------------------------
# previews
# --------------------------------------------------------------------------

def _render_preview_response(uid, path, page_param, width_param):
    try:
        page = int(page_param or 1)
        width = int(width_param or 1200)
    except ValueError:
        page, width = 1, 1200
    try:
        png, total = _renderer(uid).render_page(path, page, width)
    except Exception:
        time.sleep(1.0)
        try:
            png, total = _renderer(uid).render_page(path, page, width)
        except Exception as e2:
            return jsonify({"error": f"Preview render failed: {e2}"}), 500
    return Response(png, mimetype="image/png", headers={"X-Total-Pages": str(total), "Cache-Control": "no-store"})


@bp.route("/api/preview/<path:name>")
def api_preview(name):
    uid = _user_id()
    try:
        path = resolve_upload(uid, name)
    except Exception as e:
        return jsonify({"error": str(e)}), 404
    return _render_preview_response(uid, path, request.args.get("page"), request.args.get("w"))


def _owned_job_path(job_id):
    """Return (job, absolute jobs dir) after checking the caller owns the job."""
    uid = _user_id()
    job = JOBS.get(job_id)
    if job is None or job.get("user_id") != uid:
        return None, None
    return job, os.path.join(_jobs_dir(uid), job_id)


@bp.route("/api/output-preview/<job_id>/<path:name>")
def api_output_preview(job_id, name):
    _job, base = _owned_job_path(job_id)
    if _job is None:
        return jsonify({"error": "Job not found"}), 404
    safe = _clean_name(name)
    path = os.path.join(base, "outputs", safe)
    if not os.path.exists(path):
        return jsonify({"error": "Output not found"}), 404
    return _render_preview_response(_user_id(), path, request.args.get("page"), request.args.get("w"))


@bp.route("/api/download/<job_id>/<path:name>")
def api_download(job_id, name):
    _job, base = _owned_job_path(job_id)
    if _job is None:
        return jsonify({"error": "Job not found"}), 404
    safe = _clean_name(name)
    path = os.path.join(base, "outputs", safe)
    if not os.path.exists(path):
        return jsonify({"error": "Output not found"}), 404
    return send_file(path, as_attachment=True, download_name=safe)


@bp.route("/api/download-zip/<job_id>")
def api_download_zip(job_id):
    import zipfile

    _job, base = _owned_job_path(job_id)
    if _job is None:
        return jsonify({"error": "Job not found"}), 404
    outputs_dir = os.path.join(base, "outputs")
    if not os.path.exists(outputs_dir):
        return jsonify({"error": "Job outputs directory not found"}), 404
    files = [f for f in os.listdir(outputs_dir) if os.path.isfile(os.path.join(outputs_dir, f))]
    if not files:
        return jsonify({"error": "No output files to package"}), 404

    zip_name = "Exported_Document_Pages.zip"
    job = JOBS.get(job_id)
    if job and job.get("files"):
        base_name = os.path.splitext(os.path.basename(job["files"][0]))[0]
        zip_name = f"{base_name}_exported_pages.zip"

    zip_path = os.path.join(base, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for f in files:
            zout.write(os.path.join(outputs_dir, f), f)
    return send_file(zip_path, as_attachment=True, download_name=zip_name)


@bp.route("/api/zip-name/<job_id>")
def api_zip_name(job_id):
    job = JOBS.get(job_id)
    if job is None or job.get("user_id") != _user_id():
        return jsonify({"error": "Job not found"}), 404
    zip_name = "Exported_Document_Pages.zip"
    if job.get("files"):
        base_name = os.path.splitext(os.path.basename(job["files"][0]))[0]
        zip_name = f"{base_name}_exported_pages.zip"
    return jsonify({"name": zip_name})


# --------------------------------------------------------------------------
# export jobs
# --------------------------------------------------------------------------

def _new_job(user_id, body):
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "user_id": user_id,
        "status": "queued",
        "current_status": "Queued",
        "completed": 0,
        "total": 0,
        "success_count": 0,
        "fail_count": 0,
        "logs": [{"level": "info", "message": "Job queued."}],
        "errors": [],
        "outputs": [],
        "request": body,
        "cancel": False,
        "lock": threading.Lock(),
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    JOBS[job_id] = job
    return job


def _job_lock(job):
    return job.get("lock") or JOBS_LOCK


def _log(job, level, message):
    job["logs"].append({"level": level, "message": message})
    if len(job["logs"]) > 2000:
        job["logs"] = job["logs"][-1000:]


def _check_cancel(job):
    if job["cancel"]:
        raise _Cancelled()


def _verify_enabled(job=None):
    env = os.environ.get("VERIFY_EXPORT", "0")
    if env not in ("0", "false", "off"):
        return True
    if job and job["request"].get("verify") in (True, 1, "1", "true", "on"):
        return True
    return False


def _verify_output(job, src_path, out_path, start, end, work_dir):
    _log(job, "info", f"Verifying {os.path.basename(out_path)} against source pages {start}-{end}...")
    vdir = os.path.join(work_dir, "verify")
    try:
        report = verify.verify_export(src_path, out_path, start, end, vdir)
    except Exception as e:
        _log(job, "warn", f"Verification skipped for {os.path.basename(out_path)}: {e}")
        return
    pc = report["page_count"]
    if pc["actual"] != pc["expected"]:
        _log(job, "error",
             f"{os.path.basename(out_path)} FAIL: expected {pc['expected']} page(s), "
             f"got {pc['actual']}.")
    for se in report.get("structural_errors", []):
        _log(job, "error", f"{os.path.basename(out_path)} structural defect: {se}")
    if report["corrected"]:
        _log(job, "info",
             f"{os.path.basename(out_path)} auto-corrected ({report['corrected']} pass(es)).")
    for entry in report["pages"]:
        if not entry["match"]:
            _log(job, "warn",
                 f"{os.path.basename(out_path)} page {entry['page']} differs from "
                 f"source page {entry['source_page']}: {entry['detail']}")
        elif not entry["text_match"]:
            _log(job, "warn",
                 f"{os.path.basename(out_path)} page {entry['page']} text mismatch "
                 f"vs source page {entry['source_page']}.")
    if report["pass"]:
        _log(job, "success",
             f"{os.path.basename(out_path)} verified: {pc['actual']} page(s) match source.")


class _Cancelled(Exception):
    pass


def _process_one(job, uid, src_path):
    lock = _job_lock(job)

    def record_error(message):
        with lock:
            job["errors"].append(message)
            job["fail_count"] += 1

    def record_done():
        with lock:
            job["completed"] += 1

    spec = job["request"].get("range", "1-end")
    outputs = []
    src_ext = os.path.splitext(src_path)[1].lower()
    jobs_base = os.path.join(_jobs_dir(uid), job["id"])

    if src_ext in _PDF_EXT:
        page_count = pdf_split.pdf_page_count(src_path)
        ranges_list = ranges.parse_range_spec(spec, page_count)
        os.makedirs(os.path.join(jobs_base, "outputs"), exist_ok=True)
        for start, end in ranges_list:
            _check_cancel(job)
            try:
                name = naming.apply_template(
                    job["request"].get("naming_pattern", "{original_name}_pages_{start_page}-{end_page}"),
                    os.path.basename(src_path), start, end, "pdf")
                out = os.path.join(jobs_base, "outputs", name)
                pdf_split.split_pdf_range(src_path, start, end, out)
                outputs.append(name)
            except Exception as e:
                record_error(f"{os.path.basename(src_path)} pages {start}-{end}: {e}")
            finally:
                record_done()
        return outputs, len(ranges_list)

    work_dir = os.path.join(jobs_base, "work")
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.join(jobs_base, "outputs"), exist_ok=True)
    docx_path = src_path
    if src_ext != ".docx":
        try:
            docx_path = convert.normalize_to_docx(src_path, work_dir)
        except Exception as e:
            record_error(f"{os.path.basename(src_path)}: {e}")
            return outputs, 1

    fmt = job["request"].get("format", "same")
    if fmt == "same":
        fmt = "docx"
        if src_ext in (".doc", ".rtf", ".docm", ".dot", ".dotx", ".dotm"):
            fmt = src_ext.lstrip(".") if src_ext != ".dot" else "doc"
    target_ext = fmt.lower().lstrip(".")

    try:
        page_count, boundaries = paginate.paginate_docx(docx_path)
    except Exception as e:
        record_error(f"{os.path.basename(src_path)}: {e}")
        return outputs, 1

    ranges_list = ranges.parse_range_spec(spec, page_count)

    try:
        package_cache = docx_trim.load_package(docx_path)
    except Exception:
        package_cache = None

    pending_converts = []
    for start, end in ranges_list:
        _check_cancel(job)
        try:
            name = naming.apply_template(
                job["request"].get("naming_pattern", "{original_name}_pages_{start_page}-{end_page}"),
                os.path.basename(src_path), start, end, target_ext)
            out = os.path.join(jobs_base, "outputs", name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            split_docx = os.path.join(work_dir, "_split_%d_%d.docx" % (start, end))
            docx_trim.split_docx_range(docx_path, start, end, boundaries, split_docx,
                                       package_cache=package_cache)
            if _verify_enabled(job):
                _verify_output(job, docx_path, split_docx, start, end, work_dir)
            pending_converts.append((split_docx, target_ext, out, name))
            outputs.append(name)
        except Exception as e:
            record_error(f"{os.path.basename(src_path)} pages {start}-{end}: {e}")
        finally:
            record_done()

    if pending_converts:
        try:
            convert.convert_docx_many([(s, fmt, o) for s, fmt, o, _n in pending_converts])
        except Exception as e:
            record_error(f"{os.path.basename(src_path)} conversion failed: {e}")
    return outputs, len(ranges_list)


def _worker(job, uid):
    from concurrent.futures import ThreadPoolExecutor

    lock = _job_lock(job)

    def process_file(fname):
        _check_cancel(job)
        try:
            src = resolve_upload(uid, fname)
        except Exception as e:
            with lock:
                job["errors"].append(f"{fname}: {e}")
                job["fail_count"] += 1
                _log(job, "error", f"Failed {fname}: {e}")
            return
        with lock:
            job["current_status"] = f"Processing {os.path.basename(src)}..."
        try:
            outputs, expected = _process_one(job, uid, src)
            with lock:
                job["total"] += expected
                job["outputs"].extend(outputs)
                _log(job, "success", f"Finished {os.path.basename(src)} ({len(outputs)} output(s)).")
        except _Cancelled:
            raise
        except Exception as e:
            with lock:
                job["errors"].append(f"{fname}: {e}")
                job["fail_count"] += 1
                _log(job, "error", f"Failed {fname}: {e}")

    try:
        job["status"] = "running"
        job["current_status"] = "Starting..."
        _log(job, "info", "Job started (engine: trimming/high-fidelity).")
        files = job["request"].get("files", [])
        workers = max(1, min(len(files), 3))
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(process_file, files))
        else:
            for fname in files:
                process_file(fname)

        with lock:
            job["total"] = max(job["total"], job["completed"])
            job["success_count"] = len(job["outputs"])
            if job["cancel"]:
                job["status"] = "cancelled"
                _log(job, "warn", "Job cancelled by user.")
            else:
                job["status"] = "done"
                _log(job, "info", f"Job complete: {len(job['outputs'])} file(s) produced.")
            job["current_status"] = "Complete" if job["status"] == "done" else "Cancelled"
    except _Cancelled:
        with lock:
            job["status"] = "cancelled"
            job["current_status"] = "Cancelled"
            _log(job, "warn", "Job cancelled.")
    except Exception as e:
        with lock:
            job["status"] = "error"
            job["current_status"] = "Error"
            job["errors"].append(str(e))
            _log(job, "error", "Job error: " + str(e))


@bp.route("/api/export", methods=["POST"])
def api_export():
    data = request.get_json(silent=True) or {}
    files = data.get("files")
    if not files:
        return jsonify({"error": "No files selected."}), 400
    if not data.get("range", "").strip():
        return jsonify({"error": "Enter a valid page range."}), 400
    uid = _user_id()
    job = _new_job(uid, data)
    t = threading.Thread(target=_worker, args=(job, uid), daemon=True)
    t.start()
    return jsonify({"job_id": job["id"]})


@bp.route("/api/job/<job_id>")
def api_job(job_id):
    job = JOBS.get(job_id)
    if job is None or job.get("user_id") != _user_id():
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "current_status": job["current_status"],
        "completed": job["completed"],
        "total": job["total"],
        "success_count": job["success_count"],
        "fail_count": job["fail_count"],
        "logs": job["logs"],
        "errors": job["errors"],
        "outputs": job["outputs"],
    })


@bp.route("/api/job/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    job = JOBS.get(job_id)
    if job is not None and job.get("user_id") == _user_id():
        job["cancel"] = True
    return jsonify({"ok": True})


@bp.route("/api/clear-storage", methods=["POST"])
def api_clear_storage():
    """Clear the current user's uploads / finished job files."""
    uid = _user_id()
    freed = 0
    count = 0
    jobs_dir = _jobs_dir(uid)
    for root, _dirs, files in os.walk(jobs_dir):
        for f in files:
            p = os.path.join(root, f)
            try:
                freed += os.path.getsize(p)
                os.remove(p)
                count += 1
            except Exception:
                pass
    uploads = _uploads_dir(uid)
    for root, _dirs, files in os.walk(uploads):
        for f in files:
            p = os.path.join(root, f)
            try:
                freed += os.path.getsize(p)
                os.remove(p)
                count += 1
            except Exception:
                pass
    with JOBS_LOCK:
        for jid in [j for j, jb in JOBS.items() if jb.get("user_id") == uid]:
            JOBS.pop(jid, None)
    return jsonify({"file_count": count, "reclaimed_mb": round(freed / (1024 * 1024), 2)})


@bp.errorhandler(413)
def too_large(e):
    return jsonify({"error": "Uploaded files exceed the upload size limit."}), 413
