/**
 * PDF to Image Converter – REYDM
 * Client-side PDF rendering via PDF.js, ZIP export via JSZip + FileSaver.
 * All processing happens in the browser – nothing is uploaded.
 */

document.addEventListener('DOMContentLoaded', () => {
    if (typeof pdfjsLib !== 'undefined') {
        const workerUrl = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
        pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;
        fetch(workerUrl)
            .then(res => res.text())
            .then(code => {
                const blob = new Blob([code], { type: 'application/javascript' });
                pdfjsLib.GlobalWorkerOptions.workerSrc = URL.createObjectURL(blob);
            })
            .catch(err => console.warn('PDF.js worker blob fallback:', err));
    }

    const state = {
        pdfDoc: null,
        fileName: '',
        fileSizeStr: '',
        numPages: 0,
        dpi: 150,
        format: 'png',
        quality: 0.92,
        exportAsZip: true,
        renderedPages: [],
        selectedPages: new Set(),
        isRendering: false
    };

    let dragCounter = 0;

    const el = {
        demoBtn: document.getElementById('pc-demo-btn'),
        dropzone: document.getElementById('pc-drop'),
        fileInput: document.getElementById('pc-file'),
        converter: document.getElementById('pc-converter'),
        fileName: document.getElementById('pc-file-name'),
        fileSize: document.getElementById('pc-file-size'),
        pageCount: document.getElementById('pc-page-count'),
        changeBtn: document.getElementById('pc-change-btn'),
        presets: document.getElementById('pc-presets'),
        dpiRange: document.getElementById('pc-dpi-range'),
        dpiValue: document.getElementById('pc-dpi-value'),
        formatRadios: document.querySelectorAll('input[name="pcFormat"]'),
        qualityGroup: document.getElementById('pc-quality-group'),
        qualityRange: document.getElementById('pc-quality-range'),
        qualityValue: document.getElementById('pc-quality-value'),
        rangeOption: document.getElementById('pc-range-option'),
        rangeInput: document.getElementById('pc-range-input'),
        zipCheck: document.getElementById('pc-zip-check'),
        renderBtn: document.getElementById('pc-render-btn'),
        downloadBtn: document.getElementById('pc-download-btn'),
        progress: document.getElementById('pc-progress'),
        progressText: document.getElementById('pc-progress-text'),
        progressPct: document.getElementById('pc-progress-pct'),
        progressFill: document.getElementById('pc-progress-fill'),
        renderedCount: document.getElementById('pc-rendered-count'),
        selectAll: document.getElementById('pc-select-all'),
        deselectAll: document.getElementById('pc-deselect-all'),
        previewGrid: document.getElementById('pc-preview-grid'),
        modal: document.getElementById('pc-modal'),
        modalImage: document.getElementById('pc-modal-image'),
        modalTitle: document.getElementById('pc-modal-title'),
        modalDownload: document.getElementById('pc-modal-download'),
        modalClose: document.getElementById('pc-modal-close')
    };

    initEventListeners();

    function initEventListeners() {
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(ev => {
            document.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); }, false);
        });

        el.dropzone.addEventListener('dragenter', e => {
            e.preventDefault(); dragCounter++; el.dropzone.classList.add('over');
        }, false);
        el.dropzone.addEventListener('dragleave', e => {
            e.preventDefault(); dragCounter--; if (dragCounter <= 0) { dragCounter = 0; el.dropzone.classList.remove('over'); }
        }, false);
        document.addEventListener('dragleave', e => {
            if (!e.relatedTarget) { dragCounter = 0; el.dropzone.classList.remove('over'); }
        }, false);
        document.addEventListener('drop', e => {
            e.preventDefault(); e.stopPropagation();
            dragCounter = 0; el.dropzone.classList.remove('over');
            handleFileDrop(e);
        }, false);

        el.dropzone.addEventListener('click', () => el.fileInput.click());
        el.dropzone.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.fileInput.click(); }
        });
        el.fileInput.addEventListener('change', e => {
            if (e.target.files && e.target.files.length) handlePdfFile(e.target.files[0]);
        });

        if (el.demoBtn) el.demoBtn.addEventListener('click', e => {
            e.preventDefault(); e.stopPropagation(); generateDemoPdf();
        });

        el.changeBtn.addEventListener('click', () => {
            state.pdfDoc = null;
            el.converter.hidden = true;
            el.fileInput.value = '';
        });

        const presetBtns = el.presets.querySelectorAll('.pc-preset');
        presetBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                presetBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const dpi = parseInt(btn.dataset.dpi);
                state.dpi = dpi;
                el.dpiRange.value = dpi;
                el.dpiValue.textContent = dpi + ' DPI';
            });
        });

        el.dpiRange.addEventListener('input', e => {
            const dpi = parseInt(e.target.value);
            state.dpi = dpi;
            el.dpiValue.textContent = dpi + ' DPI';
            presetBtns.forEach(b => {
                b.classList.toggle('active', parseInt(b.dataset.dpi) === dpi);
            });
        });

        el.formatRadios.forEach(radio => {
            radio.addEventListener('change', e => {
                state.format = e.target.value;
                document.querySelectorAll('.pc-radio').forEach(c => c.classList.remove('active'));
                e.target.closest('.pc-radio').classList.add('active');
                if (state.format === 'png') {
                    el.qualityGroup.style.opacity = '0.5';
                    el.qualityGroup.style.pointerEvents = 'none';
                } else {
                    el.qualityGroup.style.opacity = '1';
                    el.qualityGroup.style.pointerEvents = 'auto';
                }
                updateNamingPatternPreview();
            });
        });

        el.qualityRange.addEventListener('input', e => {
            const val = parseInt(e.target.value);
            state.quality = val / 100;
            el.qualityValue.textContent = val + '%';
        });

        el.rangeOption.addEventListener('change', e => {
            el.rangeInput.classList.toggle('hidden', e.target.value !== 'custom');
        });

        el.zipCheck.addEventListener('change', e => {
            state.exportAsZip = e.target.checked;
            updateDownloadButtonUI();
        });

        el.downloadBtn.addEventListener('click', handleDownloadAction);
        el.renderBtn.addEventListener('click', renderPdfPages);

        el.selectAll.addEventListener('click', () => {
            document.querySelectorAll('.pc-page-check').forEach(cb => cb.checked = true);
            state.renderedPages.forEach(p => state.selectedPages.add(p.pageNum));
        });
        el.deselectAll.addEventListener('click', () => {
            document.querySelectorAll('.pc-page-check').forEach(cb => cb.checked = false);
            state.selectedPages.clear();
        });

        el.modalClose.addEventListener('click', closeModal);
        el.modal.addEventListener('click', e => { if (e.target === el.modal) closeModal(); });
    }

    function handleFileDrop(e) {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        el.dropzone.classList.remove('over');
        const files = (e && e.dataTransfer) ? e.dataTransfer.files : null;
        if (files && files.length > 0) {
            const file = files[0];
            if (isPdfFile(file)) handlePdfFile(file);
            else alert('Please upload a valid PDF document (.pdf).');
        }
    }

    function isPdfFile(file) {
        if (!file) return false;
        const name = (file.name || '').toLowerCase();
        const type = (file.type || '').toLowerCase();
        return name.endsWith('.pdf') || type.includes('pdf') || !type || type === 'application/octet-stream';
    }

    async function generateDemoPdf() {
        try {
            showProgress('Generating demo PDF sample...', 20);
            if (typeof PDFLib === 'undefined') {
                alert('PDFLib is loading. Please try again in a moment.');
                hideProgress();
                return;
            }
            const pdfDoc = await PDFLib.PDFDocument.create();

            const page1 = pdfDoc.addPage([600, 450]);
            page1.drawText('REYDM PDF to Image Converter', { x: 50, y: 360, size: 26 });
            page1.drawText('Demo Sample Document - Page 1', { x: 50, y: 320, size: 18 });
            page1.drawText('Features:', { x: 50, y: 270, size: 14 });
            page1.drawText('1. High-Resolution DPI Quality Presets (72 to 450 DPI)', { x: 70, y: 240, size: 12 });
            page1.drawText('2. Export Formats: PNG (Lossless), JPEG, WEBP', { x: 70, y: 210, size: 12 });
            page1.drawText('3. Single Click ZIP Archive Download', { x: 70, y: 180, size: 12 });
            page1.drawText('4. 100% Private Client-side Processing', { x: 70, y: 150, size: 12 });

            const page2 = pdfDoc.addPage([600, 450]);
            page2.drawText('REYDM PDF to Image Converter', { x: 50, y: 360, size: 26 });
            page2.drawText('Demo Sample Document - Page 2', { x: 50, y: 320, size: 18 });
            page2.drawText('Structured Filename Pattern:', { x: 50, y: 270, size: 14 });
            page2.drawText('All exported images follow {filename}_Page_01.png format', { x: 70, y: 240, size: 12 });
            page2.drawText('Select pages or ranges to download custom selections.', { x: 70, y: 210, size: 12 });

            const pdfBytes = await pdfDoc.save();
            const blob = new Blob([pdfBytes], { type: 'application/pdf' });
            const file = new File([blob], 'demo_sample_document.pdf', { type: 'application/pdf' });
            await handlePdfFile(file);
        } catch (err) {
            console.error('Demo PDF generation failed:', err);
            hideProgress();
        }
    }

    async function handlePdfFile(file) {
        if (!file) return;
        state.currentFile = file;
        state.fileName = file.name || 'document.pdf';
        state.fileSizeStr = formatBytes(file.size || 0);
        el.fileName.textContent = state.fileName;
        el.fileSize.textContent = state.fileSizeStr;
        await processClientSidePdf(file);
    }

    async function processClientSidePdf(file) {
        try {
            showProgress('Reading PDF file bytes locally...', 15);
            const arrayBuffer = await file.arrayBuffer();
            const typedArray = new Uint8Array(arrayBuffer);

            if (typeof pdfjsLib === 'undefined') {
                alert('PDF engine is loading. Please refresh the page.');
                hideProgress();
                return;
            }

            updateProgress('Parsing PDF document structure...', 30);
            const loadingTask = pdfjsLib.getDocument({
                data: typedArray,
                cMapUrl: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/cmaps/',
                cMapPacked: true
            });

            state.pdfDoc = await loadingTask.promise;
            state.numPages = state.pdfDoc.numPages;
            el.pageCount.textContent = state.numPages + (state.numPages === 1 ? ' Page' : ' Pages');
            updateNamingPatternPreview();

            el.converter.hidden = false;
            await renderPdfPages();
        } catch (err) {
            console.error('PDF parsing error:', err);
            alert('Failed to load PDF file: ' + (err.message || 'The file may be password-protected or corrupted.'));
            hideProgress();
        }
    }

    async function renderPdfPages() {
        if (!state.pdfDoc || state.isRendering) return;
        state.isRendering = true;
        state.renderedPages = [];
        state.selectedPages.clear();

        el.previewGrid.innerHTML = '';
        showProgress('Rendering pages...', 10);

        const pageNums = getTargetPageNumbers();

        for (let i = 0; i < pageNums.length; i++) {
            const pageNum = pageNums[i];
            const percent = Math.round(((i + 1) / pageNums.length) * 100);
            updateProgress('Rendering Page ' + pageNum + ' of ' + state.numPages + '...', percent);

            try {
                const page = await state.pdfDoc.getPage(pageNum);
                const scale = state.dpi / 72;
                const viewport = page.getViewport({ scale });
                const baseViewport = page.getViewport({ scale: 1 });

                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                canvas.width = viewport.width;
                canvas.height = viewport.height;

                await page.render({ canvasContext: ctx, viewport: viewport }).promise;

                let mimeType = 'image/png';
                if (state.format === 'jpeg') mimeType = 'image/jpeg';
                if (state.format === 'webp') mimeType = 'image/webp';

                const dataUrl = canvas.toDataURL(mimeType, state.quality);
                const finalDataUrl = applyDpiToImage(dataUrl, state.format, state.dpi);

                const pageData = {
                    pageNum,
                    dataUrl: finalDataUrl,
                    width: Math.round(viewport.width),
                    height: Math.round(viewport.height),
                    pageWidthPt: baseViewport.width,
                    pageHeightPt: baseViewport.height,
                    dpi: state.dpi
                };

                state.renderedPages.push(pageData);
                state.selectedPages.add(pageNum);
                createPageCard(pageData);
            } catch (pageErr) {
                console.error('Error rendering page ' + pageNum + ':', pageErr);
            }
        }

        el.renderedCount.textContent = state.renderedPages.length;
        hideProgress();
        state.isRendering = false;
    }

    function createPageCard(pageData) {
        const card = document.createElement('div');
        card.className = 'pc-page-card';

        const ext = state.format;
        const pageFileName = formatPageFileName(state.fileName, pageData.pageNum, state.numPages, ext);
        const pageSizeIn = (pageData.pageWidthPt / 72).toFixed(2) + ' x ' + (pageData.pageHeightPt / 72).toFixed(2) + ' in';

        card.innerHTML =
            '<div class="pc-page-hdr"><span>Page ' + pageData.pageNum + '</span>' +
            '<input type="checkbox" class="pc-page-check" data-page="' + pageData.pageNum + '" checked></div>' +
            '<div class="pc-thumb" data-page="' + pageData.pageNum + '">' +
            '<img src="' + pageData.dataUrl + '" alt="Page ' + pageData.pageNum + '">' +
            '<div class="pc-zoom"><i class="fa-solid fa-magnifying-glass-plus"></i></div></div>' +
            '<div class="pc-page-footer"><div class="pc-page-size">' +
            '<span>' + pageData.width + ' x ' + pageData.height + ' px</span>' +
            '<span>' + pageSizeIn + ' &bull; ' + pageData.dpi + ' DPI</span></div>' +
            '<a href="' + pageData.dataUrl + '" download="' + pageFileName + '" class="btn btn-secondary btn-xs" title="Download Image (' + pageFileName + ')">' +
            '<i class="fa-solid fa-download"></i></a></div>';

        const checkbox = card.querySelector('.pc-page-check');
        checkbox.addEventListener('change', e => {
            if (e.target.checked) state.selectedPages.add(pageData.pageNum);
            else state.selectedPages.delete(pageData.pageNum);
        });

        const thumb = card.querySelector('.pc-thumb');
        thumb.addEventListener('click', () => openModal(pageData));

        el.previewGrid.appendChild(card);
    }

    function getTargetPageNumbers() {
        const mode = el.rangeOption.value;
        const allNums = Array.from({ length: state.numPages }, (_, i) => i + 1);

        if (mode === 'all') return allNums;

        if (mode === 'selected') {
            return state.selectedPages.size > 0 ? Array.from(state.selectedPages).sort((a, b) => a - b) : allNums;
        }

        if (mode === 'custom') {
            const raw = el.rangeInput.value.trim();
            if (!raw) return allNums;
            const pages = new Set();
            raw.split(',').forEach(part => {
                if (part.includes('-')) {
                    const [start, end] = part.split('-').map(n => parseInt(n.trim()));
                    if (!isNaN(start) && !isNaN(end)) {
                        for (let p = Math.max(1, start); p <= Math.min(state.numPages, end); p++) pages.add(p);
                    }
                } else {
                    const p = parseInt(part.trim());
                    if (!isNaN(p) && p >= 1 && p <= state.numPages) pages.add(p);
                }
            });
            return pages.size > 0 ? Array.from(pages).sort((a, b) => a - b) : allNums;
        }

        return allNums;
    }

    function updateDownloadButtonUI() {
        if (!el.downloadBtn) return;
        if (state.exportAsZip) {
            el.downloadBtn.innerHTML = '<i class="fa-solid fa-file-zipper"></i> Download as .ZIP';
        } else {
            el.downloadBtn.innerHTML = '<i class="fa-solid fa-file-image"></i> Download Images Separately';
        }
    }

    async function handleDownloadAction() {
        if (!state.renderedPages.length) {
            alert('No rendered pages available to export.');
            return;
        }
        const pagesToExport = state.renderedPages.filter(p => state.selectedPages.has(p.pageNum));
        if (pagesToExport.length === 0) {
            alert('Please select at least one page to download.');
            return;
        }
        if (state.exportAsZip) {
            await generateAndDownloadZip();
        } else {
            showProgress('Downloading images...', 10);
            const ext = state.format;
            for (let i = 0; i < pagesToExport.length; i++) {
                const item = pagesToExport[i];
                const pageFileName = formatPageFileName(state.fileName, item.pageNum, state.numPages, ext);
                updateProgress('Downloading Page ' + item.pageNum + '...', Math.round(((i + 1) / pagesToExport.length) * 100));
                const link = document.createElement('a');
                link.href = item.dataUrl;
                link.download = pageFileName;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                await new Promise(res => setTimeout(res, 300));
            }
            updateProgress('Downloads complete!', 100);
            setTimeout(hideProgress, 1200);
        }
    }

    async function generateAndDownloadZip() {
        if (!state.renderedPages.length) {
            alert('No rendered pages available to export.');
            return;
        }
        const pagesToExport = state.renderedPages.filter(p => state.selectedPages.has(p.pageNum));
        if (pagesToExport.length === 0) {
            alert('Please select at least one page to download.');
            return;
        }
        try {
            showProgress('Preparing ZIP archive...', 10);
            const zip = new JSZip();
            const baseName = cleanFileName(state.fileName);
            const ext = state.format;
            const folder = zip.folder(baseName + '-images');

            for (let i = 0; i < pagesToExport.length; i++) {
                const item = pagesToExport[i];
                updateProgress('Compressing Page ' + item.pageNum + '...', Math.round(((i + 1) / pagesToExport.length) * 80));
                const base64Data = item.dataUrl.split(',')[1];
                const pageName = formatPageFileName(state.fileName, item.pageNum, state.numPages, ext);
                folder.file(pageName, base64Data, { base64: true });
            }

            updateProgress('Generating final ZIP file...', 95);
            const zipBlob = await zip.generateAsync({ type: 'blob' });
            saveAs(zipBlob, baseName + '-images.zip');

            updateProgress('Download complete!', 100);
            setTimeout(hideProgress, 1500);
        } catch (zipErr) {
            console.error('ZIP generation failed:', zipErr);
            alert('An error occurred while building the ZIP file.');
            hideProgress();
        }
    }

    function openModal(pageData) {
        const ext = state.format;
        const pageFileName = formatPageFileName(state.fileName, pageData.pageNum, state.numPages, ext);
        el.modalTitle.textContent = pageFileName + ' (' + pageData.width + ' x ' + pageData.height + ' px)';
        el.modalImage.src = pageData.dataUrl;
        el.modalDownload.href = pageData.dataUrl;
        el.modalDownload.download = pageFileName;
        el.modal.classList.remove('hidden');
    }

    function closeModal() {
        el.modal.classList.add('hidden');
        el.modalImage.src = '';
    }

    function showProgress(msg, percentage) {
        el.progressText.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ' + msg;
        el.progressPct.textContent = percentage + '%';
        el.progressFill.style.width = percentage + '%';
        el.progress.hidden = false;
    }

    function updateProgress(msg, percentage) {
        el.progressText.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ' + msg;
        el.progressPct.textContent = percentage + '%';
        el.progressFill.style.width = percentage + '%';
    }

    function hideProgress() {
        el.progress.hidden = true;
    }

    function formatBytes(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function cleanFileName(filename) {
        return filename.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9_-]/g, "_");
    }

    // ---- DPI metadata helpers ----
    function applyDpiToImage(dataUrl, format, dpi) {
        if (!dataUrl || !dpi) return dataUrl;
        try {
            const base64 = dataUrl.split(',')[1];
            if (!base64) return dataUrl;
            const bytes = base64ToUint8Array(base64);
            let outBytes;
            if (format === 'png') outBytes = injectPngDpi(bytes, dpi);
            else if (format === 'jpeg') outBytes = injectJpegDpi(bytes, dpi);
            else return dataUrl;
            if (outBytes === bytes) return dataUrl;
            return uint8ArrayToDataUrl(outBytes, format);
        } catch (err) {
            console.warn('DPI injection failed:', err);
            return dataUrl;
        }
    }

    function base64ToUint8Array(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        return bytes;
    }

    function uint8ArrayToDataUrl(bytes, format) {
        let binary = '';
        const chunkSize = 0x8000;
        for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
        }
        return 'data:image/' + format + ';base64,' + btoa(binary);
    }

    function injectPngDpi(bytes, dpi) {
        if (bytes.length < 33) return bytes;
        const sig = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
        for (let i = 0; i < 8; i++) {
            if (bytes[i] !== sig[i]) return bytes;
        }
        const ppm = Math.round(dpi * 39.3700787);
        const type = [0x70, 0x48, 0x59, 0x73];
        const data = new Uint8Array(9);
        new DataView(data.buffer).setUint32(0, ppm);
        new DataView(data.buffer).setUint32(4, ppm);
        data[8] = 1;
        const lengthBytes = new Uint8Array(4);
        new DataView(lengthBytes.buffer).setUint32(0, 9);
        const crcBytes = new Uint8Array(4);
        const crcInput = new Uint8Array(4 + 9);
        crcInput.set(type, 0);
        crcInput.set(data, 4);
        new DataView(crcBytes.buffer).setUint32(0, crc32(crcInput));
        const ihdrEnd = 8 + 25;
        const result = new Uint8Array(bytes.length + 21);
        result.set(bytes.subarray(0, ihdrEnd), 0);
        result.set(lengthBytes, ihdrEnd);
        result.set(type, ihdrEnd + 4);
        result.set(data, ihdrEnd + 8);
        result.set(crcBytes, ihdrEnd + 17);
        result.set(bytes.subarray(ihdrEnd), ihdrEnd + 21);
        return result;
    }

    function injectJpegDpi(bytes, dpi) {
        if (bytes.length < 4) return bytes;
        if (bytes[0] !== 0xFF || bytes[1] !== 0xD8) return bytes;
        let offset = 2;
        while (offset + 4 <= bytes.length) {
            if (bytes[offset] !== 0xFF) { offset++; continue; }
            const marker = bytes[offset + 1];
            if (marker === 0xFF) { offset++; continue; }
            if (marker === 0x01 || (marker >= 0xD0 && marker <= 0xD7)) { offset += 2; continue; }
            const segLen = (bytes[offset + 2] << 8) | bytes[offset + 3];
            if (segLen < 2 || offset + 2 + segLen > bytes.length) return bytes;
            if (marker === 0xE0 && segLen >= 14 &&
                bytes[offset + 4] === 0x4A && bytes[offset + 5] === 0x46 &&
                bytes[offset + 6] === 0x49 && bytes[offset + 7] === 0x46 &&
                bytes[offset + 8] === 0x00) {
                const p = offset + 9 + 2;
                bytes[p] = 1;
                bytes[p + 1] = (dpi >> 8) & 0xFF;
                bytes[p + 2] = dpi & 0xFF;
                bytes[p + 3] = (dpi >> 8) & 0xFF;
                bytes[p + 4] = dpi & 0xFF;
                return bytes;
            }
            offset += 2 + segLen;
        }
        const seg = new Uint8Array(18);
        seg[0] = 0xFF; seg[1] = 0xE0;
        seg[2] = 0x00; seg[3] = 0x10;
        seg[4] = 0x4A; seg[5] = 0x46; seg[6] = 0x49; seg[7] = 0x46; seg[8] = 0x00;
        seg[9] = 0x01; seg[10] = 0x01;
        seg[11] = 0x01;
        seg[12] = (dpi >> 8) & 0xFF; seg[13] = dpi & 0xFF;
        seg[14] = (dpi >> 8) & 0xFF; seg[15] = dpi & 0xFF;
        seg[16] = 0x00; seg[17] = 0x00;
        const result = new Uint8Array(bytes.length + seg.length);
        result.set(bytes.subarray(0, 2), 0);
        result.set(seg, 2);
        result.set(bytes.subarray(2), 2 + seg.length);
        return result;
    }

    function crc32(bytes) {
        let table = crc32.table;
        if (!table) {
            table = crc32.table = new Int32Array(256);
            for (let n = 0; n < 256; n++) {
                let c = n;
                for (let k = 0; k < 8; k++) {
                    c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
                }
                table[n] = c;
            }
        }
        let crc = -1;
        for (let i = 0; i < bytes.length; i++) {
            crc = (crc >>> 8) ^ table[(crc ^ bytes[i]) & 0xFF];
        }
        return (crc ^ -1) >>> 0;
    }

    function formatPageFileName(originalFileName, pageNum, totalPages, ext) {
        const baseName = cleanFileName(originalFileName);
        const padLength = Math.max(1, String(totalPages).length);
        const paddedPageNum = String(pageNum).padStart(padLength, '0');
        return baseName + '_Page_' + paddedPageNum + '.' + ext;
    }

    function updateNamingPatternPreview() {
        const patternEl = document.getElementById('pc-pattern');
        if (patternEl && state.fileName) {
            patternEl.textContent = formatPageFileName(state.fileName, 1, state.numPages || 1, state.format);
        }
    }
});
