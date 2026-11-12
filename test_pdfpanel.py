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
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from pdf_engine import BookmarkItem, PDFEngine, PopplerBridge


def _make_sample_pdf(path: str, page_titles: list, with_highres_image: bool = False):
    surf = cairo.PDFSurface(path, 595, 842)
    cr = cairo.Context(surf)
    for idx, title in enumerate(page_titles, start=1):
        if with_highres_image:
            # Pseudo-random high-resolution raster photo-like texture so /screen downsampling shrinks dramatically
            w, h = 1400, 1000
            raw = os.urandom(w * h * 4)
            img = cairo.ImageSurface.create_for_data(bytearray(raw), cairo.FORMAT_RGB24, w, h, w * 4)
            cr.save()
            cr.translate(45, 180)
            cr.scale(500.0 / w, 360.0 / h)
            cr.set_source_surface(img, 0, 0)
            cr.paint()
            cr.restore()

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
        with open(desktop_path, "r", encoding="utf-8") as f:
            desktop_txt = f.read()
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

        # 5. Split by chapter up to Level 2 (chapters + subsections -> 4 PDFs: p1, p2, p3, p4-5)
        d_bm_l2 = os.path.join(self.tmp_dir, "out_bookmarks_l2")
        sub_files = PDFEngine.split_pdf(bookmarked, d_bm_l2, mode="bookmarks", bookmark_level=2)
        self.assertEqual(len(sub_files), 4)
        self.assertEqual([PDFEngine.inspect_pdf(f).page_count for f in sub_files], [1, 1, 1, 2])

        # 6. Split with leading front matter before Chapter 1 + custom unsaved bookmarks
        d_fm = os.path.join(self.tmp_dir, "out_front_matter")
        custom_bms = [
            BookmarkItem("Chapter 1 Alpha", page=3, level=1),
            BookmarkItem("Chapter 2 Beta", page=5, level=1),
        ]
        fm_files = PDFEngine.split_pdf(
            src, d_fm, mode="bookmarks", bookmarks=custom_bms, include_front_matter=True
        )
        self.assertEqual([os.path.basename(p) for p in fm_files], [
            "00_front_matter.pdf",
            "01_Chapter_1_Alpha.pdf",
            "02_Chapter_2_Beta.pdf",
        ])
        self.assertEqual([PDFEngine.inspect_pdf(f).page_count for f in fm_files], [2, 2, 1])

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

    def test_qpdf_c_api_fast_split_and_merge(self):
        import time
        from pdf_engine import QpdfBridge, PopplerBridge

        self.assertTrue(QpdfBridge.is_available(), "libqpdf C library should load")
        src = os.path.join(self.tmp_dir, "Fast30.pdf")
        _make_sample_pdf(src, [f"Page Marker {i}" for i in range(1, 31)])

        out_dir = os.path.join(self.tmp_dir, "fast_chunks")
        t0 = time.perf_counter()
        chunks = PDFEngine.split_pdf(src, out_dir, mode="chunks", value="3", prefix="fast")
        elapsed = time.perf_counter() - t0

        self.assertEqual(len(chunks), 10)
        self.assertLess(elapsed, 1.0, f"C-level split took too long: {elapsed:.3f}s")
        self.assertEqual(PDFEngine.inspect_pdf(chunks[0]).page_count, 3)
        self.assertEqual(PDFEngine.inspect_pdf(chunks[-1]).page_count, 3)

        # Verify lossless text extraction from middle chunk via Poppler C bridge
        if PopplerBridge.is_available():
            doc = PopplerBridge.open_document(chunks[4])
            try:
                txt, _rects = PopplerBridge.get_page_text_layout(doc, 0)
            finally:
                PopplerBridge.close_document(doc)
            self.assertIn("Page Marker 13", txt)

        # Fast C merge without bookmarks
        merged_c = os.path.join(self.tmp_dir, "merged_c.pdf")
        out_p, total_p = PDFEngine.merge_pdfs([(chunks[0], "all"), (chunks[4], "1,3")], merged_c, add_file_bookmarks=False)
        self.assertEqual(out_p, merged_c)
        self.assertEqual(total_p, 5)
        self.assertEqual(PDFEngine.inspect_pdf(merged_c).page_count, 5)


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

    def test_drag_and_drop_uri_parsing_and_modes(self):
        if Gdk.Display.get_default() is None:
            self.skipTest("Requires active display server")
        from gi.repository import GLib
        from pdfpanel import PDFpanelWindow

        pdf1 = os.path.join(self.tmp_dir, "My Report 1.pdf")
        pdf2 = os.path.join(self.tmp_dir, "Annex 2.pdf")
        pdf3 = os.path.join(self.tmp_dir, "Appendix 3.pdf")
        txt_file = os.path.join(self.tmp_dir, "ignore.txt")
        _make_sample_pdf(pdf1, ["Page 1"])
        _make_sample_pdf(pdf2, ["Page A", "Page B"])
        _make_sample_pdf(pdf3, ["Appendix"])
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("not a pdf")

        uri1 = GLib.filename_to_uri(pdf1, None)
        uri2 = GLib.filename_to_uri(pdf2, None)
        txt_uri = GLib.filename_to_uri(txt_file, None)

        parsed = PDFpanelWindow.extract_dropped_pdf_paths(
            uris=[uri1, txt_uri, uri1],
            text=f"copy\n{uri2}\n# comment\n{txt_file}\n",
        )
        self.assertEqual(parsed, [pdf1, pdf2])

        win = PDFpanelWindow()
        try:
            for w in (win, win.merge_tree, win.scroll_prev, win.bm_tree):
                self.assertIsNotNone(w.drag_dest_get_target_list())

            # Single file drop on Split tab loads it as active PDF
            win.tool_stack.set_visible_child_name("split")
            self.assertTrue(win._handle_dropped_pdfs([pdf1], mode="smart"))
            self.assertEqual(win.active_pdf, pdf1)
            self.assertEqual(len(win.merge_store), 0)

            # Multi-file drop switches to Merge tab and populates Merge queue
            self.assertTrue(win._handle_dropped_pdfs([pdf1, pdf2], mode="smart"))
            self.assertEqual(win.tool_stack.get_visible_child_name(), "merge")
            self.assertEqual(len(win.merge_store), 2)

            # Single file drop while already on Merge tab appends to Merge queue
            self.assertTrue(win._handle_dropped_pdfs([pdf3], mode="merge"))
            self.assertEqual(len(win.merge_store), 3)

            # Invalid/empty drop is rejected cleanly
            self.assertFalse(win._handle_dropped_pdfs([], mode="smart"))
        finally:
            win.destroy()

    def test_watermark_logo_renders_in_ui(self):
        if Gdk.Display.get_default() is None:
            self.skipTest("Requires active display server")
        import cairo
        from pdfpanel import PDFpanelWindow

        pix = PDFpanelWindow._get_watermark_pixbuf(320)
        self.assertIsNotNone(pix)
        self.assertEqual(pix.get_width(), 320)

        win = PDFpanelWindow()
        try:
            for _ in range(15):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)

            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, 500)
            cr = cairo.Context(surface)
            self.assertFalse(win._on_draw_watermark(win.merge_tree, cr))
            self.assertFalse(win._on_draw_preview_watermark(win.scroll_prev, cr))
            # Verify non-empty alpha pixels were painted by the watermark
            data = surface.get_data()
            self.assertTrue(any(b != 0 for b in data))
        finally:
            win.destroy()

    def test_ui_split_by_chapter_from_bookmarks_controls(self):
        if Gdk.Display.get_default() is None:
            self.skipTest("Requires active display server")
        from pdfpanel import PDFpanelWindow

        raw = os.path.join(self.tmp_dir, "Chapters.pdf")
        book = os.path.join(self.tmp_dir, "ChaptersBookmarked.pdf")
        _make_sample_pdf(raw, ["Cover", "Ch1 Start", "Ch1 Sub", "Ch2 Start"])
        PDFEngine.write_bookmarks(
            raw,
            book,
            [
                BookmarkItem("Chapter One", page=2, level=1),
                BookmarkItem("Section 1.1", page=3, level=2),
                BookmarkItem("Chapter Two", page=4, level=1),
            ],
        )

        win = PDFpanelWindow(initial_files=[book])
        try:
            for _ in range(15):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)

            self.assertFalse(win.row_split_chapters.get_visible())
            win.tool_stack.set_visible_child_name("split")
            win.combo_split_mode.set_active_id("bookmarks")
            self.assertTrue(win.row_split_chapters.get_visible())
            self.assertIn("00_front_matter.pdf", win.lbl_split_result.get_text())
            self.assertIn("Planned 3 chapter PDF(s)", win.lbl_split_result.get_text())

            win.combo_split_bm_level.set_active_id("2")
            self.assertIn("Planned 4 chapter PDF(s)", win.lbl_split_result.get_text())

            win.chk_split_front_matter.set_active(False)
            self.assertIn("Planned 3 chapter PDF(s)", win.lbl_split_result.get_text())
        finally:
            win.destroy()


if __name__ == "__main__":
    unittest.main()
