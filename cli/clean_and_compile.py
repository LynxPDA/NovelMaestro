#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clean_and_compile.py — компиляция глав в TXT/EPUB/FB2 (чистый CLI).

Без интерактивного меню: все параметры через argparse.
Интерактивный режим — в лаунчере tools/run_clean_and_compile.py.

Режимы (--mode):
  txt          сборка единого TXT (Rulate): заголовки «# [Название :|: N]»
  txt-plain    сборка единого TXT: заголовки КАК В ПЕРЕВОДЕ (без
               markdown-префиксов) — только очистка и компиляция
  epub         сборка Markdown → нативная генерация EPUB (zipfile, stdlib)
  fb2          сборка Markdown → нативная генерация FB2 (+ обложка, stdlib)
  epub-chunks  EPUB частями по --chunk-size глав (default 50)
  txt-chunks   TXT частями по --chunk-size глав (default 500)
  fb2-chunks   FB2 частями по --chunk-size глав (default 50)

Единицы: --chunk-size — ГЛАВЫ (чанкование по главам, см. AGENTS.md §5).
"""
import argparse
import os
import re
import glob
import base64
import zipfile
from datetime import datetime
from html import escape as _html_escape
import sys

# ── bootstrap: поиск core/common.py подъёмом от скрипта ──
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

from core.common import (  # noqa: E402
    build_chapter_map as common_build_chapter_map,
    find_chapter_file as common_find_chapter_file,
)

# ==========================================
# ОСНОВНЫЕ НАСТРОЙКИ
# ==========================================
class Config:
    def __init__(self):
        self.start = 1
        self.end = 100
        self.set_order = 1
        self.paywall = ""
        self.volume = ""
        self.base_dir = "./chapters"
        self.tmp_dir = "."
        self.epub_cover = "./source/cover.jpg"
        self.epub_meta = "./source/metadata.yaml"
        self.epub_chunk_size = 50
        self.txt_chunk_size = 500
        self.fb2_chunk_size = 50
        self.fb2_cover = "./source/cover.jpg"
        self.fb2_inject_cover = 1
        self.add_donate_page = 1
        self.donate_file = ""             # внешний файл страницы поддержки
        self.compile_type = "polished"   # polished|redacted|translated|chapter

    @property
    def titles_file(self):
        return os.path.join(self.tmp_dir, f"titles_{self.start}_{self.end}.txt")

    def get_actual_titles_file(self):
        exact_file = self.titles_file
        if os.path.isfile(exact_file):
            return exact_file
        for f in glob.glob(os.path.join(self.tmp_dir, "titles_*_*.txt")):
            name = os.path.basename(f)
            match = re.match(r"titles_(\d+)_(\d+)\.txt", name)
            if match:
                try:
                    f_start = int(match.group(1))
                    f_end = int(match.group(2))
                except ValueError:
                    continue
                if f_start <= self.start and f_end >= self.end:
                    return f
        return exact_file

cfg = Config()

# ==========================================
# ПОСТРОЕНИЕ КАРТЫ ГЛАВ (адаптер над core.common)
# ==========================================
def build_chapter_map(base_dir):
    """{номер: путь} — единый канон core.common; дубли: последняя + предупреждение."""
    raw = common_build_chapter_map(base_dir)
    out = {}
    for num, paths in raw.items():
        if len(paths) > 1:
            print(f"[ВНИМАНИЕ] Две папки для главы {num}: "
                  f"{', '.join(os.path.basename(p) for p in paths)}. Беру последнюю.")
        out[num] = paths[-1]
    return out

def detect_range(chapter_map):
    """Возвращает (min, max) по ключам chapter_map, или None если пусто."""
    if not chapter_map:
        return None
    return min(chapter_map.keys()), max(chapter_map.keys())

# ==========================================
# ПОИСК TXT-ФАЙЛА В ПАПКЕ ГЛАВЫ
# ==========================================

def find_chapter_file(dir_path, chapter_num, want="polished", logger=None,
                      strict=False, strict_types=False):
    """Приоритеты и fallback — единые из core.common:
    polished-паттерны → единственный безопасный txt (blacklist
    raw/draft/translated/…). Возвращает (путь или None, предупреждения)."""
    return common_find_chapter_file(
        dir_path, chapter_num,
        want=want,
        logger=logger,
        strict=strict,
        strict_types=strict_types,
    )

# ==========================================
# ФУНКЦИИ
# ==========================================
def log(msg, file_obj=None):
    print(msg)
    if file_obj:
        file_obj.write(msg + "\n")

def safe_read(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="cp1251") as f:
            return f.read()

def safe_read_lines(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="cp1251") as f:
            return f.readlines()

def get_content_type(image_path):
    ext = os.path.splitext(image_path)[1].lower()
    types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }
    return types.get(ext, "image/jpeg")


_COVER_CANDIDATES = ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp")


def resolve_cover_path(cover_path):
    """Реальная обложка для сборки.

    Web-загрузка сохраняет обложку как source/cover.<ext> (jpg/jpeg/png/
    webp), а дефолт компилятора — ./source/cover.jpg. Если указанный файл
    отсутствует — ищем любой существующий cover.* рядом и берём его.
    """
    if os.path.isfile(cover_path):
        return cover_path
    d = os.path.dirname(cover_path) or "."
    for name in _COVER_CANDIDATES:
        cand = os.path.join(d, name)
        if os.path.isfile(cand):
            print(f"[ИНФО] Обложка {cover_path} не найдена — берём {cand}")
            return cand
    return cover_path

def inject_fb2_cover(fb2_path, cover_path, log_file=None):
    if not os.path.isfile(fb2_path):
        log(f"[ОШИБКА] FB2-файл не найден: {fb2_path}", log_file)
        return False
    if not os.path.isfile(cover_path):
        log(f"[ПРЕДУПРЕЖДЕНИЕ] Обложка не найдена: {cover_path}", log_file)
        return False

    fb2_content = safe_read(fb2_path)

    if "<coverpage>" in fb2_content:
        log("[ИНФО] Обложка уже присутствует в FB2, пропускаю.", log_file)
        return True

    try:
        with open(cover_path, "rb") as f:
            cover_bytes = f.read()
    except OSError as exc:
        log(f"[ОШИБКА] Не удалось прочитать обложку {cover_path}: {exc}",
            log_file)
        return False
    cover_b64 = base64.b64encode(cover_bytes).decode("ascii")
    content_type = get_content_type(cover_path)

    coverpage_xml = (
        '    <coverpage>\n'
        '      <image l:href="#cover"/>\n'
        '    </coverpage>\n'
    )
    if "</title-info>" in fb2_content:
        fb2_content = fb2_content.replace(
            "</title-info>",
            coverpage_xml + "  </title-info>",
            1
        )
    else:
        log("[ОШИБКА] Не найден тег </title-info> в FB2.", log_file)
        return False

    binary_xml = (
        f'  <binary id="cover" content-type="{content_type}">\n'
        f'{cover_b64}\n'
        f'  </binary>\n'
    )
    if "</FictionBook>" in fb2_content:
        fb2_content = fb2_content.replace(
            "</FictionBook>",
            binary_xml + "</FictionBook>",
            1
        )
    else:
        log("[ОШИБКА] Не найден тег </FictionBook> в FB2.", log_file)
        return False

    if 'xmlns:l=' not in fb2_content:
        fb2_content = fb2_content.replace(
            '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"',
            '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
            'xmlns:l="http://www.w3.org/1999/xlink"',
            1
        )

    try:
        with open(fb2_path, "w", encoding="utf-8") as f:
            f.write(fb2_content)
    except OSError as exc:
        log(f"[ОШИБКА] Не удалось записать {fb2_path}: {exc}", log_file)
        return False

    log(f"[УСПЕХ] Обложка инжецирована: "
        f"{os.path.basename(cover_path)} → {os.path.basename(fb2_path)}",
        log_file)
    return True

def load_donate_page(donate_file=None):
    """Загрузка страницы поддержки из внешнего файла.

    Порядок поиска: donate_file → ./source/donate.txt → ./prompts/donate.txt.
    Если файл не найден — возвращает None (страница не добавляется).
    Формат файла: первая строка '# Заголовок' (опционально), далее тело.
    Возвращает (title, body_lines) или None.
    """
    candidates = []
    if donate_file:
        candidates.append(donate_file)
    candidates.extend(["./source/donate.txt", "./prompts/donate.txt"])

    text = None
    for cand in candidates:
        if os.path.isfile(cand):
            text = safe_read(cand).strip()
            if text:
                break

    if text is None:
        return None

    lines = text.splitlines()
    title = "Поддержать проект"
    body_start = 0
    if lines and lines[0].strip().startswith("# "):
        title = lines[0].strip()[2:].strip()
        body_start = 1
    body = [ln.strip() for ln in lines[body_start:]]
    while body and not body[0]:
        body.pop(0)
    while body and not body[-1]:
        body.pop()
    return title, body


# ══════════════════════════════════════════════════════
# НАТИВНАЯ ГЕНЕРАЦИЯ EPUB / FB2 (без pandoc)
# ══════════════════════════════════════════════════════

def _esc(text):
    """Экранирование XML/HTML-спецсимволов."""
    return _html_escape(str(text), quote=True)


def parse_yaml_meta(path):
    """Парсер простого YAML front-matter без внешних зависимостей."""
    meta = {}
    try:
        text = safe_read(path)
    except (OSError, ValueError):
        # B12: только ошибки чтения/кодировки — программные баги не маскируем
        return meta
    lines = text.split('\n')
    # Убираем front-matter делимитеры ---
    if lines and lines[0].strip() == '---':
        end_idx = len(lines)
        for i, l in enumerate(lines[1:], 1):
            if l.strip() == '---':
                end_idx = i
                break
        lines = lines[1:end_idx]

    current_key = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('- ') and current_key:
            if not isinstance(meta.get(current_key), list):
                meta[current_key] = []
            meta[current_key].append(stripped[2:].strip())
            continue
        if ':' in stripped:
            key, _, val = stripped.partition(':')
            key = key.strip()
            val = val.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            meta[key] = val
            current_key = key
    return meta


def _chapter_xhtml(title, paragraphs):
    """XHTML-разметка одной главы."""
    body_parts = []
    for para in paragraphs:
        if para.strip() in ('* * *', '***'):
            body_parts.append('<p style="text-align:center">* * *</p>')
        elif para.strip():
            body_parts.append(f'<p>{_esc(para)}</p>')
    body = '\n'.join(body_parts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f'<head><title>{_esc(title)}</title></head>\n'
        '<body>\n'
        '<section epub:type="chapter">\n'
        f'<h1>{_esc(title)}</h1>\n'
        f'{body}\n'
        '</section>\n'
        '</body>\n'
        '</html>'
    )


def build_epub_native(chapters_data, meta, cover_path, output_path):
    """Генерация EPUB3 через zipfile (stdlib). Возвращает True при успехе."""
    if not chapters_data:
        print("[ПРЕДУПРЕЖДЕНИЕ] Нет глав для EPUB.")
        return False

    book_title = meta.get('title', 'Без названия')
    author     = meta.get('author', 'Неизвестный автор')
    lang       = meta.get('language', 'ru').split('-')[0]
    identifier = meta.get('identifier', '') or f'urn:book:{cfg.start}-{cfg.end}'
    desc       = meta.get('description', '')
    date_str   = meta.get('date', '')
    rights     = meta.get('rights', '')
    now        = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')

    has_cover  = bool(cover_path) and os.path.isfile(cover_path)
    cover_ext  = os.path.splitext(cover_path)[1] if has_cover else '.jpg'
    cover_mime = get_content_type(cover_path) if has_cover else 'image/jpeg'

    # ── content.opf ──
    manifest_items = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine_items = []
    if has_cover:
        manifest_items += [
            f'    <item id="cover-img" href="cover{cover_ext}" media-type="{cover_mime}"/>',
            '    <item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>',
        ]
        spine_items.append('    <itemref idref="cover-page" linear="no"/>')
    for i in range(len(chapters_data)):
        ch_id = f'ch{i + 1:04d}'
        fname = f'chapter_{i + 1:04d}.xhtml'
        manifest_items.append(
            f'    <item id="{ch_id}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'    <itemref idref="{ch_id}"/>')

    opt_meta = ''
    if desc:
        opt_meta += f'\n    <dc:description>{_esc(desc)}</dc:description>'
    if date_str:
        opt_meta += f'\n    <dc:date>{_esc(date_str)}</dc:date>'
    if rights:
        opt_meta += f'\n    <dc:rights>{_esc(rights)}</dc:rights>'

    content_opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="book-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:title>{_esc(book_title)}</dc:title>\n'
        f'    <dc:creator>{_esc(author)}</dc:creator>\n'
        f'    <dc:language>{_esc(lang)}</dc:language>\n'
        f'    <dc:identifier id="book-id">{_esc(identifier)}</dc:identifier>'
        f'{opt_meta}\n'
        f'    <meta property="dcterms:modified">{now}</meta>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        + '\n'.join(manifest_items) + '\n'
        '  </manifest>\n'
        '  <spine toc="ncx">\n'
        + '\n'.join(spine_items) + '\n'
        '  </spine>\n'
        '</package>'
    )

    # ── toc.ncx ──
    nav_points = []
    for i, ch in enumerate(chapters_data, 1):
        nav_points.append(
            f'    <navPoint id="ch{i:04d}" playOrder="{i}">\n'
            f'      <navLabel><text>{_esc(ch["title"])}</text></navLabel>\n'
            f'      <content src="chapter_{i:04d}.xhtml"/>\n'
            f'    </navPoint>'
        )
    toc_ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        '  <head>\n'
        f'    <meta name="dtb:uid" content="{_esc(identifier)}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        '  </head>\n'
        f'  <docTitle><text>{_esc(book_title)}</text></docTitle>\n'
        '  <navMap>\n'
        + '\n'.join(nav_points) + '\n'
        '  </navMap>\n'
        '</ncx>'
    )

    # ── nav.xhtml ──
    nav_items = '\n'.join(
        f'      <li><a href="chapter_{i:04d}.xhtml">{_esc(ch["title"])}</a></li>'
        for i, ch in enumerate(chapters_data, 1)
    )
    nav_xhtml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        '<head><title>Оглавление</title></head>\n'
        '<body>\n'
        '<nav epub:type="toc">\n'
        '  <h1>Оглавление</h1>\n'
        '  <ol>\n'
        + nav_items + '\n'
        '  </ol>\n'
        '</nav>\n'
        '</body>\n'
        '</html>'
    )

    # ── container.xml ──
    container_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>'
    )

    # ── cover.xhtml ──
    cover_xhtml = ''
    if has_cover:
        cover_xhtml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            '<head><title>Обложка</title></head>\n'
            '<body style="margin:0;padding:0;text-align:center">\n'
            f'<div><img src="cover{cover_ext}" alt="Обложка" '
            'style="max-width:100%;max-height:100%"/></div>\n'
            '</body>\n'
            '</html>'
        )

    # ── Запись ZIP ──
    try:
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            mi = zipfile.ZipInfo('mimetype', date_time=(1980, 1, 1, 0, 0, 0))
            zf.writestr(mi, 'application/epub+zip',
                        compress_type=zipfile.ZIP_STORED)
            zf.writestr('META-INF/container.xml', container_xml)
            zf.writestr('OEBPS/content.opf', content_opf)
            zf.writestr('OEBPS/toc.ncx', toc_ncx)
            zf.writestr('OEBPS/nav.xhtml', nav_xhtml)
            if has_cover:
                zf.write(cover_path, f'OEBPS/cover{cover_ext}')
                zf.writestr('OEBPS/cover.xhtml', cover_xhtml)
            for i, ch in enumerate(chapters_data, 1):
                zf.writestr(f'OEBPS/chapter_{i:04d}.xhtml',
                            _chapter_xhtml(ch['title'], ch['body']))
        print(f"[УСПЕХ] EPUB сгенерирован (нативно): {output_path}")
        return True
    except (OSError, ValueError, TypeError) as e:
        # B12: файловые/данные-ошибки — отчёт и fallback; баги кода падают явно
        print(f"[ОШИБКА] Генерация EPUB: {e}")
        return False


def build_fb2_native(chapters_data, meta, cover_path, output_path):
    """Генерация FB2 нативно (stdlib). Возвращает True при успехе."""
    book_title = meta.get('title', 'Без названия')
    author     = meta.get('author', '')
    lang       = meta.get('language', 'ru').split('-')[0]

    has_cover = bool(cover_path) and os.path.isfile(cover_path)

    subjects = meta.get('subject', [])
    if isinstance(subjects, str):
        subjects = [subjects]
    genre_xml = ''.join(f'\n      <genre>{_esc(s)}</genre>' for s in subjects[:3])

    author_xml = (
        f'\n      <author><nickname>{_esc(author)}</nickname></author>'
        if author else ''
    )

    coverpage_xml = (
        '\n      <coverpage><image l:href="#cover"/></coverpage>'
        if has_cover else ''
    )

    sections = []
    for ch in chapters_data:
        paras = '\n'.join(
            f'      <p>{_esc(p)}</p>' for p in ch['body'] if p.strip()
        )
        sections.append(
            f'    <section>\n'
            f'      <title><p>{_esc(ch["title"])}</p></title>\n'
            f'{paras}\n'
            f'    </section>'
        )
    body_xml = '\n'.join(sections)

    binary_xml = ''
    if has_cover:
        try:
            with open(cover_path, 'rb') as f:
                cover_b64 = base64.b64encode(f.read()).decode('ascii')
        except OSError:
            raise
        b64_lines = '\n'.join(
            cover_b64[i:i + 76] for i in range(0, len(cover_b64), 76)
        )
        binary_xml = (
            f'\n  <binary id="cover" content-type="{get_content_type(cover_path)}">\n'
            f'{b64_lines}\n'
            f'  </binary>'
        )

    fb2_xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"\n'
        '             xmlns:l="http://www.w3.org/1999/xlink">\n'
        '  <description>\n'
        '    <title-info>'
        + genre_xml + author_xml + '\n'
        + f'      <book-title>{_esc(book_title)}</book-title>\n'
        + f'      <lang>{_esc(lang)}</lang>'
        + coverpage_xml + '\n'
        + '    </title-info>\n'
        '  </description>\n'
        '  <body>\n'
        + body_xml + '\n'
        '  </body>'
        + binary_xml + '\n'
        '</FictionBook>'
    )

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(fb2_xml)
        print(f"[УСПЕХ] FB2 сгенерирован (нативно): {output_path}")
        return True
    except (OSError, ValueError, TypeError) as e:
        # B12: файловые/данные-ошибки — отчёт и fallback; баги кода падают явно
        print(f"[ОШИБКА] Генерация FB2: {e}")
        return False


# ==========================================
# ИМЯ ЭКСПОРТА (имя проекта + диапазон)
# ==========================================
def _export_label() -> str:
    """Метка имени файла экспорта: NFC + санитизация basename cwd.

    Имя папки проекта (валидируется valid_project_name) превращается
    в безопасную метку: пробелы → '_', ведущие/хвостовые '.'/'_' срезаются;
    пустой результат → fallback "book". Только epub/fb2 (compiled_* не трогаем).
    """
    import unicodedata
    label = unicodedata.normalize("NFC", os.path.basename(os.getcwd())).strip()
    label = label.replace(" ", "_").strip("._")
    return label or "book"


# ==========================================
# ЗАГРУЗКА КАСТОМНЫХ ЗАГОЛОВКОВ
# ==========================================
def load_custom_titles():
    custom_titles = {}
    target_file = cfg.get_actual_titles_file()
    if os.path.isfile(target_file):
        lines = safe_read_lines(target_file)
        for line in lines:
            if ":::" in line:
                parts = line.strip().split(":::", 1)
                if len(parts) == 2 and parts[0].isdigit():
                    try:
                        chapter_num = int(parts[0])
                    except ValueError:
                        continue
                    if cfg.start <= chapter_num <= cfg.end:
                        custom_titles[chapter_num] = parts[1]
    return custom_titles

# ==========================================
# СБОРКА КНИГИ
# ==========================================
def compile_book(mode):
    output_file = os.path.join(cfg.tmp_dir, f"compiled_{cfg.start}_{cfg.end}_{mode}.txt")
    try:
        os.makedirs("logs", exist_ok=True)
    except OSError as exc:
        print(f"Ошибка: не удалось создать logs/: {exc}")
        return 1
    log_file_path = os.path.join("logs", f"build_log_{mode}.txt")
    custom_titles = load_custom_titles()
    missing_chapters = []
    chapter_map = build_chapter_map(cfg.base_dir)
    chapters_data = []

    try:
        out_txt = open(output_file, "w", encoding="utf-8")
        out_log = open(log_file_path, "w", encoding="utf-8")
    except OSError as exc:
        print(f"Ошибка: не удалось создать файлы сборки: {exc}")
        return 1
    with out_txt, out_log:
        log("==========================================", out_log)
        log(f"Начало сборки ({mode}): {output_file}", out_log)
        log(f"Кастомные заголовки: {len(custom_titles)}", out_log)
        log(f"Время старта: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", out_log)
        log("==========================================", out_log)

        for i in range(cfg.start, cfg.end + 1):
            order_val = str(i) if cfg.set_order == 1 else ""
            tag_suffix = ""
            if cfg.volume:
                pay_val = cfg.paywall if cfg.paywall else " "
                tag_suffix = f" :|: {order_val} :|: {pay_val} :|: {cfg.volume}"
            elif cfg.paywall:
                tag_suffix = f" :|: {order_val} :|: {cfg.paywall}"
            elif order_val:
                tag_suffix = f" :|: {order_val}"

            # Поиск папки и файла
            dir_path = chapter_map.get(i)
            if not dir_path:
                log(f"[ПРОПУСК] Папка для главы {i} не найдена.", out_log)
                missing_chapters.append(f"{i} (нет папки)")
                continue

            file_path, warnings = find_chapter_file(
                dir_path, i, want=cfg.compile_type,
                strict_types=(cfg.compile_type != "chapter"))
            for w in warnings:
                log(w, out_log)
            if not file_path:
                log(f"[ПРЕДУПРЕЖДЕНИЕ] Файл txt не найден в папке: {dir_path}", out_log)
                missing_chapters.append(f"{i} (нет файла)")
                continue

            log(f"[OK] Глава {i}: {os.path.basename(file_path)}", out_log)

            content = safe_read(file_path)
            orig_header_match = re.search(r"^Глава\s+\d+.*", content, flags=re.MULTILINE)
            if orig_header_match:
                orig_header = orig_header_match.group(0).strip()
            else:
                # Нет «Глава N»: заголовок — первая непустая строка файла
                # (вкладка «Главы», вики-глава «Wiki Новеллы»)
                _first = next(
                    (ln.strip() for ln in content.splitlines() if ln.strip()),
                    "")
                orig_header = _first.lstrip("#").strip() or f"Глава {i}"

            final_title = custom_titles.get(i, "")
            if not final_title:
                final_title = orig_header

            if mode in ("epub", "fb2"):
                # epub/fb2 — заголовок отдельно (title)
                replacement = f"# {final_title}"
                sep_string = r"\* \* \*"
            elif mode == "txt-plain":
                # txt-plain — заголовок КАК В ПЕРЕВОДЕ, без markdown-
                # префикса: только очистка и компиляция
                replacement = final_title
                sep_string = "* * *"
            else:
                # txt (Rulate): «# [Название :|: N]» — тег загрузки на rulate
                replacement = f"# [{final_title}{tag_suffix}]"
                sep_string = "* * *"

            lines = content.splitlines()
            filtered_lines = []
            for idx, line in enumerate(lines):
                if idx >= 9:
                    clean_line = line.strip()
                    if re.match(r"^Глава\s+(\d+|\[Номер\])", clean_line):
                        continue
                filtered_lines.append(line)
            content = "\n".join(filtered_lines)

            if orig_header_match:
                content = re.sub(r"^Глава\s+\d+.*$", replacement, content,
                                 count=1, flags=re.MULTILINE)
            else:
                # заголовок из первой непустой строки — убрать её из тела
                content = re.sub(r"^\s*\S.*$", replacement, content,
                                 count=1, flags=re.MULTILINE)
            # Единый алгоритм нормализации разделителей: строка из 3+
            # разделителей (точки/звёздочки/многоточия, разделённые пробелами)
            # → универсальный сепаратор. Китайские скобки 【】 НЕ заменяем
            # (косметика, не критичная очистка — M9).
            content = re.sub(
                r"^[ \t]*(?:[.*…][ \t]*){3,}$", sep_string,
                content, flags=re.MULTILINE)

            cleaned_lines = [line.strip() for line in content.splitlines() if line.strip()]
            # Для EPUB/FB2 заголовок хранится отдельно в title —
            # убираем markdown-заголовки из тела, чтобы не дублировались
            body_for_book = cleaned_lines
            if mode in ("epub", "fb2"):
                body_for_book = [ln for ln in cleaned_lines if not ln.startswith("# ")]
            chapters_data.append({"title": final_title, "body": body_for_book})

            if mode == "fb2":
                out_txt.write("\n\n".join(cleaned_lines) + "\n\n")
            else:
                out_txt.write("\n".join(cleaned_lines) + "\n\n")

        if mode in ("epub", "fb2") and cfg.add_donate_page == 1:
            donate_result = load_donate_page(cfg.donate_file or None)
            if donate_result:
                donate_title, donate_body = donate_result
                log("[ИНФО] Добавление страницы поддержки проекта...", out_log)
                support_text = f"# {donate_title}\n\n" + "\n".join(donate_body) + "\n\n"
                out_txt.write(support_text)
                chapters_data.append({"title": donate_title, "body": donate_body})
            else:
                log("[ИНФО] Файл donate.txt не найден — страница поддержки пропущена.", out_log)

        log("==========================================", out_log)
        log(f"Сборка {mode} завершена: {output_file}", out_log)
        if missing_chapters:
            log(f"ПРОПУЩЕНО ГЛАВ: {len(missing_chapters)} ({', '.join(missing_chapters)})", out_log)

    # Нативная генерация EPUB (без pandoc)
    if mode == "epub":
        label = _export_label()
        epub_output = os.path.join(
            cfg.tmp_dir, f"{label}_{cfg.start}_{cfg.end}.epub")
        meta = parse_yaml_meta(cfg.epub_meta) if os.path.isfile(cfg.epub_meta) else {}
        build_epub_native(chapters_data, meta, cfg.epub_cover, epub_output)

    # Нативная генерация FB2 (без pandoc)
    if mode == "fb2":
        label = _export_label()
        fb2_output = os.path.join(
            cfg.tmp_dir, f"{label}_{cfg.start}_{cfg.end}.fb2")
        meta = parse_yaml_meta(cfg.epub_meta) if os.path.isfile(cfg.epub_meta) else {}
        cover = cfg.fb2_cover if cfg.fb2_inject_cover == 1 else None
        build_fb2_native(chapters_data, meta, cover, fb2_output)

# ==========================================
# СБОРКА ЧАСТЯМИ
# ==========================================
def compile_chunks(mode, chunk_size):
    """mode: 'epub' | 'txt' | 'fb2'; chunk_size — глав в одной части."""
    orig_start, orig_end = cfg.start, cfg.end
    current_start = orig_start
    while current_start <= orig_end:
        current_end = min(current_start + chunk_size - 1, orig_end)
        cfg.start, cfg.end = current_start, current_end
        print(f"\nСборка части {mode.upper()}: Главы {cfg.start} - {cfg.end}")
        compile_book(mode)
        current_start = current_end + 1
    cfg.start, cfg.end = orig_start, orig_end
    print(f"\nВсе части {mode.upper()} сгенерированы в {cfg.tmp_dir}!")

# ==========================================
# CLI
# ==========================================
CHUNK_DEFAULTS = {"epub": 50, "txt": 500, "fb2": 50}

def build_parser():
    p = argparse.ArgumentParser(
        description="Компиляция глав в TXT/EPUB/FB2 (чистый CLI, без меню).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Единицы: --chunk-size — ГЛАВЫ (чанкование по главам).
Примеры:
  %(prog)s --mode txt
  %(prog)s --mode epub --start 1 --end 120 --no-donate
  %(prog)s --mode epub-chunks --chunk-size 50
  %(prog)s --mode fb2 --source-type redacted
Интерактивный режим — лаунчер tools/run_clean_and_compile.py.
""",
    )
    p.add_argument("--mode", required=True,
                   choices=["txt", "txt-plain", "epub", "fb2",
                            "epub-chunks", "txt-chunks", "fb2-chunks"],
                   help="Действие: сборка (txt-plain/txt/epub/fb2) или "
                        "сборка частями (*-chunks). txt = TXT (Rulate) "
                        "с тегами «# [Название :|: N]»; txt-plain — TXT "
                        "как в переводе: заголовки без markdown-префиксов, "
                        "только очистка и компиляция")
    p.add_argument("--start", type=int, default=None,
                   help="Начальная глава (по умолчанию: минимальная найденная)")
    p.add_argument("--end", type=int, default=None,
                   help="Конечная глава (по умолчанию: максимальная найденная)")
    p.add_argument("--chapters-dir", default="./chapters",
                   help="Папка глав (по умолчанию: ./chapters)")
    p.add_argument("--source-type", default="polished",
                   choices=["polished", "redacted", "translated", "chapter"],
                   help="Тип исходного файла главы (по умолчанию: polished)")
    p.add_argument("--chunk-size", type=int, default=None,
                   help="Глав в одной части для *-chunks (по умолчанию: "
                        "epub=50, txt=500, fb2=50). Единица — ГЛАВЫ.")
    p.add_argument("--tmp-dir", default=".",
                   help="Каталог для compiled_*/book_*/titles_* (по умолчанию: .)")
    p.add_argument("--epub-cover", default="./source/cover.jpg",
                   help="Обложка для EPUB (по умолчанию: ./source/cover.jpg)")
    p.add_argument("--epub-meta", default="./source/metadata.yaml",
                   help="Метаданные книги, YAML (по умолчанию: ./source/metadata.yaml)")
    p.add_argument("--fb2-cover", default="./source/cover.jpg",
                   help="Обложка для инжекции в FB2 (по умолчанию: ./source/cover.jpg)")
    p.add_argument("--no-fb2-cover", action="store_true",
                   help="Не инжецировать обложку в FB2")
    p.add_argument("--no-cover", action="store_true",
                   help="Не добавлять обложку: ни в EPUB, ни в FB2")
    p.add_argument("--no-donate", action="store_true",
                   help="Не добавлять страницу поддержки в EPUB/FB2")
    p.add_argument("--donate-file", default="",
                   help="Файл страницы поддержки (по умолчанию: автопоиск "
                        "./source/donate.txt → ./prompts/donate.txt → встроенный текст)")
    return p


