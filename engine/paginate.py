"""Page-boundary detection for Word documents.

Engine priority depends on whether a *real* layout engine is available to
render the document (MS Word COM on Windows, or LibreOffice/soffice on
Linux - e.g. Render, if it's installed there):

  With a real layout engine available:
    1. PDF-backed   - renders to PDF through that real engine and maps
                       content units to the PDF page their text lands on.
                       Preferred even on Windows with Word installed: it goes
                       through Word's real, fully-committed layout (the same
                       pipeline used to print/export) rather than live
                       per-paragraph COM queries, which have been observed to
                       return a stale or misplaced page number for a stretch
                       of a document - notably around tables with vertically
                       merged cells and image-heavy rows - even though the
                       exact same query sequence is correct elsewhere in the
                       same document.
    2. MS Word COM  - live per-paragraph Information(wdActiveEndPageNumber)
                       queries (Windows only). Fallback for when a PDF export
                       can't be produced.
    3. Word markers - see below.
    4. Layout estimate - see below.

  With NO real layout engine available (e.g. deployed on Render without
  LibreOffice installed - MS Word COM is Windows-only so is never available
  there at all):
    1. Word markers - <w:lastRenderedPageBreak/> markers Word embeds in a
                       docx file the last time a real copy of Word rendered
                       it. This needs no rendering at all here, so it isn't
                       subject to the accuracy limits of whatever's rendering
                       the PDF - it's tried first in this branch precisely
                       because the PDF-backed engine below is only as good as
                       its renderer, and without Word or LibreOffice that
                       renderer is python_renderer (see next point), which is
                       measurably less accurate: verified on a 19-page
                       natural-flow test document where python_renderer's
                       pagination was off by one page while word_markers
                       matched Word exactly.
    2. PDF-backed   - same as above, but the PDF can now only come from
                       python_renderer's pure-Python approximation.
    3. MS Word COM  - unavailable in this branch by definition; kept only so
                       both branches share the same fallback list.
    4. Layout estimate - see below.

  4. Layout estimate - measured pure-Python fallback (python_renderer) when no
                    real layout engine or Word markers are available, so docs
                    authored outside Word (WPS/Google Docs/DTs) still split by
                    natural text flow instead of explicit breaks only.

Every engine's result is checked with _boundaries_sane() before being
accepted; a result that fails (e.g. several consecutive pages collapsed onto
one) falls through to the next engine instead of shipping silently-corrupt
boundaries.

Results are cached on disk (keyed by file identity) so repeated exports of
the same document skip the layout engine entirely.
"""

import hashlib
import json
import os
import tempfile

from . import convert, docx_trim, lo_paginate, word_com, word_markers
from .docx_trim import build_units, _q


