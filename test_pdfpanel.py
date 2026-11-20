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

    def test_merge_1000_pages_with_bookmarks_c_speed(self):
        import time

        src_a = os.path.join(self.tmp_dir, "Volume_A.pdf")
        src_b = os.path.join(self.tmp_dir, "Volume_B.pdf")
        _make_sample_pdf(src_a, [f"Vol A p{i}" for i in range(1, 501)])
        _make_sample_pdf(src_b, [f"Vol B p{i}" for i in range(1, 501)])

        merged_out = os.path.join(self.tmp_dir, "Merged1000.pdf")
        t0 = time.perf_counter()
        out_p, total = PDFEngine.merge_pdfs(
            [(src_a, "all"), (src_b, "all")],
            merged_out,
            add_file_bookmarks=True,
        )
        elapsed = time.perf_counter() - t0

        self.assertEqual(out_p, merged_out)
        self.assertEqual(total, 1000)
        self.assertLess(elapsed, 2.0, f"1000-page C merge took too long: {elapsed:.3f}s")
        self.assertEqual(PDFEngine.inspect_pdf(merged_out).page_count, 1000)
        bms = PDFEngine.get_bookmarks(merged_out)
        self.assertEqual([(b.title, b.page, b.level) for b in bms], [
            ("Volume_A", 1, 1),
            ("Volume_B", 501, 1),
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

    def test_menu_reload_refreshes_modified_pdf_and_preserves_page(self):
        if Gdk.Display.get_default() is None:
            self.skipTest("Requires active display server")
        from pdfpanel import PDFpanelWindow

        pdf_path = os.path.join(self.tmp_dir, "ReloadTarget.pdf")
        _make_sample_pdf(pdf_path, ["P1", "P2", "P3"])

        win = PDFpanelWindow(initial_files=[pdf_path])
        try:
            labels = [
                c.get_label()
                for c in win.app_menu.get_children()
                if hasattr(c, "get_label") and c.get_label()
            ]
            self.assertIn("Reload PDF", labels)

            win._render_preview_page(2)
            self.assertEqual(win.preview_page, 2)
            self.assertEqual(win.active_info.page_count, 3)

            # Modify file on disk to have 4 pages, then trigger Reload
            _make_sample_pdf(pdf_path, ["New P1", "New P2", "New P3", "New P4"])
            reload_item = next(
                c for c in win.app_menu.get_children()
                if hasattr(c, "get_label") and c.get_label() == "Reload PDF"
            )
            reload_item.activate()

            self.assertEqual(win.active_info.page_count, 4)
            self.assertEqual(win.preview_page, 2)
            self.assertEqual(win.merge_store[0][2], 4)
            self.assertIn("Reloaded ReloadTarget.pdf", win.lbl_status.get_text())
        finally:
            win.destroy()

    def test_standardized_dark_neon_theme_no_white_bleed(self):
        if Gdk.Display.get_default() is None:
            self.skipTest("Requires active display server")
        from pdfpanel import APP_CSS, PDFpanelWindow

        errors = []
        provider = Gtk.CssProvider()
        provider.connect("parsing-error", lambda _p, _sec, err: errors.append(str(err)))
        provider.load_from_data(APP_CSS)
        self.assertEqual(errors, [], f"CSS parsing errors: {errors}")

        win = PDFpanelWindow()
        try:
            settings = Gtk.Settings.get_default()
            self.assertTrue(settings.get_property("gtk-application-prefer-dark-theme"))

            menu_bg = win.app_menu.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
            self.assertLess(menu_bg.red, 0.15)
            self.assertLess(menu_bg.green, 0.20)
            self.assertLess(menu_bg.blue, 0.25)

            dlg = win._create_file_dialog("Test Open", Gtk.FileChooserAction.OPEN, "Open")
            try:
                dlg_bg = dlg.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
                self.assertLess(dlg_bg.red, 0.15)
                ok_btn = dlg.get_widget_for_response(Gtk.ResponseType.OK)
                self.assertTrue(ok_btn.get_style_context().has_class("neon-primary"))
            finally:
                dlg.destroy()
        finally:
            win.destroy()


class TestSecurityAndIntegrityHardening(unittest.TestCase):
    """Penetration, attack-vector, and data-integrity tests for PDFpanel."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="pdfpanel_sec_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_in_place_bookmark_and_merge_save_atomicity_no_zero_byte_corruption(self):
        import stat
        pdf_path = os.path.join(self.tmp_dir, "inplace.pdf")
        _make_sample_pdf(pdf_path, ["Page 1", "Page 2", "Page 3", "Page 4"])
        os.chmod(pdf_path, 0o644)

        bms = [
            BookmarkItem(title="Part I", page=1, level=1),
            BookmarkItem(title="Part II", page=3, level=1),
        ]
        # 1. In-place bookmark save (src_pdf == out_pdf) must not truncate open source
        PDFEngine.write_bookmarks(pdf_path, pdf_path, bms, replace_existing=True)
        info = PDFEngine.inspect_pdf(pdf_path)
        self.assertEqual(info.page_count, 4)
        self.assertGreater(info.file_size, 100)
        self.assertEqual(stat.S_IMODE(os.stat(pdf_path).st_mode), 0o644)
        loaded_bms = PDFEngine.get_bookmarks(pdf_path)
        self.assertEqual([(b.title, b.page) for b in loaded_bms], [("Part I", 1), ("Part II", 3)])

        # 2. In-place merge subset (src_pdf == out_pdf) must succeed without truncating source
        _, merged_count = PDFEngine.merge_pdfs([(pdf_path, "1-2")], pdf_path)
        self.assertEqual(merged_count, 2)
        self.assertEqual(PDFEngine.inspect_pdf(pdf_path).page_count, 2)

    def test_parse_page_spec_oom_reverse_range_and_bounds_protection(self):
        # 1. Reverse astronomical range '999999999-1' on 5-page PDF must clamp to [5,4,3,2,1], not OOM
        pages = PDFEngine.parse_page_spec("999999999-1", max_pages=5)
        self.assertEqual(pages, [5, 4, 3, 2, 1])

        # 2. Completely out-of-range specification must raise ValueError
        with self.assertRaises(ValueError):
            PDFEngine.parse_page_spec("500-600", max_pages=5)

        # 3. Repeated ranges exceeding MAX_RESOLVED_PAGES must raise ValueError
        orig_cap = PDFEngine.MAX_RESOLVED_PAGES
        try:
            PDFEngine.MAX_RESOLVED_PAGES = 50
            with self.assertRaises(ValueError):
                PDFEngine.parse_page_spec(",".join(["1-10"] * 10), max_pages=10)
        finally:
            PDFEngine.MAX_RESOLVED_PAGES = orig_cap

    def test_split_pdf_path_traversal_prefix_rejection(self):
        pdf_path = os.path.join(self.tmp_dir, "src.pdf")
        out_dir = os.path.join(self.tmp_dir, "out")
        _make_sample_pdf(pdf_path, ["A", "B"])

        for bad_prefix in ("../../escaped", "../outside", "/tmp/evil", "sub/nested", "null\x00p"):
            with self.subTest(prefix=bad_prefix):
                with self.assertRaises(ValueError):
                    PDFEngine.split_pdf(pdf_path, out_dir, mode="chunks", value="1", prefix=bad_prefix)

    def test_postscript_pdfmark_newline_and_control_char_injection_prevention(self):
        hostile_title = "Chapter 1)\n[/Title (Injected) /Page 1 /OUT pdfmark\r\x00(/etc/passwd) (r) file"
        encoded = PDFEngine._encode_pdf_text(hostile_title)
        self.assertNotIn("\n", encoded)
        self.assertNotIn("\r", encoded)
        self.assertNotIn("\x00", encoded)

        pm = PDFEngine._build_pdfmark([BookmarkItem(title=hostile_title, page=1, level=1)], max_page=5)
        # Exactly 2 lines: DOCVIEW header + 1 single-line /OUT pdfmark
        non_empty_lines = [l for l in pm.splitlines() if l.strip()]
        self.assertEqual(len(non_empty_lines), 2)

    def test_fifo_non_pdf_directory_and_null_byte_rejection(self):
        fifo_path = os.path.join(self.tmp_dir, "trap.fifo")
        os.mkfifo(fifo_path)

        fake_pdf = os.path.join(self.tmp_dir, "fake.pdf")
        with open(fake_pdf, "wb") as f:
            f.write(b"NOT_A_PDF_HEADER_1234567890")

        for invalid in (fifo_path, fake_pdf, self.tmp_dir, os.path.join(self.tmp_dir, "bad\x00.pdf")):
            with self.subTest(target=invalid):
                with self.assertRaises((ValueError, IsADirectoryError)):
                    PDFEngine.inspect_pdf(invalid)


if __name__ == "__main__":
    unittest.main()
