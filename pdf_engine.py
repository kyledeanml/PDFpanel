#!/usr/bin/env python3
"""
PDFpanel Engine - Core PDF Inspection, Splitting, Merging, Compression, and Bookmarking
Powered by Ghostscript (gs), Poppler (libpoppler-glib / pdfunite / pdfseparate), and Cairo.
"""

import ctypes
import ctypes.util
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class BookmarkItem:
    """Represents a hierarchical PDF bookmark / chapter entry."""
    title: str
    page: int
    level: int = 1


@dataclass
class PDFInfo:
    """Metadata summary for a PDF file."""
    path: str
    page_count: int
    file_size: int
    page_width: float = 595.0
    page_height: float = 842.0
    bookmark_count: int = 0
    title: str = ""


class _PopplerRectangle(ctypes.Structure):
    _fields_ = [
        ("x1", ctypes.c_double),
        ("y1", ctypes.c_double),
        ("x2", ctypes.c_double),
        ("y2", ctypes.c_double),
    ]


class _PopplerActionAny(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("title", ctypes.c_char_p),
    ]


class _PopplerDest(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("page_num", ctypes.c_int),
        ("left", ctypes.c_double),
        ("bottom", ctypes.c_double),
        ("right", ctypes.c_double),
        ("top", ctypes.c_double),
        ("zoom", ctypes.c_double),
        ("named_dest", ctypes.c_char_p),
        ("change_left", ctypes.c_uint, 1),
        ("change_top", ctypes.c_uint, 1),
        ("change_zoom", ctypes.c_uint, 1),
    ]


class _PopplerActionGotoDest(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("title", ctypes.c_char_p),
        ("dest", ctypes.POINTER(_PopplerDest)),
    ]


