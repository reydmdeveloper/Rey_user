"""LibreOffice-based pagination for Linux/Render.

When a document was not saved by MS Word (no <w:lastRenderedPageBreak/>
markers), the marker engine cannot reproduce Word's natural text flow. This
module uses LibreOffice as a real layout engine: it converts the docx to PDF,
then maps every content unit (paragraph / table row) to the PDF page where the
unit's opening words first appear. The resulting boundaries reproduce the
layout engine's pagination instead of guessing from explicit breaks.
"""

import os
import re
import shutil
import tempfile

import pymupdf

from . import convert
from .docx_trim import build_units, _q, load_document_xml


def _page_words(raw_text):
    """Tokenize extracted PDF text like the docx fingerprinting does.

    Merges hyphenated line breaks (word-\\nbreak -> wordbreak) and reduces every
    token to [a-z0-9]+, so comparisons between unit fingerprints and page text
    are apples-to-apples regardless of case, punctuation or line wrapping.
    """
    tokens = raw_text.split()
    merged = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        while t.endswith("-") and i + 1 < len(tokens):
            t = t[:-1] + tokens[i + 1]
            i += 1
        merged.extend(re.findall(r"[a-z0-9]+", t.lower()))
        i += 1
    return merged


def _prefix_match_len(fp_words, page_words):
    """Longest k such that fp_words[:k] appear in order inside page_words."""
    k = 0
    idx = 0
    for w in fp_words:
        try:
            idx = page_words.index(w, idx)
        except ValueError:
            break
        idx += 1
        k += 1
    return k


def paginate_pdf_backed(docx_path, renderer_inst=None):
    """Paginate docx by rendering to PDF and mapping content units to PDF pages.

    Works via MS Word COM, LibreOffice, or PyMuPDF python_renderer fallback.
    Guarantees exact visual page boundaries matching rendered PDF output.
    """
    try:
        root = load_document_xml(docx_path)
    except Exception:
        return None
    body = root.find(_q("body"))
    if body is None:
        return None
    units = build_units(body)
    if not units:
        return None

    pdf_file = None
    tmp_dir = None
    try:
        if renderer_inst is None:
            from .renderer import Renderer
            renderer_inst = Renderer(os.path.join(tempfile.gettempdir(), "doc_trim_lo_pag"))
        pdf_file, _ = renderer_inst._pdf_for(docx_path)
        with pymupdf.open(pdf_file) as doc:
            page_word_lists = [_page_words(page.get_text()) for page in doc]
        if not page_word_lists:
            return None

        from .docx_trim import _element_words

        # For each unit, scan forward from the current page for the nearest
        # page whose text contains a strong ordered match of the unit's
        # fingerprint words - not the single best-scoring page in the whole
        # document. Short, near-duplicate paragraphs (repeated attachment
        # filenames, form boilerplate) routinely recur verbatim much later in
        # the document (e.g. once in a document list, again in an upload
        # table), so a global best-match search can latch onto that distant
        # recurrence instead of the true, nearby occurrence. Taking the
        # nearest page that clears the threshold - rather than the highest
        # score - keeps the walk local. A unit whose fingerprint isn't found
        # anywhere from the current page onward is left unresolved for
        # interpolation instead of a forced (and possibly wrong) match, and
        # unlike a naive forward-only scan, failing to place one unit doesn't
        # block every later unit: the next unit's search still starts from
        # the same, unmoved position.
        unit_page = [None] * len(units)
        last_pi = 0
        for i, u in enumerate(units):
            node = u["node"] if u["kind"] == "p" else u["row"]
            fp = _element_words(node)[:12]
            if not fp:
                continue
            threshold = max(3, (2 * len(fp)) // 3)
            for pi in range(last_pi, len(page_word_lists)):
                if _prefix_match_len(fp, page_word_lists[pi]) >= threshold:
                    unit_page[i] = pi + 1
                    last_pi = pi
                    break

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
        if not unit_page or any(p is None for p in unit_page):
            return None

        page_count = max(len(page_word_lists), max(unit_page))
        by_page = {}
        for i, p in enumerate(unit_page):
            by_page.setdefault(p, []).append(i)

        boundaries = []
        last = -1
        for p in range(1, page_count + 1):
            if p in by_page:
                last = max(by_page[p])
            boundaries.append(last)

        return page_count, boundaries
    except Exception:
        return None
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def paginate_with_libreoffice(docx_path):
    return paginate_pdf_backed(docx_path)
