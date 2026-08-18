"""Document format conversion.

On Windows, MS Word (COM) is the conversion engine - it produces byte-for-byte
Word-exact output. On Linux (Render), LibreOffice is used only as an optional
conversion helper; if it is unavailable, docx/docm outputs still work because
the trimming engine writes real Word packages directly.
"""

import os
import shutil
import subprocess
import tempfile
import threading

from . import word_com

_lock = threading.Lock()

_CANDIDATES = [
    "soffice",
    "libreoffice",
    "soffice.bin",
    "C:\\Program Files\\LibreOffice\\program\\soffice.exe",
    "C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/usr/lib/libreoffice/program/soffice.bin",
    "/opt/libreoffice/program/soffice.bin",
    "/snap/bin/soffice",
    "/var/lib/flatpak/exports/bin/org.libreoffice.LibreOffice",
]

_soffice_cache = None
_soffice_checked = False
_soffice_install_tried = False
_soffice_install_log = []
_soffice_install_error = None


def _detect_soffice():
    """Locate an existing soffice binary without attempting an install."""
    env = os.environ.get("SOFFICE_BIN")
    if env and shutil.which(env):
        return env

    for c in _CANDIDATES:
        if os.path.sep in c:
            if os.path.exists(c):
                return c
        else:
            p = shutil.which(c)
            if p:
                return p

    # Last resort: recursive scan of common LibreOffice install roots.
    for root in ("/usr/lib", "/usr/bin", "/opt", "/usr/local", "/snap"):
        if not os.path.isdir(root):
            continue
        try:
            hits = []
            for dirpath, dirnames, filenames in os.walk(root):
                for fn in filenames:
                    if fn in ("soffice", "soffice.bin"):
                        hits.append(os.path.join(dirpath, fn))
                if len(hits) >= 1:
                    break
            if hits:
                return hits[0]
        except Exception:
            continue
    return None


def _try_install_soffice():
    """Best-effort runtime install of LibreOffice when missing (Linux/Render).

    Covers deployments that did not run the Dockerfile (e.g. Render native
    runtimes). Tries apt-get directly first, then via sudo -n when the process
    is not root, and records what happened for /api/diagnostics. Runs at most
    once per process and is guarded so overlapping requests do not fight.
    """
    global _soffice_install_tried, _soffice_install_log, _soffice_install_error
    if _soffice_install_tried or os.name == "nt":
        return
    _soffice_install_tried = True
    if not (shutil.which("apt-get") or os.path.exists("/usr/bin/apt-get")):
        _soffice_install_error = "apt-get not found - cannot install LibreOffice at runtime"
        _soffice_install_log.append(_soffice_install_error)
        print(_soffice_install_error)
        return

    def _log(line):
        _soffice_install_log.append(line)
        if len(_soffice_install_log) > 50:
            del _soffice_install_log[:-50]
        print(line)

    apt_cmd = shutil.which("apt-get") or "/usr/bin/apt-get"
    try:
        import getpass

        is_root = False
        try:
            import os as _os
            is_root = _os.geteuid() == 0
        except Exception:
            is_root = getpass.getuser() == "root"
        base = [apt_cmd]
        if not is_root:
            sudo = shutil.which("sudo")
            if not sudo:
                _soffice_install_error = (
                    "not running as root and sudo unavailable - cannot apt-get install LibreOffice"
                )
                _log(_soffice_install_error)
                return
            base = [sudo, "-n", apt_cmd]
            _log("Non-root process; attempting install via sudo -n")

        _log("LibreOffice not found - attempting runtime install (may take minutes)...")
        upd = subprocess.run(base + ["update", "-qq"], capture_output=True, text=True, timeout=300)
        _log("apt-get update rc=" + str(upd.returncode))
        inst = subprocess.run(
            base
            + [
                "install", "-y", "--no-install-recommends",
                "libreoffice-writer", "libreoffice-core", "libreoffice-common",
                "libreoffice-style-colibre", "fonts-dejavu", "fonts-liberation",
            ],
            capture_output=True, text=True, timeout=900,
        )
        _log("apt-get install rc=" + str(inst.returncode))
        if inst.returncode != 0:
            tail = (inst.stderr or inst.stdout or "").strip().splitlines()[-4:]
            _soffice_install_error = "apt-get install failed: " + " | ".join(tail)
            _log(_soffice_install_error)
            return
        _log("LibreOffice runtime install finished.")
    except Exception as e:
        _soffice_install_error = "LibreOffice runtime install failed: " + str(e)
        _log(_soffice_install_error)


def find_soffice():
    """Return the soffice binary path or None if LibreOffice is unavailable.

    If LibreOffice is not installed (Linux), a single best-effort runtime
    install is attempted so previews, pagination and verification keep working
    on servers that were deployed without the Dockerfile.
    """
    global _soffice_cache, _soffice_checked
    if _soffice_checked:
        return _soffice_cache
    with _lock:
        if _soffice_checked:
            return _soffice_cache
        found = _detect_soffice()
        if found is None:
            _try_install_soffice()
            found = _detect_soffice()
        _soffice_cache = found
        _soffice_checked = True
        return _soffice_cache


def soffice_available():
    return find_soffice() is not None


