"""Page-boundary detection for Word documents.

Engine selection (in priority order):
  1. MS Word COM  - Word's own layout engine (Windows only). Exact fidelity.
  2. PDF-backed   - LibreOffice/PyMuPDF layout reproduction (Linux/Render).
  3. Word markers - <w:lastRenderedPageBreak/> markers Word embeds in docx
                    files, reproducing Word's pagination on Linux/Render.
  4. Layout estimate - measured pure-Python fallback (python_renderer) when no
                    real layout engine or Word markers are available, so docs
                    authored outside Word (WPS/Google Docs/DTs) still split by
                    natural text flow instead of explicit breaks only.

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


def paginate_docx(docx_path, use_cache=True):
    """Return (page_count, boundaries); boundaries[page0] = last unit index."""
    cached = _cache_load(docx_path) if use_cache else None
    if cached is not None:
        return cached

    result = None
    # 1) MS Word COM (Windows, exact Word layout, no PDF render needed).
    if word_com.word_available():
        try:
            res = word_com.paginate(docx_path)
            if res is not None and res[1] and res[1][0] != -1:
                result = res
        except Exception:
            pass

    # 2) PDF-backed layout pagination (LibreOffice / PyMuPDF, Linux/Render).
    if result is None:
        try:
            res = lo_paginate.paginate_pdf_backed(docx_path)
            if res is not None and res[1] and res[1][0] != -1:
                result = res
        except Exception:
            pass

    # 3) Word's own recorded pagination markers.
    if result is None:
        try:
            res = word_markers.paginate(docx_path)
            if res is not None and res[1] and res[1][0] != -1:
                result = res
        except Exception:
            pass

    # 4) Explicit page/section break counting.
    if result is None:
        result = _explicit_break_pagination(docx_path)

    _cache_save(docx_path, result[0], result[1])
    return result
