#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбивает EPUB / ZIP / TXT на главы.

Режимы (--mode):
  toc    автоматическая разбивка epub/zip по структуре (spine/TOC/h1-h2);
         TXT не принимается; дубль названия главы в тексте удаляется;
  regex  ручная разбивка: каждая строка, начинающаяся с любого
         --split-re, — начало новой главы (вся строка — заголовок);
  chunk  разбивка по чанкам --chunk-size (СИМВОЛЫ), названия — по маске
         --chunk-mask (обязателен {num}).

Каталоги глав: <нули>_<номер>_<заголовок> — канон core.common.parse_chapter_id;
нули добивают ширину 6: 00000_1, 0000_12, 000_177, 0_12345.
Первая строка chapter.txt — заголовок главы; имя каталога — безопасная
версия заголовка, обрезанная до --title-limit (СИМВОЛЫ, дефолт 50).
"""

import argparse
import json
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


# ======================== ДЕКОДИРОВАНИЕ ================================
_FW_TABLE = str.maketrans("０１２３４５６７８９", "0123456789")
WS_RE    = re.compile(r"\s+")
TAG_RE   = re.compile(r"<[^>]+>")
CTRL_RE  = re.compile(r"[\x00-\x1f\x7f-\x9f]")
MULTI_NL_RE    = re.compile(r"\n{3,}")
LEADING_WS_RE  = re.compile(r"^[ \t]+", re.M)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
H1_RE    = re.compile(r"<h1[^>]*>(.*?)</h1>",       re.S | re.I)
_H_TAG_RE = re.compile(r"<h([12])[^>]*>(.*?)</h\1>", re.S | re.I)


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


def clean_title(s: str) -> str:
    """Заголовок: обрезка пробелов + схлопывание внутренних пробелов."""
    return WS_RE.sub(" ", (s or "").strip())


# ======================== ИМЕНА КАТАЛОГОВ ==============================
# Недопустимые символы: Windows (< > : " / \ | ? * и управляющие) +
# Linux (/ и NUL). Пробелы → '_' (канон 00000_1_Первая_строка).
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.I)


def safe_folder(title: str, title_limit: int = 50) -> str:
    """Безопасное имя каталога: недопустимые символы → '_', пробелы → '_',
    зарезервированные имена Windows, точки/пробелы в конце, лимит длины."""
    t = CTRL_RE.sub("", title)
    t = _INVALID_CHARS_RE.sub("_", t)
    t = t.replace(" ", "_")
    t = re.sub(r"_+", "_", t)
    t = t.strip(" _.")
    if _RESERVED_RE.match(t):
        t = "Chapter"
    try:
        limit = int(title_limit)
    except (TypeError, ValueError):
        limit = 50
    if limit > 0:
        t = t[:limit].rstrip(" _.")
    return t or "Chapter"


def folder_name(num: int, title: str, title_limit: int = 50) -> str:
    """Каноничный каталог главы: <нули>_<номер>_<заголовок>.
    Нули добивают ширину 6: 00000_1, 0000_12, 000_177, 0_12345;
    номер ≥ 100000 — без префикса (ширина 6 уже достигнута)."""
    zeros = "0" * max(0, 6 - len(str(num)))
    prefix = f"{zeros}_{num}" if zeros else str(num)
    return f"{prefix}_{safe_folder(title, title_limit)}"


# ======================== ОЧИСТКИ ТЕКСТА ===============================
def _safe_compile(pattern: str, name: str, multiline: bool = False):
    """Компиляция паттерна; multiline — ^/$ матчат начало/конец СТРОКИ
    (очистки построчные: «^本章完$» удаляет только строку-маркер)."""
    try:
        return re.compile(pattern, re.MULTILINE if multiline else 0)
    except re.error as e:
        sys.exit(f"  Ошибка в паттерне --{name}: {e}\n  Паттерн: {pattern}")


def _parse_replace_re(lines) -> list[tuple[re.Pattern, str]]:
    """Парсит --replace-re «паттерн -> замена» → [(compiled, repl)].

    Пустая правая часть — удаление совпадений. Битая строка — sys.exit.
    """
    out: list[tuple[re.Pattern, str]] = []
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "->" not in line:
            sys.exit(f"--replace-re строка {i}: нет разделителя «->» — "
                     f"ожидалось «паттерн -> замена»")
        pat, repl = line.split("->", 1)
        pat = pat.strip()
        if not pat:
            sys.exit(f"--replace-re строка {i}: пустой паттерн")
        compiled = _safe_compile(pat, "replace-re")
        out.append((compiled, repl.strip()))
    return out


def apply_cleanups(text: str, clean_res) -> tuple[str, list[str]]:
    """Удаляет все совпадения каждого паттерна; сжимает пустые строки."""
    removed: list[str] = []
    for rx in clean_res:
        for m in rx.finditer(text):
            removed.append(m.group(0))
        text = rx.sub("", text)
    text = MULTI_NL_RE.sub("\n\n", text)
    text = LEADING_WS_RE.sub("", text)
    return text.strip(), removed


# ======================== РАЗБИВКА =====================================
def split_by_patterns(text: str, split_res) -> list[tuple[str, str]]:
    """Строка начинается с любого паттерна — заголовок новой главы."""
    lines = text.split("\n")
    marker_idx = [
        i for i, ln in enumerate(lines)
        if ln.strip() and any(rx.match(ln.strip()) for rx in split_res)
    ]
    if not marker_idx:
        sys.exit("Ни одна строка не совпала с паттернами разбивки — "
                 "проверьте regexp (--split-re)")
    sections: list[tuple[str, str]] = []
    if marker_idx[0] > 0:
        pre = "\n".join(lines[:marker_idx[0]]).strip()
        if pre:
            heading = clean_title(first_line(pre)) or "Пролог"
            sections.append((heading, pre))
    for k, idx in enumerate(marker_idx):
        end = marker_idx[k + 1] if k + 1 < len(marker_idx) else len(lines)
        heading = clean_title(lines[idx])
        if not heading:
            heading = f"Секция {k + 1}"
        body = "\n".join(lines[idx + 1:end]).strip()
        if body:
            sections.append((heading, body))
    return sections


def split_by_chunks(text: str, chunk_size: int,
                    mask: str) -> list[tuple[str, str]]:
    """Чанки фиксированного размера (СИМВОЛЫ); заголовки — по маске."""
    if "{num}" not in mask:
        sys.exit("Маска названия чанка должна содержать {num} "
                 "(например «Глава {num}»)")
    from core.common import split_text_smart
    chunks = split_text_smart(text, target_chars=chunk_size)
    sections: list[tuple[str, str]] = []
    for i, ch in enumerate(chunks, 1):
        if ch.strip():
            sections.append((mask.replace("{num}", str(i)), ch.strip()))
    return sections


# ======================== EPUB: СТРУКТУРА ==============================
EXCLUDE_NAMES = {
    "cover.xhtml", "cover.html", "cover.htm",
    "toc.xhtml",   "toc.html",   "toc.htm",
    "nav.xhtml",   "nav.html",   "nav.htm",
}

SERVICE_TOC_RE = re.compile(
    r"^(информация|信息|info|cover|обложка|содержание|оглавление|about|简介)$",
    re.I)


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
    except (KeyError, IndexError, ValueError):
        return None


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


def _archive_html_names(zf: zipfile.ZipFile) -> set[str]:
    return {
        n for n in zf.namelist()
        if n.lower().endswith((".xhtml", ".html", ".htm"))
        and Path(n).name.lower() not in EXCLUDE_NAMES
        and not n.endswith("/")
    }


def _archive_order(zf: zipfile.ZipFile, all_html) -> list[str]:
    """Порядок чтения: spine → остальное → алфавитный фоллбэк."""
    spine = _get_spine_order(zf)
    if spine:
        names = [n for n in spine if n in all_html]
        names.extend(sorted(all_html - set(names),
                            key=lambda n: nat_key(Path(n).name)))
        return names
    return sorted(all_html, key=lambda n: nat_key(Path(n).name))


def _strip_heading_line(text: str, heading: str) -> str:
    """Убрать первую строку-заголовок (дубль названия главы) из начала тела."""
    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines[:5]):
        if not ln.strip():
            continue
        s = ln.strip()
        if heading and (heading in s or s in heading):
            return "".join(lines[i + 1:])
        break
    return text


def split_markup_by_headings(markup: str) -> list[tuple[str, str]] | None:
    """Разбить HTML по h1/h2 → [(heading, body)] | None (нет заголовков)."""
    matches = list(_H_TAG_RE.finditer(markup))
    if not matches:
        return None
    sections: list[tuple[str, str]] = []
    pre = markup[:matches[0].start()]
    if pre.strip():
        body = html_to_text(pre)
        body = norm_fw(body)
        body = normalize_newlines(body)
        body, _ = apply_cleanups(body, [])
        if body.strip():
            heading = clean_title(first_line(body)) or "Пролог"
            sections.append((heading, body.strip()))
    for i, m in enumerate(matches):
        raw_h = TAG_RE.sub("", m.group(2))
        raw_h = unescape(raw_h)
        raw_h = norm_fw(raw_h)
        heading = clean_title(WS_RE.sub(" ", raw_h))
        if not heading:
            heading = f"Секция {len(sections) + 1}"
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markup)
        body = html_to_text(markup[m.end():end])
        body = norm_fw(body)
        body = normalize_newlines(body)
        body, _ = apply_cleanups(body, [])
        if body.strip():
            sections.append((heading, body.strip()))
    return sections or None


def extract_epub_sections(arc_path: Path) -> list[tuple[str, str]]:
    """Режим toc: структурная разбивка epub/zip по spine/TOC/h1-h2."""
    with zipfile.ZipFile(arc_path) as zf:
        all_html = _archive_html_names(zf)
        if not all_html:
            sys.exit("В архиве нет HTML-файлов — режим TOC требует epub/zip "
                     "со структурой; для txt используйте ручной режим "
                     "или разбивку по чанкам")
        names = _archive_order(zf, all_html)
        toc_titles = _get_toc_titles(zf)
        sections: list[tuple[str, str]] = []
        for name in names:
            try:
                markup = decode_bytes(zf.read(name))
            except KeyError:
                continue
            markup = normalize_newlines(markup)
            title = extract_title_html(markup)
            # несколько h1/h2 в файле — режем внутри файла
            subs = split_markup_by_headings(markup)
            if subs and len(subs) > 1:
                sections.extend(subs)
                continue
            base = Path(name).name.lower()
            toc_t = toc_titles.get(base, "")
            if toc_t and SERVICE_TOC_RE.match(toc_t.strip()):
                continue  # служебная страница («Информация») — не глава
            heading = clean_title(toc_t or title or "")
            if not heading:
                continue  # страница без заголовка — не глава
            raw = html_to_text(markup)
            raw = norm_fw(raw)
            raw = normalize_newlines(raw)
            body = _strip_heading_line(raw, heading)
            if body.strip():
                sections.append((heading, body.strip()))
    return sections


def archive_to_text(arc_path: Path) -> str:
    """Весь текст архива (для regex/chunk-режимов): HTML по порядку,
    заголовки файлов — как строки-заголовки; ZIP с txt — конкатенация."""
    with zipfile.ZipFile(arc_path) as zf:
        all_html = _archive_html_names(zf)
        if all_html:
            names = _archive_order(zf, all_html)
            parts: list[str] = []
            for name in names:
                try:
                    markup = decode_bytes(zf.read(name))
                except KeyError:
                    continue
                markup = normalize_newlines(markup)
                title = extract_title_html(markup)
                text = html_to_text(markup)
                text = norm_fw(text)
                text = normalize_newlines(text)
                cleaned = clean_title(title) if title else ""
                if cleaned and not title_already_in_text(cleaned, text):
                    parts.append(cleaned + "\n" + text)
                else:
                    parts.append(text)
            return "\n\n".join(parts)
        all_txt = sorted(
            (n for n in zf.namelist()
             if n.lower().endswith(".txt") and not n.endswith("/")),
            key=lambda n: nat_key(Path(n).name))
        if not all_txt:
            sys.exit("В архиве нет ни HTML, ни TXT")
        out_parts = []
        for name in all_txt:
            raw = decode_bytes(zf.read(name))
            raw = normalize_newlines(raw)
            raw = norm_fw(raw)
            out_parts.append(raw)
        return "\n\n".join(out_parts)


def read_text_file(txt_path: Path) -> str:
    raw = decode_bytes(txt_path.read_bytes())
    raw = normalize_newlines(raw)
    raw = norm_fw(raw)
    return raw


# ======================== ОБЩИЙ ВХОД ===================================
def split_input(input_file: Path, mode: str,
                split_res, clean_res,
                replace_res=(),
                chunk_size: int = 7000, chunk_mask: str = "Chapter {num}",
                title_limit: int = 50, num_offset: int = 1,
                skips: set[int] | None = None,
                rename_chapters: bool = False) -> tuple[list[dict], int, list[str]]:
    """Разбивает исходник на главы → (entries, s_before, removed).

    entry = {seq (исходный порядок, с 1), num (номер с учётом offset/skip),
             heading, body}. skip — исходные seq, которые пропускаются,
    оставшиеся перенумеровываются с num_offset.

    replace_res — пары (compiled, repl): regexp-замены, применяются
    к тексту ДО разбивки (можно нормализовать маркеры глав, напр.
    «第\\d+章 -> Глава \\d+»); пустая repl — удаление совпадений.

    rename_chapters — заголовки ВСЕХ глав заменяются на chunk_mask
    (с номером после перенумерации); удобно после разбивки по
    TOC/паттернам: «Chapter 1», «Chapter 2»…
    """
    skips = skips or set()
    suffix = input_file.suffix.lower()
    if suffix == ".zip":
        sys.exit("ZIP не принимается — переименуйте в .epub или "
                 "извлеките текст в .txt")
    removed: list[str] = []
    if mode == "toc":
        if suffix == ".txt":
            sys.exit("Режим TOC не принимает TXT — используйте ручной "
                     "режим (regexp) или разбивку по чанкам")
        raw_sections = extract_epub_sections(input_file)
        s_before = sum(len(b) for _, b in raw_sections)
        sections: list[tuple[str, str]] = []
        for h, b in raw_sections:
            body, rem = apply_cleanups(b, clean_res)
            removed.extend(rem)
            if body.strip():
                sections.append((h, body.strip()))
    else:
        # epub в regex/chunk-режимах перегоняется в текст (архив→txt)
        text = (read_text_file(input_file) if suffix == ".txt"
                else archive_to_text(input_file))
        s_before = len(text)
        text, removed = apply_cleanups(text, clean_res)
        # regexp-замены — до разбивки (нормализация маркеров глав)
        for pat, repl in replace_res:
            text = pat.sub(repl, text)
        if mode == "regex":
            sections = split_by_patterns(text, split_res)
        else:
            sections = split_by_chunks(text, chunk_size, chunk_mask)
    entries: list[dict] = []
    num = num_offset
    for i, (h, b) in enumerate(sections, 1):
        if i in skips:
            continue
        heading = chunk_mask.replace("{num}", str(num)) if rename_chapters else h
        entries.append({"seq": i, "num": num, "heading": heading, "body": b})
        num += 1
    return entries, s_before, removed


# ======================== ЗАПИСЬ =======================================
# Имена выходных файлов — канон артефактов стадий (AGENTS §7):
# chapter.txt → translated.txt → redacted.txt → polished.txt
OUTPUT_FILES = {"chapter": "chapter.txt", "translated": "translated.txt",
                "redacted": "redacted.txt", "polished": "polished.txt"}


def write_section(output_dir, num: int, heading: str, body: str,
                  output_type: str = "chapter", title_limit: int = 50,
                  dry_run: bool = False) -> None:
    fname = OUTPUT_FILES.get(output_type, "chapter.txt")
    folder = output_dir / folder_name(num, heading, title_limit)
    if dry_run:
        print(f"  [{folder.name}] {fname}")
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        content = (heading + "\n\n" + body + "\n") if body else (heading + "\n")
        (folder / fname).write_text(content, encoding="utf-8")
        print(f"  [{folder.name}] {fname}")
    except OSError as e:
        print(f"  ⚠ Ошибка записи [{folder.name}] {fname}: {e}",
              file=sys.stderr)


def write_entries(entries: list[dict], output_dir: Path,
                  output_type: str = "chapter", title_limit: int = 50,
                  dry_run: bool = False) -> None:
    for e in entries:
        write_section(output_dir, e["num"], e["heading"], e["body"],
                      output_type, title_limit, dry_run)


def write_preview_json(entries: list[dict], preview_path: Path,
                       source: str, num_offset: int,
                       title_limit: int = 50) -> None:
    """Предпросмотр: JSON с секциями (папки + заголовки + тексты)."""
    from core.common import atomic_write
    data = {
        "source": source,
        "num_offset": num_offset,
        "title_limit": title_limit,
        "entries": [
            {"seq": e["seq"], "num": e["num"],
             "folder": folder_name(e["num"], e["heading"], title_limit),
             "heading": e["heading"], "text": e["body"]}
            for e in entries
        ],
    }
    atomic_write(str(preview_path),
                 json.dumps(data, ensure_ascii=False, indent=1))


# ========================== ARGPARSE ===================================
def build_parser():
    p = argparse.ArgumentParser(
        prog="split_book",
        description="Разбивает EPUB / TXT на главы.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Режимы (--mode):
  toc    автоматическая разбивка epub/zip по структуре (spine/TOC/h1-h2);
         TXT не принимается; дубль названия главы в тексте удаляется;
  regex  ручная разбивка: каждая строка, начинающаяся с любого
         --split-re, — начало новой главы (вся строка — заголовок);
  chunk  разбивка по чанкам --chunk-size (СИМВОЛЫ), названия — по маске
         --chunk-mask (обязателен {num}).

Каталоги глав: <нули>_<номер>_<заголовок> — канон parse_chapter_id;
нули добивают ширину 6: 00000_1, 0000_12, 000_177, 0_12345.

Примеры:
  %(prog)s --input book.epub
  %(prog)s --input book.txt --mode regex --split-re "Глава \\d+"
  %(prog)s --input book.txt --mode chunk --chunk-size 7000 \\
          --chunk-mask "Часть {num}"
  %(prog)s --input book.epub --num-offset 875 --skip 3 --skip 7
  %(prog)s --input book.epub --preview-json tmp/preview.json
  %(prog)s --input book.txt --mode regex \
          --replace-re "第(\\d+)章 -> Глава \\1" \
          --split-re "Глава \\d+"
""",
    )
    p.add_argument("--input", type=Path, required=True, metavar="FILE",
                   help="Исходник: .epub/.txt (обязателен)")
    p.add_argument("--output", type=Path, default=Path("chapters"),
                   help="Выходная папка (default: ./chapters)")
    p.add_argument("--mode", choices=["toc", "regex", "chunk"],
                   default="toc", help="Режим разбивки (default: toc)")

    g = p.add_argument_group("Ручная разбивка и очистки")
    g.add_argument("--split-re", dest="split_re", action="append",
                   default=[], metavar="RE",
                   help="Паттерн маркера главы (regex; строка начинается "
                        "с паттерна — заголовок); можно повторять")
    g.add_argument("--clean-re", dest="clean_re", action="append",
                   default=[], metavar="RE",
                   help="Очистка текста: все совпадения удаляются; "
                        "можно повторять")
    g.add_argument("--replace-re", dest="replace_re", action="append",
                   default=[], metavar="PAT -> REPL",
                   help="Regexp-замена ДО разбивки (можно повторять); "
                        "пустая REPL — удаление; обратные ссылки в REPL "
                        "работают: \"第(\\d+)章 -> Глава \\1\"")

    g = p.add_argument_group("Разбивка по чанкам")
    g.add_argument("--chunk-size", type=int, default=7000, metavar="N",
                   help="Размер чанка, СИМВОЛЫ (default: 7000)")
    g.add_argument("--chunk-mask", default="Chapter {num}", metavar="MASK",
                   help="Маска названия (чанка или переопределённых глав), "
                        "{num} — номер (default: «Chapter {num}»)")

    g = p.add_argument_group("Каталоги глав")
    g.add_argument("--title-limit", type=int, default=50, metavar="N",
                   help="Длина названия каталога, СИМВОЛЫ (default: 50)")
    g.add_argument("--num-offset", type=int, default=1, metavar="N",
                   help="Первый номер каталога (default: 1); "
                        "875 → 000_875_…")
    g.add_argument("--skip", action="append", default=[], metavar="N",
                   help="Пропустить секцию по исходному номеру; "
                        "оставшиеся перенумеровываются; можно повторять")

    g = p.add_argument_group("Запись")
    g.add_argument("--output-type", dest="output_type",
                   choices=list(OUTPUT_FILES), default="chapter",
                   metavar="TYPE",
                   help="Выходной файл: chapter/translated/redacted/polished "
                        "(default: chapter.txt)")
    g.add_argument("--clean-output", action="store_true",
                   help="Удалить старые папки глав перед записью")
    g.add_argument("--rename-chapters", action="store_true",
                   help="Переопределить названия ВСЕХ глав на --chunk-mask "
                        "(номер после перенумерации); работает во всех "
                        "режимах (toc/regex/chunk)")
    g.add_argument("--preview-json", type=Path, default=None, metavar="FILE",
                   help="Вместо записи — JSON предпросмотра "
                        "{source, num_offset, title_limit, entries}")
    g.add_argument("--dry-run", action="store_true",
                   help="Показать список глав без записи на диск")
    g.add_argument("--report", type=Path,
                   default=Path("./logs/epub_to_txt_clean_report.txt"),
                   metavar="FILE", help="Лог удалённых фрагментов")
    return p


