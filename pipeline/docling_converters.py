"""Lazy-cached Docling DocumentConverter factory functions.

Docling models are ~3 GB and take 10-30 seconds to load on first use.
Each converter is initialised exactly once and cached as a module-level
singleton so subsequent calls return immediately.

A threading lock prevents duplicate initialisation when multiple threads
call the same getter concurrently (double-checked locking pattern).

Three converters are provided:
  - get_ocr_converter()       — full-page Tesseract OCR (nld+eng)
  - get_layout_converter()    — layout analysis only, no OCR (~3 s/doc)
  - get_financial_converter() — table extraction in ACCURATE mode
"""

import logging
import threading

logger = logging.getLogger(__name__)

_ocr_converter = None
_layout_converter = None
_financial_converter = None
_init_lock = threading.Lock()


def get_ocr_converter():
    """Return a DocumentConverter configured for garbled-OCR recovery.

    Uses Tesseract CLI with Dutch + English language packs and
    force_full_page_ocr=True.  MPS is used for Docling neural net stages
    (layout detection, table structure); Tesseract subprocess runs on CPU.
    """
    global _ocr_converter
    if _ocr_converter is not None:
        return _ocr_converter

    with _init_lock:
        if _ocr_converter is not None:
            return _ocr_converter

        logger.info("Initialising Docling OCR converter (Tesseract nld+eng, full-page, MPS)…")

        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TesseractCliOcrOptions,
            AcceleratorOptions,
            AcceleratorDevice,
        )
        from docling.datamodel.base_models import InputFormat

        ocr_options = TesseractCliOcrOptions(lang=["nld", "eng"], force_full_page_ocr=True)
        pipeline_options = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_options)
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=4, device=AcceleratorDevice.MPS
        )
        _ocr_converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        return _ocr_converter


def get_layout_converter():
    """Return a DocumentConverter for layout analysis without OCR.

    Suitable for table-rich documents that already have clean extracted
    text.  MPS is used for DocLayoutYOLO and table structure inference.
    Roughly 3 s/doc instead of ~8 s/doc with OCR enabled.
    """
    global _layout_converter
    if _layout_converter is not None:
        return _layout_converter

    with _init_lock:
        if _layout_converter is not None:
            return _layout_converter

        logger.info("Initialising Docling layout converter (no OCR, MPS)…")

        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            AcceleratorOptions,
            AcceleratorDevice,
        )
        from docling.datamodel.base_models import InputFormat

        pipeline_options = PdfPipelineOptions(do_ocr=False)
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=4, device=AcceleratorDevice.MPS
        )
        _layout_converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        return _layout_converter


def get_financial_converter():
    """Return a DocumentConverter tuned for financial table extraction.

    Enables table structure detection in ACCURATE mode with automatic
    accelerator selection (MPS/CUDA/CPU) and 4 worker threads.
    """
    global _financial_converter
    if _financial_converter is not None:
        return _financial_converter

    with _init_lock:
        if _financial_converter is not None:
            return _financial_converter

        logger.info("Initialising Docling financial converter (TableFormer ACCURATE)…")

        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TableFormerMode,
            AcceleratorDevice,
            AcceleratorOptions,
        )
        from docling.datamodel.base_models import InputFormat

        pipeline_options = PdfPipelineOptions(do_table_structure=True)
        pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=4, device=AcceleratorDevice.AUTO
        )
        _financial_converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        return _financial_converter


def reset_converters():
    """Reset all cached converters to None (for testing)."""
    global _ocr_converter, _layout_converter, _financial_converter
    _ocr_converter = None
    _layout_converter = None
    _financial_converter = None
