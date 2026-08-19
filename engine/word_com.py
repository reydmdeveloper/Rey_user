"""MS Word COM engine - the authoritative, high-fidelity Word engine.

Runs only on Windows with Microsoft Word installed. Used for:
  * True Word pagination (page boundaries via Word's own layout engine).
  * Format conversion (docx / doc / rtf / pdf / docm) via Word's SaveAs.
  * PDF export for document previews.

Every function degrades gracefully when Word is unavailable.
"""

import difflib
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


def _pair_units_to_entries(units, entries):
    """Align XML units to Word COM entries and return a per-unit page list.

    Both sequences walk the document in the same reading order, so this
    builds a token per non-empty unit/row and per non-empty entry (its
    normalized text) and aligns the two token sequences with
    difflib.SequenceMatcher instead of either:

      * a raw index-for-index zip, which silently corrupts everything
        downstream of the first spot where the two sides' empty-item counts
        or placement disagree (form fields, spacer paragraphs, vertical-merge
        continuation cells rarely line up exactly even when the totals
        happen to match), or
      * a small fixed-lookahead text search, which locks onto the wrong
        occurrence on documents with many near-duplicate paragraphs (repeated
        form rows, boilerplate) and then drifts, collapsing many source pages
        onto one.

    SequenceMatcher finds matching blocks - runs of tokens that are exactly
    equal on both sides, in order - by search over the *whole* remaining
    sequence rather than a small window, so nearby duplicates don't fool it.
    Content between two matched blocks (or outside all of them) doesn't need
    the two sides' counts to agree there: it just inherits the nearest
    matched page number, so a local mismatch degrades gracefully instead of
    corrupting the rest of the document.

    Returns a list of per-unit page numbers, or None if nothing could be
    anchored at all (e.g. the document has no text content).
    """
    from .docx_trim import _element_text

    unit_idx = []
    unit_tokens = []
    for i, u in enumerate(units):
        node = u["node"] if u["kind"] == "p" else (u["row"] if u["kind"] == "row" else u.get("table"))
        if node is None:
            continue
        text = "".join(_norm(_element_text(node)))
        if text:
            unit_idx.append(i)
            unit_tokens.append(text[:80])

    entry_idx = []
    entry_tokens = []
    for i, e in enumerate(entries):
        text = "".join(e[1])
        if text:
            entry_idx.append(i)
            entry_tokens.append(text[:80])

    if not unit_idx or not entry_idx:
        return None

    unit_page = [None] * len(units)
    sm = difflib.SequenceMatcher(None, unit_tokens, entry_tokens, autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            unit_page[unit_idx[a + k]] = entries[entry_idx[b + k]][2]

    # Forward-fill from each anchor, then back-fill the leading gap (if any)
    # before the first anchor from the first known page.
    last = None
    for i in range(len(unit_page)):
        if unit_page[i] is not None:
            last = unit_page[i]
        elif last is not None:
            unit_page[i] = last
    nxt = None
    for i in range(len(unit_page) - 1, -1, -1):
        if unit_page[i] is not None:
            nxt = unit_page[i]
        elif nxt is not None:
            unit_page[i] = nxt

    if any(p is None for p in unit_page):
        return None
    return unit_page


def _pagination_looks_sane(page_count, boundaries):
    """Reject results where too many pages ended up with no content of their own.

    Range.Information(wdActiveEndPageNumber) is queried against an invisible,
    headless Word window here, and has been observed to intermittently report
    a stale page number for a stretch of paragraphs even though the exact
    same query sequence returns correct values on a different run against the
    same document. When that happens several consecutive pages collapse onto
    the same boundary (all their content gets attributed to one earlier
    page), which silently corrupts every split touching that region. A run of
    a few genuinely blank pages is normal; a systematic collapse is not.
    """
    if not boundaries or len(boundaries) != page_count:
        return False
    distinct = len(set(boundaries))
    allowed_blank = max(1, page_count // 15)
    return distinct >= page_count - allowed_blank


def paginate(docx_path, max_attempts=3):
    """Return (page_count, boundaries) using MS Word's real layout engine.

    boundaries[page0] = last content-unit index on that page. Retries a few
    times when the result fails a basic sanity check (see
    _pagination_looks_sane) since a fresh Word session sometimes succeeds
    where the previous one returned stale per-paragraph page numbers. If
    every attempt fails the check, returns None rather than a known-bad
    result, so the caller (engine.paginate) falls back to the PDF-export-
    backed engine instead of silently shipping corrupt boundaries.
    """
    for attempt in range(max_attempts):
        result = _paginate_once(docx_path)
        if result is not None and _pagination_looks_sane(*result):
            return result
    return None


def _paginate_once(docx_path):
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
                table_count = main_story.Tables.Count
            except Exception:
                table_count = 0
            for t in range(1, table_count + 1):
                try:
                    tbl = main_story.Tables(t)
                except Exception:
                    continue
                try:
                    row_count = tbl.Rows.Count
                except Exception:
                    row_count = None
                if row_count is not None:
                    got_all_rows = True
                    for r in range(1, row_count + 1):
                        try:
                            row = tbl.Rows(r)
                            rng = row.Range
                            text = (rng.Text or "").rstrip("\r\x07")
                            page = int(rng.Information(3))
                            entries.append((int(rng.Start), _norm(text), page))
                        except Exception:
                            got_all_rows = False
                            break
                    if got_all_rows:
                        continue
                    # A row raised mid-loop (merge boundary hit lazily) -
                    # discard the partial rows just added and use the
                    # paragraph-based fallback below for this table instead.
                    entries = [e for e in entries if e[0] < int(tbl.Range.Start)]
                # tbl.Rows is unusable on tables with vertically merged
                # cells ("Cannot access individual rows in this collection
                # because the table has vertically merged cells."). Rebuild
                # rows by grouping the table's paragraphs by their row index
                # (wdEndOfRangeRowNumber = 14), which stays valid even when
                # rows/cells are merged, instead of skipping the whole table
                # (and every table after it, since a bare except around the
                # full loop used to abort the rest of the document).
                try:
                    row_map = {}
                    row_order = []
                    for para in tbl.Range.Paragraphs:
                        rng = para.Range
                        try:
                            ridx = int(rng.Information(14))  # wdEndOfRangeRowNumber
                        except Exception:
                            continue
                        text = (rng.Text or "").rstrip("\r\x07")
                        page = int(rng.Information(3))
                        if ridx not in row_map:
                            row_map[ridx] = [int(rng.Start), [], page]
                            row_order.append(ridx)
                        entry = row_map[ridx]
                        if text:
                            entry[1].append(text)
                        entry[2] = page
                    for ridx in row_order:
                        start, parts, page = row_map[ridx]
                        entries.append((start, _norm(" ".join(parts)), page))
                except Exception:
                    pass

            entries.sort(key=lambda e: e[0])

            # Align XML units to Word COM entries
            from .docx_trim import build_units, load_document_xml, _q
            root = load_document_xml(docx_path)
            body = root.find(_q("body"))
            units = build_units(body) if body is not None else []

            if not units or not entries:
                items = [(w, p) for _, w, p in entries]
                page_count = max(page_count, max([p for _, p in items] + [1]))
                return page_count, _unit_end_pages_to_boundaries(items, page_count)

            unit_page = _pair_units_to_entries(units, entries)
            if unit_page is None:
                return None

            page_count = max([page_count] + unit_page)
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
