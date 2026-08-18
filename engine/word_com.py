"""MS Word COM engine - the authoritative, high-fidelity Word engine.

Runs only on Windows with Microsoft Word installed. Used for:
  * True Word pagination (page boundaries via Word's own layout engine).
  * Format conversion (docx / doc / rtf / pdf / docm) via Word's SaveAs.
  * PDF export for document previews.

Every function degrades gracefully when Word is unavailable.
"""

import os
import re
import threading
import unicodedata

_win32com = None
_pythoncom = None
_word_checked = False
_word_ok = False

# Word COM is not thread-safe. Every call that touches Word must be serialized
# through this lock so overlapping previews / pagination / verification jobs do
# not spin up colliding Word.Application instances (the cause of intermittent
# HTTP 500s on previews).
_WORD_LOCK = threading.Lock()


def _load_word():
    global _win32com, _pythoncom, _word_checked, _word_ok
    if _word_checked:
        return _win32com if _word_ok else None
    _word_checked = True
    if os.name != "nt":
        return None
    try:
        import pythoncom
        import win32com.client  # noqa

        _pythoncom = pythoncom
        _win32com = win32com.client
        _word_ok = True
    except Exception:
        _win32com = None
        _pythoncom = None
        _word_ok = False
    return _win32com if _word_ok else None


def word_available():
    return _load_word() is not None


def _open_word():
    if not word_available():
        raise RuntimeError("MS Word (win32com) is not available on this server.")
    pythoncom = _pythoncom
    pythoncom.CoInitialize()
    word = _win32com.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        word.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
    except Exception:
        pass
    try:
        word.Options.ConfirmConversions = False
    except Exception:
        pass
    return word


def _shutdown_word(word):
    try:
        word.Quit()
    except Exception:
        pass
    try:
        _pythoncom.CoUninitialize()
    except Exception:
        pass


def _open_doc(word, path, readonly=True):
    try:
        return word.Documents.Open(
            FileName=os.path.abspath(path),
            ReadOnly=readonly,
            AddToRecentFiles=False,
            Visible=False,
            ConfirmConversions=False,
            Revert=False,
        )
    except Exception:
        try:
            os.system("taskkill /f /im WINWORD.EXE 2>NUL")
            time.sleep(0.5)
        except Exception:
            pass
        fresh_word = _open_word()
        return fresh_word.Documents.Open(
            FileName=os.path.abspath(path),
            ReadOnly=readonly,
            AddToRecentFiles=False,
            Visible=False,
            ConfirmConversions=False,
            Revert=False,
        )


def get_page_count(docx_path):
    """Return exact rendered page count of document in MS Word."""
    with _WORD_LOCK:
        word = _open_word()
        doc = None
        try:
            doc = _open_doc(word, docx_path, readonly=True)
            doc.Repaginate()
            return int(doc.ComputeStatistics(2))
        finally:
            if doc is not None:
                try:
                    doc.Close(0)
                except Exception:
                    pass
            _shutdown_word(word)


