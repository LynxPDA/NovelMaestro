#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archive.py — упаковка проекта в один файл и обратная распаковка.

Готовая книга — тысячи мелких файлов (одна боевая: 10 243 файла, 197 МБ на
диске). Хранить её «как есть» дорого не из-за места, а из-за количества: ФС
тратит блок на семикилобайтный файл, а копирование, индексирование и бэкап
замедляются с каждым файлом. Сплошное сжатие решает обе беды разом: 197 → 54 МБ
и один файл вместо десяти тысяч.

Формат — обычный `tar` с потоком `zstd` внутри. Своего формата нет намеренно:
архив обязан открываться без программы (7-Zip, `tar --zstd`, `zstd -d | tar -x`,
Windows 11), иначе «архив» превращается в склад, который читается только
NovelMaestro. Первым членом идёт манифест `.nm_archive.json`: имя проекта, дата,
версия и счётчики. Манифест читается из потока за десяток миллисекунд, поэтому
список архивов не распаковывает книги.

Бэкенды сжатия выбираются по наличию, от лучшего к худшему:

1. `zstandard` (pip, есть `threads=-1` — все ядра);
2. stdlib `compression.zstd` (Python ≥ 3.14, однопоточно);
3. `gzip` + `tar` (есть всегда — кнопка «Архивировать» не падает ни в Docker,
   ни в portable-сборке без установленного пакета; архив просто больше).

Прогресс — по файлам, а не по байтам: упаковка боевой книги занимает доли
секунды, бар на неё был бы дороже самой работы. `progress` — колбэк
`(done, total)`, его зовёт вызывающий слой (web — строками лога запуска).

Запись атомарна: рядом пишется `.tmp`, архив подменяется `os.replace` только
после успешной записи. Оборванная упаковка оставляет и проект, и мусорный
`.tmp`, но не битый архив. Распаковка так же идёт во временный каталог.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import shutil
import tarfile
import time
from pathlib import Path

__all__ = [
    "ArchiveError", "ARCHIVE_EXT", "ARCHIVE_DIR", "ARCHIVE_MANIFEST",
    "ARCHIVE_VERSION", "COMPRESSION_LEVEL", "backend", "archive_path",
    "archive_dir", "archive_manifest", "pack_project", "unpack_project",
    "archive_size", "archive_available", "archive_free_space",
]

log = logging.getLogger("core.archive")

#: расширение архива проекта
ARCHIVE_EXT = ".nmz"
#: каталог архивов внутри раздела (не выглядит проектом)
ARCHIVE_DIR = ".archive"
#: первый член архива — манифест (читается без распаковки книги)
ARCHIVE_MANIFEST = ".nm_archive.json"
#: версия формата; незнакомая читается как есть (состав манифеста расширяем)
ARCHIVE_VERSION = 1
#: уровень zstd: 9 — 25,6 МБ / 0,4 с на боевой книге; 19 даёт 20,4 МБ,
#: но 13,8 с, и это уже заметная пауза при нажатии кнопки
COMPRESSION_LEVEL = 9

# Временные имена рядом с целью — не видны как проект/архив
_TMP_SUFFIX = ".tmp"
_RESTORE_PREFIX = ".nm_restore_"
# Как часто звать progress(): колбэк стадии пишет строку в лог, на каждый
# файл это сотни строк на книгу
_PROGRESS_EVERY = 64


class ArchiveError(Exception):
    """Ошибка упаковки/распаковки: причина человеческим текстом."""


# ════════════════════════════════════════════════════════════════════
# бэкенды сжатия


def _backend_zstandard():
    """pip-пакет zstandard: многопоточный zstd (лучший вариант)."""
    zstandard = importlib.import_module("zstandard")

    class _Writer:
        """Приёмник, совместимый с tarfile (write + flush + close)."""

        def __init__(self, fh):
            self._c = zstandard.ZstdCompressor(
                level=COMPRESSION_LEVEL, threads=-1)
            self._w = self._c.stream_writer(fh, closefd=False)

        def write(self, data):
            return self._w.write(data)

        def flush(self):
            self._w.flush()

        def close(self):
            self._w.close()

    class _Reader:
        """Источник, совместимый с tarfile (read + readable)."""

        def __init__(self, fh):
            self._r = zstandard.ZstdDecompressor().stream_reader(fh)

        def read(self, n=-1):
            return self._r.read(n)

        def readable(self):
            return True

    return {
        "id": "zstandard",
        "label": "zstandard",
        "ext": "tar.zst",
        "open_write": _Writer,
        "open_read": _Reader,
    }