class PopplerBridge:
    """Lightweight ctypes bindings to libpoppler-glib for fast inspection, rendering, and bookmarks."""

    _lib = None
    _glib = None
    _gobject = None
    _init_attempted = False
    _available = False

    @classmethod
    def _ensure_loaded(cls) -> bool:
        if cls._init_attempted:
            return cls._available
        cls._init_attempted = True
        try:
            glib_path = ctypes.util.find_library("glib-2.0")
            gobj_path = ctypes.util.find_library("gobject-2.0")
            poppler_path = ctypes.util.find_library("poppler-glib")
            if not poppler_path:
                for cand in (
                    "libpoppler-glib.so.8",
                    "/usr/lib/x86_64-linux-gnu/libpoppler-glib.so.8",
                ):
                    if os.path.exists(cand) or cand.startswith("lib"):
                        poppler_path = cand
                        break
            if not poppler_path:
                return False

            cls._glib = ctypes.CDLL(glib_path or "libglib-2.0.so.0")
            cls._gobject = ctypes.CDLL(gobj_path or "libgobject-2.0.so.0")
            cls._lib = ctypes.CDLL(poppler_path)

            cls._gobject.g_object_unref.argtypes = [ctypes.c_void_p]
            cls._gobject.g_object_unref.restype = None
            cls._glib.g_free.argtypes = [ctypes.c_void_p]
            cls._glib.g_free.restype = None

            cls._lib.poppler_document_new_from_file.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            cls._lib.poppler_document_new_from_file.restype = ctypes.c_void_p
            cls._lib.poppler_document_get_n_pages.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_document_get_n_pages.restype = ctypes.c_int
            cls._lib.poppler_document_get_page.argtypes = [ctypes.c_void_p, ctypes.c_int]
            cls._lib.poppler_document_get_page.restype = ctypes.c_void_p
            cls._lib.poppler_page_get_size.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
            ]
            cls._lib.poppler_page_get_size.restype = None
            cls._lib.poppler_page_get_text.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_page_get_text.restype = ctypes.c_void_p
            cls._lib.poppler_page_get_text_layout.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.POINTER(_PopplerRectangle)),
                ctypes.POINTER(ctypes.c_uint),
            ]
            cls._lib.poppler_page_get_text_layout.restype = ctypes.c_int
            cls._lib.poppler_page_render.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            cls._lib.poppler_page_render.restype = None

            cls._lib.poppler_document_find_dest.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            cls._lib.poppler_document_find_dest.restype = ctypes.POINTER(_PopplerDest)
            cls._lib.poppler_dest_free.argtypes = [ctypes.POINTER(_PopplerDest)]
            cls._lib.poppler_dest_free.restype = None

            cls._lib.poppler_index_iter_new.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_index_iter_new.restype = ctypes.c_void_p
            cls._lib.poppler_index_iter_get_action.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_index_iter_get_action.restype = ctypes.c_void_p
            cls._lib.poppler_index_iter_get_child.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_index_iter_get_child.restype = ctypes.c_void_p
            cls._lib.poppler_index_iter_next.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_index_iter_next.restype = ctypes.c_int
            cls._lib.poppler_index_iter_free.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_index_iter_free.restype = None
            cls._lib.poppler_action_free.argtypes = [ctypes.c_void_p]
            cls._lib.poppler_action_free.restype = None

            cls._available = True
            return True
        except Exception:
            cls._available = False
            return False

    @classmethod
    def is_available(cls) -> bool:
        return cls._ensure_loaded()

    @classmethod
    def open_document(cls, pdf_path: str) -> Optional[ctypes.c_void_p]:
        if not cls._ensure_loaded():
            return None
        from gi.repository import GLib
        uri = GLib.filename_to_uri(os.path.abspath(pdf_path), None)
        err = ctypes.c_void_p(None)
        doc = cls._lib.poppler_document_new_from_file(uri.encode("utf-8"), None, ctypes.byref(err))
        if err.value:
            cls._glib.g_free(err)
        return doc if doc else None

    @classmethod
    def close_document(cls, doc_handle: ctypes.c_void_p):
        if doc_handle and cls._gobject:
            cls._gobject.g_object_unref(doc_handle)

    @classmethod
    def get_n_pages(cls, doc_handle: ctypes.c_void_p) -> int:
        if not doc_handle or not cls._ensure_loaded():
            return 0
        return int(cls._lib.poppler_document_get_n_pages(doc_handle))

    @classmethod
    def get_page_size(cls, doc_handle: ctypes.c_void_p, page_index: int) -> Tuple[float, float]:
        if not doc_handle or not cls._ensure_loaded():
            return (595.0, 842.0)
        page = cls._lib.poppler_document_get_page(doc_handle, page_index)
        if not page:
            return (595.0, 842.0)
        try:
            w = ctypes.c_double(0.0)
            h = ctypes.c_double(0.0)
            cls._lib.poppler_page_get_size(page, ctypes.byref(w), ctypes.byref(h))
            return (w.value, h.value)
        finally:
            cls._gobject.g_object_unref(page)

    @classmethod
    def get_page_text_layout(
        cls, doc_handle: ctypes.c_void_p, page_index: int
    ) -> Tuple[str, List[Tuple[float, float, float, float]]]:
        if not doc_handle or not cls._ensure_loaded():
            return "", []
        page = cls._lib.poppler_document_get_page(doc_handle, page_index)
        if not page:
            return "", []
        try:
            ptr = cls._lib.poppler_page_get_text(page)
            if not ptr:
                return "", []
            try:
                raw_bytes = ctypes.cast(ptr, ctypes.c_char_p).value or b""
                txt = raw_bytes.decode("utf-8", errors="replace")
            finally:
                cls._glib.g_free(ptr)

            rects_ptr = ctypes.POINTER(_PopplerRectangle)()
            n_rects = ctypes.c_uint(0)
            ok = cls._lib.poppler_page_get_text_layout(page, ctypes.byref(rects_ptr), ctypes.byref(n_rects))
            rects: List[Tuple[float, float, float, float]] = []
            if ok and rects_ptr:
                try:
                    count = min(int(n_rects.value), len(txt))
                    for i in range(count):
                        r = rects_ptr[i]
                        rects.append((r.x1, r.y1, r.x2, r.y2))
                finally:
                    cls._glib.g_free(rects_ptr)
            return txt, rects
        finally:
            cls._gobject.g_object_unref(page)

    @classmethod
    def render_page_surface(cls, doc_handle: ctypes.c_void_p, page_index: int, scale: float = 1.0):
        """Render 0-based page_index to a Cairo ImageSurface."""
        if not doc_handle or not cls._ensure_loaded():
            return None
        import cairo
        page = cls._lib.poppler_document_get_page(doc_handle, page_index)
        if not page:
            return None
        try:
            w = ctypes.c_double(0.0)
            h = ctypes.c_double(0.0)
            cls._lib.poppler_page_get_size(page, ctypes.byref(w), ctypes.byref(h))
            pw = max(1, int(math.ceil(w.value * scale)))
            ph = max(1, int(math.ceil(h.value * scale)))
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, pw, ph)
            cr = cairo.Context(surf)
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.paint()
            cr.scale(scale, scale)
            py_capsule = cr._capsule if hasattr(cr, "_capsule") else None
            if py_capsule is None:
                # PyCairo standard C pointer lookup via ctypes
                class _PyCairoContext(ctypes.Structure):
                    _fields_ = [
                        ("ob_refcnt", ctypes.c_ssize_t),
                        ("ob_type", ctypes.c_void_p),
                        ("ctx", ctypes.c_void_p),
                    ]
                ctx_ptr = _PyCairoContext.from_address(id(cr)).ctx
                cls._lib.poppler_page_render(page, ctypes.c_void_p(ctx_ptr))
            surf.flush()
            return surf
        except Exception:
            return None
        finally:
            cls._gobject.g_object_unref(page)

    @classmethod
    def get_pdf_bookmarks(cls, doc_handle: ctypes.c_void_p) -> List[BookmarkItem]:
        """Extract hierarchical embedded bookmarks via PopplerIndexIter."""
        if not doc_handle or not cls._ensure_loaded():
            return []
        root_iter = cls._lib.poppler_index_iter_new(doc_handle)
        if not root_iter:
            return []

        results: List[BookmarkItem] = []

        def _walk(it_ptr: ctypes.c_void_p, level: int):
            while it_ptr:
                act_ptr = cls._lib.poppler_index_iter_get_action(it_ptr)
                if act_ptr:
                    try:
                        any_act = ctypes.cast(act_ptr, ctypes.POINTER(_PopplerActionAny)).contents
                        title = ""
                        if any_act.title:
                            title = any_act.title.decode("utf-8", errors="replace").strip()
                            title = re.sub(r"\s+", " ", title)
                        target_page = 1
                        if any_act.type == 2:  # POPPLER_ACTION_GOTO_DEST
                            goto_act = ctypes.cast(act_ptr, ctypes.POINTER(_PopplerActionGotoDest)).contents
                            if goto_act.dest:
                                dest = goto_act.dest.contents
                                if dest.page_num >= 1:
                                    target_page = dest.page_num
                                if dest.named_dest:
                                    resolved = cls._lib.poppler_document_find_dest(doc_handle, dest.named_dest)
                                    if resolved:
                                        try:
                                            if resolved.contents.page_num >= 1:
                                                target_page = resolved.contents.page_num
                                        finally:
                                            cls._lib.poppler_dest_free(resolved)
                        if title:
                            results.append(BookmarkItem(title=title, page=max(1, target_page), level=level))
                    finally:
                        cls._lib.poppler_action_free(act_ptr)

                child_iter = cls._lib.poppler_index_iter_get_child(it_ptr)
                if child_iter:
                    try:
                        _walk(child_iter, min(level + 1, 6))
                    finally:
                        cls._lib.poppler_index_iter_free(child_iter)

                if not cls._lib.poppler_index_iter_next(it_ptr):
                    break

        try:
            _walk(root_iter, 1)
        finally:
            cls._lib.poppler_index_iter_free(root_iter)
        return results


