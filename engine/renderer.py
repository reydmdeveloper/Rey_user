"""Document preview rendering (page -> PNG).

On Windows the docx is rendered through MS Word (Word COM) so previews match
Word exactly. On Linux (Render) LibreOffice is used only as a renderer when
available. PDFs are rendered directly with PyMuPDF.

The generated PDF and the rendered page PNGs are cached so paging through a
document is fast after the first render. All Word COM access is serialized by
a lock so overlapping preview requests cannot collide.
"""

import hashlib
import io
import os
import tempfile
import threading

import pymupdf

from . import convert, word_com

MAX_PNG_CACHE = 200


class Renderer:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._pdf_lock = threading.Lock()
        self._png_cache = {}
        self._png_lock = threading.Lock()

    def _cache_key(self, path):
        st = os.stat(path)
        key = f"{os.path.basename(path)}-{st.st_mtime_ns}-{st.st_size}"
        return hashlib.md5(key.encode()).hexdigest()

    def _pdf_for(self, path):
        """Return a PDF file for a document (converting when needed)."""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            return path, True
        key = self._cache_key(path)
        pdf = os.path.join(self.cache_dir, key + ".pdf")
        if not os.path.exists(pdf):
            # Serialize conversion so two threads never export the same doc to
            # the same target (Word COM is not safe to run in parallel).
            with self._pdf_lock:
                if os.path.exists(pdf):
                    return pdf, False
                tmp = tempfile.mkdtemp(prefix="render_")
                try:
                    rendered = False
                    if word_com.word_available():
                        try:
                            word_com.export_pdf(path, pdf)
                            rendered = True
                        except Exception:
                            pass
                    if not rendered and convert.soffice_available():
                        try:
                            produced = convert.convert_to_pdf(path, tmp)
                            os.replace(produced, pdf)
                            rendered = True
                        except Exception:
                            pass
                    if not rendered:
                        from . import python_renderer
                        python_renderer.convert_docx_to_pdf_python(path, pdf)
                finally:
                    import shutil

                    shutil.rmtree(tmp, ignore_errors=True)
        return pdf, False

    def _render_png(self, pdf, page, width):
        with pymupdf.open(pdf) as doc:
            total = doc.page_count
            page = max(1, min(page, total))
            pg = doc[page - 1]
            zoom = max(0.5, (width or 1200) / 1200)
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            buf = io.BytesIO(pix.tobytes("png"))
        return buf.getvalue(), total

    def render_page(self, path, page, width):
        """Return (png_bytes, total_pages). page is 1-indexed."""
        pdf, _ = self._pdf_for(path)
        cache_key = f"{os.path.basename(pdf)}-p{page}-w{width}"
        with self._png_lock:
            hit = self._png_cache.get(cache_key)
        if hit is not None:
            return hit
        png, total = self._render_png(pdf, page, width)
        with self._png_lock:
            if len(self._png_cache) >= MAX_PNG_CACHE:
                self._png_cache.clear()
            self._png_cache[cache_key] = (png, total)
        return png, total