def _backend_stdlib():
    """stdlib compression.zstd (Python ≥ 3.14): тот же формат, одно ядро."""
    zstd = importlib.import_module("compression.zstd")

    class _Writer:
        def __init__(self, fh):
            self._c = zstd.ZstdCompressor(level=COMPRESSION_LEVEL)
            self._fh = fh

        def write(self, data):
            chunk = self._c.compress(bytes(data))
            if chunk:
                self._fh.write(chunk)
            return len(data)

        def flush(self):
            chunk = self._c.flush(zstd.ZstdCompressor.FLUSH_BLOCK)
            if chunk:
                self._fh.write(chunk)
            self._fh.flush()

        def close(self):
            tail = self._c.flush()
            if tail:
                self._fh.write(tail)
            self._fh.flush()

    class _Reader:
        """Потоковая распаковка: tarfile просит и read(n), и read()."""

        def __init__(self, fh):
            self._d = zstd.ZstdDecompressor()
            self._fh = fh
            self._buf = b""
            self._eof = False

        def _pull(self) -> bool:
            data = self._fh.read(1 << 20)
            if not data:
                self._eof = True
                return False
            try:
                self._buf += self._d.decompress(data)
            except zstd.ZstdError as exc:  # битый архив
                raise ArchiveError(f"Архив повреждён: {exc}") from exc
            return True

        def read(self, n=-1):
            if n is None or n < 0:
                while not self._eof and self._pull():
                    pass
                out, self._buf = self._buf, b""
                return out
            while len(self._buf) < n and not self._eof:
                self._pull()
            out, self._buf = self._buf[:n], self._buf[n:]
            return out

        def readable(self):
            return True

    return {
        "id": "stdlib",
        "label": "compression.zstd",
        "ext": "tar.zst",
        "open_write": _Writer,
        "open_read": _Reader,
    }


def _backend_gzip():
    """Последний рубеж: tar с gzip — понимают все, но архив больше."""
    from gzip import GzipFile

    class _Writer:
        def __init__(self, fh):
            self._z = GzipFile(fileobj=fh, mode="wb", compresslevel=6,
                               mtime=0)

        def write(self, data):
            return self._z.write(data)

        def flush(self):
            self._z.flush()

        def close(self):
            self._z.close()

    class _Reader:
        def __init__(self, fh):
            self._z = GzipFile(fileobj=fh, mode="rb")

        def read(self, n=-1):
            return self._z.read(n)

        def readable(self):
            return True

    return {
        "id": "gzip",
        "label": "gzip",
        "ext": "tar.gz",
        "open_write": _Writer,
        "open_read": _Reader,
    }


_BACKENDS = (
    ("zstandard", _backend_zstandard),
    ("compression.zstd", _backend_stdlib),
    (None, _backend_gzip),
)
_backend_cache: dict | None = None


def _probe(module: str | None) -> bool:
    """Есть ли модуль; битое окружение — «нет», а не падение."""
    if module is None:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def backend() -> dict:
    """Активный бэкенд сжатия: `{id, label, ext, open_write, open_read}`.

    Выбор один раз за процесс (как транспорт): набор установленных пакетов
    посреди работы не меняется, а перепроверка на каждую книгу — лишний
    импорт. `id` попадает в манифест, чтобы будущая версия могла сказать
    «этот архив писался gzip».
    """
    global _backend_cache
    if _backend_cache is not None:
        return _backend_cache
    for module, factory in _BACKENDS:
        if not _probe(module):
            continue
        try:
            _backend_cache = factory()
            return _backend_cache
        except Exception as exc:  # noqa: BLE001 — битый пакет не должен ронять
            log.warning("Бэкенд сжатия %s не загрузился: %s", module, exc)
    # недостижимо: gzip есть в stdlib, но оставляем явную ошибку
    raise ArchiveError("Нет ни одного бэкенда сжатия")


