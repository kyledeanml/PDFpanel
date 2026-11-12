#!/usr/bin/env python3
"""
Unit & Integration Test Suite for PDFpanel
Verifies PDF merge, split, compress, bookmarking, auto-chapter detection, and GTK UI.
"""

import os
import shutil
import tempfile
import unittest

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from pdf_engine import BookmarkItem, PDFEngine, PopplerBridge


def _make_sample_pdf(path: str, page_titles: list, with_highres_image: bool = False):
    surf = cairo.PDFSurface(path, 595, 842)
    cr = cairo.Context(surf)
    for idx, title in enumerate(page_titles, start=1):
        if with_highres_image:
            # Paint a detailed noisy RGB image so Ghostscript downsampling yields strong savings
            img = cairo.ImageSurface(cairo.FORMAT_RGB24, 600, 400)
            icr = cairo.Context(img)
            for bx in range(0, 600, 12):
                icr.set_source_rgb((bx % 97) / 97.0, ((bx * 3) % 71) / 71.0, ((idx * 29 + bx) % 53) / 53.0)
                icr.rectangle(bx, 0, 12, 400)
                icr.fill()
            cr.set_source_surface(img, 50, 200)
            cr.paint()

        cr.set_source_rgb(0.0, 0.0, 0.0)
        cr.set_font_size(18)
        cr.move_to(60, 80)
        cr.show_text(title)
        cr.set_font_size(11)
        cr.move_to(60, 120)
        cr.show_text(f"Sample body paragraph on physical page {idx}.")
        surf.show_page()
    surf.finish()


class TestPDFpanelAssets(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))

    def test_svg_desktop_and_scripts(self):
        svg_path = os.path.join(self.base_dir, "pdfpanel.svg")
        self.assertTrue(os.path.isfile(svg_path))
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(svg_path, 256, 256, True)
        self.assertIsNotNone(pix)
        self.assertEqual(pix.get_width(), 256)

        desktop_path = os.path.join(self.base_dir, "pdfpanel.desktop")
        self.assertTrue(os.path.isfile(desktop_path))
        desktop_txt = open(desktop_path, "r", encoding="utf-8").read()
        self.assertIn("Name=PDFpanel", desktop_txt)
        self.assertIn("StartupWMClass=pdfpanel", desktop_txt)
        self.assertIn("MimeType=application/pdf;", desktop_txt)

        for script in ("run.sh", "install.sh", "pdfpanel.py"):
            p = os.path.join(self.base_dir, script)
            self.assertTrue(os.path.isfile(p))
            self.assertTrue(os.access(p, os.X_OK), f"{script} should be executable")


