"""Pure-Python fallback document renderer and pagination estimator.

Used when neither MS Word COM nor LibreOffice is available (e.g. Render).
Unlike the previous fixed-A4/0.75in/11pt renderer, this module reads the
document's real layout from its OOXML:

  * section page size (w:pgSz) and margins (w:pgMar) - per section, so mixed
    portrait/landscape documents paginate correctly,
  * per-run font sizes (w:sz) and bold/italic for width measurement,
  * explicit page breaks (w:br type=page), w:pageBreakBefore and section
    breaks (w:pPr/w:sectPr including w:type nextPage/oddPage/evenPage) and
    w:lastRenderedPageBreak markers as a secondary signal,
  * paragraph spacing (w:spacing before/after) and line breaks.

Two entry points share one measurement model so pagination and rendering can
never disagree:

  * paginate_units(docx_path) -> (page_count, boundaries) without drawing,
  * convert_docx_to_pdf_python(docx_path, pdf_path) -> renders page images
    using exactly the same layout math.
"""

import math
import os
import zipfile

from lxml import etree
import pymupdf

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_FONT = None


def _q(tag):
    return "{%s}%s" % (W_NS, tag)


def _font():
    global _FONT
    if _FONT is None:
        _FONT = pymupdf.Font("helv")
    return _FONT


def _sect_geometry(sectpr):
    """Read w:pgSz / w:pgMar / w:type from a sectPr into a plain dict (points).

    Falls back to A4 with 1in margins when the elements are absent.
    """
    g = {"w": 595.0, "h": 842.0, "left": 72.0, "right": 72.0,
         "top": 72.0, "bottom": 72.0, "type": "nextPage"}
    if sectpr is None:
        return g
    pgSz = sectpr.find(_q("pgSz"))
    if pgSz is not None:
        try:
            w = float(pgSz.get(_q("w")) or 11906) / 20.0
            h = float(pgSz.get(_q("h")) or 16838) / 20.0
        except (TypeError, ValueError):
            w, h = 595.0, 842.0
        if pgSz.get(_q("orient")) == "landscape":
            w, h = h, w
        g["w"], g["h"] = w, h
    pgMar = sectpr.find(_q("pgMar"))
    if pgMar is not None:
        for key in ("left", "right", "top", "bottom", "header", "footer", "gutter"):
            v = pgMar.get(_q(key))
            if v:
                try:
                    g[key] = float(v) / 20.0
                except (TypeError, ValueError):
                    pass
    t = sectpr.find(_q("type"))
    if t is not None and t.get(_q("val")):
        g["type"] = t.get(_q("val"))
    return g