def archive_available() -> bool:
    """Можно ли упаковывать (всегда да: последний фолбэк — stdlib)."""
    try:
        backend()
        return True
    except ArchiveError:
        return False


# ════════════════════════════════════════════════════════════════════
# пути и манифест


def archive_dir(section_dir) -> Path:
    """Каталог архивов раздела: `<раздел>/.archive`."""
    return Path(section_dir) / ARCHIVE_DIR


def archive_path(section_dir, name: str) -> Path:
    """Путь архива проекта: `<раздел>/.archive/<имя>.nmz`."""
    return archive_dir(section_dir) / f"{name}{ARCHIVE_EXT}"


def archive_size(path) -> int:
    """Размер архива в байтах; нет файла — 0."""
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def archive_free_space(path) -> int:
    """Сколько байт свободно на диске, где лежит архив (0 — не узнать)."""
    try:
        return shutil.disk_usage(str(Path(path).parent)).free
    except OSError:
        return 0


def _arc_name(name: str) -> str:
    """Имя члена архива без ведущего "./" (tar пишет "./" в разных режимах).

    `lstrip("./")` для этого не годится: он срезает СИМВОЛЫ, и у манифеста
    `.nm_archive.json` съедает не только слэш, но и точку.
    """
    while name.startswith("./"):
        name = name[2:]
    return name


def archive_manifest(path) -> dict:
    """Манифест архива без распаковки книги (пустой — файл не читается).

    Манифест — первый член архива, поэтому читается только его голова:
    список архивов не разворачивает книги. Ошибок не бросает: по битому
    файлу карточка скажет «архив повреждён», а не уронит страницу.
    """
    path = Path(path)
    be = backend()
    try:
        with open(path, "rb") as fh:
            reader = be["open_read"](fh)
            with tarfile.open(fileobj=reader, mode="r|") as tf:
                for member in tf:
                    if _arc_name(member.name) == ARCHIVE_MANIFEST:
                        data = tf.extractfile(member)
                        raw = data.read() if data is not None else b""
                        got = json.loads(raw.decode("utf-8"))
                        return got if isinstance(got, dict) else {}
                    break  # манифест первый — остальное не сканируем
    except (OSError, tarfile.TarError, ArchiveError, ValueError,
            UnicodeDecodeError, EOFError) as exc:
        log.debug("Манифест %s не читается: %s", path, exc)
    return {}


def _tree_stats(src: Path, skip: set[str]) -> tuple[int, int]:
    """(файлов, байт) в каталоге, без служебных подпапок верхнего уровня."""
    files = 0
    total = 0
    for root, dirs, names in os.walk(src):
        rel = Path(root).relative_to(src)
        if not rel.parts:
            dirs[:] = [d for d in dirs if d not in skip]
        for n in names:
            p = Path(root) / n
            if p.is_symlink():
                continue
            try:
                total += p.stat().st_size
            except OSError:
                continue
            files += 1
    return files, total


def _manifest(src: Path, skip: set[str]) -> dict:
    """Манифест книги: имя, дата, версия, счётчики, исключённое."""
    files, total = _tree_stats(src, skip)
    return {
        "name": src.name,
        "packed": round(time.time(), 3),
        "packed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": ARCHIVE_VERSION,
        "files": files,
        "bytes": total,
        "skip": sorted(skip),
        "compression": backend()["id"],
    }


# ════════════════════════════════════════════════════════════════════
# упаковка


def _walk_sorted(src: Path, skip: set[str]):
    """(относительный путь, полный путь) в порядке обхода: папки, затем файлы.

    Пустые каталоги тоже отдаются: каркас проекта (`chapters/`, `prompts/`)
    без них после распаковки исчезает, и стадия падает на «нет папки» вместо
    «нет глав».
    """
    for root, dirs, names in os.walk(src):
        rel = Path(root).relative_to(src)
        if not rel.parts:
            dirs[:] = sorted(d for d in dirs if d not in skip)
        else:
            dirs[:] = sorted(dirs)
        for d in dirs:
            yield (rel / d) if rel.parts else Path(d), Path(root) / d
        for n in sorted(names):
            p = Path(root) / n
            if p.is_symlink():  # симлинк — побег за пределы книги
                continue
            yield (rel / n) if rel.parts else Path(n), p


