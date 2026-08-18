"""Automated fidelity verification for DOCX page-range exports.

Renders the selected source pages and the exported document to page images,
then compares them (pixel-level, text-level, and page count). Fixable
differences (trailing blank pages, stray trailing breaks on the final page)
are corrected automatically and the comparison is re-run until it passes or
the correction budget is exhausted.
"""

import os
import shutil
import tempfile

import pymupdf

from . import convert, word_com

_MISSING = object()


def _pdf_for(path, out_dir):
    """Convert a document to PDF in out_dir (Word COM on Windows, else LibreOffice / PyMuPDF)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return path
    pdf = os.path.join(out_dir, "doc.pdf")
    if word_com.word_available():
        try:
            word_com.export_pdf(path, pdf)
            if os.path.exists(pdf) and os.path.getsize(pdf) > 0:
                return pdf
        except Exception:
            pass
    if convert.soffice_available():
        try:
            return convert.convert_to_pdf(path, out_dir)
        except Exception:
            pass
    try:
        from . import python_renderer
        python_renderer.convert_docx_to_pdf_python(path, pdf)
        if os.path.exists(pdf) and os.path.getsize(pdf) > 0:
            return pdf
    except Exception:
        pass
    return pdf


def render_pages(path, out_dir, pages, width=1100):
    """Render the given 1-indexed pages of a document to PNG files.

    Returns a dict {page: png_path}.
    """
    pdf = _pdf_for(path, out_dir)
    result = {}
    with pymupdf.open(pdf) as doc:
        total = doc.page_count
        zoom = width / 1100.0
        for p in pages:
            if p < 1 or p > total:
                continue
            pix = doc[p - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            out = os.path.join(out_dir, "page_%03d.png" % p)
            pix.save(out)
            result[p] = out
    return result


def compare_png(a, b, max_diff_pct=1.0, downsample=4, color_tol=40):
    """Compare two PNGs robustly against renderer antialiasing noise.

    Both images are downsampled (averaging out subpixel glyph noise) and only
    non-white pixels are compared with a per-channel tolerance. Returns
    (ok, detail).
    """
    def _downsample(path):
        doc = pymupdf.open(path)
        try:
            pix = doc.load_page(0).get_pixmap(matrix=pymupdf.Matrix(1.0 / downsample, 1.0 / downsample))
            return pix
        finally:
            doc.close()

    pa = _downsample(a)
    pb = _downsample(b)
    if pa.width != pb.width or pa.height != pb.height:
        return False, "size %dx%d vs %dx%d" % (pa.width, pa.height, pb.width, pb.height)
    da, db = pa.samples, pb.samples
    n = len(da)
    nch = n // (pa.width * pa.height)
    diffs = 0
    total = 0
    for i in range(0, n, nch):
        if da[i] > 200 and db[i] > 200:
            continue  # both white
        total += 1
        if (abs(da[i] - db[i]) > color_tol or abs(da[i + 1] - db[i + 1]) > color_tol
                or abs(da[i + 2] - db[i + 2]) > color_tol):
            diffs += 1
    pct = 100.0 * diffs / total if total else 0.0
    return pct < max_diff_pct, "%.2f%% of ink pixels differ" % pct


def page_text(path, pages=None):
    """Extract normalized text per page from a document (via PDF conversion)."""
    with tempfile.TemporaryDirectory(prefix="verify_text_") as tmp:
        try:
            pdf = _pdf_for(path, tmp)
        except Exception:
            return {}
        out = {}
        with pymupdf.open(pdf) as doc:
            if pages is None:
                pages = list(range(1, doc.page_count + 1))
            for p in pages:
                if 1 <= p <= doc.page_count:
                    out[p] = _normalize(doc[p - 1].get_text())
    return out


def _normalize(text):
    import re

    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return " ".join(words)


def _page_count(path, out_dir):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx" and word_com.word_available():
        try:
            return word_com.get_page_count(path)
        except Exception:
            pass
    pdf = _pdf_for(path, out_dir)
    with pymupdf.open(pdf) as doc:
        return doc.page_count


def strip_trailing_breaks(path):
    """Remove trailing empty/break-only paragraphs and blank rows from the last page.

    Fixes the 'extra blank page' and 'trailing page boundary' cases. Returns the
    path (a corrected copy is written alongside). Best effort.
    """
    try:
        from . import docx_trim

        root = docx_trim.load_document_xml(path)
        body = root.find(docx_trim._q("body"))
        children = list(body)
        changed = False
        while children:
            last = children[-1]
            if last.tag == docx_trim._q("sectPr"):
                break  # body-level section properties must be kept last
            if last.tag == docx_trim._q("p"):
                if docx_trim._is_section_break_paragraph(last):
                    break  # section-property paragraph is meaningful
                if docx_trim._has_text(last):
                    # keep text, but drop trailing page/line break runs
                    docx_trim._strip_trailing_break_runs(last)
                    break
                if docx_trim._paragraph_has_only_breaks(last):
                    body.remove(last)
                    changed = True
                    children = list(body)
                    continue
                break
            elif last.tag == docx_trim._q("tbl"):
                docx_trim._strip_trailing_empty_rows(last)
                if docx_trim._has_text(last):
                    break
                body.remove(last)
                changed = True
                children = list(body)
            else:
                body.remove(last)
                changed = True
                children = list(body)
        if not changed:
            return path

        tmp = path + ".fixed"
        import zipfile

        with zipfile.ZipFile(path) as zin:
            names = zin.namelist()
            xml = docx_trim.etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for name in names:
                    if name == "word/document.xml":
                        zout.writestr(name, xml)
                    else:
                        zout.writestr(name, zin.read(name))
        if os.path.exists(path):
            os.remove(path)
        os.replace(tmp, path)
        return path
    except Exception:
        return path


def structural_check(path):
    """Validate OOXML structure that Word requires: returns list of error strings."""
    import zipfile

    from lxml import etree

    from . import docx_trim

    errors = []
    try:
        with zipfile.ZipFile(path) as z:
            root = etree.fromstring(z.read("word/document.xml"))
    except Exception as e:
        return ["document.xml unreadable: %s" % e]
    body = root.find(docx_trim._q("body"))
    if body is None:
        return ["no w:body"]
    direct_sectpr = [c for c in body if c.tag == docx_trim._q("sectPr")]
    if len(direct_sectpr) > 1:
        errors.append("multiple body-level sectPr (%d)" % len(direct_sectpr))
    if not direct_sectpr:
        errors.append("missing body-level sectPr")
    return errors


def verify_export(src_path, out_path, start_page, end_page, work_dir=None):
    """Compare exported pages against the source pages.

    Returns a report dict:
      {
        'pass': bool,
        'page_count': {'expected': n, 'actual': m},
        'pages': [{'page': i, 'match': bool, 'detail': str, 'text_match': bool}],
        'corrected': int,   # number of auto-correction attempts applied
      }
    """
    work_dir = work_dir or tempfile.mkdtemp(prefix="verify_")
    os.makedirs(work_dir, exist_ok=True)
    expected = end_page - start_page + 1
    report = {
        "pass": True,
        "page_count": {"expected": expected, "actual": None},
        "pages": [],
        "corrected": 0,
        "note": "",
        "structural_errors": [],
    }

    structural = structural_check(out_path)
    report["structural_errors"] = structural
    if structural:
        report["pass"] = False
        report["note"] = "; ".join(structural)
        return report

    try:
        actual = _page_count(out_path, work_dir)
    except Exception as e:
        report["pass"] = False
        report["note"] = "Could not render output: %s" % e
        return report
    report["page_count"]["actual"] = actual

    corrected = 0
    for attempt in range(3):
        if actual == expected:
            break
        if actual > expected:
            out_path = strip_trailing_breaks(out_path)
            corrected += 1
            try:
                actual = _page_count(out_path, work_dir)
            except Exception:
                break
        else:
            break

    # Boundary auto-adjustment if actual > expected
    if actual > expected and src_path and os.path.exists(src_path):
        try:
            from . import docx_trim, paginate
            _, bd = paginate.paginate_docx(src_path)
            if bd and end_page - 1 < len(bd):
                orig_ue = bd[end_page - 1]
                us = (bd[start_page - 2] + 1) if start_page > 1 else 0
                for delta in range(1, 40):
                    test_ue = orig_ue - delta
                    if test_ue <= us:
                        break
                    test_bd = list(bd)
                    test_bd[end_page - 1] = test_ue
                    try:
                        docx_trim.split_docx_range(src_path, start_page, end_page, test_bd, out_path)
                        strip_trailing_breaks(out_path)
                        test_actual = _page_count(out_path, work_dir)
                        if test_actual == expected:
                            actual = expected
                            corrected += 1
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    report["corrected"] = corrected
    report["page_count"]["actual"] = actual

    if actual != expected:
        report["pass"] = False
        report["note"] = (
            "Page count mismatch: expected %d, got %d" % (expected, actual)
        )
        return report

    src_dir = os.path.join(work_dir, "src")
    out_dir = os.path.join(work_dir, "out")
    os.makedirs(src_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    src_pages = render_pages(src_path, src_dir, list(range(start_page, end_page + 1)))
    out_pages = render_pages(out_path, out_dir, list(range(1, expected + 1)))

    src_text = page_text(src_path, list(range(start_page, end_page + 1)))
    out_text = page_text(out_path, list(range(1, expected + 1)))

    for i in range(1, expected + 1):
        sp = start_page + i - 1
        entry = {"page": i, "source_page": sp, "match": True, "detail": "", "text_match": True}
        if sp in src_pages and i in out_pages:
            ok, detail = compare_png(src_pages[sp], out_pages[i])
            entry["match"] = ok
            entry["detail"] = detail
        else:
            entry["match"] = False
            entry["detail"] = "missing render"
        st = src_text.get(sp, "")
        ot = out_text.get(i, "")
        entry["text_match"] = st == ot
        if not entry["match"] or not entry["text_match"]:
            report["pass"] = False
        report["pages"].append(entry)

    return report