def _norm(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.findall(r"[a-z0-9]+", text)


def paginate(docx_path):
    """Return (page_count, boundaries) using MS Word's real layout engine.

    boundaries[page0] = last content-unit index on that page.
    """
    with _WORD_LOCK:
        word = _open_word()
        doc = None
        try:
            doc = _open_doc(word, docx_path, readonly=True)
            doc.Repaginate()
            page_count = int(doc.ComputeStatistics(2))  # wdStatisticPages

            # Inspect main text story only (wdMainTextStory = 1)
            main_story = doc.StoryRanges(1)

            entries = []
            try:
                for para in main_story.Paragraphs:
                    if para.Range.Tables.Count > 0:
                        continue  # paragraph lives inside a table cell
                    rng = para.Range
                    text = (rng.Text or "").rstrip("\r")
                    page = int(rng.Information(3))  # wdActiveEndPageNumber
                    entries.append((int(rng.Start), _norm(text), page))
            except Exception:
                entries = []
                try:
                    for i in range(1, main_story.Paragraphs.Count + 1):
                        para = main_story.Paragraphs(i)
                        if para.Range.Tables.Count > 0:
                            continue
                        rng = para.Range
                        text = (rng.Text or "").rstrip("\r")
                        page = int(rng.Information(3))
                        entries.append((int(rng.Start), _norm(text), page))
                except Exception:
                    pass

            try:
                for t in range(1, main_story.Tables.Count + 1):
                    tbl = main_story.Tables(t)
                    for r in range(1, tbl.Rows.Count + 1):
                        row = tbl.Rows(r)
                        rng = row.Range
                        text = (rng.Text or "").rstrip("\r\x07")
                        page = int(rng.Information(3))
                        entries.append((int(rng.Start), _norm(text), page))
            except Exception:
                pass

            entries.sort(key=lambda e: e[0])

            # Fingerprint match XML units to Word COM entries
            from .docx_trim import build_units, load_document_xml, _strip_punct, _element_text, _q
            root = load_document_xml(docx_path)
            body = root.find(_q("body"))
            units = build_units(body) if body is not None else []

            if not units or not entries:
                items = [(w, p) for _, w, p in entries]
                page_count = max(page_count, max([p for _, p in items] + [1]))
                return page_count, _unit_end_pages_to_boundaries(items, page_count)

            unit_page = []
            entry_idx = 0
            num_entries = len(entries)
            for u in units:
                node = u["node"] if u["kind"] == "p" else (u["row"] if u["kind"] == "row" else u.get("table"))
                if node is None:
                    p = entries[entry_idx][2] if entry_idx < num_entries else (unit_page[-1] if unit_page else 1)
                    unit_page.append(p)
                    continue
                u_words = _norm(" ".join(_element_text(node)))
                u_text_str = "".join(u_words)
                if not u_text_str:
                    p = entries[entry_idx][2] if entry_idx < num_entries else (unit_page[-1] if unit_page else 1)
                    if entry_idx < num_entries and not "".join(entries[entry_idx][1]):
                        entry_idx += 1
                    unit_page.append(p)
                    continue

                best_ei = None
                for ei in range(entry_idx, min(entry_idx + 10, num_entries)):
                    e_words = entries[ei][1]
                    e_text_str = "".join(e_words)
                    if not e_text_str:
                        continue
                    if (e_text_str in u_text_str) or (u_text_str in e_text_str) or (u_words[:3] and e_words[:3] and u_words[:3] == e_words[:3]):
                        best_ei = ei
                        break

                if best_ei is not None:
                    matched_page = entries[best_ei][2]
                    entry_idx = best_ei + 1
                    while entry_idx < num_entries:
                        nxt_str = "".join(entries[entry_idx][1])
                        if nxt_str and nxt_str in u_text_str:
                            matched_page = entries[entry_idx][2]
                            entry_idx += 1
                        else:
                            break
                    unit_page.append(matched_page)
                else:
                    p = entries[entry_idx][2] if entry_idx < num_entries else (unit_page[-1] if unit_page else 1)
                    unit_page.append(p)

            page_count = max([page_count] + unit_page + [1])
            by_page = {}
            for i, p in enumerate(unit_page):
                by_page.setdefault(p, []).append(i)
            boundaries = []
            last = -1
            for p in range(1, page_count + 1):
                lst = by_page.get(p)
                if lst:
                    last = max(last, max(lst))
                boundaries.append(last)
            if not boundaries or any(b == -1 for b in boundaries):
                return None
            return page_count, boundaries
        finally:
            try:
                if doc is not None:
                    doc.Close(SaveChanges=0)
            except Exception:
                pass
            _shutdown_word(word)


def _unit_end_pages_to_boundaries(items, page_count):
    """items: [(unit_words, end_page)] aligned 1:1 with XML content units.

    Returns boundaries[page0] = last unit index on that page.
    """
    by_page = {}
    for u, (_w, p) in enumerate(items):
        by_page.setdefault(p, []).append(u)
    boundaries = []
    last = -1
    for p in range(1, page_count + 1):
        lst = by_page.get(p)
        if lst:
            last = max(last, max(lst))
        boundaries.append(last)
    return boundaries


_SAVE_FORMAT = {
    "docx": 12,  # wdFormatXMLDocument
    "doc": 0,  # wdFormatDocument
    "rtf": 6,  # wdFormatRTF
    "docm": 13,  # wdFormatXMLDocumentMacroEnabled
    "dotx": 7,
    "dotm": 9,
    "txt": 2,
    "odt": 23,
}


def convert(src_path, out_format, out_path):
    """Convert a Word-family document to the target format using Word SaveAs."""
    fmt = out_format.lower().strip().lstrip(".")
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)

    with _WORD_LOCK:
        word = _open_word()
        doc = None
        try:
            doc = _open_doc(word, src_path, readonly=False)
            if fmt == "pdf":
                doc.ExportAsFixedFormat(
                    OutputFileName=out_path,
                    ExportFormat=17,  # wdExportFormatPDF
                    OpenAfterExport=False,
                    OptimizeFor=0,
                    CreateBookmarks=1,
                    DocStructureTags=True,
                )
            elif fmt in _SAVE_FORMAT:
                doc.SaveAs(out_path, FileFormat=_SAVE_FORMAT[fmt])
            else:
                raise ValueError(f"Unsupported format for MS Word conversion: {out_format}")
        finally:
            try:
                if doc is not None:
                    doc.Close(SaveChanges=0)
            except Exception:
                pass
            _shutdown_word(word)
    if not os.path.exists(out_path):
        raise RuntimeError("MS Word did not produce the converted file.")
    return out_path