# ============================= MAIN ====================================
def main():
    args = build_parser().parse_args()
    # R9: фактическая команда запуска
    import shlex as _shlex
    print(f"Запуск: {_shlex.join(sys.argv)}")

    input_file = args.input.resolve()
    if not input_file.is_file():
        sys.exit(f"Файл не найден: {input_file}")
    suffix = input_file.suffix.lower()
    if suffix not in (".epub", ".zip", ".txt"):
        sys.exit(f"Неподдерживаемый формат: {suffix} — нужен .epub/.zip/.txt")

    mode = args.mode
    if mode == "regex" and not args.split_re:
        sys.exit("Режим regex требует хотя бы один --split-re")
    split_res = [_safe_compile(p, "split-re") for p in args.split_re]
    clean_res = [_safe_compile(p, "clean-re", multiline=True)
                 for p in args.clean_re]
    replace_res = _parse_replace_re(args.replace_re)
    title_limit = max(1, args.title_limit)
    num_offset = max(1, args.num_offset)
    if args.rename_chapters and "{num}" not in args.chunk_mask:
        sys.exit("--rename-chapters: маска названия должна содержать "
                 "{num} (например «Chapter {num}»)")
    skips: set[int] = set()
    for s in args.skip:
        try:
            skips.add(int(s))
        except ValueError:
            sys.exit(f"--skip: ожидалось число, получено: {s}")
    output_type = args.output_type

    print(f"\n  Файл:    {input_file.name}")
    print(f"  Режим:   {mode}"
          + (f" · чанк {args.chunk_size} симв. · маска «{args.chunk_mask}»"
             if mode == "chunk" else "")
          + (f" · переопределение названий «{args.chunk_mask}»"
             if args.rename_chapters else ""))
    print(f"  Каталог: <нули>_<номер>_<заголовок>, лимит {title_limit} симв.")
    print(f"  Номера:  с {num_offset}"
          + (f" · пропуск: {sorted(skips)}" if skips else ""))

    entries, s_before, removed = split_input(
        input_file, mode, split_res, clean_res, replace_res,
        chunk_size=args.chunk_size, chunk_mask=args.chunk_mask,
        title_limit=title_limit, num_offset=num_offset, skips=skips,
        rename_chapters=args.rename_chapters)

    if args.preview_json:
        preview = Path(args.preview_json).resolve()
        write_preview_json(entries, preview, input_file.name,
                           num_offset, title_limit)
        print(f"\n  Предпросмотр: {len(entries)} секций → {preview}")
        for e in entries:
            folder = folder_name(e["num"], e["heading"], title_limit)
            print(f"  {e['num']:>6}  {folder}  ({len(e['body'])} симв.)")
        return

    output_dir = args.output.resolve()
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Повторный запуск: старые папки глав
    old_dirs = []
    if output_dir.is_dir():
        old_dirs = [p for p in output_dir.iterdir()
                    if p.is_dir() and re.match(r"^0*_\d+", p.name)]
    if old_dirs and not args.dry_run:
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
    elif old_dirs and args.dry_run:
        print(f"  ⚠ В {output_dir} уже есть {len(old_dirs)} папок глав "
              "(dry-run: не трону)")

    if not entries:
        print("  Секций нет — ничего не записано.")
        return

    write_entries(entries, output_dir, output_type, title_limit,
                  args.dry_run)

    # ── Сверка (приближённая) ──
    s_after = sum(len(e["heading"]) + len(e["body"]) for e in entries)
    delta = s_before - s_after
    rem_chars = sum(len(s) for s in removed)
    print(f"\n{'=' * 55}")
    print(f"  Секций:             {len(entries)}")
    print(f"  Символов ДО:        {s_before}")
    print(f"  Символов ПОСЛЕ:     {s_after}  (включая заголовки секций)")
    print(f"  Разница:            {delta}")
    print(f"  Удалено фрагментов: {len(removed)}  ({rem_chars} симв.)")
    tol = max(len(entries) * 400, 300)
    if abs(delta - rem_chars) > tol:
        print(f"\n  ⚠  Разница ({delta}) ≠ удалённые ({rem_chars}).")
        print("     Часть разницы — чистка заголовков / сжатие строк.")
    else:
        print("  ✓  Сверка в норме.")

    if args.report and not args.dry_run:
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