class PDFEngine:
    """High-level PDF manipulation engine (inspect, merge, split, compress, bookmark)."""

    COMPRESSION_PRESETS = {
        "screen": {
            "label": "Screen (72 dpi — Smallest File)",
            "gs_setting": "/screen",
            "color_dpi": 72,
            "gray_dpi": 72,
            "mono_dpi": 150,
        },
        "ebook": {
            "label": "eBook (150 dpi — Balanced Quality)",
            "gs_setting": "/ebook",
            "color_dpi": 150,
            "gray_dpi": 150,
            "mono_dpi": 300,
        },
        "printer": {
            "label": "Printer (300 dpi — High Quality)",
            "gs_setting": "/printer",
            "color_dpi": 300,
            "gray_dpi": 300,
            "mono_dpi": 600,
        },
        "prepress": {
            "label": "Prepress (300 dpi — Maximum Color Fidelity)",
            "gs_setting": "/prepress",
            "color_dpi": 300,
            "gray_dpi": 300,
            "mono_dpi": 600,
        },
    }

    _RE_TOC_CONTAINER = re.compile(
        r"^(Chapter|CHAPTER|Part|PART|Unit|UNIT|Module|MODULE|Section|SECTION|Appendix|APPENDIX|Book|BOOK)\s+([0-9IVXivxA-Z]+)\b[:.\-\s]*(.*)$"
    )
    _RE_TOC_DOTS = re.compile(r"^(.+?)\s*(?:\.{2,}|·{2,}|…+|\s{2,})\s*(\d{1,4})\s*$")
    _RE_TOC_TIGHT = re.compile(r"^(.+?[A-Za-z\)])\.(\d{1,4})\s*$")
    _RE_TOC_SUBSECTION = re.compile(r"^(\d+\.\d+|[A-Z]\.\d+)\s+")

    @staticmethod
    def format_size(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        val = float(num_bytes)
        for unit in ("KB", "MB", "GB"):
            val /= 1024.0
            if val < 1024.0 or unit == "GB":
                return f"{val:.2f} {unit}"
        return f"{num_bytes} B"

    @staticmethod
    def _ensure_gs() -> str:
        gs = shutil.which("gs")
        if not gs:
            raise RuntimeError("Ghostscript ('gs') is not installed or not in PATH.")
        return gs

    @classmethod
    def inspect_pdf(cls, pdf_path: str) -> PDFInfo:
        """Read page count, dimensions, file size, and bookmark count."""
        abs_path = os.path.abspath(pdf_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f"PDF file not found: {abs_path}")
        size = os.path.getsize(abs_path)
        pages = 0
        w, h = 595.0, 842.0
        bm_count = 0
        title = os.path.splitext(os.path.basename(abs_path))[0]

        if PopplerBridge.is_available():
            doc = PopplerBridge.open_document(abs_path)
            if doc:
                try:
                    pages = PopplerBridge.get_n_pages(doc)
                    if pages > 0:
                        w, h = PopplerBridge.get_page_size(doc, 0)
                    bm_count = len(PopplerBridge.get_pdf_bookmarks(doc))
                finally:
                    PopplerBridge.close_document(doc)

        if pages == 0 and shutil.which("pdfinfo"):
            proc = subprocess.run(
                ["pdfinfo", "--", abs_path],
                capture_output=True, text=True, errors="replace", check=False,
            )
            for line in proc.stdout.splitlines():
                if line.lower().startswith("pages:"):
                    pages = max(1, int(line.split(":", 1)[1].strip()))
                elif line.lower().startswith("title:"):
                    t = line.split(":", 1)[1].strip()
                    if t:
                        title = t

        return PDFInfo(
            path=abs_path,
            page_count=max(0, pages),
            file_size=size,
            page_width=w,
            page_height=h,
            bookmark_count=bm_count,
            title=title,
        )

    @classmethod
    def get_bookmarks(cls, pdf_path: str) -> List[BookmarkItem]:
        """Return embedded bookmarks from a PDF."""
        if not os.path.isfile(pdf_path) or not PopplerBridge.is_available():
            return []
        doc = PopplerBridge.open_document(pdf_path)
        if not doc:
            return []
        try:
            return PopplerBridge.get_pdf_bookmarks(doc)
        finally:
            PopplerBridge.close_document(doc)

    @staticmethod
    def _split_columns_on_page(txt: str, rects: List[Tuple[float, float, float, float]], page_w: float) -> List[str]:
        segments = []
        cur: List[str] = []
        cur_x = 0.0
        cur_y = 0.0
        prev_x2 = None
        for ch, rc in zip(txt, rects):
            if ch == "\n":
                if cur:
                    segments.append((cur_x, cur_y, "".join(cur).strip()))
                cur = []
                prev_x2 = None
            else:
                if prev_x2 is not None and (rc[0] - prev_x2) > 16.0:
                    if cur:
                        segments.append((cur_x, cur_y, "".join(cur).strip()))
                    cur = []
                    cur_x = rc[0]
                    cur_y = rc[1]
                elif not cur:
                    cur_x = rc[0]
                    cur_y = rc[1]
                cur.append(ch)
                prev_x2 = rc[2]
        if cur:
            segments.append((cur_x, cur_y, "".join(cur).strip()))

        cleaned = [
            (x, y, re.sub(r"[\x00-\x1f]+", " ", s).strip())
            for x, y, s in segments
            if s.strip()
        ]
        mid = page_w * 0.45
        left = [(y, s) for x, y, s in cleaned if x < mid]
        right = [(y, s) for x, y, s in cleaned if x >= mid]
        left_alpha = sum(1 for _, s in left if re.search(r"[A-Za-z]{2,}", s))
        right_alpha = sum(1 for _, s in right if re.search(r"[A-Za-z]{2,}", s))
        if (len(left) >= 4 and len(right) >= 4) or (left_alpha >= 3 and right_alpha >= 3):
            left.sort(key=lambda t: t[0])
            right.sort(key=lambda t: t[0])
            return [s for _, s in left] + [s for _, s in right]
        return [s for _, _, s in cleaned]

    @classmethod
    def auto_detect_chapters(cls, pdf_path: str) -> List[BookmarkItem]:
        """Auto-detect hierarchical chapters & sections for blank-bookmark PDFs."""
        if not os.path.isfile(pdf_path) or not PopplerBridge.is_available():
            return []
        doc = PopplerBridge.open_document(pdf_path)
        if not doc:
            return []
        try:
            page_count = PopplerBridge.get_n_pages(doc)
            if page_count <= 0:
                return []

            # Tier 1: Printed TOC with 2-column splitting & offset calibration
            toc_items = cls._extract_toc_chapters(doc, page_count)
            if len(toc_items) >= 2:
                return toc_items

            # Tier 2: In-body Chapter / Part / Module / Unit / Appendix headers
            body_items = cls._extract_body_chapters(doc, page_count)
            if len(body_items) >= 2:
                return body_items

            # Tier 3: Typographic heading detection via font bounding-box heights
            font_items = cls._extract_font_headings(doc, page_count)
            if len(font_items) >= 2:
                return font_items

            return []
        finally:
            PopplerBridge.close_document(doc)

    @classmethod
    def _extract_toc_chapters(cls, doc, page_count: int) -> List[BookmarkItem]:
        max_scan = min(18, page_count)
        raw_items: List[BookmarkItem] = []
        seen = set()
        toc_pages: List[int] = []
        started = False

        for p in range(1, max_scan + 1):
            txt, rects = PopplerBridge.get_page_text_layout(doc, p - 1)
            if not txt:
                continue
            pw, _ = PopplerBridge.get_page_size(doc, p - 1)
            lines = cls._split_columns_on_page(txt, rects, pw)
            has_toc_kw = any(re.search(r"\b(table of contents|contents)\b", l, re.I) for l in lines[:6])
            matches = [l for l in lines if cls._RE_TOC_DOTS.match(l) or cls._RE_TOC_TIGHT.match(l)]

            if (has_toc_kw and len(matches) >= 2) or (started and len(matches) >= 4):
                page_items = []
                dup_count = 0
                idx = 0
                while idx < len(lines):
                    l = lines[idx]
                    idx += 1
                    if re.match(r"^(table of contents|contents|\d{1,4})$", l, re.I) or (
                        "table of contents" in l.lower() and not cls._RE_TOC_DOTS.match(l)
                    ):
                        continue
                    m = cls._RE_TOC_DOTS.match(l) or cls._RE_TOC_TIGHT.match(l)
                    if m:
                        title = m.group(1).strip(" .·…")
                        pg = int(m.group(2))
                        if 1 <= pg <= page_count + 40 and len(title) >= 2:
                            is_chap = bool(
                                cls._RE_TOC_CONTAINER.match(title)
                                or re.match(r"^(Answers|Index|Glossary|Appendix|Preface|Solutions|Bibliography)", title, re.I)
                            )
                            is_sub = bool(cls._RE_TOC_SUBSECTION.match(title))
                            lvl = 1 if (is_chap or not is_sub) else 2
                            key = (title.lower(), pg)
                            if key in seen:
                                dup_count += 1
                            else:
                                page_items.append((key, BookmarkItem(title=title, page=pg, level=lvl)))
                    elif cls._RE_TOC_CONTAINER.match(l):
                        if idx < len(lines):
                            nxt = lines[idx]
                            m_nxt = cls._RE_TOC_DOTS.match(nxt) or cls._RE_TOC_TIGHT.match(nxt)
                            if m_nxt and not cls._RE_TOC_CONTAINER.match(nxt) and not cls._RE_TOC_SUBSECTION.match(nxt):
                                combined = f"{l} {m_nxt.group(1).strip(' .·…')}"
                                pg = int(m_nxt.group(2))
                                idx += 1
                                key = (combined.lower(), pg)
                                if key in seen:
                                    dup_count += 1
                                else:
                                    page_items.append((key, BookmarkItem(title=combined, page=pg, level=1)))
                                continue
                        key = (l.lower(), 0)
                        if key in seen:
                            dup_count += 1
                        else:
                            page_items.append((key, BookmarkItem(title=l, page=0, level=1)))

                if started and dup_count > len(page_items):
                    break
                started = True
                toc_pages.append(p)
                for key, it in page_items:
                    seen.add(key)
                    raw_items.append(it)
            elif started:
                break

        if len(raw_items) < 2:
            return []

        votes: Dict[int, int] = {}
        toc_end_page = toc_pages[-1]
        for item in raw_items:
            if item.page > 0 and sum(votes.values()) < 5:
                words = [
                    w for w in re.findall(r"[A-Za-z]{4,}", item.title)
                    if w.lower() not in ("chapter", "section", "module", "part", "unit", "introduction", "application")
                ]
                kw = words[0].lower() if words else ""
                if kw:
                    for off in [0] + list(range(1, 35)) + list(range(-1, -6, -1)):
                        cand_p = item.page + off
                        if toc_end_page < cand_p <= page_count:
                            ptxt, _ = PopplerBridge.get_page_text_layout(doc, cand_p - 1)
                            if kw in ptxt.lower():
                                votes[off] = votes.get(off, 0) + 1
                                break

        offset = max(votes.items(), key=lambda kv: kv[1])[0] if votes else 0

        for i, item in enumerate(raw_items):
            if item.page > 0:
                item.page = max(1, min(item.page + offset, page_count))
            else:
                next_p = next((raw_items[j].page for j in range(i + 1, len(raw_items)) if raw_items[j].page > 0), 1)
                item.page = max(1, min(next_p + (offset if next_p + offset <= page_count else 0), page_count))
        return raw_items

    @classmethod
    def _extract_body_chapters(cls, doc, page_count: int) -> List[BookmarkItem]:
        scan_limit = min(page_count, 250)
        line_freq: Dict[str, int] = {}
        page_first_lines: List[Tuple[int, List[str]]] = []

        for p in range(1, scan_limit + 1):
            txt, _ = PopplerBridge.get_page_text_layout(doc, p - 1)
            lines = [re.sub(r"\s+", " ", l).strip() for l in txt.splitlines() if l.strip()]
            page_first_lines.append((p, lines[:8]))
            for l in lines[:3]:
                low = l.lower()
                line_freq[low] = line_freq.get(low, 0) + 1

        max_repeat = max(3, int(scan_limit * 0.15))
        results: List[BookmarkItem] = []
        seen_keys = set()

        for p, top_lines in page_first_lines:
            i = 0
            while i < len(top_lines):
                l = top_lines[i]
                low = l.lower()
                i += 1
                if line_freq.get(low, 0) > max_repeat or len(l) > 95:
                    continue
                m = cls._RE_TOC_CONTAINER.match(l)
                if m:
                    title = l
                    rest = m.group(3).strip()
                    if not rest and i < len(top_lines):
                        nxt = top_lines[i]
                        if 3 <= len(nxt) <= 75 and not cls._RE_TOC_CONTAINER.match(nxt) and not re.match(r"^\d+$", nxt):
                            title = f"{l}: {nxt}"
                            i += 1
                    key = re.sub(r"[^a-z0-9]+", "", title.lower())[:28]
                    if key not in seen_keys:
                        seen_keys.add(key)
                        results.append(BookmarkItem(title=title, page=p, level=1))
        return results

    @classmethod
    def _extract_font_headings(cls, doc, page_count: int) -> List[BookmarkItem]:
        heights = []
        per_page_lines = []
        scan_limit = min(page_count, 160)

        for p in range(1, scan_limit + 1):
            txt, rects = PopplerBridge.get_page_text_layout(doc, p - 1)
            if not txt or not rects:
                continue
            lines = []
            cur_chars: List[str] = []
            cur_h: List[float] = []
            cur_y0 = None
            for ch, rc in zip(txt, rects):
                if ch == "\n":
                    if cur_chars:
                        s = re.sub(r"\s+", " ", "".join(cur_chars)).strip()
                        if s:
                            lines.append((statistics.median(cur_h), cur_y0 or 0.0, s))
                    cur_chars = []
                    cur_h = []
                    cur_y0 = None
                else:
                    h = rc[3] - rc[1]
                    if 4.0 <= h <= 72.0:
                        cur_h.append(h)
                        heights.append(h)
                    if cur_y0 is None:
                        cur_y0 = rc[1]
                    cur_chars.append(ch)
            if cur_chars and cur_h:
                s = re.sub(r"\s+", " ", "".join(cur_chars)).strip()
                if s:
                    lines.append((statistics.median(cur_h), cur_y0 or 0.0, s))
            per_page_lines.append((p, lines))

        if len(heights) < 20:
            return []
        med_h = statistics.median(heights)
        heading_thresh = med_h * 1.18

        freq: Dict[str, int] = {}
        for _, lines in per_page_lines:
            for h, _, s in lines:
                if h >= heading_thresh and 3 <= len(s) <= 85:
                    freq[s.lower()] = freq.get(s.lower(), 0) + 1

        max_repeat = max(3, int(scan_limit * 0.15))
        results: List[BookmarkItem] = []
        for p, lines in per_page_lines:
            cands = [
                (h, y, s) for h, y, s in lines
                if h >= heading_thresh and 3 <= len(s) <= 85
                and freq.get(s.lower(), 0) <= max_repeat
                and not re.match(r"^(Page\s+)?\d+$", s, re.I)
                and not re.search(r"[.!?]$", s)
            ]
            if cands:
                cands.sort(key=lambda t: (-t[0], t[1]))
                top_h, _, top_title = cands[0]
                lvl = 1 if top_h >= med_h * 1.45 else 2
                results.append(BookmarkItem(title=top_title, page=p, level=lvl))

        if len(results) > 1 and all(r.level == 2 for r in results):
            for r in results:
                r.level = 1
        return results

    # ----------------------------------------------------------------------
    # PDFmark Encoding & Bookmark Embedding
    # ----------------------------------------------------------------------
    @staticmethod
    def _encode_pdf_text(text: str) -> str:
        """Encode bookmark title safely for PostScript pdfmark (ASCII or UTF-16BE hex)."""
        try:
            text.encode("ascii")
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            return f"({escaped})"
        except UnicodeEncodeError:
            be_bytes = b"\xfe\xff" + text.encode("utf-16-be")
            return f"<{be_bytes.hex().upper()}>"

    @classmethod
    def _build_pdfmark(cls, bookmarks: List[BookmarkItem], max_page: int) -> str:
        """Convert flat BookmarkItem list with levels into valid nested pdfmark /OUT commands."""
        if not bookmarks:
            return ""
        # Normalize levels cleanly so tree counts stay consistent
        items = [
            BookmarkItem(
                title=(b.title.strip() or f"Page {b.page}"),
                page=max(1, min(int(b.page), max(1, max_page))),
                level=max(1, min(int(b.level), 6)),
            )
            for b in bookmarks
        ]

        # Count direct children for each item index in preorder tree
        # First normalize level jumps (a child can be at most parent_level + 1)
        norm_levels = []
        prev_lvl = 0
        for it in items:
            lvl = 1 if prev_lvl == 0 else min(it.level, prev_lvl + 1)
            norm_levels.append(lvl)
            prev_lvl = lvl

        lines = ["[/PageMode /UseOutlines /DOCVIEW pdfmark"]
        n = len(items)
        for i, it in enumerate(items):
            my_lvl = norm_levels[i]
            direct_children = 0
            j = i + 1
            while j < n and norm_levels[j] > my_lvl:
                if norm_levels[j] == my_lvl + 1:
                    direct_children += 1
                j += 1
            title_token = cls._encode_pdf_text(it.title)
            if direct_children > 0:
                lines.append(
                    f"[/Count {direct_children} /Title {title_token} /Page {it.page} /View [/XYZ null null null] /OUT pdfmark"
                )
            else:
                lines.append(
                    f"[/Title {title_token} /Page {it.page} /View [/XYZ null null null] /OUT pdfmark"
                )
        return "\n".join(lines) + "\n"

    @classmethod
    def _strip_existing_outlines(cls, src_pdf: str, temp_dir: str) -> str:
        """Return path to a temp copy with catalog /Outlines removed cleanly."""
        raw = open(src_pdf, "rb").read()
        pattern = re.compile(rb"/Outlines\s+\d+\s+\d+\s+R")
        if pattern.search(raw):
            cleaned = pattern.sub(lambda m: b" " * len(m.group(0)), raw)
            out_clean = os.path.join(temp_dir, "stripped.pdf")
            with open(out_clean, "wb") as f:
                f.write(cleaned)
            return out_clean

        # Fallback: expand object streams via CompatibilityLevel=1.4, then blank /Outlines
        gs = cls._ensure_gs()
        uncomp = os.path.join(temp_dir, "uncomp.pdf")
        subprocess.run(
            [
                gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                f"-sOutputFile={uncomp}", src_pdf,
            ],
            check=True, capture_output=True,
        )
        raw2 = open(uncomp, "rb").read()
        cleaned2 = pattern.sub(lambda m: b" " * len(m.group(0)), raw2)
        out_clean2 = os.path.join(temp_dir, "stripped.pdf")
        with open(out_clean2, "wb") as f:
            f.write(cleaned2)
        return out_clean2

    @classmethod
    def write_bookmarks(
        cls,
        src_pdf: str,
        out_pdf: str,
        bookmarks: List[BookmarkItem],
        replace_existing: bool = True,
    ) -> str:
        """Write hierarchical PDF bookmarks into out_pdf (preserving pages and content)."""
        gs = cls._ensure_gs()
        info = cls.inspect_pdf(src_pdf)
        out_abs = os.path.abspath(out_pdf)
        os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="pdfpanel_bm_") as tmp_dir:
            input_pdf = os.path.abspath(src_pdf)
            if replace_existing and info.bookmark_count > 0:
                input_pdf = cls._strip_existing_outlines(input_pdf, tmp_dir)

            marks_ps = os.path.join(tmp_dir, "marks.ps")
            pm_content = cls._build_pdfmark(bookmarks, max_page=max(1, info.page_count))
            with open(marks_ps, "w", encoding="utf-8") as f:
                f.write(pm_content)

            tmp_out = os.path.join(tmp_dir, "result.pdf")
            cmd = [gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite", f"-sOutputFile={tmp_out}", input_pdf]
            if pm_content.strip():
                cmd.append(marks_ps)
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not os.path.isfile(tmp_out):
                raise RuntimeError(f"Ghostscript bookmark write failed:\n{proc.stderr or proc.stdout}")
            shutil.move(tmp_out, out_abs)
        return out_abs

    # ----------------------------------------------------------------------
    # Merge PDFs
    # ----------------------------------------------------------------------
    @staticmethod
    def parse_page_spec(spec: str, max_pages: int) -> List[int]:
        """Parse page specs like '1-3, 5, 8-10' or 'all' into ordered 1-based page numbers."""
        s = (spec or "").strip().lower()
        if not s or s in ("all", "*"):
            return list(range(1, max_pages + 1))
        pages: List[int] = []
        for part in s.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a_s, b_s = part.split("-", 1)
                a = max(1, int(a_s.strip())) if a_s.strip() else 1
                b = min(max_pages, int(b_s.strip())) if b_s.strip() else max_pages
                if a <= b:
                    pages.extend(range(a, b + 1))
                else:
                    pages.extend(range(a, b - 1, -1))
            else:
                pg = int(part)
                if 1 <= pg <= max_pages:
                    pages.append(pg)
        if not pages:
            raise ValueError(f"No valid pages resolved from specification '{spec}' (max={max_pages}).")
        return pages

    @classmethod
    def merge_pdfs(
        cls,
        sources: List[Tuple[str, str]],  # [(path, page_spec), ...]
        out_pdf: str,
        add_file_bookmarks: bool = True,
    ) -> Tuple[str, int]:
        """Merge multiple PDF files (optionally with page specs and per-file bookmarks).
        Returns (out_pdf_abs, total_pages).
        """
        if not sources:
            raise ValueError("Add at least one PDF file to merge.")
        gs = cls._ensure_gs()
        out_abs = os.path.abspath(out_pdf)
        os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="pdfpanel_merge_") as tmp_dir:
            prepared_files: List[str] = []
            merged_bookmarks: List[BookmarkItem] = []
            running_page = 1

            for idx, (src_path, page_spec) in enumerate(sources, start=1):
                info = cls.inspect_pdf(src_path)
                selected = cls.parse_page_spec(page_spec, max(1, info.page_count))

                is_full_contiguous = selected == list(range(1, info.page_count + 1))
                if is_full_contiguous:
                    prep = os.path.abspath(src_path)
                else:
                    # Extract exact page subset preserving order
                    prep = os.path.join(tmp_dir, f"subset_{idx}.pdf")
                    if shutil.which("pdfseparate") and shutil.which("pdfunite"):
                        sep_dir = os.path.join(tmp_dir, f"sep_{idx}")
                        os.makedirs(sep_dir, exist_ok=True)
                        subprocess.run(
                            ["pdfseparate", os.path.abspath(src_path), os.path.join(sep_dir, "p_%05d.pdf")],
                            check=True, capture_output=True,
                        )
                        pick_files = [os.path.join(sep_dir, f"p_{pg:05d}.pdf") for pg in selected]
                        if len(pick_files) == 1:
                            shutil.copyfile(pick_files[0], prep)
                        else:
                            subprocess.run(["pdfunite", *pick_files, prep], check=True, capture_output=True)
                    else:
                        # Ghostscript PageList fallback
                        page_list = ",".join(str(p) for p in selected)
                        subprocess.run(
                            [
                                gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite",
                                f"-sPageList={page_list}", f"-sOutputFile={prep}",
                                os.path.abspath(src_path),
                            ],
                            check=True, capture_output=True,
                        )
                prepared_files.append(prep)

                if add_file_bookmarks:
                    label = os.path.splitext(os.path.basename(src_path))[0]
                    merged_bookmarks.append(BookmarkItem(title=label, page=running_page, level=1))
                running_page += len(selected)

            tmp_out = os.path.join(tmp_dir, "merged.pdf")
            if add_file_bookmarks and merged_bookmarks:
                marks_ps = os.path.join(tmp_dir, "merge_marks.ps")
                with open(marks_ps, "w", encoding="utf-8") as f:
                    f.write(cls._build_pdfmark(merged_bookmarks, max_page=max(1, running_page - 1)))
                cmd = [
                    gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite",
                    f"-sOutputFile={tmp_out}", *prepared_files, marks_ps,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if proc.returncode != 0 or not os.path.isfile(tmp_out):
                    raise RuntimeError(f"Merge failed:\n{proc.stderr or proc.stdout}")
            else:
                if shutil.which("pdfunite") and len(prepared_files) > 1:
                    proc = subprocess.run(["pdfunite", *prepared_files, tmp_out], capture_output=True, text=True, check=False)
                    if proc.returncode != 0:
                        subprocess.run(
                            [gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite", f"-sOutputFile={tmp_out}", *prepared_files],
                            check=True, capture_output=True,
                        )
                elif len(prepared_files) == 1:
                    shutil.copyfile(prepared_files[0], tmp_out)
                else:
                    subprocess.run(
                        [gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite", f"-sOutputFile={tmp_out}", *prepared_files],
                        check=True, capture_output=True,
                    )

            shutil.move(tmp_out, out_abs)
        return out_abs, max(0, running_page - 1)

    # ----------------------------------------------------------------------
    # Split PDFs
    # ----------------------------------------------------------------------
    @staticmethod
    def _sanitize_slug(text: str) -> str:
        norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", norm).strip("._-")
        return slug[:60] or "section"

    @classmethod
    def split_pdf(
        cls,
        src_pdf: str,
        output_dir: str,
        mode: str = "chunks",  # "ranges" | "chunks" | "extract" | "bookmarks"
        value: str = "1",
        prefix: Optional[str] = None,
    ) -> List[str]:
        """Split a PDF according to mode:
        - 'ranges': value e.g. '1-3; 4-7; 8-10' or comma/semicolon groups
        - 'chunks': value is integer chunk size N (e.g. '1' for single pages, '5' for 5-page parts)
        - 'extract': value e.g. '1,3,5-8' combined into a single extracted PDF
        - 'bookmarks': split at top-level bookmark / auto-chapter boundaries
        Returns list of generated PDF file paths.
        """
        gs = cls._ensure_gs()
        info = cls.inspect_pdf(src_pdf)
        total = max(1, info.page_count)
        base = prefix or cls._sanitize_slug(os.path.splitext(os.path.basename(src_pdf))[0])
        out_dir = os.path.abspath(output_dir)
        os.makedirs(out_dir, exist_ok=True)

        groups: List[Tuple[str, List[int]]] = []

        if mode == "chunks":
            step = max(1, int((value or "1").strip()))
            part_idx = 1
            for start in range(1, total + 1, step):
                end = min(total, start + step - 1)
                groups.append((f"{base}_part_{part_idx:03d}_p{start}-{end}.pdf", list(range(start, end + 1))))
                part_idx += 1
        elif mode == "extract":
            pages = cls.parse_page_spec(value, total)
            groups.append((f"{base}_extracted.pdf", pages))
        elif mode == "ranges":
            raw_groups = [g.strip() for g in re.split(r"[;\n]+", value or "") if g.strip()]
            if not raw_groups:
                raise ValueError("Enter one or more page ranges separated by semicolons (e.g. 1-3; 4-8).")
            for idx, rg in enumerate(raw_groups, start=1):
                pages = cls.parse_page_spec(rg, total)
                groups.append((f"{base}_part_{idx:02d}.pdf", pages))
        elif mode == "bookmarks":
            bms = cls.get_bookmarks(src_pdf)
            if not bms:
                bms = cls.auto_detect_chapters(src_pdf)
            top_bms = [b for b in bms if b.level == 1] or bms
            if not top_bms:
                raise ValueError("No bookmarks or chapters found to split by.")
            # Deduplicate starting pages cleanly in ascending order
            seen_p = set()
            starts: List[Tuple[int, str]] = []
            for b in sorted(top_bms, key=lambda x: x.page):
                pg = max(1, min(b.page, total))
                if pg not in seen_p:
                    seen_p.add(pg)
                    starts.append((pg, b.title))
            for idx, (sp, title) in enumerate(starts):
                ep = (starts[idx + 1][0] - 1) if (idx + 1 < len(starts)) else total
                if ep < sp:
                    ep = sp
                slug = cls._sanitize_slug(title)
                groups.append((f"{idx + 1:02d}_{slug}.pdf", list(range(sp, ep + 1))))
        else:
            raise ValueError(f"Unknown split mode: {mode}")

        created: List[str] = []
        with tempfile.TemporaryDirectory(prefix="pdfpanel_split_") as tmp_dir:
            use_poppler_tools = bool(shutil.which("pdfseparate") and shutil.which("pdfunite"))
            sep_dir = os.path.join(tmp_dir, "pages")
            separated = False

            for filename, pages in groups:
                target = os.path.join(out_dir, filename)
                is_contig = pages == list(range(pages[0], pages[-1] + 1)) and len(pages) == (pages[-1] - pages[0] + 1)
                if is_contig and not (len(pages) > 1 and not use_poppler_tools):
                    # Ghostscript range is fast & self-contained; or pdfseparate for single page
                    if len(pages) == 1 and shutil.which("pdfseparate"):
                        subprocess.run(
                            ["pdfseparate", "-f", str(pages[0]), "-l", str(pages[0]),
                             os.path.abspath(src_pdf), target],
                            check=True, capture_output=True,
                        )
                    else:
                        subprocess.run(
                            [
                                gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite",
                                f"-dFirstPage={pages[0]}", f"-dLastPage={pages[-1]}",
                                f"-sOutputFile={target}", os.path.abspath(src_pdf),
                            ],
                            check=True, capture_output=True,
                        )
                elif use_poppler_tools:
                    if not separated:
                        os.makedirs(sep_dir, exist_ok=True)
                        subprocess.run(
                            ["pdfseparate", os.path.abspath(src_pdf), os.path.join(sep_dir, "p_%05d.pdf")],
                            check=True, capture_output=True,
                        )
                        separated = True
                    pick = [os.path.join(sep_dir, f"p_{p:05d}.pdf") for p in pages]
                    if len(pick) == 1:
                        shutil.copyfile(pick[0], target)
                    else:
                        subprocess.run(["pdfunite", *pick, target], check=True, capture_output=True)
                else:
                    page_list = ",".join(str(p) for p in pages)
                    subprocess.run(
                        [
                            gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite",
                            f"-sPageList={page_list}", f"-sOutputFile={target}",
                            os.path.abspath(src_pdf),
                        ],
                        check=True, capture_output=True,
                    )
                created.append(target)
        return created

    # ----------------------------------------------------------------------
    # Compress PDFs
    # ----------------------------------------------------------------------
    @classmethod
    def compress_pdf(
        cls,
        src_pdf: str,
        out_pdf: str,
        preset: str = "ebook",
        custom_dpi: Optional[int] = None,
        grayscale: bool = False,
    ) -> Tuple[str, int, int]:
        """Compress src_pdf to out_pdf using Ghostscript.
        Returns (out_pdf_abs, original_bytes, compressed_bytes).
        """
        gs = cls._ensure_gs()
        src_abs = os.path.abspath(src_pdf)
        out_abs = os.path.abspath(out_pdf)
        if not os.path.isfile(src_abs):
            raise FileNotFoundError(f"PDF file not found: {src_abs}")
        orig_size = os.path.getsize(src_abs)
        os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)

        cfg = cls.COMPRESSION_PRESETS.get(preset, cls.COMPRESSION_PRESETS["ebook"])
        dpi = int(custom_dpi) if custom_dpi and custom_dpi >= 36 else cfg["color_dpi"]

        with tempfile.TemporaryDirectory(prefix="pdfpanel_comp_") as tmp_dir:
            tmp_out = os.path.join(tmp_dir, "compressed.pdf")
            cmd = [
                gs,
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.5",
                f"-dPDFSETTINGS={cfg['gs_setting']}",
                "-dNOPAUSE",
                "-dQUIET",
                "-dBATCH",
                "-dDetectDuplicateImages=true",
                "-dCompressFonts=true",
                "-dSubsetFonts=true",
                "-dCompressStreams=true",
                "-dDownsampleColorImages=true",
                "-dDownsampleGrayImages=true",
                "-dDownsampleMonoImages=true",
                f"-dColorImageResolution={dpi}",
                f"-dGrayImageResolution={dpi}",
                f"-dMonoImageResolution={max(dpi, cfg['mono_dpi'])}",
            ]
            if grayscale:
                cmd.extend(["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray"])
            cmd.extend([f"-sOutputFile={tmp_out}", src_abs])

            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not os.path.isfile(tmp_out):
                raise RuntimeError(f"PDF compression failed:\n{proc.stderr or proc.stdout}")
            shutil.move(tmp_out, out_abs)

        new_size = os.path.getsize(out_abs)
        return out_abs, orig_size, new_size