def convert_many(items):
    """Convert several documents inside a single Word session.

    items: list of (src_path, out_format, out_path). Returns the list of
    (src_path, out_format, out_path) entries Word could not produce so the
    caller can fall back to another engine. Starting Word once per batch
    instead of once per file dramatically speeds up multi-page exports.
    """
    failed = []
    if not items:
        return failed
    with _WORD_LOCK:
        word = _open_word()
        docs = []
        try:
            for src, fmt, out in items:
                fmt = fmt.lower().strip().lstrip(".")
                out = os.path.abspath(out)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                if os.path.exists(out):
                    os.remove(out)
                try:
                    doc = _open_doc(word, src, readonly=False)
                except Exception:
                    failed.append((src, fmt, out))
                    continue
                docs.append((doc, src, fmt, out))
            for doc, src, fmt, out in docs:
                try:
                    if fmt == "pdf":
                        doc.ExportAsFixedFormat(
                            OutputFileName=out,
                            ExportFormat=17,  # wdExportFormatPDF
                            OpenAfterExport=False,
                            OptimizeFor=0,
                            CreateBookmarks=1,
                            DocStructureTags=True,
                        )
                    elif fmt in _SAVE_FORMAT:
                        doc.SaveAs(out, FileFormat=_SAVE_FORMAT[fmt])
                    else:
                        raise ValueError(f"Unsupported format for MS Word conversion: {fmt}")
                except Exception:
                    failed.append((src, fmt, out))
                finally:
                    try:
                        doc.Close(SaveChanges=0)
                    except Exception:
                        pass
        finally:
            for doc, _src, _fmt, _out in docs:
                try:
                    doc.Close(SaveChanges=0)
                except Exception:
                    pass
            _shutdown_word(word)
    for src, fmt, out in items:
        if os.path.exists(out) and os.path.getsize(out) > 0:
            continue
        if (src, fmt, out) not in failed:
            failed.append((src, fmt, out))
    return failed


def export_pdf(src_path, out_pdf):
    """Export a Word-family document to PDF (for previews)."""
    return convert(src_path, "pdf", out_pdf)