def _cache_dir():
    base = os.environ.get("RUNTIME_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime"
    )
    d = os.path.join(base, "paginate_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.join(tempfile.gettempdir(), "doc_trim_paginate_cache")
        os.makedirs(d, exist_ok=True)
    return d


def _cache_path(docx_path):
    st = os.stat(docx_path)
    key = hashlib.md5(
        ("%s-%s-%s" % (os.path.basename(docx_path), st.st_mtime_ns, st.st_size)).encode()
    ).hexdigest()
    return os.path.join(_cache_dir(), key + ".json")


def _cache_load(docx_path):
    try:
        with open(_cache_path(docx_path), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) == 2:
            page_count, boundaries = data
            if isinstance(page_count, int) and isinstance(boundaries, list) and boundaries and boundaries[0] != -1:
                return page_count, boundaries
    except Exception:
        pass
    return None


def _cache_save(docx_path, page_count, boundaries):
    try:
        with open(_cache_path(docx_path), "w", encoding="utf-8") as f:
            json.dump([page_count, boundaries], f)
    except Exception:
        pass


def _explicit_break_pagination(docx_path):
    """Last-resort pagination: estimate natural text flow from the document's
    own layout (page size, margins, run sizes and wrap math) instead of only
    counting explicit breaks.

    Explicit-break counting alone collapses documents whose pages flow
    naturally (no w:br/pageBreakBefore/sectPr per page) into a single page,
    which makes every subsequent range split land on the wrong content.
    """
    try:
        from . import python_renderer
        res = python_renderer.paginate_units(docx_path)
        if res is not None:
            return res
    except Exception:
        pass

    root = docx_trim.load_document_xml(docx_path)
    body = root.find(_q("body"))
    units = build_units(body)
    if not units:
        return 1, [0]

    unit_page = []
    current_page = 1
    next_new_page = False
    for u in units:
        new_page = False
        ends_section = False
        if u["kind"] == "p":
            node = u["node"]
            pPr = node.find(_q("pPr"))
            if pPr is not None:
                if pPr.find(_q("pageBreakBefore")) is not None:
                    new_page = True
                if pPr.find(_q("sectPr")) is not None:
                    ends_section = True
                    new_page = True
            if not new_page:
                for br in node.iter(_q("br")):
                    if br.get(_q("type")) == "page":
                        new_page = True
                        break
        if next_new_page:
            current_page += 1
            next_new_page = False
        elif new_page:
            current_page += 1
        if u["kind"] == "p" and ends_section:
            next_new_page = True
        unit_page.append(current_page)

    page_count = max(unit_page)
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
    return page_count, boundaries


def _forced_section_starts(docx_path):
    """Unit indices that MUST begin a new page: the first unit of every
    section whose break is nextPage/oddPage/evenPage (i.e. every section
    break except a continuous one).

    This is exact, structural knowledge straight from the OOXML - not an
    estimate - so it's used to correct whatever a layout engine measured,
    rather than being just another signal fed into the measurement.
    """
    try:
        root = docx_trim.load_document_xml(docx_path)
        body = root.find(_q("body"))
        units = build_units(body)
        sections = docx_trim._collect_sections(body, units)
    except Exception:
        return []
    forced = []
    for i in range(1, len(sections)):
        node = sections[i]["node"]
        type_elem = node.find(_q("type")) if node is not None else None
        val = type_elem.get(_q("val")) if type_elem is not None else "nextPage"
        if val in ("continuous",):
            continue
        first_unit = sections[i]["first_unit"]
        if 0 < first_unit < len(units):
            forced.append(first_unit)
    return forced


def _enforce_forced_page_starts(page_count, boundaries, forced_starts):
    """Reassign units to the following page wherever a forced section start
    (see _forced_section_starts) was measured as sharing a page with what
    precedes it.

    A layout engine's page-per-unit measurement is always an estimate (no
    public API reports Word's true rendered layout short of comparing page
    images), so it can occasionally place a couple of units on the wrong
    side of a page boundary - as seen with a low-confidence fingerprint match
    pulling a section's first few units onto the previous page. A forced
    section break removes that ambiguity entirely: Word guarantees the
    section's first unit starts a fresh page, so when the measurement has it
    sharing a page with what comes before, that page's end is pulled back to
    right before it - those units simply belong to the next page instead,
    which already exists and needs no new page inserted. Word's own page
    count is authoritative and must not change here; only a measurement
    error is being corrected, not real content reflow.
    """
    boundaries = list(boundaries)
    for first_unit in forced_starts:
        # Page (1-indexed) that unit `u` currently falls on.
        def page_of(u):
            for p, b in enumerate(boundaries):
                if b >= u:
                    return p + 1
            return len(boundaries)

        prev_page = page_of(first_unit - 1)
        cur_page = page_of(first_unit)
        if cur_page > prev_page:
            continue  # already starts its own page
        k = prev_page - 1  # 0-indexed position of the shared page
        boundaries[k] = first_unit - 1
    return page_count, boundaries


def _boundaries_sane(page_count, boundaries):
    """Reject a pagination result where too many pages have no content of
    their own (boundary equal to the previous page's).

    A layout engine that collapses several consecutive pages onto one still
    returns a structurally valid-looking (page_count, boundaries) pair, so a
    downstream split silently produces corrupt output rather than an error.
    A handful of genuinely blank pages is normal; a systematic collapse
    across many pages is a sign the engine misfired and another engine
    should be tried instead.
    """
    if not boundaries or len(boundaries) != page_count:
        return False
    if any(boundaries[i] > boundaries[i + 1] for i in range(len(boundaries) - 1)):
        return False
    distinct = len(set(boundaries))
    allowed_blank = max(1, page_count // 15)
    return distinct >= page_count - allowed_blank


def paginate_docx(docx_path, use_cache=True):
    """Return (page_count, boundaries); boundaries[page0] = last unit index."""
    cached = _cache_load(docx_path) if use_cache else None
    if cached is not None:
        return cached

    forced_starts = _forced_section_starts(docx_path)

    def fixup(res):
        if res is None:
            return None
        return _enforce_forced_page_starts(res[0], res[1], forced_starts)

    def try_pdf_backed():
        return lo_paginate.paginate_pdf_backed(docx_path)

    def try_word_com():
        if not word_com.word_available():
            return None
        return word_com.paginate(docx_path)

    def try_word_markers():
        return word_markers.paginate(docx_path)

    if word_com.word_available() or convert.soffice_available():
        # A real layout engine can back the PDF render, so try that first.
        engines = [try_pdf_backed, try_word_com, try_word_markers]
    else:
        # No real layout engine (e.g. deployed without LibreOffice): the
        # PDF-backed engine would only be backed by python_renderer's
        # approximate layout, which measured less accurate than Word's own
        # embedded page-break markers on a natural-flow test document (see
        # module docstring) - so try the markers first here instead.
        engines = [try_word_markers, try_pdf_backed, try_word_com]

    result = None
    for engine in engines:
        try:
            res = fixup(engine())
            if res is not None and res[1] and res[1][0] != -1 and _boundaries_sane(*res):
                result = res
                break
        except Exception:
            pass

    # Last resort: explicit page/section break counting.
    if result is None:
        result = fixup(_explicit_break_pagination(docx_path))

    _cache_save(docx_path, result[0], result[1])
    return result
