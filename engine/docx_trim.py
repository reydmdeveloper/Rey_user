"""High-fidelity DOCX page-range splitting.

The splitter clones the source package (styles, headers, footers, numbering,
theme, media) and rewrites only word/document.xml so that every layout element
is preserved exactly. Explicit page breaks are inserted at source page
boundaries so each output page mirrors the source page, and all trailing
"breaks" (empty paragraphs, page/line break runs, section breaks) are stripped
from the last page of every split file.
"""

import os
import re
import zipfile

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = {"w": W_NS}

DOCUMENT_PART = "word/document.xml"


def load_document_xml(path):
    """Parse word/document.xml from a docx (zip) package."""
    with zipfile.ZipFile(path) as z:
        return etree.fromstring(z.read(DOCUMENT_PART))


def _q(tag):
    return "{%s}%s" % (W_NS, tag)


def _strip_punct(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def _iter_text(elem):
    if elem.tag == _q("t"):
        yield elem.text or ""
    for child in elem:
        yield from _iter_text(child)


def _element_text(elem):
    return "".join(_iter_text(elem))


def _element_words(elem):
    return _strip_punct(_element_text(elem))


def _iter_body_content_nodes(parent, body_index):
    """Yield dicts for content units inside parent element."""
    for child in parent:
        tag = child.tag
        if tag == _q("p"):
            yield {"kind": "p", "body_index": body_index, "node": child}
        elif tag == _q("tbl"):
            rows = child.findall(_q("tr"))
            if not rows:
                yield {"kind": "tbl", "body_index": body_index, "table": child, "rows": []}
            else:
                for row in rows:
                    yield {"kind": "row", "body_index": body_index, "table": child, "row": row}
        elif tag in (_q("sdt"), _q("customXml"), _q("ins"), _q("del"), _q("smartTag")):
            sdtContent = child.find(_q("sdtContent"))
            container = sdtContent if sdtContent is not None else child
            yield from _iter_body_content_nodes(container, body_index)


def build_units(body):
    """Build a flat list of content units (paragraphs and table rows).

    Each unit is a dict: {'kind': 'p'|'row', 'body_index', 'node'|('table','row')}
    """
    if body is None:
        return []
    units = []
    for idx, child in enumerate(body):
        units.extend(list(_iter_body_content_nodes([child], idx)))
    return units


def unit_words(unit):
    if unit["kind"] == "p":
        return _element_words(unit["node"])
    return _element_words(unit["row"])


def _make_page_break_paragraph():
    p = etree.fromstring(
        '<w:p xmlns:w="%s"><w:r><w:br w:type="page"/></w:r></w:p>' % W_NS
    )
    return p


def _add_page_break_before(paragraph):
    pPr = paragraph.find(_q("pPr"))
    if pPr is None:
        pPr = etree.SubElement(paragraph, _q("pPr"))
        paragraph.insert(0, pPr)
    if pPr.find(_q("pageBreakBefore")) is None:
        pb = etree.Element(_q("pageBreakBefore"))
        pPr.insert(0, pb)


def _has_text(elem):
    for t in elem.iter(_q("t")):
        if (t.text or "").strip():
            return True
    return False


def _paragraph_has_only_breaks(paragraph):
    """True if the paragraph has no text and only page/line break runs."""
    if _has_text(paragraph):
        return False
    pPr = paragraph.find(_q("pPr"))
    if pPr is not None and pPr.find(_q("sectPr")) is not None:
        return False  # section break paragraph is handled separately
    for r in paragraph.findall(_q("r")):
        for br in r.findall(_q("br")):
            if br.get(_q("type")) in (None, "textWrapping", "page"):
                continue
        # run with drawing/pict etc. is content
        if r.find(_q("drawing")) is not None or r.find(_q("pict")) is not None or r.find(_q("object")) is not None:
            return False
    return True


def _strip_trailing_break_runs(paragraph):
    """Remove trailing page-break/line-break runs from the end of a paragraph.

    Also removes a page/line break that sits at the very end of the last run,
    even when that run also contains text before the break.
    """
    runs = paragraph.findall(_q("r"))
    while runs:
        r = runs[-1]
        # strip a trailing break that is the last child of this run
        children = list(r)
        if children and children[-1].tag == _q("br"):
            last = children[-1]
            if last.get(_q("type")) in (None, "textWrapping", "page"):
                r.remove(last)
                if not list(r):
                    paragraph.remove(r)
                    runs = paragraph.findall(_q("r"))
                continue
        has_br = any(
            br.get(_q("type")) in (None, "textWrapping", "page")
            for br in r.findall(_q("br"))
        )
        has_text = _has_text(r)
        has_drawing = r.find(_q("drawing")) is not None or r.find(_q("pict")) is not None
        if has_text or has_drawing or not has_br:
            break
        paragraph.remove(r)
        runs = paragraph.findall(_q("r"))


def _strip_trailing_empty_rows(table):
    rows = table.findall(_q("tr"))
    while rows:
        r = rows[-1]
        if _has_text(r):
            break
        if r.find(_q("tc")) is None:
            break
        table.remove(r)
        rows = table.findall(_q("tr"))


def _is_section_break_paragraph(paragraph):
    pPr = paragraph.find(_q("pPr"))
    if pPr is None:
        return False
    return pPr.find(_q("sectPr")) is not None


def _find_final_sectpr(body):
    children = list(body)
    if children and children[-1].tag == _q("sectPr"):
        return children[-1]
    return None


def deepcopy_or_none(node):
    return etree.fromstring(etree.tostring(node))


def _collect_sections(body, units):
    """Return the document's sections in order.

    Each entry is {'node': sectPr element, 'first_unit': first unit index,
    'last_unit': last unit index} of the section governed by that sectPr.
    """
    children = list(body)
    sections = []
    cur_first = 0
    for idx, child in enumerate(children):
        if child.tag == _q("sectPr"):
            sections.append({"node": child, "first_unit": cur_first, "last_unit": len(units) - 1})
            return sections
        uis = [ui for ui, u in enumerate(units) if u["body_index"] == idx]
        if not uis:
            continue
        if child.tag == _q("p"):
            pPr = child.find(_q("pPr"))
            sectPr = pPr.find(_q("sectPr")) if pPr is not None else None
            if sectPr is not None:
                sections.append({"node": sectPr, "first_unit": cur_first, "last_unit": uis[-1]})
                cur_first = uis[-1] + 1
    final = _find_final_sectpr(body)
    if final is not None:
        sections.append({"node": final, "first_unit": cur_first, "last_unit": len(units) - 1})
    return sections


def _section_containing(sections, unit_index):
    for i, s in enumerate(sections):
        if s["last_unit"] >= unit_index:
            return i
    return len(sections) - 1


def _unit_page(boundaries, unit_index):
    """1-indexed source page that a unit index starts on."""
    for p, b in enumerate(boundaries):
        if b >= unit_index:
            return p + 1
    return len(boundaries) or 1


def _governing_sectpr(sections, units, ue):
    """Return (sectPr node, section index) governing the last kept content unit.

    The section containing unit ue is the one whose sectPr must become the
    body-level sectPr of the output so its headers/footers match the source.
    """
    if not sections:
        return None, None
    if ue >= len(units):
        return sections[-1]["node"], len(sections) - 1
    for i, s in enumerate(sections):
        if s["first_unit"] <= ue <= s["last_unit"]:
            return s["node"], i
    return sections[-1]["node"], len(sections) - 1


def _resolve_section_headers(sectpr, sections, index):
    """Materialize inherited (link-to-previous) header/footer references.

    In OOXML a section whose sectPr has no header/footer reference of a given
    type is 'linked to previous' in Word: the reference of the nearest earlier
    section that defines that slot applies. After a split, later sections are
    detached from that chain, so the inherited references must be copied into
    the output sectPr or the header/footer silently disappears on the last
    (and any later) split file. The rIds remain valid because the whole
    package (parts + rels) is cloned into the output.
    """
    for kind in ("headerReference", "footerReference"):
        for slot in ("default", "even", "first"):
            if any(r.get(_q("type")) == slot for r in sectpr.findall(_q(kind))):
                continue
            j = index - 1
            while j >= 0:
                node = sections[j]["node"]
                ref = None
                if node is not None:
                    for r in node.findall(_q(kind)):
                        if r.get(_q("type")) == slot:
                            ref = r
                            break
                if ref is not None:
                    sectpr.insert(0, deepcopy_or_none(ref))
                    break
                j -= 1


def _swap_even_odd(sectpr):
    """Swap even/odd header refs so page parity stays aligned with the source."""
    for kind in ("headerReference", "footerReference"):
        refs = sectpr.findall(_q(kind))
        def_ = even = None
        for r in refs:
            t = r.get(_q("type"))
            if t == "default":
                def_ = r
            elif t == "even":
                even = r
        if def_ is not None and even is not None:
            def_copy = deepcopy_or_none(def_)
            even_copy = deepcopy_or_none(even)
            def_copy.set(_q("type"), "even")
            even_copy.set(_q("type"), "default")
            sectpr.replace(def_, even_copy)
            sectpr.replace(even, def_copy)


def _adjust_first_section(sectpr, start_page, first_page_of_section, forced_parity=False):
    """Fix the first section of a split that starts mid-source-section.

    When the output's first page is not the first page of its section in the
    source, the 'different first page' header must not be used, and when the
    source start page is even the even/odd headers must be swapped so every
    output page keeps the exact header the source page had.

    forced_parity: True when the section is introduced by an oddPage/evenPage
    section break. Word then pins the section's first page to an absolute
    (odd/even) page number, so the even/odd slots keep matching the source
    without a swap.
    """
    if start_page != first_page_of_section:
        tp = sectpr.find(_q("titlePg"))
        if tp is not None:
            sectpr.remove(tp)
        for r in list(sectpr.findall(_q("headerReference")) + sectpr.findall(_q("footerReference"))):
            if r.get(_q("type")) == "first":
                sectpr.remove(r)
    if not forced_parity and start_page % 2 == 0 and sectpr.find(_q("evenAndOddHeaders")) is not None:
        _swap_even_odd(sectpr)


def _apply_parity_swap(sectpr, start_page, forced_parity=False):
    if not forced_parity and start_page % 2 == 0 and sectpr.find(_q("evenAndOddHeaders")) is not None:
        _swap_even_odd(sectpr)


def _section_forced_parity(sections, index):
    """True when the section starts on a layout-pinned odd/even page."""
    node = sections[index]["node"]
    if node is None:
        return False
    type_elem = node.find(_q("type"))
    if type_elem is not None and type_elem.get(_q("val")) in ("oddPage", "evenPage"):
        return True
    return False


def _build_new_body(original_body, us, ue, break_unit_indices, boundaries, start_page):
    """Build a new <w:body> containing only units in [us, ue] with page breaks."""
    units = build_units(original_body)
    children = list(original_body)

    body_units = {}
    for ui, u in enumerate(units):
        body_units.setdefault(u["body_index"], []).append(ui)

    sections = _collect_sections(original_body, units)
    section_of = {id(s["node"]): i for i, s in enumerate(sections)}
    output_sections = []  # (sectPr element in new body, source section index)

    new_body = etree.Element(_q("body"))
    new_children = []

    for i, child in enumerate(children):
        if child.tag == _q("sectPr"):
            continue  # final sectPr re-added at the end
        uis = body_units.get(i, [])
        if not uis:
            continue
        if child.tag == _q("p"):
            ui = uis[0]
            if not (us <= ui <= ue):
                continue
            orig_sectpr = None
            pPr = child.find(_q("pPr"))
            if pPr is not None:
                orig_sectpr = pPr.find(_q("sectPr"))
            new_p = etree.fromstring(etree.tostring(child))
            if ui in break_unit_indices:
                _add_page_break_before(new_p)
            if orig_sectpr is not None:
                np_pr = new_p.find(_q("pPr"))
                new_sectpr = np_pr.find(_q("sectPr"))
                src_idx = section_of.get(id(orig_sectpr))
                if src_idx is not None and new_sectpr is not None:
                    output_sections.append((new_sectpr, src_idx))
            new_children.append(new_p)
        elif child.tag == _q("tbl"):
            kept = [ui for ui in uis if us <= ui <= ue]
            if not kept:
                continue
            # split into segments at break boundaries
            segments = []
            cur = []
            for ui in kept:
                if ui in break_unit_indices and cur:
                    segments.append(cur)
                    cur = [ui]
                else:
                    cur.append(ui)
            if cur:
                segments.append(cur)

            for s_idx, seg in enumerate(segments):
                if s_idx > 0:
                    new_children.append(_make_page_break_paragraph())
                seg_tbl = etree.fromstring(etree.tostring(child))
                keep_set = set(seg)
                row_nodes = seg_tbl.findall(_q("tr"))
                for rn in row_nodes:
                    seg_tbl.remove(rn)
                for j, ui in enumerate(uis):
                    if ui in keep_set:
                        seg_tbl.append(row_nodes[j])
                new_children.append(seg_tbl)
        else:
            kept = [ui for ui in uis if us <= ui <= ue]
            if kept:
                new_children.append(etree.fromstring(etree.tostring(child)))

    # Trailing break removal on the last page ---------------------------------
    replacement_final_sectpr = None
    replacement_final_index = None
    while new_children:
        last = new_children[-1]
        if last.tag == _q("p"):
            if _is_section_break_paragraph(last):
                pPr = last.find(_q("pPr"))
                sectPr = pPr.find(_q("sectPr"))
                replacement_final_sectpr = deepcopy_or_none(sectPr)
                if output_sections:
                    replacement_final_index = output_sections.pop()[1]
                new_children.pop()
                continue
            if _has_text(last):
                _strip_trailing_break_runs(last)
                break
            if _paragraph_has_only_breaks(last):
                new_children.pop()
                continue
            _strip_trailing_break_runs(last)
            break
        elif last.tag == _q("tbl"):
            _strip_trailing_empty_rows(last)
            if _has_text(last):
                break
            new_children.pop()
        else:
            new_children.pop()

    # Final section properties -------------------------------------------------
    final_sectpr = None
    final_src_index = None
    if replacement_final_sectpr is not None:
        final_sectpr = replacement_final_sectpr
        final_src_index = replacement_final_index
    else:
        governing_node, gov_index = _governing_sectpr(sections, units, ue)
        final_node = _find_final_sectpr(original_body)
        if governing_node is not None and governing_node is not final_node:
            final_sectpr = deepcopy_or_none(governing_node)
            final_src_index = gov_index
        elif final_node is not None:
            final_sectpr = deepcopy_or_none(final_node)
            final_src_index = len(sections) - 1 if sections else None

    for nc in new_children:
        new_body.append(nc)
    if final_sectpr is not None:
        # Only collapse a page-starting break to "continuous" when this
        # trailing sectPr is the sole section of the output (nothing earlier
        # in the body to break away from). If an earlier section break
        # survived the trim (output_sections is non-empty), the break here is
        # real and must be kept, or the two kept sections silently merge onto
        # the same page.
        if not output_sections:
            type_elem = final_sectpr.find(_q("type"))
            if type_elem is not None and type_elem.get(_q("val")) in ("nextPage", "oddPage", "evenPage"):
                type_elem.set(_q("val"), "continuous")
        new_body.append(final_sectpr)
        output_sections.append((final_sectpr, final_src_index))

    # Header/footer fidelity ---------------------------------------------------
    # 1) Materialize inherited (link-to-previous) header/footer references so
    #    every split keeps exactly the headers/footers the source page had,
    #    even when the range falls on sections that inherit from earlier ones.
    # 2) Fix "different first page" and even/odd parity for ranges that start
    #    in the middle of a source section.
    if sections and output_sections:
        for el, idx in output_sections:
            _resolve_section_headers(el, sections, idx)
        first_el, idx0 = output_sections[0]
        sec_k = _section_containing(sections, us)
        first_page_of_sec = _unit_page(boundaries, sections[sec_k]["first_unit"])
        _adjust_first_section(first_el, start_page, first_page_of_sec,
                              _section_forced_parity(sections, idx0))
        for el, idx in output_sections[1:]:
            _apply_parity_swap(el, start_page, _section_forced_parity(sections, idx))

    return new_body


def load_package(src_docx):
    """Read a docx package once: parsed document.xml root + raw bytes of every
    other part. The result is reused across all page ranges of an export so
    splitting many ranges does not re-read and re-compress the whole package
    for every output file.
    """
    with zipfile.ZipFile(src_docx) as zin:
        root = _parse_xml(zin, DOCUMENT_PART)
        parts = {name: zin.read(name) for name in zin.namelist() if name != DOCUMENT_PART}
    return root, parts


def split_docx_range(src_docx, start_page, end_page, boundaries, out_path, package_cache=None):
    """Split src_docx into a new file containing source pages start_page..end_page.

    boundaries: list where boundaries[page0] = last unit index of that page.
    package_cache: optional (root, parts) from load_package() to avoid re-reading
    the source package when splitting several ranges of the same document.
    """
    page_count = len(boundaries)
    if start_page < 1:
        start_page = 1
    if end_page > page_count:
        end_page = page_count
    if start_page > page_count:
        start_page = page_count
    if end_page < start_page:
        raise ValueError(f"No content on pages {start_page}-{end_page}.")

    us = (boundaries[start_page - 2] + 1) if start_page > 1 else 0
    ue = boundaries[end_page - 1]
    if ue < us:
        raise ValueError(f"No content on pages {start_page}-{end_page}.")

    if package_cache is None:
        root, parts = load_package(src_docx)
    else:
        root, parts = package_cache
    root = deepcopy_or_none(root)

    # Deliberately not forcing a page break at every *internal* page boundary
    # here. A forced break was previously inserted at every measured page
    # boundary within the range so each output page would line up with the
    # corresponding source page - but that makes every split as precise as
    # the weakest page-boundary measurement in the whole range. Layout
    # engines only ever estimate page boundaries (no reliable API reports
    # Word's true layout without full page-image comparison), and even a
    # single mis-measured boundary - e.g. a one- or two-word paragraph whose
    # fingerprint is too short to place confidently - forces an artificial
    # break where the source had none, which can shorten a page and push its
    # tail content onto a spurious extra page (observed: a two-page range
    # rendered as three pages because a low-confidence boundary forced a
    # break before content that, in the source, shared the bottom of the
    # previous page).
    #
    # The output keeps *exactly* the source's paragraphs/rows and section
    # properties for [us, ue], unchanged and in order, with no forced breaks
    # of our own - only whatever explicit page/section breaks the source
    # itself already contains. Word's layout is deterministic given content
    # and formatting, so reflowing that unchanged content from its own first
    # page reproduces the source's internal page breaks on its own, without
    # depending on our boundary measurement's precision at every page in the
    # middle of the range - only at the two ends, to select the right slice.
    break_unit_indices = set()

    _rewrite_document_xml(root, parts, out_path, us, ue, break_unit_indices, start_page, end_page, boundaries)


def _parse_xml(zf, path):
    return etree.fromstring(zf.read(path))


def _rewrite_document_xml(root, parts, out_path, us, ue, break_unit_indices, start_page, end_page, boundaries):
    tmp = out_path + ".tmp"
    body = root.find(_q("body"))
    new_body = _build_new_body(body, us, ue, break_unit_indices, boundaries, start_page)
    root.replace(body, new_body)

    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr(DOCUMENT_PART, xml)
        for name, data in parts.items():
            if name == "docProps/core.xml":
                updated = _try_set_title(data, out_path)
                zout.writestr(name, updated if updated is not None else data)
            else:
                zout.writestr(name, data)
    if os.path.exists(out_path):
        os.remove(out_path)
    os.replace(tmp, out_path)


def _try_set_title(core_xml, out_path):
    """Best-effort set of the dc:title in docProps/core.xml. Returns bytes or None."""
    try:
        root = etree.fromstring(core_xml)
    except Exception:
        return None
    title = os.path.splitext(os.path.basename(out_path))[0]
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    el = root.find("dc:title", ns)
    if el is None:
        el = etree.SubElement(root, "{http://purl.org/dc/elements/1.1/}title")
    el.text = title
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