class TestPDFEngineOperations(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="pdfpanel_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_merge_with_page_ranges_and_bookmarks(self):
        p1 = os.path.join(self.tmp_dir, "Part_Alpha.pdf")
        p2 = os.path.join(self.tmp_dir, "Part_Beta.pdf")
        merged = os.path.join(self.tmp_dir, "combined.pdf")

        _make_sample_pdf(p1, ["Alpha 1", "Alpha 2", "Alpha 3"])
        _make_sample_pdf(p2, ["Beta 1", "Beta 2", "Beta 3"])

        out_path, total = PDFEngine.merge_pdfs(
            [(p1, "1-2"), (p2, "2-3")],
            merged,
            add_file_bookmarks=True,
        )
        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(total, 4)

        info = PDFEngine.inspect_pdf(out_path)
        self.assertEqual(info.page_count, 4)
        self.assertEqual(info.bookmark_count, 2)

        bms = PDFEngine.get_bookmarks(out_path)
        self.assertEqual([(b.title, b.page) for b in bms], [("Part_Alpha", 1), ("Part_Beta", 3)])

    def test_split_chunks_ranges_extract_and_bookmarks(self):
        src = os.path.join(self.tmp_dir, "Manual.pdf")
        bookmarked = os.path.join(self.tmp_dir, "Manual_bm.pdf")
        _make_sample_pdf(src, ["Intro", "Ch1 Start", "Ch1 End", "Ch2 Start", "Appendix"])

        # Add chapter bookmarks first
        PDFEngine.write_bookmarks(
            src,
            bookmarked,
            [
                BookmarkItem("Introduction", page=1, level=1),
                BookmarkItem("Chapter 1: Setup", page=2, level=1),
                BookmarkItem("Section 1.1", page=3, level=2),
                BookmarkItem("Chapter 2: Usage", page=4, level=1),
            ],
        )

        # 1. Fixed 2-page chunks (5 pages -> 3 files: 2, 2, 1)
        d_chunks = os.path.join(self.tmp_dir, "out_chunks")
        chunks = PDFEngine.split_pdf(bookmarked, d_chunks, mode="chunks", value="2")
        self.assertEqual(len(chunks), 3)
        self.assertEqual([PDFEngine.inspect_pdf(c).page_count for c in chunks], [2, 2, 1])

        # 2. Custom ranges
        d_ranges = os.path.join(self.tmp_dir, "out_ranges")
        ranges = PDFEngine.split_pdf(bookmarked, d_ranges, mode="ranges", value="1-2; 4-5")
        self.assertEqual(len(ranges), 2)
        self.assertEqual([PDFEngine.inspect_pdf(r).page_count for r in ranges], [2, 2])

        # 3. Extract specific non-contiguous pages
        d_extract = os.path.join(self.tmp_dir, "out_extract")
        ext = PDFEngine.split_pdf(bookmarked, d_extract, mode="extract", value="1, 3, 5")
        self.assertEqual(len(ext), 1)
        self.assertEqual(PDFEngine.inspect_pdf(ext[0]).page_count, 3)

        # 4. Split by top-level bookmarks -> 3 chapter PDFs (p1, p2-3, p4-5)
        d_bm = os.path.join(self.tmp_dir, "out_bookmarks")
        chap_files = PDFEngine.split_pdf(bookmarked, d_bm, mode="bookmarks")
        self.assertEqual(len(chap_files), 3)
        self.assertEqual([PDFEngine.inspect_pdf(f).page_count for f in chap_files], [1, 2, 2])
        self.assertIn("Chapter_1_Setup", os.path.basename(chap_files[1]))

    def test_compress_pdf_reduces_size_and_preserves_pages(self):
        src = os.path.join(self.tmp_dir, "heavy.pdf")
        out = os.path.join(self.tmp_dir, "compressed.pdf")
        _make_sample_pdf(src, ["Cover Sheet", "Diagram Page", "Summary"], with_highres_image=True)

        out_path, orig_b, new_b = PDFEngine.compress_pdf(src, out, preset="screen")
        self.assertTrue(os.path.isfile(out_path))
        self.assertLess(new_b, orig_b)
        self.assertEqual(PDFEngine.inspect_pdf(out_path).page_count, 3)

    def test_write_and_replace_nested_unicode_bookmarks_and_autodetect(self):
        # Construct a PDF with explicit chapter headers on blank bookmarks
        src = os.path.join(self.tmp_dir, "Book.pdf")
        _make_sample_pdf(
            src,
            [
                "Preface",
                "Chapter 1 Foundations of Logic",
                "Exercise Set",
                "Chapter 2 Rings & Fields",
            ],
        )
        self.assertEqual(PDFEngine.get_bookmarks(src), [])

        detected = PDFEngine.auto_detect_chapters(src)
        self.assertGreaterEqual(len(detected), 2)
        self.assertTrue(any("Chapter 1" in b.title for b in detected))
        self.assertTrue(any("Chapter 2" in b.title for b in detected))

        # Write nested + Unicode bookmarks
        step1 = os.path.join(self.tmp_dir, "step1.pdf")
        PDFEngine.write_bookmarks(
            src,
            step1,
            [
                BookmarkItem("Old Chapter — Alpha", page=1, level=1),
                BookmarkItem("Old Sub", page=2, level=2),
            ],
        )
        bms1 = PDFEngine.get_bookmarks(step1)
        self.assertEqual([(b.level, b.page, b.title) for b in bms1], [
            (1, 1, "Old Chapter — Alpha"),
            (2, 2, "Old Sub"),
        ])

        # Replace existing bookmarks cleanly (old entries must NOT survive)
        step2 = os.path.join(self.tmp_dir, "step2.pdf")
        PDFEngine.write_bookmarks(
            step1,
            step2,
            [
                BookmarkItem("Unit I • Foundations", page=2, level=1),
                BookmarkItem("1.1 Axioms", page=3, level=2),
                BookmarkItem("Unit II • Algebra", page=4, level=1),
            ],
            replace_existing=True,
        )
        bms2 = PDFEngine.get_bookmarks(step2)
        self.assertEqual([(b.level, b.page, b.title) for b in bms2], [
            (1, 2, "Unit I • Foundations"),
            (2, 3, "1.1 Axioms"),
            (1, 4, "Unit II • Algebra"),
        ])


class TestPDFpanelGUI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="pdfpanel_ui_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_window_loads_pdf_populates_preview_and_bookmarks(self):
        if Gdk.Display.get_default() is None:
            self.skipTest("Requires active display server")
        from pdfpanel import PDFpanelWindow

        pdf_path = os.path.join(self.tmp_dir, "Sample.pdf")
        _make_sample_pdf(pdf_path, ["Chapter 1 Intro", "Chapter 2 Body"])

        win = PDFpanelWindow(initial_files=[pdf_path])
        try:
            for _ in range(15):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)

            self.assertEqual(win.active_pdf, pdf_path)
            self.assertIsNotNone(win.preview_image.get_pixbuf())
            self.assertEqual(len(win.merge_store), 1)

            # Switch across all 4 tool views
            for name in ("merge", "split", "compress", "bookmark"):
                win.tool_stack.set_visible_child_name(name)
                self.assertEqual(win.tool_stack.get_visible_child_name(), name)

            # Add top-level chapter + subsection in Bookmark Studio
            win._add_bookmark(level=1)
            win._add_bookmark(level=2)
            collected = win._collect_bm_tree()
            self.assertEqual(len(collected), 2)
            self.assertEqual(collected[0].level, 1)
            self.assertEqual(collected[1].level, 2)
        finally:
            win.destroy()


if __name__ == "__main__":
    unittest.main()
