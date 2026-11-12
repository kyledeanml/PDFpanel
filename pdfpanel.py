#!/usr/bin/env python3
"""
PDFpanel - PDF Manipulation & Bookmark Studio
Split, merge, compress, and bookmark PDF files natively on Linux Mint Cinnamon.
Styled after the PythonPanel suite: dark neon theme (#0b0f19) with cyan accents.
"""

import os
import subprocess
import sys
import threading
from typing import Callable, List, Optional, Tuple
from urllib.parse import unquote

sys.dont_write_bytecode = True

import gi
import cairo

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from pdf_engine import BookmarkItem, PDFEngine, PDFInfo, PopplerBridge


APP_CSS = b"""
window, .background {
    background-color: #0b0f19;
    color: #f8fafc;
}
headerbar {
    background: linear-gradient(to bottom, #0f172a, #0b0f19);
    border-bottom: 1px solid #1e293b;
    color: #ffffff;
}
headerbar button, headerbar .stack-switcher button {
    background-color: #111827;
    color: #e2e8f0;
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 5px 10px;
}
headerbar .stack-switcher button:checked {
    background-color: #082f49;
    color: #00f0ff;
    border-color: #00d4ff;
    font-weight: bold;
}
headerbar button:hover {
    border-color: #38bdf8;
    color: #ffffff;
}
.panel-card {
    background-color: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 8px;
    padding: 12px;
}
.section-title {
    color: #00f0ff;
    font-weight: bold;
    font-size: 10.5pt;
    letter-spacing: 0.4px;
}
.muted-label {
    color: #94a3b8;
    font-size: 9pt;
}
.stat-badge {
    background-color: #082f49;
    color: #e0f2fe;
    border: 1px solid #0284c7;
    border-radius: 6px;
    padding: 8px 12px;
}
button.neon-primary {
    background: linear-gradient(to bottom, #0284c7, #0369a1);
    color: #ffffff;
    border: 1px solid #00f0ff;
    border-radius: 6px;
    font-weight: bold;
    padding: 7px 14px;
}
button.neon-primary:hover {
    background: linear-gradient(to bottom, #0ea5e9, #0284c7);
}
button {
    background-color: #162033;
    color: #f1f5f9;
    border: 1px solid #26354d;
    border-radius: 5px;
    padding: 5px 10px;
}
button:hover {
    border-color: #00d4ff;
    color: #ffffff;
}
entry, spinbutton, combobox button {
    background-color: #090e1a;
    color: #ffffff;
    border: 1px solid #334155;
    border-radius: 5px;
}
entry:focus, spinbutton:focus {
    border-color: #00f0ff;
}
treeview {
    background-color: #0a0f1d;
    color: #f8fafc;
}
treeview:selected {
    background-color: #034b75;
    color: #ffffff;
}
treeview header button {
    background-color: #0f172a;
    color: #38bdf8;
    border-bottom: 1px solid #1e293b;
    font-weight: bold;
}
.status-bar {
    background-color: #070b13;
    border-top: 1px solid #1e293b;
    padding: 4px 10px;
    color: #94a3b8;
}
.drop-active {
    border: 2px dashed #00f0ff;
    background-color: rgba(0, 240, 255, 0.08);
    border-radius: 6px;
}
"""


