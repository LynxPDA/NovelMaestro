#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбивает EPUB / ZIP / TXT на главы.

Обычный режим:   chapters/00000_1_Заголовок/chapter.txt
Polished режим:  chapters/00000_1_Заголовок/polished.txt

Архив: текст из всех HTML конкатенируется (с заголовками из <title>),
разбивается по маркерам секций (главы, прологи, эпилоги, экстра).
"""

import argparse
import re
import sys
import os
import zipfile
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

# ── bootstrap: поиск core/ подъёмом от скрипта ──
def _bootstrap_core() -> None:
    from pathlib import Path as _P
    p = _P(os.path.dirname(os.path.abspath(__file__)))
    for _ in range(6):
        if (p / "core" / "common.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            return
        if p.parent == p:
            break
        p = p.parent

_bootstrap_core()

# ======================== HTML → TEXT ==================================
SKIP_TAGS  = {"script", "style", "head", "title", "svg", "rt", "rp"}
BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "ul", "ol", "blockquote", "pre", "tr", "table",
    "section", "article", "header", "footer", "hr",
    "dt", "dd", "figure", "figcaption", "aside",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif self._skip == 0:
            if tag in BLOCK_TAGS:
                self.parts.append("\n\n")
            elif tag == "br":
                self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif self._skip == 0 and tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._skip == 0:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    ex = _TextExtractor()
    ex.feed(markup)
    ex.close()
    return "".join(ex.parts)


# ======================== ПРЕСЕТЫ ЯЗЫКОВ ===============================
LANG_PRESETS: dict[str, dict[str, str]] = {
    "zh": {
        # БЕЗ .*? — иначе ложные маркеры вида «详见第5章…».
        # Маркер — целая строка (с допуском префикса книги/тома).
        "chapter_re":  r"第\d+章",
        "front_re":    r"序章|前言|楔子|引子",
        # extra_re намеренно отключён: 番外 / 特典 / 附錄 / 後記
        # дают ложные срабатывания внутри основного текста.
        # "extra_re": r"番外|特典|附錄|後記",
        "epilogue_re": r"尾聲|結語",
        "page_re":     r"^[ \t]*第\d+頁[ \t]*$",
        # book_re — ТОЛЬКО для clean_heading (префикс книги в заголовке)
        # и как допустимый префикс строки-маркера. К телу главы не применяется.
        "book_re":     r"《[^》]*》",
        # Фоллбэк-маркер: китайские числительные (第一章), если арабских
        # (第1章) в тексте нет вообще.
        "chapter_cn_re": r"第[一二三四五六七八九十百千零两]+章",
        # Префикс тома перед маркером: 卷二 第5章 / 第二卷 第5章
        "volume_re":   r"卷[一二三四五六七八九十百千零两]+|第[一二三四五六七八九十百千零两]+卷",
        # Дубли заголовков глав с иероглифическими цифрами (第三章 и т. п.)
        # — удаляются из тела, когда рядом есть арабский эквивалент (第3章).
        "dup_chapter_re":
                       r"^[ \t]*第[一二三四五六七八九十百千零两]+章[^\n]*$",
        "end_re":      r"[\s（(]*本章完[)）]\s*$",
        "note_re":     r"^作者有話要說[：:]?[^\n]*(?:\n(?!\n)[^\n]*)*",
        # Хвост заголовка: 第1章 神秘道种 (完)
        "heading_tail_re": r"[\s（(【]*完[)）】]*\s*$",
    },        
    "en": {
        "chapter_re":  r"Chapter\s+\d+",
        "front_re":    r"Prologue|Preface|Introduction|Foreword",
        "extra_re":    r"Extra|Bonus|Side\s+Story|Appendix",
        "epilogue_re": r"Epilogue|Afterword",
        "page_re":     r"^[ \t]*Page\s+\d+[ \t]*$",
        "book_re":     r"",
        "dup_chapter_re": r"",
        "end_re":      r"[\s(]*\[?The End\]?[)]\s*$",
        "note_re":     r"^Author'?s Note[：:]?[^\n]*(?:\n(?!\n)[^\n]*)*",
        "volume_re":   r"Book\s+\d+|Volume\s+\d+",
        "heading_tail_re": r"[\s(]*\[?The End\]?[)]?\s*$",
    },
    "ru": {
        "chapter_re":  r"Глава\s+\d+",
        "front_re":    r"Пролог|Предисловие|Введение|Предыстория",
        "extra_re":    r"Экстра|Бонус|Приложение|Дополнение",
        "epilogue_re": r"Эпилог|Послесловие|Заключение",
        "page_re":     r"^[ \t]*Страница\s+\d+[ \t]*$",
        "book_re":     r"",
        "dup_chapter_re": r"",
        "end_re":      r"[\s(]*Конец главы[)]\s*$",
        "note_re":     r"^Примечание автора[：:]?[^\n]*(?:\n(?!\n)[^\n]*)*",
        "volume_re":   r"Том\s+\d+",
        "heading_tail_re": r"[\s(]*Конец главы[)]?\s*$",
    },
}

LANG_LABELS = {"zh": "中文", "en": "English", "ru": "Русский"}

EXCLUDE_NAMES = {
    "cover.xhtml", "cover.html", "cover.htm",
    "toc.xhtml",   "toc.html",   "toc.htm",
    "nav.xhtml",   "nav.html",   "nav.htm",
}

TITLE_RE    = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
H1_RE       = re.compile(r"<h1[^>]*>(.*?)</h1>",       re.S | re.I)
TAG_RE      = re.compile(r"<[^>]+>")
WS_RE       = re.compile(r"\s+")
CTRL_RE     = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MULTI_NL_RE = re.compile(r"\n{3,}")
LEADING_WS_RE = re.compile(r"^[ \t]+", re.M)
_FW_TABLE   = str.maketrans("０１２３４５６７８９", "0123456789")
BADCHAR_RE  = re.compile(r'[/\\?%*:|"<>(),，（）\s]')
MAX_FOLDER  = 150


# =========================== ХЕЛПЕРЫ ===================================
def norm_fw(s: str) -> str:
    """Полноширинные цифры → ASCII; идеографический пробел → обычный."""
    s = s.translate(_FW_TABLE)
    s = s.replace("\u3000", " ")
    return s


def normalize_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def decode_bytes(data: bytes) -> str:
    m = re.search(rb'encoding=["\']([\w-]+)', data[:200])
    encodings = ([m.group(1).decode()] if m else []) + [
        "utf-8-sig", "utf-8", "gb18030", "big5", "latin-1",
    ]
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def nat_key(name: str):
    out = []
    for p in re.split(r"(\d+)", name):
        try:
            out.append(int(p) if p.isdigit() else p.lower())
        except ValueError:
            out.append(p.lower())
    return out


def extract_title_html(markup: str) -> str:
    m = TITLE_RE.search(markup) or H1_RE.search(markup)
    if not m:
        return ""
    raw = TAG_RE.sub("", m.group(1))
    raw = unescape(raw)
    raw = norm_fw(raw)
    return WS_RE.sub(" ", raw).strip()


def safe_folder(title: str) -> str:
    t = CTRL_RE.sub("", title)
    t = BADCHAR_RE.sub("_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t[:MAX_FOLDER].rstrip("_") or "Chapter"


def first_line(text: str, max_len: int = 40) -> str:
    for line in text.split("\n"):
        s = line.strip()
        if s:
            return s[:max_len]
    return ""


def title_already_in_text(title: str, text: str) -> bool:
    if not title:
        return True
    t = title.strip()
    head = text.strip()[:500]
    return t in head


# ── Очистка заголовка от названия новеллы ─────────────────────────────
#   book_re применяется ЗДЕСЬ и НЕ применяется в apply_cleanings.
def clean_heading(heading: str, pat,
                  book_title: str | None = None) -> str:
    h = heading.strip()
    if not h:
        return h

    # 1. Явно заданное название (--book-title)
    if book_title:
        escaped = re.escape(book_title.strip())
        h = re.sub(
            r'^[\s\-–—:：.。,，]*' + escaped + r'[\s\-–—:：.。,，]*',
            '', h, count=1, flags=re.I,
        )

    # 2. book_re из пресета (《…》 и т. п.) — только для heading
    if pat.book_re:
        h = pat.book_re.sub('', h)

    # 3. Если маркер секции внутри строки — всё до него отбрасываем
    if pat.section_re:
        m = pat.section_re.search(h)
        if m and m.start() > 0:
            h = h[m.start():]

    # 4. Начальные / конечные разделители
    h = re.sub(r'^[\s\-–—:：.。,，]+', '', h)
    h = re.sub(r'[\s\-–—:：.。,，]+$', '', h)

    # 5. Мусор в конце заголовка: (完), (Конец главы), (The End)
    if pat.heading_tail_re:
        h = pat.heading_tail_re.sub('', h)

    h = WS_RE.sub(' ', h).strip()

    return h

# ======================== ПАТТЕРНЫ =====================================
def _safe_compile(pattern: str, flags: int, name: str):
    if not pattern:
        return None
    try:
        return re.compile(pattern, flags)
    except re.error as e:
        sys.exit(f"  Ошибка в паттерне --{name}: {e}\n"
                 f"  Паттерн: {pattern}")


class Patterns:
    def __init__(self, preset: dict, overrides: dict):
        merged = {**preset}
        for k, v in overrides.items():
            if v is not None:
                merged[k] = v

        self.chapter_re  = _safe_compile(merged.get("chapter_re", ""),
                                         re.M | re.I, "chapter-re")
        self.front_re    = _safe_compile(merged.get("front_re", ""),
                                         re.M | re.I, "front-re")
        self.extra_re    = _safe_compile(merged.get("extra_re", ""),
                                         re.M | re.I, "extra-re")
        self.epilogue_re = _safe_compile(merged.get("epilogue_re", ""),
                                         re.M | re.I, "epilogue-re")
        self.page_re     = _safe_compile(merged.get("page_re", ""),
                                         re.M | re.I, "page-re")
        # book_re — только для clean_heading, НЕ для apply_cleanings
        self.book_re     = _safe_compile(merged.get("book_re", ""),
                                         re.M, "book-re")
        self.dup_chapter_re = _safe_compile(
                                         merged.get("dup_chapter_re", ""),
                                         re.M, "dup-chapter-re")
        self.end_re      = _safe_compile(merged.get("end_re", ""),
                                         re.M, "end-re")
        self.note_re     = _safe_compile(merged.get("note_re", ""),
                                         re.M, "note-re")
        # Фоллбэк-маркер (第一章), префикс тома, хвост заголовка
        self.chapter_cn_re = _safe_compile(merged.get("chapter_cn_re", ""),
                                           re.M | re.I, "chapter-cn-re")
        self.volume_re     = _safe_compile(merged.get("volume_re", ""),
                                           re.M | re.I, "volume-re")
        self.heading_tail_re = _safe_compile(merged.get("heading_tail_re", ""),
                                             re.M, "heading-tail-re")

        # section_re = объединение всех типов секций
        parts = []
        for key in ("chapter_re", "front_re", "extra_re", "epilogue_re"):
            p = merged.get(key, "")
            if p:
                parts.append(f"(?:{p})")
        if parts:
            self.section_pattern = "|".join(parts)
            self.section_re = _safe_compile(self.section_pattern,
                                            re.M | re.I, "section")
        else:
            self.section_pattern = None
            self.section_re = None

        # Числовой префикс перед главами
        ch = merged.get("chapter_re", "")
        if ch:
            self.num_prefix_re = _safe_compile(
                r"^[ \t]*[\d.]+\s*(?=" + ch + r")",
                re.M | re.I, "num-prefix")
        else:
            self.num_prefix_re = None

        # Дубль заголовка (одна и та же строка дважды подряд)
        if self.section_pattern:
            self.dup_re = _safe_compile(
                r"^[ \t]*((?:" + self.section_pattern + r")[^\n]*)[ \t]*\n"
                r"(?:[ \t]*\n)*[ \t]*\1[ \t]*\n",
                re.M | re.I, "dup-title")
        else:
            self.dup_re = None

        # txt_marker — компилируем один раз; допускаем префиксы
        # книги (book_re) и тома (volume_re) перед маркером.
        book_prefix = ""
        vol_prefix  = ""
        if merged.get("book_re"):
            book_prefix = f"(?:(?:{merged['book_re']})[\\s]*)?"
        if merged.get("volume_re"):
            vol_prefix = f"(?:(?:{merged['volume_re']})[\\s、.，,]*)?"
        if self.section_pattern:
            self._txt_marker = _safe_compile(
                r"^[ \t]*(" + book_prefix + vol_prefix
                + r"(?:" + self.section_pattern
                + r"))[ \t]*(.*?)[ \t]*$",
                re.M | re.I, "txt-marker")
        else:
            self._txt_marker = None

        # Фоллбэк: китайские числительные (第一章), если основной
        # маркер не дал ни одной главы.
        if merged.get("chapter_cn_re"):
            self._txt_marker_cn = _safe_compile(
                r"^[ \t]*((?:" + merged["chapter_cn_re"]
                + r"))[ \t]*(.*?)[ \t]*$",
                re.M | re.I, "txt-marker-cn")
        else:
            self._txt_marker_cn = None

    def txt_marker(self, cn_fallback=False):
        if cn_fallback and self._txt_marker_cn is not None:
            return self._txt_marker_cn
        return self._txt_marker


# ======================== ЧИСТКИ ТЕЛА ==================================
#   book_re здесь НЕТ — он работает только в clean_heading.
def apply_cleanings(text, title_raw, pat, do_clean, do_pages):
    removed: list[str] = []

    if do_pages and pat.page_re:
        for m in pat.page_re.finditer(text):
            removed.append(m.group(0))
        text = pat.page_re.sub("", text)

    if do_clean:
        for rx in (pat.num_prefix_re, pat.dup_re,
                   pat.dup_chapter_re,
                   pat.end_re, pat.note_re):
            if rx:
                for m in rx.finditer(text):
                    removed.append(m.group(0))
                text = rx.sub("", text)

    # Сжатие пустых строк + удаление ведущих пробелов / табов
    text = MULTI_NL_RE.sub("\n\n", text)
    text = LEADING_WS_RE.sub("", text)

    return text.strip(), removed


# ==================== ЗАПИСЬ СЕКЦИИ ====================================
def write_section(output_dir, counter, heading, body,
                  polished, dry_run=False):
    # Превращаем число в строку
    c_str = str(counter)
    # Считаем количество нулей (как это делал zfill для длины 6)
    zeros = "0" * max(0, 6 - len(c_str))
    # Формируем новый префикс вида 00000_1
    prefix = f"{zeros}_{c_str}" 
    folder_name = f"{prefix}_{safe_folder(heading)}"
    fname = f"chapter{counter}_polished.txt" if polished else "chapter.txt"

    if dry_run:
        print(f"  [{prefix}] {folder_name}/{fname}")
        return

    folder = output_dir / folder_name
    try:
        folder.mkdir(parents=True, exist_ok=True)
        content = (heading + "\n\n" + body + "\n") if body else (heading + "\n")
        (folder / fname).write_text(content, encoding="utf-8")
        print(f"  [{prefix}] {folder_name}/{fname}")
    except OSError as e:
        print(f"  ⚠ Ошибка записи [{prefix}] {folder_name}/{fname}: {e}",
              file=sys.stderr)


# ============================================================
#  ОБЩАЯ ЛОГИКА: разбивка по маркерам секций + запись
# ============================================================
def _find_markers(text, pat):
    """Маркеры: основной → фоллбэк (第一章), если основных нет."""
    markers = []
    if pat.txt_marker():
        markers = list(pat.txt_marker().finditer(text))
    if not markers and pat.txt_marker(cn_fallback=True):
        markers = list(pat.txt_marker(cn_fallback=True).finditer(text))
    return markers


def split_and_write(text, pat, do_clean, do_pages,
                    polished, output_dir,
                    book_title=None, dry_run=False,
                    chunk_size=7000):
    markers = _find_markers(text, pat)

    chapters: list[tuple[str, str]] = []
    removed_all: list[str] = []

    if not markers:
        body, rem = apply_cleanings(text, "", pat, do_clean, do_pages)
        removed_all.extend(rem)
        if body:
            # Фоллбэк: маркеров нет — режем на чанки (нейтральные
            # заголовки «Часть N», НЕ «Глава N» — это канон перевода).
            from core.common import split_text_smart
            chunks = split_text_smart(body, target_chars=chunk_size)
            for i, ch in enumerate(chunks, 1):
                if ch.strip():
                    chapters.append((f"Часть {i}", ch.strip()))
    else:
        # Текст до первого маркера
        if markers[0].start() > 0:
            pre = text[: markers[0].start()]
            pre, rem = apply_cleanings(pre, "", pat, do_clean, do_pages)
            removed_all.extend(rem)
            if pre.strip():
                raw_h   = first_line(pre) or "Пролог"
                heading = clean_heading(raw_h, pat, book_title) or "Пролог"
                chapters.append((heading, pre))

        # Секции по маркерам
        for i, m in enumerate(markers):
            raw_h   = m.group(0).strip()
            heading = clean_heading(raw_h, pat, book_title)
            if not heading:
                heading = f"Секция {i + 1}"

            body_start = m.end()
            body_end   = (markers[i + 1].start()
                          if i + 1 < len(markers) else len(text))
            body = text[body_start:body_end]
            body, rem = apply_cleanings(body, heading, pat,
                                        do_clean, do_pages)
            removed_all.extend(rem)
            # Пустая секция после чисток — не пишем (нет папки)
            if body.strip():
                chapters.append((heading, body))

    s_after = 0
    for idx, (heading, body) in enumerate(chapters, 1):
        write_section(output_dir, idx, heading, body, polished, dry_run)
        s_after += len(heading) + len(body)

    return len(chapters), s_after, removed_all


# ==================== EPUB: порядок из <spine> =========================
def _get_spine_order(zf: zipfile.ZipFile) -> list[str] | None:
    try:
        container = zf.read("META-INF/container.xml").decode(
            "utf-8", errors="replace")
        m = re.search(r'full-path\s*=\s*"([^"]+)"', container, re.I)
        if not m:
            return None
        opf_path = m.group(1)
        opf_raw  = zf.read(opf_path).decode("utf-8", errors="replace")

        manifest: dict[str, str] = {}
        for item_m in re.finditer(r'<item\s[^>]*>', opf_raw, re.I):
            tag    = item_m.group(0)
            id_m   = re.search(r'\bid\s*=\s*"([^"]*)"',   tag, re.I)
            href_m = re.search(r'\bhref\s*=\s*"([^"]*)"', tag, re.I)
            if id_m and href_m:
                manifest[id_m.group(1)] = unquote(href_m.group(1))

        spine_ids = re.findall(
            r'<itemref\s[^>]*idref\s*=\s*"([^"]*)"', opf_raw, re.I)

        opf_dir = str(Path(opf_path).parent)
        if opf_dir == ".":
            opf_dir = ""

        result = []
        for sid in spine_ids:
            href = manifest.get(sid)
            if href:
                result.append((opf_dir + "/" + href) if opf_dir else href)
        return result if result else None
    except (KeyError, Exception):
        return None


SERVICE_TOC_RE = re.compile(
    r"^(информация|信息|info|cover|обложка|содержание|оглавление|about|简介)$",
    re.I)


def _get_toc_titles(zf: zipfile.ZipFile) -> dict[str, str]:
    """basename(файл) → первый заголовок из TOC (toc.ncx / nav.xhtml)."""
    out: dict[str, str] = {}
    for name in zf.namelist():
        low = name.lower()
        if not low.endswith((".ncx", ".xhtml", ".html", ".htm")):
            continue
        base = Path(name).name.lower()
        if not (low.endswith(".ncx") or "toc" in base or "nav" in base):
            continue
        try:
            data = decode_bytes(zf.read(name))
        except KeyError:
            continue
        data = normalize_newlines(data)
        if low.endswith(".ncx"):
            for m in re.finditer(r"<navPoint[^>]*>(.*?)</navPoint>",
                                 data, re.S | re.I):
                block = m.group(1)
                tm = re.search(r"<text>(.*?)</text>", block, re.S | re.I)
                sm = re.search(r'<content\s+src\s*=\s*"([^"]+)"',
                               block, re.I)
                if tm and sm:
                    t = WS_RE.sub(" ", TAG_RE.sub("", tm.group(1))).strip()
                    href = sm.group(1).split("#")[0]
                    out.setdefault(Path(href).name.lower(), t)
        else:
            for a in re.finditer(
                    r'<a\s[^>]*href\s*=\s*"([^"]+)"[^>]*>(.*?)</a>',
                    data, re.S | re.I):
                href = a.group(1).split("#")[0]
                t = WS_RE.sub(" ", TAG_RE.sub("", a.group(2))).strip()
                if href and t:
                    out.setdefault(Path(href).name.lower(), t)
    return out


_H_TAG_RE = re.compile(r"<h([12])[^>]*>(.*?)</h\1>", re.S | re.I)


def split_markup_by_headings(markup, pat, book_title,
                             do_clean, do_pages):
    """Разбить HTML по h1/h2 → [(heading, body)] | None (нет заголовков)."""
    matches = list(_H_TAG_RE.finditer(markup))
    if not matches:
        return None
    sections: list[tuple[str, str]] = []
    # Текст до первого заголовка — «Пролог»
    pre = markup[:matches[0].start()]
    if pre.strip():
        body = html_to_text(pre)
        body = norm_fw(body)
        body = normalize_newlines(body)
        body, _ = apply_cleanings(body, "", pat, do_clean, do_pages)
        if body.strip():
            raw_h = first_line(body) or "Пролог"
            heading = clean_heading(raw_h, pat, book_title) or "Пролог"
            sections.append((heading, body))
    for i, m in enumerate(matches):
        raw_h = TAG_RE.sub("", m.group(2))
        raw_h = unescape(raw_h)
        raw_h = norm_fw(raw_h)
        heading = clean_heading(WS_RE.sub(" ", raw_h).strip(),
                                pat, book_title)
        if not heading:
            heading = f"Секция {len(sections) + 1}"
        body_start = m.end()
        body_end   = (matches[i + 1].start()
                      if i + 1 < len(matches) else len(markup))
        body = html_to_text(markup[body_start:body_end])
        body = norm_fw(body)
        body = normalize_newlines(body)
        body, _ = apply_cleanings(body, heading, pat, do_clean, do_pages)
        if body.strip():
            sections.append((heading, body))
    return sections or None


def _strip_heading_line(text, heading, pat):
    """Убрать первую строку-заголовок (h1-дубль) из начала тела."""
    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines[:5]):
        if not ln.strip():
            continue
        s = ln.strip()
        if heading and (heading in s or s in heading):
            return "".join(lines[i + 1:])
        # строка начинается с маркера секции — это заголовок
        if pat.section_re and pat.section_re.search(s) \
                and pat.section_re.search(s).start() == 0 \
                and len(s) < 80:
            return "".join(lines[i + 1:])
        break
    return text


# ==================== ОБРАБОТКА АРХИВА =================================
def process_archive(arc_path, pat, do_clean, do_pages,
                    polished, output_dir,
                    book_title=None, dry_run=False,
                    chunk_size=7000):
    entries: list[tuple[str, str, str, str]] = []  # name, title, raw, markup

    with zipfile.ZipFile(arc_path) as zf:
        all_html = {
            n for n in zf.namelist()
            if n.lower().endswith((".xhtml", ".html", ".htm"))
            and Path(n).name.lower() not in EXCLUDE_NAMES
            and not n.endswith("/")
        }
        if not all_html:
            # ZIP без HTML, но с txt — разбираем как TXT
            all_txt = sorted(
                (n for n in zf.namelist()
                 if n.lower().endswith(".txt") and not n.endswith("/")),
                key=lambda n: nat_key(Path(n).name))
            if all_txt:
                parts = []
                for name in all_txt:
                    raw = decode_bytes(zf.read(name))
                    raw = normalize_newlines(raw)
                    raw = norm_fw(raw)
                    parts.append(raw)
                full_text = "\n\n".join(parts)
                n, s_after, removed = split_and_write(
                    full_text, pat, do_clean, do_pages, polished,
                    output_dir, book_title, dry_run, chunk_size)
                return n, len(full_text), s_after, removed
            print("  В архиве нет HTML-файлов.")
            return 0, 0, 0, []

        # Порядок: spine → fallback алфавитный
        spine = _get_spine_order(zf)
        if spine:
            names = [n for n in spine if n in all_html]
            remaining = sorted(all_html - set(names),
                               key=lambda n: nat_key(Path(n).name))
            names.extend(remaining)
        else:
            names = sorted(all_html,
                           key=lambda n: nat_key(Path(n).name))

        toc_titles = _get_toc_titles(zf)

        for name in names:
            try:
                markup = decode_bytes(zf.read(name))
            except KeyError:
                continue
            markup = normalize_newlines(markup)
            title  = extract_title_html(markup)
            raw    = html_to_text(markup)
            raw    = norm_fw(raw)
            raw    = normalize_newlines(raw)
            entries.append((name, title, raw, markup))

    # Структурный путь: ≥3 файлов — один файл ≈ одна секция
    if len(entries) >= 3:
        removed_all: list[str] = []
        s_after = 0
        written = 0
        for name, title, raw, markup in entries:
            base = Path(name).name.lower()
            # несколько h1/h2 в файле — режем внутри файла
            sections = split_markup_by_headings(
                markup, pat, book_title, do_clean, do_pages)
            if sections and len(sections) > 1:
                for heading, body in sections:
                    if body.strip():
                        written += 1
                        write_section(output_dir, written, heading, body,
                                      polished, dry_run)
                        s_after += len(heading) + len(body)
                continue

            # Одна секция: заголовок TOC → <title>/<h1> → «Секция N»
            toc_t = toc_titles.get(base, "")
            if toc_t and SERVICE_TOC_RE.match(toc_t.strip()):
                continue  # служебная страница («Информация») — не глава
            heading = toc_t or title or ""
            if not heading:
                continue  # страница без заголовка
            heading = clean_heading(heading, pat, book_title)
            if not heading:
                heading = f"Секция {written + 1}"
            body = _strip_heading_line(raw, heading, pat)
            body, rem = apply_cleanings(body, heading, pat,
                                        do_clean, do_pages)
            removed_all.extend(rem)
            if body.strip():
                written += 1
                write_section(output_dir, written, heading, body,
                              polished, dry_run)
                s_after += len(heading) + len(body)
        return written, sum(len(e[2]) for e in entries), s_after, removed_all

    # 1–2 файла: конкатенация + regex (как раньше)
    parts = []
    for _name, title, text, _markup in entries:
        cleaned = clean_heading(title, pat, book_title) if title else ""
        if cleaned and not title_already_in_text(cleaned, text):
            parts.append(cleaned + "\n" + text)
        else:
            parts.append(text)

    full_text = "\n\n".join(parts)
    s_before  = len(full_text)

    has_markers = bool(_find_markers(full_text, pat))

    if has_markers:
        n, s_after, removed = split_and_write(
            full_text, pat, do_clean, do_pages, polished, output_dir,
            book_title, dry_run, chunk_size)
        return n, s_before, s_after, removed

    # Fallback: маркеров нет → каждый HTML-файл = секция
    removed_all: list[str] = []
    s_after = 0
    written = 0
    for _name, title, raw, _markup in entries:
        body, rem = apply_cleanings(raw, title, pat, do_clean, do_pages)
        removed_all.extend(rem)
        heading = clean_heading(title, pat, book_title) if title else ""
        if not heading:
            heading = f"Глава {written + 1}"
        if body.strip():
            written += 1
            write_section(output_dir, written, heading, body,
                          polished, dry_run)
            s_after += len(heading) + len(body)

    return written, s_before, s_after, removed_all


# ==================== ОБРАБОТКА TXT ====================================
def process_txt(txt_path, pat, do_clean, do_pages,
                polished, output_dir,
                book_title=None, dry_run=False,
                chunk_size=7000):
    data = txt_path.read_bytes()
    raw  = decode_bytes(data)
    raw  = normalize_newlines(raw)
    raw  = norm_fw(raw)
    s_before = len(raw)

    n, s_after, removed = split_and_write(
        raw, pat, do_clean, do_pages, polished, output_dir,
        book_title, dry_run, chunk_size)
    return n, s_before, s_after, removed


# ========================== ARGPARSE ===================================
def build_parser():
    p = argparse.ArgumentParser(
        prog="split_book",
        description="Разбивает EPUB / ZIP / TXT на главы.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Примеры:
  %(prog)s                                  автопоиск в ./source (один файл)
  %(prog)s --input book.epub
  %(prog)s --input trans.txt --polished 1 --lang ru
  %(prog)s --clean 0 --remove-pages 0       без чисток
  %(prog)s --front-re "Пролог|Вступление"   свой паттерн пролога
  %(prog)s --book-title "Название Новеллы"  убрать название из заголовков
  %(prog)s --dry-run                        только показать, не писать

Единицы и форматы:
  сверка ДО/ПОСЛЕ и чистки — в СИМВОЛАХ;
  папки глав — 00000_N_Заголовок (единый канон
  core.common.parse_chapter_id: 00000_1…, 0000_10…, legacy 000001…).
""",
    )
    p.add_argument("--input", type=Path, default=None, metavar="FILE",
                   help="Путь к .epub/.zip/.txt (default: автопоиск)")
    p.add_argument("--source", type=Path, default=Path("./source"),
                   help="Папка для автопоиска (default: ./source)")
    p.add_argument("--output", type=Path, default=Path("chapters"),
                   help="Выходная папка (default: ./chapters)")

    g = p.add_argument_group("Язык и паттерны")
    g.add_argument("--lang", choices=list(LANG_PRESETS), default=None,
                   help="Пресет (default: меню; polished → ru)")
    g.add_argument("--chapter-re",  dest="chapter_re",  metavar="RE",
                   help="Маркер главы")
    g.add_argument("--front-re",    dest="front_re",    metavar="RE",
                   help="Пролог / предисловие")
    g.add_argument("--extra-re",    dest="extra_re",    metavar="RE",
                   help="Экстра / бонус")
    g.add_argument("--epilogue-re", dest="epilogue_re", metavar="RE",
                   help="Эпилог / послесловие")
    g.add_argument("--page-re",     dest="page_re",     metavar="RE",
                   help="Номер страницы")
    g.add_argument("--book-re",     dest="book_re",     metavar="RE",
                   help="Префикс книги в заголовке (regex, только heading)")
    g.add_argument("--book-title",  dest="book_title",  metavar="STR",
                   help="Название книги — удаляется из заголовков секций")
    g.add_argument("--end-re",      dest="end_re",      metavar="RE",
                   help="Маркер конца главы")
    g.add_argument("--note-re",     dest="note_re",     metavar="RE",
                   help="Заметки автора")
    g.add_argument("--volume-re",   dest="volume_re",   metavar="RE",
                   help="Префикс тома перед маркером (Том 2., 卷二)")
    g.add_argument("--heading-tail-re", dest="heading_tail_re",
                   metavar="RE", help="Мусор в конце заголовка ((完), (Конец))")

    g = p.add_argument_group("Чистки")
    g.add_argument("--clean", type=int, choices=[0, 1], default=1,
                   help="Все чистки (default: 1)")
    g.add_argument("--remove-pages", type=int, choices=[0, 1], default=1,
                   help="Номера страниц (default: 1)")

    g = p.add_argument_group("Режим")
    g.add_argument("--polished", type=int, choices=[0, 1], default=None,
                   help="0 = chapter.txt, 1 = polished.txt")
    g.add_argument("--chunk-size", type=int, default=7000, metavar="N",
                   help="Размер чанка фоллбэка, если маркеров нет, "
                        "СИМВОЛЫ (default: 7000)")
    g.add_argument("--clean-output", action="store_true",
                   help="Удалить старые папки глав перед записью")
    g.add_argument("--move-done", action="store_true",
                   help="Перенести файл в done/ после обработки")
    g.add_argument("--report", type=Path, default="./logs/epub_to_txt_clean_report.txt", metavar="FILE",
                   help="Лог удалённых строк")
    g.add_argument("--dry-run", action="store_true",
                   help="Показать список глав без записи на диск")
    return p