def pack_project(src, dst, *, skip=None, progress=None,
                 manifest_extra=None) -> dict:
    """Упаковать каталог проекта в один архив (атомарно).

    `skip` — имена папок ВЕРХНЕГО уровня проекта, которые не входят в архив
    (например `logs` и `tmp`); по умолчанию не исключается ничего: следы
    работы (логи, состояние вкладок) — тоже часть книги.

    `progress(done, total)` зовётся раз в `_PROGRESS_EVERY` файлов и в конце:
    колбэк стадии пишет строку в лог, на каждый файл это сотни строк на книгу.

    Возвращает манифест: `{name, packed, packed_at, version, files, bytes,
    skip, compression, ...manifest_extra}`.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_dir():
        raise ArchiveError(f"Проект не найден: {src}")
    skip_names: set[str] = set(skip or ())
    dst.parent.mkdir(parents=True, exist_ok=True)
    be = backend()
    man = _manifest(src, skip_names)
    if manifest_extra:
        man.update({k: v for k, v in manifest_extra.items() if k not in man})
    man["compression"] = be["id"]

    tmp = dst.with_name(dst.name + _TMP_SUFFIX)
    written = 0
    total = man["files"]
    try:
        with open(tmp, "wb") as fh:
            writer = be["open_write"](fh)
            try:
                with tarfile.open(fileobj=writer, mode="w|") as tf:
                    head = json.dumps(man, ensure_ascii=False,
                                      indent=2).encode("utf-8")
                    info = tarfile.TarInfo(ARCHIVE_MANIFEST)
                    info.size = len(head)
                    info.mtime = int(man["packed"])
                    info.mode = 0o644
                    tf.addfile(info, _BytesIO(head))
                    for rel, path in _walk_sorted(src, skip_names):
                        try:
                            tf.add(str(path), arcname=str(rel),
                                   recursive=False)
                        except OSError as exc:  # файл исчез посреди упаковки
                            log.debug("Пропущен %s: %s", path, exc)
                            continue
                        if not path.is_dir():
                            written += 1
                            if progress and (written % _PROGRESS_EVERY == 0
                                             or written == total):
                                progress(written, total)
            finally:
                writer.close()
        if progress:
            progress(total, total)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return man


def _BytesIO(data: bytes):
    """Байтовый поток для tarfile (импорт io держим рядом с использованием)."""
    import io

    return io.BytesIO(data)


def unpack_project(archive, dst, *, progress=None) -> dict:
    """Распаковать архив в каталог `dst` (атомарно, через временный).

    Существующий `dst` — ошибка: вызывающий обязан решить, что делать с
    проектом на месте, а не терять его молча.
    """
    archive = Path(archive)
    dst = Path(dst)
    if dst.exists():
        raise ArchiveError(f"Куда распаковывать, уже занято: {dst}")
    if not archive.is_file():
        raise ArchiveError(f"Архив не найден: {archive}")
    be = backend()
    man = archive_manifest(archive)
    try:
        total = int(man.get("files") or 0)
    except (TypeError, ValueError):  # манифест мог прийти чужим/битым
        total = 0
    tmp = dst.parent / (_RESTORE_PREFIX + dst.name)
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
    except OSError as exc:
        raise ArchiveError(f"Не удалось очистить временный каталог: {exc}") from exc
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        done = 0
        with open(archive, "rb") as fh:
            reader = be["open_read"](fh)
            with tarfile.open(fileobj=reader, mode="r|") as tf:
                for member in tf:
                    if _arc_name(member.name) == ARCHIVE_MANIFEST:
                        continue
                    tf.extract(member, tmp, filter="data")
                    if member.isfile():
                        done += 1
                        if progress and (done % _PROGRESS_EVERY == 0
                                         or done == total):
                            progress(done, total or done)
        if progress:
            progress(total or done, total or done)
        os.replace(tmp, dst)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return man