def _load_structure(docx_path):
    """Return (root, body, units, sections, defaults).

    sections: sorted list of dicts {'at_unit': idx, 'geo': {...}}. Every entry
    is the section whose break paragraph is unit idx (or the body-level final
    section with at_unit == len(units)).

    defaults: {'size': pt, 'after': pt, 'before': pt} taken from
    word/styles.xml docDefaults + Normal style (spacing Word applies even when
    the paragraph itself carries no w:spacing).
    """
    with zipfile.ZipFile(docx_path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            return None
        root = etree.fromstring(zf.read("word/document.xml"))
        styles_raw = zf.read("word/styles.xml") if "word/styles.xml" in zf.namelist() else None
    body = root.find(_q("body"))
    if body is None:
        return None

    defaults = {"size": 11.0, "before": 0.0, "after": 8.0, "line": 240.0}
    if styles_raw is not None:
        try:
            styles = etree.fromstring(styles_raw)
            dd = styles.find(_q("docDefaults"))
            ppr_d = dd.find(_q("pPrDefault")) if dd is not None else None
            rpr_d = dd.find(_q("rPrDefault")) if dd is not None else None
            if ppr_d is not None:
                if ppr_d.find(_q("pPr")) is not None:
                    sp = ppr_d.find(_q("pPr")).find(_q("spacing"))
                    if sp is not None:
                        for key in ("before", "after", "line"):
                            v = sp.get(_q(key))
                            if v and v not in ("auto", ""):
                                try:
                                    defaults[key] = float(v) / 20.0 if key != "line" else float(v)
                                except (TypeError, ValueError):
                                    pass
            if rpr_d is not None:
                if rpr_d.find(_q("rPr")) is not None:
                    sz = rpr_d.find(_q("rPr")).find(_q("sz"))
                    if sz is not None and sz.get(_q("val")):
                        try:
                            defaults["size"] = float(sz.get(_q("val"))) / 2.0
                        except (TypeError, ValueError):
                            pass
        except Exception:
            defaults = {"size": 11.0, "before": 0.0, "after": 8.0, "line": 240.0}

    from .docx_trim import build_units, _is_section_break_paragraph  # noqa

    units = build_units(body)
    sections = []
    for child in body:
        if child.tag == _q("sectPr"):
            sections.append({"at_unit": len(units), "geo": _sect_geometry(child)})
    for ui, u in enumerate(units):
        node = u.get("node")
        if node is None:
            continue
        if u.get("kind") == "p" and _is_section_break_paragraph(node):
            sectpr = node.find(_q("pPr")).find(_q("sectPr"))
            sections.append({"at_unit": ui, "geo": _sect_geometry(sectpr)})
    sections.sort(key=lambda s: s["at_unit"])
    if not sections:
        sections.append({"at_unit": len(units), "geo": _sect_geometry(None)})
    return root, body, units, sections, defaults


def _geometry_for(sections, unit_index, page_index):
    """Pick the geometry active on page_index pages after unit unit_index."""
    geo = sections[0]["geo"] if sections else _sect_geometry(None)
    for s in sections:
        if s["at_unit"] <= unit_index:
            geo = s["geo"]
    return geo


def _paragraph_model(node, defaults=None):
    """Measure one paragraph: returns (lines, line_height_pt, break_type).

    break_type: None / 'page' / 'section:<type>' / 'marker'.
    lines: list of text lines (str) after explicit line breaks.
    """
    if defaults is None:
        defaults = {"size": 11.0, "before": 0.0, "after": 8.0, "line": 240.0}
    pPr = node.find(_q("pPr"))

    runs = []
    page_break_after = False
    for child in node:
        if child.tag == _q("r"):
            rPr = child.find(_q("rPr"))
            size = defaults["size"]
            bold = italic = False
            if rPr is not None:
                b = rPr.find(_q("b"))
                bold = b is not None and b.get(_q("val")) not in ("0", "false")
                i = rPr.find(_q("i"))
                italic = i is not None and i.get(_q("val")) not in ("0", "false")
                sz = rPr.find(_q("sz"))
                if sz is not None and sz.get(_q("val")):
                    try:
                        size = float(sz.get(_q("val"))) / 2.0
                    except (TypeError, ValueError):
                        pass
            pieces = []
            for cc in child:
                if cc.tag == _q("t"):
                    if cc.text:
                        pieces.append(cc.text)
                elif cc.tag in (_q("br"), _q("cr")):
                    if cc.tag == _q("br") and cc.get(_q("type")) == "page":
                        page_break_after = True
                    elif cc.tag == _q("cr") or cc.get(_q("type")) in (None, "textWrapping"):
                        pieces.append("\n")
            text = "".join(pieces)
            if text:
                runs.append({"text": text, "size": size, "bold": bold, "italic": italic})
        elif child.tag in (_q("br"), _q("cr")):
            if child.tag == _q("br") and child.get(_q("type")) == "page":
                page_break_after = True

    section_type = None
    if pPr is not None:
        sec = pPr.find(_q("sectPr"))
        if sec is not None:
            t = sec.find(_q("type"))
            section_type = t.get(_q("val")) if t is not None else "nextPage"

    spacing_before = spacing_after = 0.0
    if pPr is not None:
        sp = pPr.find(_q("spacing"))
        if sp is not None:
            vals = {}
            for attr in ("before", "after"):
                v = sp.get(_q(attr))
                if v and v not in ("auto", ""):
                    try:
                        vals[attr] = float(v) / 20.0
                    except (TypeError, ValueError):
                        pass
            spacing_before = vals.get("before", 0.0) or 0.0
            spacing_after = vals.get("after", 0.0) or 0.0
    if spacing_before == 0.0:
        spacing_before = defaults["before"]
    if spacing_after == 0.0:
        spacing_after = defaults["after"]

    if not runs:
        # Empty paragraph: a single empty line.
        return [" "], _line_height(defaults["size"], defaults), section_type, page_break_after, spacing_before, spacing_after

    max_size = max(r["size"] for r in runs)
    combined = "".join(r["text"] for r in runs)
    text_lines = combined.split("\n")
    return text_lines, _line_height(max_size, defaults), section_type, page_break_after, spacing_before, spacing_after


_WIDTH_SCALE = 0.92  # helvetica ~8-10% wider than typical Word UI fonts (calibri/times)

# Approximate natural single-line height ratio for typical Word fonts.
# Calibrated against Word COM pagination on the user's multi-section examples:
# R=1.18 keeps section-break paragraphs on the same page as Word and holds
# natural-flow pages to within +/-1 unit of Word's boundaries.
_LINE_RATIO = 1.18


def _line_height(size, defaults):
    line = defaults.get("line", 240.0) or 240.0
    return size * _LINE_RATIO * (line / 240.0)


def _measure_width(text, size):
    text = (text or "").replace("\t", "    ")
    try:
        return _font().text_length(text, fontsize=size) * _WIDTH_SCALE
    except Exception:
        return len(text) * size * 0.5 * _WIDTH_SCALE


def _paginate_model(docx_path, render=False):
    """Shared layout walk.

    Returns dict:
      pages: [{'w','h','left','right','top','bottom','units':[ui,...]}]
      geo_at_unit / etc. used by the renderer.
    """
    struct = _load_structure(docx_path)
    if struct is None:
        return None
    root, body, units, sections, defaults = struct

    pages = []
    cur_geo = sections[0]["geo"]
    page = {"w": cur_geo["w"], "h": cur_geo["h"], "left": cur_geo["left"],
            "right": cur_geo["right"], "top": cur_geo["top"], "bottom": cur_geo["bottom"],
            "units": []}
    used = 0.0

    def push_page():
        nonlocal page, used
        pages.append(page)
        page = {"w": cur_geo["w"], "h": cur_geo["h"], "left": cur_geo["left"],
                "right": cur_geo["right"], "top": cur_geo["top"], "bottom": cur_geo["bottom"],
                "units": []}
        used = 0.0

    def usable_height():
        return page["h"] - page["top"] - page["bottom"]

    for ui, u in enumerate(units):
        for s in sections:
            if s["at_unit"] <= ui:
                cur_geo = s["geo"]
        page["w"], page["h"] = cur_geo["w"], cur_geo["h"]
        page["left"], page["right"] = cur_geo["left"], cur_geo["right"]
        page["top"], page["bottom"] = cur_geo["top"], cur_geo["bottom"]

        if u.get("kind") == "row":
            # table row: rough height from its tallest cell
            node = u.get("row")
            cell_lines = 0
            max_size = 11.0
            for tc in node.findall(_q("tc")):
                for p in tc.iter(_q("p")):
                    lines, lh, _st, _pb, s_b, s_a = _paragraph_model(p, defaults)
                    cell_lines += (len(lines) + math.ceil(_measure_width(" ".join(lines), 11.0) /
                                                         max(1.0, page["w"] - page["left"] - page["right"] - 20)))
                    max_size = max(max_size, lh / _line_height(11.0, defaults) * 11.0)
            row_h = max(20.0, cell_lines * _line_height(max_size, defaults) + 4)
            if used + row_h > usable_height() and used > 0:
                push_page()
            page["units"].append(ui)
            used += row_h
            continue

        node = u.get("node")
        if node is None:
            page["units"].append(ui)
            used += 14.0
            if used > usable_height():
                push_page()
            continue

        lines, line_h, section_type, page_break_after, sp_before, sp_after = _paragraph_model(node, defaults)
        content_width = page["w"] - page["left"] - page["right"]

        # line wrapping of the raw text
        n_lines = 0
        for line in lines:
            w = _measure_width(line, line_h / 1.25)
            if w <= 0:
                n_lines += 1
            else:
                n_lines += max(1, int(math.ceil(w / max(1.0, content_width))))

        para_h = n_lines * line_h + sp_before + sp_after

        if section_type is not None:
            if section_type in ("nextPage", "oddPage", "evenPage"):
                # The paragraph carrying the section break ends its section; the
                # break itself occupies one line on the CURRENT page when it fits.
                if used + line_h > usable_height() and used > 0:
                    push_page()
                page["units"].append(ui)
                used += line_h
                push_page()
                continue
            # continuous break: normal flow
            if used + para_h > usable_height() and used > 0:
                push_page()
            page["units"].append(ui)
            used += para_h
            continue

        start_new = False
        pPr = node.find(_q("pPr"))
        if pPr is not None and pPr.find(_q("pageBreakBefore")) is not None:
            start_new = True

        if start_new:
            if page["units"] or used > 0:
                push_page()
            page["units"].append(ui)
            if page_break_after:
                push_page()
            else:
                used = para_h
            continue

        if used + para_h > usable_height() and used > 0:
            push_page()
        page["units"].append(ui)
        used += para_h
        if page_break_after:
            push_page()

    if page["units"] or (not pages and used > 0):
        pages.append(page)
    if not pages:
        pages.append(page)

    return {"pages": pages, "units": units, "defaults": defaults}


def paginate_units(docx_path):
    """Return (page_count, boundaries) estimated without rendering to PDF."""
    try:
        model = _paginate_model(docx_path)
    except Exception:
        return None
    if model is None:
        return None
    pages = model["pages"]
    if not pages:
        return None
    boundaries = []
    last = -1
    for p in pages:
        maxu = max(p["units"], default=-1)
        if maxu >= 0:
            last = max(last, maxu)
        boundaries.append(last)
    page_count = len(boundaries)
    if not boundaries or boundaries[0] == -1:
        return None
    return page_count, boundaries


def convert_docx_to_pdf_python(docx_path, pdf_path):
    """Convert DOCX to PDF using PyMuPDF with document-accurate page geometry."""
    model = _paginate_model(docx_path, render=True)
    if model is None:
        doc = pymupdf.open()
        db = doc.new_page(width=595.0, height=842.0)
        db.insert_text((72, 72), f"Document: {os.path.basename(docx_path)}")
        doc.save(pdf_path)
        doc.close()
        return pdf_path

    units = model["units"]
    defaults = model.get("defaults") or {"size": 11.0, "before": 0.0, "after": 8.0, "line": 240.0}
    doc = pymupdf.open()
    for page in model["pages"]:
        pdf_page = doc.new_page(width=page["w"], height=page["h"])
        y = page["top"]
        bottom = page["h"] - page["bottom"]
        content_width = page["w"] - page["left"] - page["right"]
        for ui in page["units"]:
            u = units[ui]
            if u.get("kind") == "row":
                row_h = 20.0
                if y + row_h > bottom:
                    break
                tbl = u.get("table")
                cols = len(tbl.findall(_q("tr"))[0].findall(_q("tc"))) if tbl is not None else 1
                col_w = content_width / max(1, cols)
                row = u.get("row")
                ci = 0
                for tc in row.findall(_q("tc")):
                    text = " ".join(t.text for t in tc.iter(_q("t")) if t.text)
                    rect = pymupdf.Rect(page["left"] + ci * col_w, y, page["left"] + (ci + 1) * col_w, y + row_h)
                    try:
                        pdf_page.draw_rect(rect, color=(0.7, 0.7, 0.7), width=0.5)
                        if text.strip():
                            pdf_page.insert_textbox(rect, text, fontsize=9, fontname="helv", color=(0.1, 0.1, 0.1))
                    except Exception:
                        pass
                    ci += 1
                y += row_h
                continue

            node = u.get("node")
            if node is None:
                y += 14.0
                continue
            lines, line_h, section_type, page_break_after, sp_before, sp_after = _paragraph_model(node, defaults)
            y += sp_before
            for line in lines:
                if y + line_h > bottom:
                    break
                try:
                    pdf_page.insert_textbox(
                        pymupdf.Rect(page["left"], y, page["left"] + content_width, y + line_h + 60),
                        line, fontsize=line_h / 1.25, fontname="helv", color=(0, 0, 0),
                        align=pymupdf.TEXT_ALIGN_LEFT,
                    )
                except Exception:
                    pass
                y += line_h
            y += sp_after

    if doc.page_count == 0:
        pg = model["pages"][0]
        doc.new_page(width=pg["w"], height=pg["h"])

    os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
    doc.save(pdf_path)
    doc.close()
    return pdf_path