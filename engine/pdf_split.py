"""PDF page-range extraction and preview rendering with PyMuPDF."""

import pymupdf


def pdf_page_count(pdf_path):
    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def split_pdf_range(pdf_path, start_page, end_page, out_path):
    """Extract source pages start_page..end_page (1-indexed, inclusive) into out_path."""
    with pymupdf.open(pdf_path) as src:
        count = src.page_count
        start_page = max(1, start_page)
        end_page = min(count, end_page)
        if end_page < start_page:
            raise ValueError(f"No content on pages {start_page}-{end_page}.")
        dst = pymupdf.open()
        dst.insert_pdf(src, from_page=start_page - 1, to_page=end_page - 1)
        # Trim trailing fully-blank pages (the "last page" of the split).
        while dst.page_count > 1 and _page_is_blank(dst[dst.page_count - 1]):
            dst.delete_page(dst.page_count - 1)
        dst.save(out_path, deflate=True)
        dst.close()


def _page_is_blank(page):
    text = page.get_text().strip()
    if text:
        return False
    pix = page.get_pixmap(dpi=40)
    samples = pix.samples
    nonwhite = 0
    step = 8
    for i in range(0, len(samples), step * pix.n):
        for c in samples[i:i + pix.n]:
            if c < 250:
                nonwhite += 1
                break
    return nonwhite == 0
