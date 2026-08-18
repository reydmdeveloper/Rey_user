"""Pure-Python fallback document renderer.

Converts DOCX documents into PDF files using PyMuPDF (pymupdf) and lxml.
Used as a reliable fallback when neither MS Word COM nor LibreOffice is
available or when external renderers fail/timeout.
"""

import os
import zipfile
from lxml import etree
import pymupdf

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _q(tag):
    return "{%s}%s" % (W_NS, tag)


def convert_docx_to_pdf_python(docx_path, pdf_path):
    """Convert a DOCX file to PDF using PyMuPDF and lxml."""
    doc = pymupdf.open()
    page_width, page_height = 595.0, 842.0  # A4 size in points
    margin = 54.0  # 0.75 inch margins
    content_width = page_width - (2 * margin)

    # Open zip and load document.xml
    with zipfile.ZipFile(docx_path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            # Fallback for non-docx
            page = doc.new_page(width=page_width, height=page_height)
            page.insert_text((margin, margin), f"Document: {os.path.basename(docx_path)}")
            doc.save(pdf_path)
            doc.close()
            return pdf_path

        xml_bytes = zf.read("word/document.xml")
        root = etree.fromstring(xml_bytes)

    body = root.find(_q("body"))
    if body is None:
        page = doc.new_page(width=page_width, height=page_height)
        doc.save(pdf_path)
        doc.close()
        return pdf_path

    # Page rendering state
    cur_page = [doc.new_page(width=page_width, height=page_height)]
    cur_y = [margin]

    def _new_page():
        cur_page[0] = doc.new_page(width=page_width, height=page_height)
        cur_y[0] = margin

    def _check_y(needed=20):
        if cur_y[0] + needed > page_height - margin:
            _new_page()

    def _render_paragraph(p_elem):
        pPr = p_elem.find(_q("pPr"))
        if pPr is not None:
            if pPr.find(_q("pageBreakBefore")) is not None:
                _new_page()

        runs = []
        is_page_break = False

        for child in p_elem:
            if child.tag == _q("r"):
                # Check for explicit page break in run
                for br in child.findall(_q("br")):
                    if br.get(_q("type")) == "page":
                        is_page_break = True

                # Check text
                t_texts = [t.text for t in child.findall(_q("t")) if t.text]
                if t_texts:
                    text = "".join(t_texts)
                    rPr = child.find(_q("rPr"))
                    bold = False
                    italic = False
                    size = 11.0
                    if rPr is not None:
                        if rPr.find(_q("b")) is not None:
                            b_val = rPr.find(_q("b")).get(_q("val"))
                            bold = b_val not in ("0", "false")
                        if rPr.find(_q("i")) is not None:
                            i_val = rPr.find(_q("i")).get(_q("val"))
                            italic = i_val not in ("0", "false")
                        sz = rPr.find(_q("sz"))
                        if sz is not None and sz.get(_q("val")):
                            try:
                                size = float(sz.get(_q("val"))) / 2.0
                            except ValueError:
                                pass
                    runs.append({"text": text, "bold": bold, "italic": italic, "size": size})

        if not runs and not is_page_break:
            cur_y[0] += 12
            _check_y(12)
            return

        if runs:
            full_text = "".join(r["text"] for r in runs)
            if full_text.strip():
                font_size = max(r["size"] for r in runs)
                is_bold = any(r["bold"] for r in runs)
                fontname = "helv-bold" if is_bold else "helv"
                line_height = font_size * 1.25

                _check_y(line_height + 4)
                rect = pymupdf.Rect(margin, cur_y[0], margin + content_width, cur_y[0] + line_height + 50)

                try:
                    rc = cur_page[0].insert_textbox(
                        rect,
                        full_text,
                        fontsize=font_size,
                        fontname=fontname,
                        color=(0, 0, 0),
                        align=pymupdf.TEXT_ALIGN_LEFT
                    )
                    if rc < 0:
                        _new_page()
                        rect = pymupdf.Rect(margin, cur_y[0], margin + content_width, cur_y[0] + line_height + 50)
                        cur_page[0].insert_textbox(
                            rect,
                            full_text,
                            fontsize=font_size,
                            fontname=fontname,
                            color=(0, 0, 0),
                            align=pymupdf.TEXT_ALIGN_LEFT
                        )
                except Exception:
                    pass

                cur_y[0] += line_height + 4

        if is_page_break:
            _new_page()

    def _render_table(tbl_elem):
        rows = tbl_elem.findall(_q("tr"))
        if not rows:
            return

        row_height = 20.0
        for row in rows:
            cells = row.findall(_q("tc"))
            if not cells:
                continue
            _check_y(row_height + 4)
            col_width = content_width / max(1, len(cells))

            for c_idx, cell in enumerate(cells):
                cell_text = "".join([t.text for t in cell.iter(_q("t")) if t.text])
                cell_rect = pymupdf.Rect(
                    margin + (c_idx * col_width),
                    cur_y[0],
                    margin + ((c_idx + 1) * col_width),
                    cur_y[0] + row_height
                )
                cur_page[0].draw_rect(cell_rect, color=(0.7, 0.7, 0.7), width=0.5)
                if cell_text.strip():
                    cur_page[0].insert_textbox(
                        cell_rect,
                        cell_text.strip(),
                        fontsize=9.0,
                        fontname="helv",
                        color=(0.1, 0.1, 0.1)
                    )
            cur_y[0] += row_height + 2

    # Process all top-level children in body
    for child in body:
        tag = child.tag
        if tag == _q("p"):
            _render_paragraph(child)
        elif tag == _q("tbl"):
            _render_table(child)

    if doc.page_count == 0:
        doc.new_page(width=page_width, height=page_height)

    os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
    doc.save(pdf_path)
    doc.close()
    return pdf_path