# ============================= MAIN ====================================
def main():
    args       = build_parser().parse_args()
    # R9: фактическая команда запуска
    import shlex as _shlex
    import sys as _sys
    print(f"Запуск: {_shlex.join(_sys.argv)}")
    output_dir = args.output.resolve()
    dry_run    = args.dry_run

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Входной файл ──
    if args.input:
        input_file = args.input.resolve()
        if not input_file.is_file():
            sys.exit(f"Файл не найден: {input_file}")
    else:
        src = args.source.resolve()
        if not src.is_dir():
            sys.exit(f"Папка не найдена: {src}")
        exts = {".epub", ".zip", ".txt"}
        candidates = sorted(
            f for f in src.iterdir()
            if f.is_file() and f.suffix.lower() in exts
        )
        if not candidates:
            sys.exit(f"В {src} нет .epub / .zip / .txt")
        if len(candidates) == 1:
            input_file = candidates[0]
            print(f"Найден: {input_file.name}")
        else:
            print(f"В {src} несколько подходящих файлов — укажите --input:")
            for f in candidates:
                print(f"  - {f.name}")
            print("Интерактивный выбор — в лаунчере tools/run_epub_to_chapters.py")
            sys.exit(1)

    suffix = input_file.suffix.lower()
    if suffix not in (".epub", ".zip", ".txt"):
        sys.exit(f"Неподдерживаемый формат: {suffix}")

    # ── 2. Режим ──
    if args.polished is not None:
        polished = bool(args.polished)
    else:
        polished = False  # обычный режим (chapter.txt)

    # ── 3. Язык ──
    if args.lang:
        lang = args.lang
    else:
        lang = "ru" if polished else "zh"

    # ── 4. Паттерны ──
    overrides = {
        "chapter_re":  args.chapter_re,
        "front_re":    args.front_re,
        "extra_re":    args.extra_re,
        "epilogue_re": args.epilogue_re,
        "page_re":     args.page_re,
        "book_re":     args.book_re,
        "end_re":      args.end_re,
        "note_re":     args.note_re,
        "volume_re":   args.volume_re,
        "heading_tail_re": args.heading_tail_re,
    }
    pat        = Patterns(LANG_PRESETS[lang], overrides)
    do_clean   = bool(args.clean)
    do_pages   = bool(args.remove_pages)
    book_title = args.book_title

    # ── 5. Обработка ──
    print(f"\n  Файл:    {input_file.name}")
    print(f"  Пресет:  {lang} ({LANG_LABELS[lang]})")
    print(f"  Режим:   {'polished' if polished else 'обычный'}")
    try:
        _clean_i, _pages_i = int(do_clean), int(do_pages)
    except (TypeError, ValueError):
        _clean_i, _pages_i = 0, 0
    print(f"  Чистки:  clean={_clean_i}  pages={_pages_i}")
    if book_title:
        print(f"  Книга:   «{book_title}» (удаляется из заголовков)")
    print(f"  Выход:   {output_dir}")
    print(f"  Чанк:    {args.chunk_size} симв. (фоллбэк без маркеров)")
    if dry_run:
        print("  *** DRY RUN — файлы не записываются ***")
    print()

    # ── 5.5 Повторный запуск: старые папки глав ──
    old_dirs = []
    if output_dir.is_dir():
        old_dirs = [p for p in output_dir.iterdir()
                    if p.is_dir()
                    and re.match(r"^0*_\d+", p.name)]
    if old_dirs and not dry_run:
        if args.clean_output:
            import shutil
            for d in old_dirs:
                try:
                    shutil.rmtree(d)
                except OSError as e:
                    print(f"  ⚠ Не удалось удалить {d.name}: {e}",
                          file=sys.stderr)
            print(f"  Удалено старых папок глав: {len(old_dirs)}")
        else:
            print(f"  ⚠ В {output_dir} уже есть {len(old_dirs)} папок глав; "
                  "--clean-output удалит их перед записью")
    elif old_dirs and dry_run:
        print(f"  ⚠ В {output_dir} уже есть {len(old_dirs)} папок глав "
              "(dry-run: не трону)")

    if suffix in (".epub", ".zip"):
        n, s_b, s_a, removed = process_archive(
            input_file, pat, do_clean, do_pages, polished, output_dir,
            book_title, dry_run, args.chunk_size)
    else:
        n, s_b, s_a, removed = process_txt(
            input_file, pat, do_clean, do_pages, polished, output_dir,
            book_title, dry_run, args.chunk_size)

    # ── 6. done ──
    if args.move_done and n and not dry_run:
        done = input_file.parent / "done"
        done.mkdir(exist_ok=True)
        dest = done / input_file.name
        if dest.exists():
            stem, ext, k = input_file.stem, input_file.suffix, 1
            while dest.exists():
                dest = done / f"{stem}_{k}{ext}"
                k += 1
        input_file.rename(dest)
        print(f"  Перенесено → {dest}")

    # ── 7. Сверка (приближённая) ──
    delta     = s_b - s_a
    rem_chars = sum(len(s) for s in removed)

    print(f"\n{'=' * 55}")
    print(f"  Секций:             {n}")
    print(f"  Символов ДО:        {s_b}")
    print(f"  Символов ПОСЛЕ:     {s_a}  (включая заголовки секций)")
    print(f"  Разница:            {delta}")
    print(f"  Удалено фрагментов: {len(removed)}  ({rem_chars} симв.)")

    # Допуск: сжатие строк/отступов даёт до ~400 симв. на секцию.
    tol = max(n * 400, 300)
    if abs(delta - rem_chars) > tol:
        print(f"\n  ⚠  Разница ({delta}) ≠ удалённые ({rem_chars}).")
        print(f"     Часть разницы — чистка заголовков / сжатие строк.")
        print(f"     Проверьте --report для деталей.")
    else:
        print(f"  ✓  Сверка в норме.")

    if args.report and not dry_run:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"  ⚠ Не удалось создать папку отчёта: {exc}")
        else:
            try:
                with open(args.report, "w", encoding="utf-8") as f:
                    for line in removed:
                        f.write(repr(line) + "\n")
                print(f"  Лог: {args.report}")
            except OSError as exc:
                print(f"  ⚠ Не удалось записать отчёт: {exc}")

    print(f"\n  Готово → {output_dir}")


if __name__ == "__main__":
    main()