class PDFpanelWindow(Gtk.ApplicationWindow):
    """Main application window for PDFpanel."""

    APP_NAME = "PDFpanel"
    _watermark_pixbufs: dict = {}

    def __init__(self, app: Optional[Gtk.Application] = None, initial_files: Optional[List[str]] = None):
        super().__init__(application=app, title=self.APP_NAME)
        self.set_default_size(1160, 760)
        self.set_wmclass("pdfpanel", "PDFpanel")

        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfpanel.svg")
        if os.path.exists(icon_path):
            try:
                self.set_icon_from_file(icon_path)
            except Exception:
                pass

        self._apply_css()

        # State
        self.active_pdf: Optional[str] = None
        self.active_info: Optional[PDFInfo] = None
        self.preview_doc = None
        self.preview_page = 1
        self._busy = False
        self._last_output_path: Optional[str] = None

        self._build_headerbar()
        self._build_layout()
        self._bind_shortcuts()
        self._init_drag_and_drop()
        self._init_watermarks()
        self.show_all()
        self._on_split_mode_changed()
        self._on_comp_preset_changed()
        self._update_preview_controls()

        if initial_files:
            pdfs = [os.path.abspath(p) for p in initial_files if p and os.path.isfile(p) and p.lower().endswith(".pdf")]
            if len(pdfs) > 1:
                self.tool_stack.set_visible_child_name("merge")
                for p in pdfs:
                    self._add_merge_file(p)
                self.load_active_pdf(pdfs[0])
            elif len(pdfs) == 1:
                self.load_active_pdf(pdfs[0])
                self._add_merge_file(pdfs[0])

        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_args):
        if self.preview_doc:
            PopplerBridge.close_document(self.preview_doc)
            self.preview_doc = None

    def _apply_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS)
        screen = Gdk.Screen.get_default()
        if screen:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build_headerbar(self):
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title(self.APP_NAME)
        hb.set_subtitle("Split, Merge, Compress & Bookmark PDFs")
        self.set_titlebar(hb)

        btn_open = Gtk.Button(label="Open PDF")
        btn_open.set_tooltip_text("Open a PDF file (Ctrl+O)")
        btn_open.connect("clicked", lambda *_: self.on_open_active_pdf())
        hb.pack_start(btn_open)

        self.tool_stack = Gtk.Stack()
        self.tool_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.tool_stack.set_transition_duration(120)

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.tool_stack)
        hb.set_custom_title(None)
        hb.pack_start(switcher)

        btn_menu = Gtk.MenuButton()
        btn_menu.set_label("Menu")
        menu = Gtk.Menu()
        for label, cb in (
            ("Open in DocuPanel...", self._on_open_in_docupanel),
            ("Open Last Output Folder", self._on_open_last_folder),
            ("About PDFpanel", self._on_about),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda *_, fn=cb: fn())
            menu.append(item)
        menu.show_all()
        btn_menu.set_popup(menu)
        hb.pack_end(btn_menu)

        self.spinner = Gtk.Spinner()
        hb.pack_end(self.spinner)

    def _build_layout(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(True)
        outer.pack_start(paned, True, True, 0)

        # Left workbench stack
        self.left_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.left_wrap.set_border_width(12)
        self.left_wrap.pack_start(self.tool_stack, True, True, 0)
        paned.pack1(self.left_wrap, True, False)

        # Build the 4 core workspaces
        self._build_merge_tab()
        self._build_split_tab()
        self._build_compress_tab()
        self._build_bookmark_tab()

        # Right preview & inspector sidebar
        self.right_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.right_panel.set_border_width(12)
        self.right_panel.set_size_request(330, -1)
        paned.pack2(self.right_panel, False, False)
        paned.set_position(800)

        info_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        info_card.get_style_context().add_class("panel-card")
        lbl_hdr = Gtk.Label(label="ACTIVE PDF & LIVE PREVIEW")
        lbl_hdr.get_style_context().add_class("section-title")
        lbl_hdr.set_xalign(0.0)
        info_card.pack_start(lbl_hdr, False, False, 0)

        self.lbl_active_name = Gtk.Label(label="No PDF selected")
        self.lbl_active_name.set_xalign(0.0)
        self.lbl_active_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        info_card.pack_start(self.lbl_active_name, False, False, 0)

        self.lbl_active_meta = Gtk.Label(label="Open or drop a PDF here to inspect pages, size, and bookmarks.")
        self.lbl_active_meta.get_style_context().add_class("muted-label")
        self.lbl_active_meta.set_xalign(0.0)
        self.lbl_active_meta.set_line_wrap(True)
        info_card.pack_start(self.lbl_active_meta, False, False, 0)
        self.right_panel.pack_start(info_card, False, False, 0)

        # Thumbnail preview frame
        self.prev_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.prev_card.get_style_context().add_class("panel-card")

        self.scroll_prev = Gtk.ScrolledWindow()
        self.scroll_prev.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroll_prev.set_min_content_height(380)
        self.preview_image = Gtk.Image()
        self.scroll_prev.add(self.preview_image)
        self.prev_card.pack_start(self.scroll_prev, True, True, 0)

        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.btn_prev_page = Gtk.Button(label="Prev")
        self.btn_prev_page.connect("clicked", lambda *_: self._step_preview(-1))
        self.spin_preview_page = Gtk.SpinButton.new_with_range(1, 1, 1)
        self.spin_preview_page.connect("value-changed", self._on_preview_spin_changed)
        self.lbl_preview_total = Gtk.Label(label="/ 0")
        self.btn_next_page = Gtk.Button(label="Next")
        self.btn_next_page.connect("clicked", lambda *_: self._step_preview(1))

        nav_box.pack_start(self.btn_prev_page, False, False, 0)
        nav_box.pack_start(self.spin_preview_page, True, True, 0)
        nav_box.pack_start(self.lbl_preview_total, False, False, 2)
        nav_box.pack_start(self.btn_next_page, False, False, 0)
        self.prev_card.pack_start(nav_box, False, False, 0)

        self.right_panel.pack_start(self.prev_card, True, True, 0)

        # Status bar
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        status_box.get_style_context().add_class("status-bar")
        self.lbl_status = Gtk.Label(label="Ready — Open or drop PDF files to begin.")
        self.lbl_status.set_xalign(0.0)
        self.lbl_status.set_ellipsize(Pango.EllipsizeMode.END)
        status_box.pack_start(self.lbl_status, True, True, 0)
        outer.pack_start(status_box, False, False, 0)

    # ------------------------------------------------------------------
    # Tab 1: Merge PDFs
    # ------------------------------------------------------------------
    def _build_merge_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        hdr = Gtk.Label(label="MERGE MULTIPLE PDF DOCUMENTS")
        hdr.get_style_context().add_class("section-title")
        hdr.set_xalign(0.0)
        box.pack_start(hdr, False, False, 0)

        desc = Gtk.Label(
            label="Add or drag-and-drop two or more PDFs from your Desktop or file manager, reorder them, customize page ranges (e.g. 'all' or '1-5, 8'), and combine into one PDF."
        )
        desc.get_style_context().add_class("muted-label")
        desc.set_xalign(0.0)
        desc.set_line_wrap(True)
        box.pack_start(desc, False, False, 0)

        # Toolbar
        tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for lbl, cb in (
            ("+ Add PDFs", self._on_merge_add),
            ("Up", lambda *_: self._move_merge_row(-1)),
            ("Down", lambda *_: self._move_merge_row(1)),
            ("Remove", self._on_merge_remove),
            ("Clear", self._on_merge_clear),
        ):
            b = Gtk.Button(label=lbl)
            b.connect("clicked", cb)
            tb.pack_start(b, False, False, 0)
        box.pack_start(tb, False, False, 0)

        # File path, basename, pages_str, size_str, page_spec
        self.merge_store = Gtk.ListStore(str, str, int, str, str)
        self.merge_tree = Gtk.TreeView(model=self.merge_store)
        self.merge_tree.connect("cursor-changed", self._on_merge_cursor_changed)

        col_name = Gtk.TreeViewColumn("File Name", Gtk.CellRendererText(), text=1)
        col_name.set_expand(True)
        self.merge_tree.append_column(col_name)

        col_pages = Gtk.TreeViewColumn("Pages", Gtk.CellRendererText(), text=2)
        self.merge_tree.append_column(col_pages)

        col_size = Gtk.TreeViewColumn("Size", Gtk.CellRendererText(), text=3)
        self.merge_tree.append_column(col_size)

        r_spec = Gtk.CellRendererText()
        r_spec.set_property("editable", True)
        r_spec.connect("edited", self._on_merge_spec_edited)
        col_spec = Gtk.TreeViewColumn("Page Range (editable)", r_spec, text=4)
        self.merge_tree.append_column(col_spec)

        self.merge_scroll = Gtk.ScrolledWindow()
        self.merge_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.merge_scroll.add(self.merge_tree)
        box.pack_start(self.merge_scroll, True, True, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.chk_merge_bookmarks = Gtk.CheckButton(label="Create top-level bookmark per merged file")
        self.chk_merge_bookmarks.set_active(True)
        foot.pack_start(self.chk_merge_bookmarks, True, True, 0)

        self.btn_merge_run = Gtk.Button(label="Merge & Save PDF...")
        self.btn_merge_run.get_style_context().add_class("neon-primary")
        self.btn_merge_run.connect("clicked", lambda *_: self.on_run_merge())
        foot.pack_end(self.btn_merge_run, False, False, 0)
        box.pack_start(foot, False, False, 0)

        self.tool_stack.add_titled(box, "merge", "Merge")

    # ------------------------------------------------------------------
    # Tab 2: Split PDF
    # ------------------------------------------------------------------
    def _build_split_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.split_box = box

        hdr = Gtk.Label(label="SPLIT PDF INTO PARTS, RANGES, OR CHAPTERS")
        hdr.get_style_context().add_class("section-title")
        hdr.set_xalign(0.0)
        box.pack_start(hdr, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("panel-card")

        row_mode = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row_mode.pack_start(Gtk.Label(label="Split Mode:"), False, False, 0)
        self.combo_split_mode = Gtk.ComboBoxText()
        self.combo_split_mode.append("chunks", "Fixed Page Chunks (every N pages)")
        self.combo_split_mode.append("ranges", "Custom Page Ranges (semicolon separated)")
        self.combo_split_mode.append("extract", "Extract Specific Pages into One PDF")
        self.combo_split_mode.append("bookmarks", "Split by Chapter (from Bookmarks / Auto-Chapters)")
        self.combo_split_mode.set_active_id("chunks")
        self.combo_split_mode.connect("changed", self._on_split_mode_changed)
        row_mode.pack_start(self.combo_split_mode, True, True, 0)
        card.pack_start(row_mode, False, False, 0)

        self.split_param_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_split_param = Gtk.Label(label="Pages per output file:")
        self.spin_split_chunks = Gtk.SpinButton.new_with_range(1, 5000, 1)
        self.spin_split_chunks.set_value(1)
        self.entry_split_spec = Gtk.Entry()
        self.entry_split_spec.set_placeholder_text("e.g. 1-3; 4-8; 9-12")
        self.split_param_box.pack_start(self.lbl_split_param, False, False, 0)
        self.split_param_box.pack_start(self.spin_split_chunks, False, False, 0)
        self.split_param_box.pack_start(self.entry_split_spec, True, True, 0)
        card.pack_start(self.split_param_box, False, False, 0)

        self.row_split_chapters = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row_bm_lvl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row_bm_lvl.pack_start(Gtk.Label(label="Chapter Level:"), False, False, 0)
        self.combo_split_bm_level = Gtk.ComboBoxText()
        self.combo_split_bm_level.append("1", "Top-Level Chapters (Level 1)")
        self.combo_split_bm_level.append("2", "Chapters & Subsections (Levels 1–2)")
        self.combo_split_bm_level.append("0", "All Bookmark Entries (Every Level)")
        self.combo_split_bm_level.set_active_id("1")
        self.combo_split_bm_level.connect("changed", lambda *_: self._refresh_split_chapter_preview())
        row_bm_lvl.pack_start(self.combo_split_bm_level, True, True, 0)
        self.row_split_chapters.pack_start(row_bm_lvl, False, False, 0)

        self.chk_split_front_matter = Gtk.CheckButton(
            label="Include leading pages before first chapter as 00_front_matter.pdf"
        )
        self.chk_split_front_matter.set_active(True)
        self.chk_split_front_matter.connect("toggled", lambda *_: self._refresh_split_chapter_preview())
        self.row_split_chapters.pack_start(self.chk_split_front_matter, False, False, 0)
        card.pack_start(self.row_split_chapters, False, False, 0)

        row_prefix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row_prefix.pack_start(Gtk.Label(label="Filename Prefix:"), False, False, 0)
        self.entry_split_prefix = Gtk.Entry()
        self.entry_split_prefix.set_placeholder_text("Optional custom filename prefix")
        row_prefix.pack_start(self.entry_split_prefix, True, True, 0)
        card.pack_start(row_prefix, False, False, 0)

        box.pack_start(card, False, False, 0)

        self.lbl_split_result = Gtk.Label(label="Select a PDF and choose a split mode, then pick an output folder.")
        self.lbl_split_result.get_style_context().add_class("stat-badge")
        self.lbl_split_result.set_xalign(0.0)
        self.lbl_split_result.set_line_wrap(True)
        box.pack_start(self.lbl_split_result, False, False, 0)

        act = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_split_run = Gtk.Button(label="Split PDF to Folder...")
        self.btn_split_run.get_style_context().add_class("neon-primary")
        self.btn_split_run.connect("clicked", lambda *_: self.on_run_split())
        act.pack_end(self.btn_split_run, False, False, 0)
        box.pack_start(act, False, False, 0)

        self._on_split_mode_changed()
        self.tool_stack.add_titled(box, "split", "Split")

    # ------------------------------------------------------------------
    # Tab 3: Compress PDF
    # ------------------------------------------------------------------
    def _build_compress_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.compress_box = box

        hdr = Gtk.Label(label="COMPRESS & OPTIMIZE PDF FILE SIZE")
        hdr.get_style_context().add_class("section-title")
        hdr.set_xalign(0.0)
        box.pack_start(hdr, False, False, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("panel-card")

        row_preset = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row_preset.pack_start(Gtk.Label(label="Compression Preset:"), False, False, 0)
        self.combo_comp_preset = Gtk.ComboBoxText()
        for key, cfg in PDFEngine.COMPRESSION_PRESETS.items():
            self.combo_comp_preset.append(key, cfg["label"])
        self.combo_comp_preset.append("custom", "Custom Image DPI")
        self.combo_comp_preset.set_active_id("ebook")
        self.combo_comp_preset.connect("changed", self._on_comp_preset_changed)
        row_preset.pack_start(self.combo_comp_preset, True, True, 0)
        card.pack_start(row_preset, False, False, 0)

        self.row_custom_dpi = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.row_custom_dpi.pack_start(Gtk.Label(label="Target Image DPI:"), False, False, 0)
        self.spin_comp_dpi = Gtk.SpinButton.new_with_range(36, 600, 12)
        self.spin_comp_dpi.set_value(150)
        self.row_custom_dpi.pack_start(self.spin_comp_dpi, False, False, 0)
        card.pack_start(self.row_custom_dpi, False, False, 0)

        self.chk_comp_gray = Gtk.CheckButton(label="Convert color images to grayscale (extra size savings)")
        card.pack_start(self.chk_comp_gray, False, False, 0)
        box.pack_start(card, False, False, 0)

        self.lbl_comp_result = Gtk.Label(
            label="Uses Ghostscript pdfwrite with stream & font subsetting and duplicate-image deduplication."
        )
        self.lbl_comp_result.get_style_context().add_class("stat-badge")
        self.lbl_comp_result.set_xalign(0.0)
        self.lbl_comp_result.set_line_wrap(True)
        box.pack_start(self.lbl_comp_result, False, False, 0)

        act = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_comp_run = Gtk.Button(label="Compress & Save PDF...")
        self.btn_comp_run.get_style_context().add_class("neon-primary")
        self.btn_comp_run.connect("clicked", lambda *_: self.on_run_compress())
        act.pack_end(self.btn_comp_run, False, False, 0)
        box.pack_start(act, False, False, 0)

        self._on_comp_preset_changed()
        self.tool_stack.add_titled(box, "compress", "Compress")

    # ------------------------------------------------------------------
    # Tab 4: Bookmark Studio
    # ------------------------------------------------------------------
    def _build_bookmark_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        hdr = Gtk.Label(label="BOOKMARK & CHAPTER STUDIO")
        hdr.get_style_context().add_class("section-title")
        hdr.set_xalign(0.0)
        box.pack_start(hdr, False, False, 0)

        tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        for lbl, cb in (
            ("Auto-Populate Chapters", lambda *_: self.on_auto_populate_bookmarks()),
            ("+ Chapter", lambda *_: self._add_bookmark(level=1)),
            ("+ Subsection", lambda *_: self._add_bookmark(level=2)),
            ("Indent", lambda *_: self._adjust_selected_level(1)),
            ("Outdent", lambda *_: self._adjust_selected_level(-1)),
            ("Delete", lambda *_: self._delete_selected_bookmark()),
            ("Clear", lambda *_: self.bm_store.clear()),
        ):
            b = Gtk.Button(label=lbl)
            b.connect("clicked", cb)
            tb.pack_start(b, False, False, 0)
        box.pack_start(tb, False, False, 0)

        # (title, target_page, level, badge)
        self.bm_store = Gtk.TreeStore(str, int, int, str)
        self.bm_tree = Gtk.TreeView(model=self.bm_store)
        self.bm_tree.connect("cursor-changed", self._on_bm_cursor_changed)

        r_title = Gtk.CellRendererText()
        r_title.set_property("editable", True)
        r_title.set_property("ellipsize", Pango.EllipsizeMode.END)
        r_title.connect("edited", self._on_bm_title_edited)
        col_title = Gtk.TreeViewColumn("Bookmark / Chapter Title (click to edit)", r_title, text=0)
        col_title.set_expand(True)
        self.bm_tree.append_column(col_title)

        r_page = Gtk.CellRendererText()
        r_page.set_property("editable", True)
        r_page.set_property("xalign", 1.0)
        r_page.connect("edited", self._on_bm_page_edited)
        col_page = Gtk.TreeViewColumn("Page", r_page, text=1)
        self.bm_tree.append_column(col_page)

        col_lvl = Gtk.TreeViewColumn("Level", Gtk.CellRendererText(), text=3)
        self.bm_tree.append_column(col_lvl)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.add(self.bm_tree)
        box.pack_start(sc, True, True, 0)

        # Quick row editor bar
        edit_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry_bm_title = Gtk.Entry()
        self.entry_bm_title.set_placeholder_text("Selected bookmark title...")
        self.spin_bm_page = Gtk.SpinButton.new_with_range(1, 99999, 1)
        btn_update_row = Gtk.Button(label="Update Selected")
        btn_update_row.connect("clicked", lambda *_: self._apply_quick_bookmark_edit())

        edit_bar.pack_start(Gtk.Label(label="Title:"), False, False, 0)
        edit_bar.pack_start(self.entry_bm_title, True, True, 0)
        edit_bar.pack_start(Gtk.Label(label="Page:"), False, False, 0)
        edit_bar.pack_start(self.spin_bm_page, False, False, 0)
        edit_bar.pack_start(btn_update_row, False, False, 0)
        box.pack_start(edit_bar, False, False, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_bm_source = Gtk.Label(label="Load a PDF to inspect, auto-detect, or edit embedded bookmarks.")
        self.lbl_bm_source.get_style_context().add_class("muted-label")
        self.lbl_bm_source.set_xalign(0.0)
        foot.pack_start(self.lbl_bm_source, True, True, 0)

        self.btn_bm_split_chapters = Gtk.Button(label="Split by Chapters...")
        self.btn_bm_split_chapters.set_tooltip_text(
            "Switch to Split by Chapter using these bookmarks and choose an output folder"
        )
        self.btn_bm_split_chapters.connect("clicked", lambda *_: self.on_split_by_bookmarks_studio())
        foot.pack_end(self.btn_bm_split_chapters, False, False, 0)

        self.btn_bm_save = Gtk.Button(label="Embed & Save PDF...")
        self.btn_bm_save.get_style_context().add_class("neon-primary")
        self.btn_bm_save.connect("clicked", lambda *_: self.on_save_bookmarks())
        foot.pack_end(self.btn_bm_save, False, False, 0)
        box.pack_start(foot, False, False, 0)

        self.tool_stack.add_titled(box, "bookmark", "Bookmark")

    # ------------------------------------------------------------------
    # Active PDF & Preview Helpers
    # ------------------------------------------------------------------
    def _bind_shortcuts(self):
        accel = Gtk.AccelGroup()
        self.add_accel_group(accel)
        for key, mod, fn in (
            ("o", Gdk.ModifierType.CONTROL_MASK, lambda *_: self.on_open_active_pdf()),
            ("1", Gdk.ModifierType.CONTROL_MASK, lambda *_: self.tool_stack.set_visible_child_name("merge")),
            ("2", Gdk.ModifierType.CONTROL_MASK, lambda *_: self.tool_stack.set_visible_child_name("split")),
            ("3", Gdk.ModifierType.CONTROL_MASK, lambda *_: self.tool_stack.set_visible_child_name("compress")),
            ("4", Gdk.ModifierType.CONTROL_MASK, lambda *_: self.tool_stack.set_visible_child_name("bookmark")),
            ("q", Gdk.ModifierType.CONTROL_MASK, lambda *_: self.close()),
        ):
            kval, kmod = Gtk.accelerator_parse(f"<Control>{key}" if mod else key)
            accel.connect(kval, kmod, Gtk.AccelFlags.VISIBLE, fn)

    def _init_drag_and_drop(self):
        targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, 0),
            Gtk.TargetEntry.new("x-special/gnome-icon-list", 0, 1),
            Gtk.TargetEntry.new("text/plain", 0, 2),
        ]
        drop_widgets = [
            (self.merge_tree, "merge", self.merge_scroll),
            (self.merge_scroll, "merge", self.merge_scroll),
            (self.bm_tree, "active", self.bm_tree),
            (self.scroll_prev, "active", self.prev_card),
            (self.right_panel, "active", self.prev_card),
            (self.left_wrap, "smart", self.left_wrap),
            (self, "smart", self.left_wrap),
        ]
        actions = Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK
        for widget, mode, hl_widget in drop_widgets:
            widget.drag_dest_set(Gtk.DestDefaults.ALL, targets, actions)
            widget.connect("drag-motion", self._on_drag_motion, hl_widget)
            widget.connect("drag-leave", self._on_drag_leave, hl_widget)
            widget.connect("drag-data-received", self._on_drag_data_received, mode, hl_widget)

    def _on_drag_motion(self, _widget, context, _x: int, _y: int, time: int, hl_widget: Gtk.Widget) -> bool:
        Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        hl_widget.get_style_context().add_class("drop-active")
        return True

    def _on_drag_leave(self, _widget, _context, _time: int, hl_widget: Gtk.Widget):
        hl_widget.get_style_context().remove_class("drop-active")

    def _on_drag_data_received(
        self,
        _widget,
        context,
        _x: int,
        _y: int,
        selection_data,
        _info: int,
        time: int,
        mode: str,
        hl_widget: Gtk.Widget,
    ):
        hl_widget.get_style_context().remove_class("drop-active")
        uris = selection_data.get_uris() or []
        text = selection_data.get_text()
        paths = self.extract_dropped_pdf_paths(uris, text)
        handled = self._handle_dropped_pdfs(paths, mode=mode)
        Gtk.drag_finish(context, handled, False, time)

    @staticmethod
    def _is_pdf_file(path: str) -> bool:
        if not os.path.isfile(path):
            return False
        if path.lower().endswith(".pdf"):
            return True
        try:
            with open(path, "rb") as f:
                return f.read(5) == b"%PDF-"
        except OSError:
            return False

    @classmethod
    def extract_dropped_pdf_paths(cls, uris: Optional[List[str]] = None, text: Optional[str] = None) -> List[str]:
        """Parse dropped URIs or file-manager clipboard/drop lines into deduplicated local PDF paths."""
        raw_items: List[str] = []
        if uris:
            raw_items.extend(u for u in uris if u)
        if text:
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or stripped in ("copy", "cut"):
                    continue
                raw_items.append(stripped)

        resolved: List[str] = []
        seen = set()
        for item in raw_items:
            candidate = item.strip().strip("\"'")
            if not candidate:
                continue
            path: Optional[str] = None
            if candidate.startswith("file://"):
                try:
                    path, _host = GLib.filename_from_uri(candidate)
                except Exception:
                    raw_path = candidate[len("file://"):]
                    if raw_path.startswith("localhost/"):
                        raw_path = raw_path[len("localhost"):]
                    path = unquote(raw_path)
            elif "://" not in candidate:
                path = unquote(candidate) if "%" in candidate and not os.path.exists(candidate) else candidate

            if not path:
                continue
            abs_path = os.path.abspath(os.path.expanduser(path))
            if abs_path not in seen and cls._is_pdf_file(abs_path):
                seen.add(abs_path)
                resolved.append(abs_path)
        return resolved

    def _handle_dropped_pdfs(self, paths: List[str], mode: str = "smart") -> bool:
        if not paths:
            self.set_status("Drop ignored: no valid PDF files found.")
            return False
        if self._busy:
            self.set_status("Busy processing current task — drop again once complete.")
            return False

        effective_mode = mode
        if mode == "smart":
            if self.tool_stack.get_visible_child_name() == "merge" or len(paths) > 1:
                effective_mode = "merge"
            else:
                effective_mode = "active"

        if effective_mode == "merge":
            added: List[str] = []
            for p in paths:
                try:
                    self._add_merge_file(p)
                    added.append(p)
                except Exception as e:
                    self._show_error(str(e))
            if not added:
                return False
            self.tool_stack.set_visible_child_name("merge")
            if not self.active_pdf:
                self.load_active_pdf(added[0])
            self.set_status(f"Added {len(added)} PDF(s) to Merge queue via drag & drop.")
            return True

        self.load_active_pdf(paths[0])
        if len(paths) > 1:
            added_count = 0
            for p in paths:
                try:
                    self._add_merge_file(p)
                    added_count += 1
                except Exception as e:
                    self._show_error(str(e))
            if added_count:
                self.set_status(
                    f"Loaded {os.path.basename(paths[0])} and queued {added_count} PDFs for Merge."
                )
        return True

    @classmethod
    def _get_watermark_pixbuf(cls, size: int = 340) -> Optional[GdkPixbuf.Pixbuf]:
        """Load and cache the official PDFpanel SVG watermark at the requested size."""
        if size not in cls._watermark_pixbufs:
            svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfpanel.svg")
            pix = None
            if os.path.exists(svg_path):
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(svg_path, size, size, True)
                except Exception:
                    pix = None
            cls._watermark_pixbufs[size] = pix
        return cls._watermark_pixbufs[size]

    def _init_watermarks(self):
        self.merge_tree.connect_after("draw", self._on_draw_watermark, 0.28, 320, 0.55, 0.58)
        self.bm_tree.connect_after("draw", self._on_draw_watermark, 0.28, 320, 0.55, 0.58)
        self.split_box.connect("draw", self._on_draw_watermark, 0.30, 320, 0.54, 0.84)
        self.compress_box.connect("draw", self._on_draw_watermark, 0.30, 320, 0.54, 0.84)
        self.scroll_prev.connect_after("draw", self._on_draw_preview_watermark)

    def _on_draw_watermark(
        self,
        widget: Gtk.Widget,
        cr: cairo.Context,
        alpha: float = 0.30,
        target_size: int = 320,
        x_frac: float = 0.55,
        y_frac: float = 0.58,
    ) -> bool:
        """Render the translucent PDFpanel yoga-pad watermark on workspace canvases."""
        alloc = widget.get_allocation()
        if alloc.width < 140 or alloc.height < 140:
            return False
        fit_size = min(target_size, int(min(alloc.width, alloc.height) * 0.72))
        bucket = max(120, (fit_size // 20) * 20)
        pixbuf = self._get_watermark_pixbuf(bucket)
        if pixbuf is None:
            return False

        try:
            pw = pixbuf.get_width()
            ph = pixbuf.get_height()
            x = max(12.0, (alloc.width - pw) * x_frac)
            y = max(12.0, (alloc.height - ph) * y_frac)
            cr.save()
            cr.set_operator(cairo.OPERATOR_OVER)
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, x, y)
            cr.paint_with_alpha(alpha)
            cr.restore()
        except Exception:
            pass
        return False

    def _on_draw_preview_watermark(self, widget: Gtk.Widget, cr: cairo.Context) -> bool:
        """Show the centered PDFpanel logo watermark in the preview pane until a PDF is loaded."""
        if self.active_pdf is not None and self.preview_doc is not None:
            return False
        return self._on_draw_watermark(widget, cr, alpha=0.42, target_size=260, x_frac=0.5, y_frac=0.5)

    def load_active_pdf(self, pdf_path: str):
        abs_p = os.path.abspath(pdf_path)
        if not os.path.isfile(abs_p):
            self._show_error(f"File not found: {abs_p}")
            return
        try:
            info = PDFEngine.inspect_pdf(abs_p)
        except Exception as e:
            self._show_error(str(e))
            return

        if self.preview_doc:
            PopplerBridge.close_document(self.preview_doc)
            self.preview_doc = None
        if PopplerBridge.is_available():
            self.preview_doc = PopplerBridge.open_document(abs_p)

        self.active_pdf = abs_p
        self.active_info = info
        self.preview_page = 1

        self.lbl_active_name.set_text(os.path.basename(abs_p))
        self.lbl_active_meta.set_text(
            f"{info.page_count} pages  •  {PDFEngine.format_size(info.file_size)}  •  "
            f"{int(round(info.page_width))}x{int(round(info.page_height))} pt  •  "
            f"{info.bookmark_count} embedded bookmarks"
        )
        self.entry_split_prefix.set_text(PDFEngine._sanitize_slug(os.path.splitext(os.path.basename(abs_p))[0]))
        self.spin_preview_page.set_range(1, max(1, info.page_count))
        self.spin_bm_page.set_range(1, max(1, info.page_count))
        self.lbl_preview_total.set_text(f"/ {info.page_count}")
        self._render_preview_page(1)
        self._load_bookmarks_for_active_pdf()
        self.set_status(f"Loaded {os.path.basename(abs_p)} ({info.page_count} pages, {PDFEngine.format_size(info.file_size)})")

    def _render_preview_page(self, page_num: int):
        if not self.active_info or not self.preview_doc:
            self.preview_image.clear()
            self._update_preview_controls()
            return
        max_p = max(1, self.active_info.page_count)
        self.preview_page = max(1, min(page_num, max_p))
        self.spin_preview_page.handler_block_by_func(self._on_preview_spin_changed)
        self.spin_preview_page.set_value(self.preview_page)
        self.spin_preview_page.handler_unblock_by_func(self._on_preview_spin_changed)
        self._update_preview_controls()

        pw, _ = PopplerBridge.get_page_size(self.preview_doc, self.preview_page - 1)
        scale = min(1.5, 290.0 / max(100.0, pw))
        surf = PopplerBridge.render_page_surface(self.preview_doc, self.preview_page - 1, scale=scale)
        if surf is not None:
            pix = Gdk.pixbuf_get_from_surface(surf, 0, 0, surf.get_width(), surf.get_height())
            if pix:
                self.preview_image.set_from_pixbuf(pix)

    def _update_preview_controls(self):
        has = bool(self.active_info and self.active_info.page_count > 0)
        self.btn_prev_page.set_sensitive(has and self.preview_page > 1)
        self.btn_next_page.set_sensitive(has and self.preview_page < self.active_info.page_count)
        self.spin_preview_page.set_sensitive(has)

    def _step_preview(self, delta: int):
        if self.active_info:
            self._render_preview_page(self.preview_page + delta)

    def _on_preview_spin_changed(self, spin):
        self._render_preview_page(int(spin.get_value()))

    def set_status(self, msg: str):
        self.lbl_status.set_text(msg)

    def _set_busy(self, busy: bool):
        self._busy = busy
        for w in (self.btn_merge_run, self.btn_split_run, self.btn_comp_run, self.btn_bm_save):
            w.set_sensitive(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _run_async(self, work_fn: Callable[[], object], on_done: Callable[[object], None]):
        if self._busy:
            return
        self._set_busy(True)

        def worker():
            try:
                res = work_fn()
                GLib.idle_add(lambda: (self._set_busy(False), on_done(res), False)[-1])
            except Exception as exc:
                GLib.idle_add(lambda e=exc: (self._set_busy(False), self._show_error(str(e)), False)[-1])

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Merge Handlers
    # ------------------------------------------------------------------
    def _add_merge_file(self, path: str, page_spec: str = "all"):
        info = PDFEngine.inspect_pdf(path)
        self.merge_store.append([
            info.path,
            os.path.basename(info.path),
            info.page_count,
            PDFEngine.format_size(info.file_size),
            page_spec,
        ])

    def _on_merge_add(self, *_args):
        paths = self._choose_open_pdfs(multiple=True)
        for p in paths:
            try:
                self._add_merge_file(p)
            except Exception as e:
                self._show_error(str(e))
        if paths and not self.active_pdf:
            self.load_active_pdf(paths[0])

    def _on_merge_remove(self, *_args):
        model, it = self.merge_tree.get_selection().get_selected()
        if it:
            model.remove(it)

    def _on_merge_clear(self, *_args):
        self.merge_store.clear()

    def _move_merge_row(self, delta: int):
        model, it = self.merge_tree.get_selection().get_selected()
        if not it:
            return
        path = model.get_path(it)
        idx = path.get_indices()[0]
        target_idx = idx + delta
        if 0 <= target_idx < len(model):
            other = model.get_iter(Gtk.TreePath.new_from_indices([target_idx]))
            model.swap(it, other)

    def _on_merge_spec_edited(self, _renderer, path_str: str, new_text: str):
        it = self.merge_store.get_iter(path_str)
        if it:
            self.merge_store.set_value(it, 4, (new_text or "all").strip())

    def _on_merge_cursor_changed(self, tree):
        model, it = tree.get_selection().get_selected()
        if it:
            p = model.get_value(it, 0)
            if p != self.active_pdf:
                self.load_active_pdf(p)

    def on_run_merge(self):
        sources: List[Tuple[str, str]] = []
        it = self.merge_store.get_iter_first()
        while it:
            sources.append((self.merge_store.get_value(it, 0), self.merge_store.get_value(it, 4)))
            it = self.merge_store.iter_next(it)
        if not sources:
            self._show_error("Add at least one PDF file to the merge list first.")
            return
        out_path = self._choose_save_pdf("Save Merged PDF", "merged_output.pdf")
        if not out_path:
            return
        add_bm = self.chk_merge_bookmarks.get_active()
        self.set_status("Merging PDFs...")
        self._run_async(
            lambda: PDFEngine.merge_pdfs(sources, out_path, add_file_bookmarks=add_bm),
            lambda res: self._on_merge_finished(res),
        )

    def _on_merge_finished(self, res):
        out_path, total_pages = res
        self._last_output_path = out_path
        self.load_active_pdf(out_path)
        self.set_status(f"Merged {total_pages} pages into {os.path.basename(out_path)}")

    # ------------------------------------------------------------------
    # Split Handlers
    # ------------------------------------------------------------------
    def _on_split_mode_changed(self, *_args):
        mode = self.combo_split_mode.get_active_id()
        if mode == "chunks":
            self.lbl_split_param.set_text("Pages per output file:")
            self.lbl_split_param.show()
            self.spin_split_chunks.show()
            self.entry_split_spec.hide()
            self.row_split_chapters.hide()
        elif mode == "ranges":
            self.lbl_split_param.set_text("Page ranges (; separated):")
            self.entry_split_spec.set_placeholder_text("e.g. 1-3; 4-8; 9-12")
            self.lbl_split_param.show()
            self.spin_split_chunks.hide()
            self.entry_split_spec.show()
            self.row_split_chapters.hide()
        elif mode == "extract":
            self.lbl_split_param.set_text("Pages to extract:")
            self.entry_split_spec.set_placeholder_text("e.g. 1, 3, 5-8")
            self.lbl_split_param.show()
            self.spin_split_chunks.hide()
            self.entry_split_spec.show()
            self.row_split_chapters.hide()
        else:
            self.lbl_split_param.set_text(
                "Uses Bookmark Studio / embedded PDF bookmarks (or auto-detected chapters)."
            )
            self.lbl_split_param.show()
            self.spin_split_chunks.hide()
            self.entry_split_spec.hide()
            self.row_split_chapters.show()
            self._refresh_split_chapter_preview()

    def _refresh_split_chapter_preview(self):
        if getattr(self, "combo_split_mode", None) is None or self.combo_split_mode.get_active_id() != "bookmarks":
            return
        if not self.active_pdf:
            self.lbl_split_result.set_text(
                "Open a PDF to preview chapters from embedded or Bookmark Studio bookmarks."
            )
            return
        try:
            lvl = int(self.combo_split_bm_level.get_active_id() or "1")
            include_fm = self.chk_split_front_matter.get_active()
            studio_bms = self._collect_bm_tree() or None
            plan = PDFEngine.plan_bookmark_chapter_splits(
                self.active_pdf,
                bookmarks=studio_bms,
                max_level=lvl,
                include_front_matter=include_fm,
            )
            preview_parts = []
            for fname, pages in plan[:5]:
                p_lbl = f"p.{pages[0]}" if len(pages) == 1 else f"p.{pages[0]}-{pages[-1]}"
                preview_parts.append(f"{fname} ({p_lbl})")
            more = f", +{len(plan) - 5} more" if len(plan) > 5 else ""
            self.lbl_split_result.set_text(
                f"Planned {len(plan)} chapter PDF(s): " + ", ".join(preview_parts) + more
            )
        except Exception as e:
            self.lbl_split_result.set_text(str(e))

    def on_split_by_bookmarks_studio(self):
        if not self.active_pdf:
            self._show_error("Open a PDF file first.")
            return
        self.combo_split_mode.set_active_id("bookmarks")
        self.tool_stack.set_visible_child_name("split")
        self._refresh_split_chapter_preview()
        self.on_run_split()

    def on_run_split(self):
        if not self.active_pdf:
            self._show_error("Open a PDF file first.")
            return
        out_dir = self._choose_folder("Choose Output Folder for Split PDFs")
        if not out_dir:
            return
        mode = self.combo_split_mode.get_active_id() or "chunks"
        val = str(int(self.spin_split_chunks.get_value())) if mode == "chunks" else self.entry_split_spec.get_text().strip()
        prefix = self.entry_split_prefix.get_text().strip() or None
        studio_bms = (self._collect_bm_tree() or None) if mode == "bookmarks" else None
        bm_level = int(self.combo_split_bm_level.get_active_id() or "1") if mode == "bookmarks" else 1
        include_fm = self.chk_split_front_matter.get_active() if mode == "bookmarks" else True
        self.set_status("Splitting PDF...")
        self._run_async(
            lambda: PDFEngine.split_pdf(
                self.active_pdf,
                out_dir,
                mode=mode,
                value=val,
                prefix=prefix,
                bookmarks=studio_bms,
                bookmark_level=bm_level,
                include_front_matter=include_fm,
            ),
            lambda created: self._on_split_finished(created, out_dir),
        )

    def _on_split_finished(self, created: List[str], out_dir: str):
        if created:
            self._last_output_path = created[0]
        self.lbl_split_result.set_text(
            f"Created {len(created)} PDF file(s) in {out_dir}:\n" + ", ".join(os.path.basename(p) for p in created[:6])
            + (" ..." if len(created) > 6 else "")
        )
        self.set_status(f"Split complete: {len(created)} file(s) saved to {out_dir}")

    # ------------------------------------------------------------------
    # Compress Handlers
    # ------------------------------------------------------------------
    def _on_comp_preset_changed(self, *_args):
        is_custom = self.combo_comp_preset.get_active_id() == "custom"
        self.row_custom_dpi.set_visible(is_custom)

    def on_run_compress(self):
        if not self.active_pdf:
            self._show_error("Open a PDF file first.")
            return
        base = os.path.splitext(os.path.basename(self.active_pdf))[0]
        out_path = self._choose_save_pdf("Save Compressed PDF", f"{base}_compressed.pdf")
        if not out_path:
            return
        preset = self.combo_comp_preset.get_active_id() or "ebook"
        custom_dpi = int(self.spin_comp_dpi.get_value()) if preset == "custom" else None
        effective_preset = "ebook" if preset == "custom" else preset
        gray = self.chk_comp_gray.get_active()
        self.set_status("Compressing PDF...")
        self._run_async(
            lambda: PDFEngine.compress_pdf(
                self.active_pdf, out_path, preset=effective_preset, custom_dpi=custom_dpi, grayscale=gray
            ),
            lambda res: self._on_compress_finished(res),
        )

    def _on_compress_finished(self, res):
        out_path, orig_b, new_b = res
        self._last_output_path = out_path
        saved = max(0, orig_b - new_b)
        pct = (saved / orig_b * 100.0) if orig_b > 0 else 0.0
        self.lbl_comp_result.set_text(
            f"Saved: {PDFEngine.format_size(orig_b)}  ->  {PDFEngine.format_size(new_b)} "
            f"(reduced by {PDFEngine.format_size(saved)}, {pct:.1f}%)"
        )
        self.load_active_pdf(out_path)
        self.set_status(f"Compressed PDF saved ({pct:.1f}% smaller): {os.path.basename(out_path)}")

    # ------------------------------------------------------------------
    # Bookmark Studio Handlers
    # ------------------------------------------------------------------
    @staticmethod
    def _level_badge(level: int) -> str:
        return "Chapter" if level <= 1 else ("Section" if level == 2 else f"L{level}")

    def _populate_bm_tree(self, items: List[BookmarkItem]):
        self.bm_store.clear()
        stack: List[Tuple[int, Gtk.TreeIter]] = []
        for it in items:
            lvl = max(1, min(6, int(it.level)))
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1] if stack else None
            row_iter = self.bm_store.append(parent, [it.title, max(1, int(it.page)), lvl, self._level_badge(lvl)])
            stack.append((lvl, row_iter))
        self.bm_tree.expand_all()
        self._refresh_split_chapter_preview()

    def _collect_bm_tree(self) -> List[BookmarkItem]:
        items: List[BookmarkItem] = []

        def _walk(it: Optional[Gtk.TreeIter], depth: int):
            while it:
                title = (self.bm_store.get_value(it, 0) or "").strip()
                page = max(1, int(self.bm_store.get_value(it, 1) or 1))
                items.append(BookmarkItem(title=title, page=page, level=depth))
                if self.bm_store.iter_has_child(it):
                    _walk(self.bm_store.iter_children(it), min(depth + 1, 6))
                it = self.bm_store.iter_next(it)

        _walk(self.bm_store.get_iter_first(), 1)
        return items

    def _load_bookmarks_for_active_pdf(self):
        if not self.active_pdf:
            self.bm_store.clear()
            return
        bms = PDFEngine.get_bookmarks(self.active_pdf)
        self._populate_bm_tree(bms)
        if bms:
            self.lbl_bm_source.set_text(f"Loaded {len(bms)} embedded PDF bookmark(s).")
        else:
            self.lbl_bm_source.set_text("No embedded bookmarks yet — click 'Auto-Populate Chapters' or '+ Chapter'.")

    def on_auto_populate_bookmarks(self):
        if not self.active_pdf:
            self._show_error("Open a PDF file first.")
            return
        self.set_status("Scanning PDF for Table of Contents and chapter headings...")
        self._run_async(
            lambda: PDFEngine.auto_detect_chapters(self.active_pdf),
            lambda items: self._on_auto_populated(items),
        )

    def _on_auto_populated(self, items: List[BookmarkItem]):
        if items:
            self._populate_bm_tree(items)
            self.lbl_bm_source.set_text(f"Auto-populated {len(items)} chapter/section bookmarks (ready to embed).")
            self.set_status(f"Auto-populated {len(items)} bookmarks")
        else:
            self.lbl_bm_source.set_text("No automatic chapter headers found — add custom bookmarks with '+ Chapter'.")
            self.set_status("No automatic chapters detected")

    def _add_bookmark(self, level: int = 1):
        max_p = self.active_info.page_count if self.active_info else 1
        target_p = max(1, min(self.preview_page, max_p))
        model, sel = self.bm_tree.get_selection().get_selected()
        if level >= 2 and sel:
            parent_lvl = model.get_value(sel, 2)
            lvl = min(6, parent_lvl + 1)
            row = model.append(sel, [f"Section (p.{target_p})", target_p, lvl, self._level_badge(lvl)])
        else:
            row = model.append(None, [f"Chapter (p.{target_p})", target_p, 1, self._level_badge(1)])
        self.bm_tree.expand_all()
        self.bm_tree.get_selection().select_iter(row)
        self._sync_quick_bm_editor(row)

    def _delete_selected_bookmark(self):
        model, sel = self.bm_tree.get_selection().get_selected()
        if sel:
            model.remove(sel)

    def _adjust_selected_level(self, delta: int):
        items = self._collect_bm_tree()
        model, sel = self.bm_tree.get_selection().get_selected()
        if not sel:
            return
        # Map flat preorder index of sel
        sel_path = model.get_path(sel).to_string()
        idx_box = [-1]
        counter = [0]

        def _find(it: Optional[Gtk.TreeIter]):
            while it:
                if model.get_path(it).to_string() == sel_path:
                    idx_box[0] = counter[0]
                    return
                counter[0] += 1
                if model.iter_has_child(it):
                    _find(model.iter_children(it))
                it = model.iter_next(it)

        _find(model.get_iter_first())
        idx = idx_box[0]
        if 0 <= idx < len(items):
            items[idx].level = max(1, min(6, items[idx].level + delta))
            self._populate_bm_tree(items)

    def _on_bm_title_edited(self, _renderer, path_str: str, new_text: str):
        it = self.bm_store.get_iter(path_str)
        if it and new_text.strip():
            self.bm_store.set_value(it, 0, new_text.strip())
            self._sync_quick_bm_editor(it)

    def _on_bm_page_edited(self, _renderer, path_str: str, new_text: str):
        it = self.bm_store.get_iter(path_str)
        if it:
            try:
                max_p = self.active_info.page_count if self.active_info else 99999
                pg = max(1, min(int(new_text.strip()), max_p))
                self.bm_store.set_value(it, 1, pg)
                self._sync_quick_bm_editor(it)
                self._render_preview_page(pg)
            except ValueError:
                pass

    def _sync_quick_bm_editor(self, it: Gtk.TreeIter):
        self.entry_bm_title.set_text(self.bm_store.get_value(it, 0) or "")
        self.spin_bm_page.set_value(float(self.bm_store.get_value(it, 1) or 1))

    def _on_bm_cursor_changed(self, tree):
        model, it = tree.get_selection().get_selected()
        if it:
            self._sync_quick_bm_editor(it)
            pg = model.get_value(it, 1)
            if pg:
                self._render_preview_page(pg)

    def _apply_quick_bookmark_edit(self):
        model, it = self.bm_tree.get_selection().get_selected()
        if not it:
            return
        title = self.entry_bm_title.get_text().strip()
        pg = max(1, int(self.spin_bm_page.get_value()))
        if title:
            model.set_value(it, 0, title)
        model.set_value(it, 1, pg)
        self._render_preview_page(pg)

    def on_save_bookmarks(self):
        if not self.active_pdf:
            self._show_error("Open a PDF file first.")
            return
        items = self._collect_bm_tree()
        base = os.path.splitext(os.path.basename(self.active_pdf))[0]
        out_path = self._choose_save_pdf("Save Bookmarked PDF", f"{base}_bookmarked.pdf")
        if not out_path:
            return
        self.set_status("Embedding bookmarks into PDF...")
        self._run_async(
            lambda: PDFEngine.write_bookmarks(self.active_pdf, out_path, items, replace_existing=True),
            lambda saved_path: self._on_bookmarks_saved(saved_path, len(items)),
        )

    def _on_bookmarks_saved(self, saved_path: str, count: int):
        self._last_output_path = saved_path
        self.load_active_pdf(saved_path)
        self.lbl_bm_source.set_text(f"Embedded {count} bookmark(s) into {os.path.basename(saved_path)}.")
        self.set_status(f"Saved {count} PDF bookmarks to {os.path.basename(saved_path)}")

    # ------------------------------------------------------------------
    # File Dialogs & Companion Launchers
    # ------------------------------------------------------------------
    def _choose_open_pdfs(self, multiple: bool = False) -> List[str]:
        dlg = Gtk.FileChooserDialog(
            title="Select PDF File(s)" if multiple else "Open PDF File",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        dlg.set_select_multiple(multiple)
        flt = Gtk.FileFilter()
        flt.set_name("PDF Documents (*.pdf)")
        flt.add_mime_type("application/pdf")
        flt.add_pattern("*.pdf")
        dlg.add_filter(flt)
        if self.active_pdf:
            dlg.set_current_folder(os.path.dirname(self.active_pdf))
        res = dlg.get_filenames() if dlg.run() == Gtk.ResponseType.OK else []
        dlg.destroy()
        return res

    def _choose_save_pdf(self, title: str, default_name: str) -> Optional[str]:
        dlg = Gtk.FileChooserDialog(title=title, parent=self, action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name(default_name)
        if self.active_pdf:
            dlg.set_current_folder(os.path.dirname(self.active_pdf))
        out = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        if out and not out.lower().endswith(".pdf"):
            out += ".pdf"
        return out

    def _choose_folder(self, title: str) -> Optional[str]:
        dlg = Gtk.FileChooserDialog(title=title, parent=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if self.active_pdf:
            dlg.set_current_folder(os.path.dirname(self.active_pdf))
        out = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        return out

    def on_open_active_pdf(self):
        paths = self._choose_open_pdfs(multiple=False)
        if paths:
            self.load_active_pdf(paths[0])

    def _on_open_in_docupanel(self):
        target = self.active_pdf
        if not target:
            self._show_error("Open or create a PDF first.")
            return
        try:
            subprocess.Popen(["docupanel", target], start_new_session=True)
        except Exception as e:
            self._show_error(f"Could not launch DocuPanel:\n{e}")

    def _on_open_last_folder(self):
        path = self._last_output_path or self.active_pdf
        folder = os.path.dirname(path) if path else os.path.expanduser("~")
        try:
            subprocess.Popen(["xdg-open", folder], start_new_session=True)
        except Exception as e:
            self._show_error(f"Could not open folder:\n{e}")

    def _on_about(self):
        dlg = Gtk.AboutDialog(transient_for=self, modal=True)
        dlg.set_program_name(self.APP_NAME)
        dlg.set_version("1.0")
        dlg.set_comments(
            "PDF Toolkit & Bookmark Studio for Linux Mint Cinnamon.\n"
            "Split, merge, compress, and auto-bookmark PDF documents."
        )
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfpanel.svg")
        if os.path.exists(icon_path):
            try:
                dlg.set_logo(GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 96, 96, True))
            except Exception:
                pass
        dlg.run()
        dlg.destroy()

    def _show_error(self, msg: str):
        self.set_status(f"Error: {msg.splitlines()[0]}")
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0, message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK, text="PDFpanel Error",
        )
        dlg.format_secondary_text(msg)
        dlg.run()
        dlg.destroy()


class PDFpanelApp(Gtk.Application):
    def __init__(self, initial_files: Optional[List[str]] = None):
        super().__init__(
            application_id="org.linuxmint.pdfpanel",
            flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.files_to_open = initial_files or []

    def do_activate(self):
        win = PDFpanelWindow(self, initial_files=self.files_to_open)
        win.present()

    def do_open(self, files, _n_files, _hint):
        paths = [f.get_path() for f in files if f.get_path()]
        win = PDFpanelWindow(self, initial_files=paths)
        win.present()


def main():
    GLib.set_prgname("pdfpanel")
    GLib.set_application_name("PDFpanel")
    app = PDFpanelApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