def _run_soffice(args, timeout=600):
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) is not installed on this server.")
    cmd = [soffice, "--headless", "--norestore", "--nologo", "--nodefault"]
    cmd += args
    with _lock:
        profile = tempfile.mkdtemp(prefix="loffice_")
        cmd += ["-env:UserInstallation=file:///" + profile.replace("\\", "/")]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("LibreOffice conversion timed out.")
        finally:
            try:
                shutil.rmtree(profile, ignore_errors=True)
            except Exception:
                pass
    if proc.returncode != 0:
        raise RuntimeError("LibreOffice failed: " + (proc.stderr or proc.stdout or "").strip()[-2000:])
    return proc


def convert_to_pdf(src, out_dir):
    """Convert any file to PDF (MS Word -> LibreOffice -> Pure-Python fallback)."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    if word_com.word_available():
        try:
            base = os.path.splitext(os.path.basename(src))[0]
            pdf = os.path.join(out_dir, base + ".pdf")
            word_com.export_pdf(src, pdf)
            return pdf
        except Exception:
            pass
    if soffice_available():
        try:
            _run_soffice(["--convert-to", "pdf", "--outdir", out_dir, os.path.abspath(src)])
            base = os.path.splitext(os.path.basename(src))[0]
            pdf = os.path.join(out_dir, base + ".pdf")
            if os.path.exists(pdf):
                return pdf
        except Exception:
            pass
    base = os.path.splitext(os.path.basename(src))[0]
    pdf = os.path.join(out_dir, base + ".pdf")
    from . import python_renderer
    python_renderer.convert_docx_to_pdf_python(src, pdf)
    return pdf


def convert_docx_to(src_docx, out_format, out_path):
    """Convert a split docx to the target format.

    Uses MS Word COM when available; otherwise LibreOffice; otherwise Python fallback for PDF
    or standard Word package copy for docx/docm.
    """
    fmt = out_format.lower().strip().lstrip(".")
    if fmt in ("docx", "docm"):
        # The trimming engine writes a complete Word package directly;
        # copying preserves the exact trimmed result (re-saving through Word
        # would re-insert trailing breaks, undoing the last-page trimming).
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        shutil.copyfile(src_docx, out_path)
        return out_path

    if word_com.word_available():
        try:
            return word_com.convert(src_docx, fmt, out_path)
        except Exception:
            if fmt != "pdf":
                raise

    if soffice_available():
        try:
            out_dir = os.path.dirname(os.path.abspath(out_path))
            os.makedirs(out_dir, exist_ok=True)
            _run_soffice(["--convert-to", fmt, "--outdir", out_dir, os.path.abspath(src_docx)])
            base = os.path.splitext(os.path.basename(src_docx))[0]
            produced = os.path.join(out_dir, base + "." + fmt)
            if os.path.exists(produced):
                os.replace(produced, out_path)
                return out_path
        except Exception:
            if fmt != "pdf":
                raise

    if fmt == "pdf":
        from . import python_renderer
        return python_renderer.convert_docx_to_pdf_python(src_docx, out_path)

    raise RuntimeError(
        f"Format '{fmt}' requires MS Word (Windows) or LibreOffice on the server."
    )


def convert_docx_many(items):
    """Batch-convert several split docx files to their target formats.

    items: list of (src_docx, out_format, out_path). docx/docm outputs are
    exact package copies; every other format is converted inside a single MS
    Word session when Word is available (one Word startup per batch instead of
    per file), with the per-file fallbacks kept for anything Word rejects.
    """
    copy_items = []
    word_items = []
    rest = []
    for src_docx, out_format, out_path in items:
        fmt = out_format.lower().strip().lstrip(".")
        if fmt in ("docx", "docm"):
            copy_items.append((src_docx, out_path))
        elif word_com.word_available():
            word_items.append((src_docx, fmt, out_path))
        else:
            rest.append((src_docx, fmt, out_path))

    for src_docx, out_path in copy_items:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        shutil.copyfile(src_docx, out_path)

    failed = word_com.convert_many(word_items) if word_items else []
    for src_docx, fmt, out_path in failed:
        convert_docx_to(src_docx, fmt, out_path)

    for src_docx, fmt, out_path in rest:
        convert_docx_to(src_docx, fmt, out_path)


def normalize_to_docx(src, out_dir):
    """Convert a .doc/.rtf/.docm/.dotx file into a .docx for processing."""
    ext = os.path.splitext(src)[1].lower()
    if ext in (".docx", ".docm", ".dotx"):
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "input_normalized.docx")
        shutil.copyfile(src, out)
        return out
    if ext == ".pdf":
        return None
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "input_normalized.docx")
    if word_com.word_available():
        word_com.convert(src, "docx", out)
        return out
    if soffice_available():
        _run_soffice(["--convert-to", "docx", "--outdir", out_dir, os.path.abspath(src)])
        base = os.path.splitext(os.path.basename(src))[0]
        produced = os.path.join(out_dir, base + ".docx")
        if not os.path.exists(produced):
            raise RuntimeError("Could not normalize file to docx.")
        return produced
    raise RuntimeError(
        "Normalizing this format requires MS Word (Windows) or LibreOffice on the server."
    )