def main():
    args = build_parser().parse_args()
    # R9: фактическая команда запуска
    import shlex as _shlex
    import sys as _sys
    print(f"Запуск: {_shlex.join(_sys.argv)}")

    cfg.base_dir = args.chapters_dir
    cfg.tmp_dir = args.tmp_dir
    cfg.compile_type = args.source_type
    cfg.epub_cover = "" if args.no_cover else resolve_cover_path(args.epub_cover)
    cfg.epub_meta = args.epub_meta
    cfg.fb2_cover = "" if args.no_cover else resolve_cover_path(args.fb2_cover)
    cfg.fb2_inject_cover = 0 if (args.no_fb2_cover or args.no_cover) else 1
    cfg.add_donate_page = 0 if args.no_donate else 1
    cfg.donate_file = args.donate_file
    try:
        os.makedirs(cfg.tmp_dir, exist_ok=True)
    except OSError as exc:
        print(f"Ошибка: не удалось создать {cfg.tmp_dir}: {exc}")
        return 1

    # ── диапазон глав ──
    chapter_map = build_chapter_map(cfg.base_dir)
    auto_range = detect_range(chapter_map)
    if auto_range is None:
        sys.exit(f"❌ В '{cfg.base_dir}' не найдено ни одной папки с главами.")
    auto_start, auto_end = auto_range
    cfg.start = args.start if args.start is not None else auto_start
    cfg.end = args.end if args.end is not None else auto_end
    if cfg.start > cfg.end:
        sys.exit(f"❌ START ({cfg.start}) > END ({cfg.end})")

    print(f"Главы: {cfg.start}–{cfg.end} (авто: {auto_start}–{auto_end}) | "
          f"источник: {cfg.compile_type} | папка: {cfg.base_dir}")

    mode = args.mode
    if mode in ("txt", "txt-plain", "epub", "fb2"):
        compile_book(mode)
    else:  # *-chunks
        base_mode = mode.split("-", 1)[0]  # epub|txt|fb2
        chunk_size = args.chunk_size or CHUNK_DEFAULTS[base_mode]
        if chunk_size <= 0:
            sys.exit("❌ --chunk-size должен быть > 0.")
        compile_chunks(base_mode, chunk_size)


if __name__ == "__main__":
    main()
