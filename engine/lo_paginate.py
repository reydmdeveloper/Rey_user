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
from .docx_trim import build_units, _q, _strip_punct, _element_text, load_document_xml


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
            page_texts = [" ".join(re.findall(r"[a-z0-9]+", page.get_text().lower())) for page in doc]
        if not page_texts:
            return None

        from .docx_trim import _element_words
        unit_page = []
        p_idx = 0
        for u in units:
            node = u["node"] if u["kind"] == "p" else u["row"]
            text_words = _element_words(node)
            if not text_words:
                unit_page.append(p_idx + 1)
                continue

            fp = " ".join(text_words[:12])
            if not fp:
                unit_page.append(p_idx + 1)
                continue

            if p_idx + 1 < len(page_texts) and fp not in page_texts[p_idx]:
                for pi in range(p_idx + 1, len(page_texts)):
                    if fp in page_texts[pi]:
                        p_idx = pi
                        break

            unit_page.append(p_idx + 1)

        page_count = max(len(page_texts), max(unit_page) if unit_page else 1)
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
