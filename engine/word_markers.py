"""Pagination for Linux/Render using structural page breaks.

Combines two Word-derived signals to reproduce MS Word pagination without
running Word:
  * <w:lastRenderedPageBreak/> markers - Word records exactly where pages broke
    during its last render (captures natural text overflow).
  * explicit page/section breaks - <w:br w:type="page"/>, w:pageBreakBefore and
    section breaks (captures intentional breaks Word may not re-mark).

Returns None when there is no explicit break and no marker at all, so the
caller can fall back to explicit-break counting.
"""

from lxml import etree

from .docx_trim import build_units, _q, load_document_xml


def _unit_break_signals(node):
    """Return (start_new_page, end_new_page) signals for a unit node.

    start_new_page: this unit itself begins a new page.
    end_new_page:   a break occurs inside/after this unit, so the NEXT unit
                    begins a new page.
    """
    has_text = False
    explicit_page_break = False
    for el in node.iter():
        if el.tag == _q("t"):
            if (el.text or "").strip():
                has_text = True
        elif el.tag == _q("br") and el.get(_q("type")) == "page":
            explicit_page_break = True

    start_new = False
    end_new = False
    pPr = node.find(_q("pPr")) if node.tag == _q("p") else None
    if pPr is not None:
        if pPr.find(_q("pageBreakBefore")) is not None:
            start_new = True
        if pPr.find(_q("sectPr")) is not None:
            end_new = True

    if explicit_page_break:
        # An explicit page break is authoritative: the current page ends at/after
        # this unit and the next unit starts a new page. Markers around it are
        # the same break and must not be double-counted.
        end_new = True
        return start_new, end_new

    # No explicit break: use Word's recorded render markers for natural overflow.
    has_text = False
    for el in node.iter():
        if el.tag == _q("t"):
            if (el.text or "").strip():
                has_text = True
        elif el.tag == _q("lastRenderedPageBreak"):
            if has_text:
                end_new = True
            else:
                start_new = True
    return start_new, end_new


def paginate(docx_path):
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

    found_marker = bool(body.findall(".//" + _q("lastRenderedPageBreak")))
    found_explicit = False
    for u in units:
        node = u["node"] if u["kind"] == "p" else u["row"]
        s, e = _unit_break_signals(node)
        if s or e:
            found_explicit = True
            break
    if not (found_marker or found_explicit):
        return None

    unit_page = []
    current_page = 1
    next_new_page = False
    for u in units:
        node = u["node"] if u["kind"] == "p" else u["row"]
        start_new, end_new = _unit_break_signals(node)

        if next_new_page:
            # A unit that itself ends the page belongs to the page that is
            # ending (e.g. a section-break paragraph right after a page break).
            if not end_new:
                current_page += 1
                next_new_page = False
        elif start_new:
            current_page += 1
        unit_page.append(current_page)
        if end_new:
            next_new_page = True

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
