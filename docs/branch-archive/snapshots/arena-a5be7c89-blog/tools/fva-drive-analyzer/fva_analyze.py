#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FVA Drive Analyzer — a READ-ONLY analyzer for a local copy of a Google Drive
tree (downloaded / synced / rclone-mounted) or for a Drive metadata inventory
JSON.

Safety contract:
  * The source tree is never modified, moved, renamed or deleted.
  * The output directory must be outside the source tree (unless explicitly
    overridden), so reports are not written back into a synced Drive folder.
  * Sensitive files are never opened for content, never hashed, and no snippet
    from any file is written to any output.  Evidence is keyword *names* only.
  * Duplicate detection only *reports* hash-identical candidates.
"""

from __future__ import annotations

import argparse
import bz2
import codecs
import csv
import gzip
import hashlib
import io
import json
import lzma
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional
from xml.etree import ElementTree

SCRIPT_DIR = Path(__file__).resolve().parent

SECTION_DEFINITE = "الف) مرتبط قطعی"
SECTION_PROBABLE = "ب) مرتبط احتمالی"
SECTION_UNRELATED = "ج) نامرتبط"
SECTION_UNKNOWN = "د) شک‌دار یا خوانده‌نشده"

HEADERS = [
    "نام",
    "شناسه",
    "مسیر فعلی",
    "نوع",
    "حجم",
    "تاریخ ویرایش",
    "اشتراکی؟",
    "قابل‌خواندن؟",
    "بخش",
    "اطمینان",
    "دلیل (با شاهد)",
    # Helper columns are appended after the 11 requested columns.
    "حساس؟",
    "هش (SHA-256/MD5)",
    "گروه کاندید تکراری",
    "تعداد اعضای آرشیو",
    "سطل زباله؟",
]

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json",
    ".jsonl", ".xml", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h",
    ".hpp", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".html", ".htm",
    ".css", ".sql", ".patch", ".diff", ".srt", ".vtt",
}
DOCUMENT_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".odt", ".epub"}
ZIP_EXTENSIONS = {".zip"}
TAR_SUFFIXES = (".tar.gz.tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2",
                ".tar.xz", ".txz", ".tar")
SINGLE_STREAM_SUFFIXES = (".gz", ".bz2", ".xz")
EXTERNAL_ARCHIVE_EXTENSIONS = {".rar", ".7z"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg",
}
GOOGLE_SHORTCUT_EXTENSIONS = {
    ".gdoc", ".gsheet", ".gslides", ".gdraw", ".gform", ".gjam",
}
FOLDER_MIME = "application/vnd.google-apps.folder"


# --------------------------------------------------------------------------- #
# Normalisation / keyword matching
# --------------------------------------------------------------------------- #

_NORMALISE_MAP = str.maketrans({
    "\u064a": "\u06cc",  # Arabic yeh -> Farsi yeh
    "\u0643": "\u06a9",  # Arabic kaf -> Farsi keheh
    "\u0623": "\u0627",
    "\u0625": "\u0627",
    "\u0640": "",        # tatweel
    "\u200c": " ",       # ZWNJ
    "\u200d": "",        # ZWJ
    "\ufeff": "",
})


def normalise(text: str) -> str:
    """Lower-case and canonicalise text so Persian/Arabic/delimiter
    variants compare equal.  Delimiters become spaces and repeated
    whitespace is collapsed."""
    text = (text or "").lower().translate(_NORMALISE_MAP)
    text = re.sub(r"[_\-./\\]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@dataclass
class KeywordSet:
    strong: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    sensitive: list[str] = field(default_factory=list)
    sensitive_extensions: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Optional[Path]) -> "KeywordSet":
        data: dict[str, Any] = {}
        if path and Path(path).is_file():
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        return cls(
            strong=[normalise(t) for t in data.get("strong_terms", []) if t],
            weak=[normalise(t) for t in data.get("weak_terms", []) if t],
            sensitive=[normalise(t) for t in data.get("sensitive_terms", []) if t],
            sensitive_extensions={
                e.lower() if e.startswith(".") else "." + e.lower()
                for e in data.get("sensitive_extensions", [])
            },
        )


_SHORT_ASCII = {"fva", "ocr", "tts"}


def find_terms(normalised_text: str, terms: Iterable[str]) -> dict[str, int]:
    """Return {term: occurrence-count} for every configured term found.

    Short ASCII tokens require word boundaries, so 'fva' does not fire
    inside unrelated longer words."""
    found: dict[str, int] = {}
    if not normalised_text:
        return found
    for term in terms:
        if not term:
            continue
        if term in _SHORT_ASCII or (
            len(term) <= 3 and term.isascii() and term.isalpha()
        ):
            hits = len(re.findall(rf"\b{re.escape(term)}\b", normalised_text))
        else:
            hits = normalised_text.count(term)
        if hits:
            found[term] = hits
    return found


def is_sensitive(name: str, rel_path: str, kw: KeywordSet) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix in kw.sensitive_extensions:
        return True
    joined = normalise(name + " " + rel_path)
    # Sensitive terms are matched against the file name as well as its path;
    # e.g. anything under a Takeout folder is treated as sensitive and is
    # never opened — only labelled.
    raw = (name + " " + rel_path).lower()
    for term in kw.sensitive:
        if term and (term in joined or term in raw):
            return True
    # ``.env`` style names have no "extension" in Path terms; catch them here.
    return name.lower() == ".env" or name.lower().startswith(".env.")


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

@dataclass
class Evidence:
    name_strong: dict[str, int] = field(default_factory=dict)
    name_weak: dict[str, int] = field(default_factory=dict)
    content_strong: dict[str, int] = field(default_factory=dict)
    content_weak: dict[str, int] = field(default_factory=dict)
    archive_strong: dict[str, int] = field(default_factory=dict)
    archive_weak: dict[str, int] = field(default_factory=dict)

    def describe(self) -> str:
        parts: list[str] = []

        def fmt(label: str, terms: dict[str, int]) -> None:
            if terms:
                shown = "، ".join(sorted(terms)[:8])
                parts.append(f"{label}: {shown}")

        fmt("نام/مسیر قوی", self.name_strong)
        fmt("نام/مسیر ضعیف", self.name_weak)
        fmt("محتوای خوانده‌شده قوی", self.content_strong)
        fmt("محتوای خوانده‌شده ضعیف", self.content_weak)
        fmt("فهرست داخلی آرشیو قوی", self.archive_strong)
        fmt("فهرست داخلی آرشیو ضعیف", self.archive_weak)
        return "؛ ".join(parts) if parts else "هیچ سرنخی یافت نشد"


def classify(
    *,
    item_type: str,
    sensitive: bool,
    read_status: str,   # full | partial | archive_listed | unread | folder
    unread_reason: str,
    ev: Evidence,
) -> tuple[str, str, str]:
    """Return (section, confidence, reason-with-evidence).

    Confidence is deliberately conservative:
      * بالا  only when readable content was actually read to the end;
      * name/path/archive-member-name evidence alone is at most متوسط;
      * an unread file can never be declared "نامرتبط".
    """
    if sensitive:
        return SECTION_UNKNOWN, "پایین", "حساس — عمداً خوانده نشد"

    if item_type == "folder":
        if ev.name_strong:
            return SECTION_PROBABLE, "متوسط", (
                "پوشه؛ فقط نام/مسیر شاهد است و فایل‌هایش جداگانه ردیف دارند؛ "
                + ev.describe()
            )
        if len(ev.name_weak) >= 2:
            return SECTION_PROBABLE, "پایین", (
                "پوشه؛ فقط نام/مسیر شاهد است؛ " + ev.describe()
            )
        return SECTION_UNKNOWN, "پایین", (
            "پوشه؛ محتوای آن در ردیف فایل‌ها ارزیابی می‌شود؛ " + ev.describe()
        )

    if read_status in ("full", "partial"):
        confidence = "بالا" if read_status == "full" else "متوسط"
        if ev.content_strong and (
            len(ev.content_strong) >= 2 or ev.content_weak or ev.name_strong
        ):
            return SECTION_DEFINITE, confidence, ev.describe()
        if ev.content_strong or len(ev.content_weak) >= 2:
            return SECTION_PROBABLE, confidence, ev.describe()
        if ev.content_weak:
            return SECTION_PROBABLE, "پایین", ev.describe()
        if ev.name_strong or len(ev.name_weak) >= 2:
            return SECTION_PROBABLE, "متوسط", (
                "محتوای خوانده‌شده سرنخی نداشت؛ فقط نام/مسیر شاهد است؛ "
                + ev.describe()
            )
        if ev.name_weak:
            return SECTION_UNKNOWN, "پایین", (
                "محتوای خوانده‌شده سرنخی نداشت و شاهد نام ضعیف است؛ "
                + ev.describe()
            )
        return SECTION_UNRELATED, confidence, (
            "محتوا خوانده شد و هیچ سرنخ FarsiVoiceAI نداشت؛ " + ev.describe()
        )

    if read_status == "archive_listed":
        if ev.archive_strong or ev.name_strong:
            return SECTION_PROBABLE, "متوسط", (
                "فقط فهرست اعضای آرشیو خوانده شد، نه محتوای اعضا؛ "
                + ev.describe()
            )
        if len(ev.archive_weak) >= 2 or len(ev.name_weak) >= 2:
            return SECTION_PROBABLE, "پایین", (
                "فقط فهرست اعضای آرشیو خوانده شد؛ " + ev.describe()
            )
        return SECTION_UNKNOWN, "پایین", (
            "فهرست اعضای آرشیو سرنخ کافی نداشت و محتوای اعضا خوانده نشد؛ "
            + ev.describe()
        )

    # Unread file: name/path evidence only — never declare it unrelated.
    reason_tail = f"خوانده نشد: {unread_reason}" if unread_reason else "خوانده نشد"
    if ev.name_strong:
        return SECTION_PROBABLE, "متوسط", reason_tail + "؛ " + ev.describe()
    if len(ev.name_weak) >= 2:
        return SECTION_PROBABLE, "پایین", reason_tail + "؛ " + ev.describe()
    return SECTION_UNKNOWN, "پایین", reason_tail + "؛ " + ev.describe()


# --------------------------------------------------------------------------- #
# Content extraction (standard library only)
# --------------------------------------------------------------------------- #

class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.chunks.append(data)


def _strip_html(text: str) -> str:
    parser = _TextCollector()
    try:
        parser.feed(text)
        return " ".join(parser.chunks)
    except Exception:
        return re.sub(r"<[^>]+>", " ", text)


def _xml_text(blob: bytes) -> str:
    try:
        root = ElementTree.fromstring(blob)
    except Exception:
        return ""
    return " ".join(t.strip() for t in root.itertext() if t and t.strip())


def looks_like_text(sample: bytes) -> bool:
    if not sample:
        return True
    if b"\x00" in sample:
        try:
            sample.decode("utf-16")
            return True
        except Exception:
            return False
    for encoding in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            sample.decode(encoding)
            return True
        except Exception:
            continue
    return False


def extract_text_from_stream(
    byte_iter: Iterator[bytes], max_chars: int
) -> tuple[str, bool]:
    """Decode a byte stream incrementally.  Returns (text, reached_eof).

    Decoding tries UTF-8 (with BOM) then UTF-16 then Windows-1256 (common
    for older Persian text), always tolerating bad bytes so one corrupt
    byte cannot make a whole book "unreadable".
    """
    buffered = bytearray()
    reached_eof = False
    char_budget = max_chars * 4  # bytes are an upper bound for decoded chars
    for chunk in byte_iter:
        buffered.extend(chunk)
        if len(buffered) >= char_budget:
            break
    else:
        reached_eof = True
    data = bytes(buffered)
    for encoding in ("utf-8-sig", "utf-16", "cp1256"):
        try:
            text = data.decode(encoding)
            break
        except Exception:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars]
        reached_eof = False
    return text, reached_eof


def read_plain_text(path: Path, max_chars: int) -> tuple[str, str]:
    """Return (text, status) where status is full|partial."""

    def gen() -> Iterator[bytes]:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                yield chunk

    text, eof = extract_text_from_stream(gen(), max_chars)
    if path.suffix.lower() in (".html", ".htm"):
        text = _strip_html(text)
    return text, "full" if eof else "partial"


def extract_office_text(path: Path, max_chars: int) -> tuple[str, str]:
    """Extract text from docx/xlsx/pptx/odt/epub using only zip+xml."""
    suffix = path.suffix.lower()
    texts: list[str] = []
    total = 0
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if suffix == ".docx":
                wanted = [n for n in names if n == "word/document.xml"]
            elif suffix == ".xlsx":
                wanted = [
                    n for n in names
                    if n == "xl/sharedStrings.xml" or n.startswith("xl/worksheets/")
                ]
            elif suffix == ".pptx":
                wanted = [n for n in names if n.startswith("ppt/slides/slide")]
            elif suffix == ".odt":
                wanted = [n for n in names if n == "content.xml"]
            elif suffix == ".epub":
                wanted = [
                    n for n in names
                    if n.lower().endswith((".xhtml", ".html", ".htm", ".xml", ".opf", ".ncx"))
                ]
            else:
                wanted = []
            truncated = False
            for name in sorted(wanted):
                try:
                    blob = zf.read(name)
                except Exception:
                    continue
                if suffix == ".epub" and not name.lower().endswith((".opf", ".ncx", ".xml")):
                    piece = _strip_html(
                        blob.decode("utf-8", errors="replace")
                    )
                else:
                    piece = _xml_text(blob)
                if piece:
                    texts.append(piece)
                    total += len(piece)
                if total >= max_chars:
                    truncated = True
                    break
    except (zipfile.BadZipFile, OSError):
        return "", "unread"
    text = " ".join(texts)[:max_chars]
    if not text:
        return "", "unread"
    return text, "partial" if truncated else "full"


def extract_pdf_text(path: Path, max_chars: int, max_pages: int) -> tuple[str, str, str]:
    """Best-effort PDF text: optional pypdf/PyPDF2, else `pdftotext`.

    Returns (text, status, unread_reason).  An image-only scan yields no
    text and is honestly reported as unread."""
    pdf_module = None
    for module_name in ("pypdf", "PyPDF2"):
        try:
            pdf_module = __import__(module_name)
            break
        except Exception:
            continue
    if pdf_module is not None:
        try:
            reader = pdf_module.PdfReader(str(path))
            pages = list(getattr(reader, "pages", []) or [])
            selected = pages[: max_pages if max_pages > 0 else None]
            pieces: list[str] = []
            for page in selected:
                try:
                    pieces.append(page.extract_text() or "")
                except Exception:
                    continue
            text = " ".join(pieces)[:max_chars]
            if not text.strip():
                return "", "unread", "PDF بدون لایه متن (اسکن تصویری)"
            status = "full" if len(selected) >= len(pages) else "partial"
            return text, status, ""
        except Exception as exc:  # malformed/encrypted PDF
            return "", "unread", f"خطای خواندن PDF: {type(exc).__name__}"

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        cmd = [pdftotext, "-layout"]
        partial = False
        if max_pages > 0:
            cmd += ["-f", "1", "-l", str(max_pages)]
            partial = True
        cmd += [str(path), "-"]
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120
            )
        except Exception as exc:
            return "", "unread", f"خطای pdftotext: {type(exc).__name__}"
        text = proc.stdout.decode("utf-8", errors="replace")[:max_chars]
        if not text.strip():
            return "", "unread", "PDF بدون لایه متن (اسکن تصویری)"
        return text, "partial" if partial else "full", ""

    return "", "unread", "PDF — نه pypdf نصب است نه pdftotext در PATH هست"


# --------------------------------------------------------------------------- #
# Archives: listing only, never extracting
# --------------------------------------------------------------------------- #

@dataclass
class ArchiveMember:
    archive: str
    member: str
    size: str
    compressed_size: str
    kind: str
    note: str


def _member_note(member_name: str) -> str:
    lowered = member_name.lower()
    if lowered.endswith((".zip", ".rar", ".7z", ".tar", ".tgz", ".gz", ".bz2", ".xz")):
        return "آرشیو تو در تو — در این ابزار باز نشد"
    return ""


def list_zip(path: Path) -> tuple[list[ArchiveMember], str]:
    members: list[ArchiveMember] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                note = _member_note(info.filename)
                if info.flag_bits & 0x1:
                    note = (note + "؛ " if note else "") + "رمزدار"
                members.append(ArchiveMember(
                    archive="",
                    member=info.filename,
                    size=str(info.file_size),
                    compressed_size=str(info.compress_size),
                    kind="folder" if info.is_dir() else "file",
                    note=note,
                ))
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        return [], f"zip خوانده نشد: {type(exc).__name__}"
    return members, ""


def list_tar(path: Path) -> tuple[list[ArchiveMember], str]:
    members: list[ArchiveMember] = []
    try:
        with tarfile.open(path, "r:*") as tf:
            for info in tf:
                members.append(ArchiveMember(
                    archive="",
                    member=info.name,
                    size=str(info.size),
                    compressed_size="",
                    kind="folder" if info.isdir() else "file",
                    note=_member_note(info.name),
                ))
    except (tarfile.TarError, OSError, EOFError) as exc:
        return [], f"tar خوانده نشد: {type(exc).__name__}"
    return members, ""


def list_single_stream(path: Path) -> tuple[list[ArchiveMember], str]:
    suffix = path.suffix.lower()
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}.get(suffix)
    if opener is None:
        return [], "فرمت فشرده پشتیبانی نمی‌شود"
    try:
        # One small read proves the stream opens; size of the payload is
        # unknown without decompressing everything, so it stays blank.
        with opener(path, "rb") as fh:  # type: ignore[operator]
            fh.read(1)
    except (OSError, EOFError) as exc:
        return [], f"جریان فشرده خوانده نشد: {type(exc).__name__}"
    inner = path.name[: -len(suffix)] or path.name
    return [ArchiveMember("", inner, "", "", "file",
                          "فایل تکی فشرده‌شده؛ حجم بازشده بدون بازگشایی کامل معلوم نیست")], ""


def list_external_archive(path: Path) -> tuple[list[ArchiveMember], str]:
    """rar/7z listing via an external lister if one exists (read-only)."""
    bsdtar = shutil.which("bsdtar")
    if bsdtar:
        try:
            proc = subprocess.run(
                [bsdtar, "-tf", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
            )
            if proc.returncode == 0:
                names = proc.stdout.decode("utf-8", errors="replace").splitlines()
                return [
                    ArchiveMember("", n, "", "", "file", _member_note(n))
                    for n in names if n.strip()
                ], ""
        except Exception:
            pass
    for tool, argv in (
        ("7z", ["l", "-ba", str(path)]),
        ("7zz", ["l", "-ba", str(path)]),
        ("unrar", ["lb", str(path)]),
    ):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            proc = subprocess.run(
                [exe, *argv], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=180,
            )
        except Exception:
            continue
        if proc.returncode != 0:
            continue
        names: list[str] = []
        for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            # ``unrar lb`` prints bare names; ``7z l -ba`` prints columns
            # ending with the name.  Take the last whitespace-separated
            # field for 7z output and the whole line for unrar.
            names.append(line if tool == "unrar" else line.split()[-1])
        if names:
            return [
                ArchiveMember("", n, "", "", "file", _member_note(n)) for n in names
            ], ""
    return [], (
        "rar/7z — هیچ‌کدام از bsdtar/7z/unrar در PATH نیست؛ "
        "محتوای داخلی نامشخص و حدس زده نمی‌شود"
    )


def archive_kind(path: Path) -> Optional[str]:
    name = path.name.lower()
    for suffix in TAR_SUFFIXES:
        if name.endswith(suffix):
            return "tar"
    suffix = path.suffix.lower()
    if suffix in ZIP_EXTENSIONS:
        return "zip"
    if suffix in EXTERNAL_ARCHIVE_EXTENSIONS:
        return "external"
    if name.endswith(SINGLE_STREAM_SUFFIXES):
        return "single"
    return None


def list_archive(path: Path) -> tuple[list[ArchiveMember], str]:
    kind = archive_kind(path)
    if kind == "zip":
        return list_zip(path)
    if kind == "tar":
        return list_tar(path)
    if kind == "single":
        return list_single_stream(path)
    if kind == "external":
        return list_external_archive(path)
    return [], "آرشیو نیست"


# --------------------------------------------------------------------------- #
# Hashing (streaming, cached)
# --------------------------------------------------------------------------- #

class HashCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self.entries = raw
            except Exception:
                self.entries = {}

    def get(self, key: str, size: int, mtime_ns: int) -> Optional[str]:
        entry = self.entries.get(key)
        if entry and entry.get("size") == size and entry.get("mtime_ns") == mtime_ns:
            value = entry.get("sha256")
            return value if isinstance(value, str) else None
        return None

    def put(self, key: str, size: int, mtime_ns: int, digest: str) -> None:
        self.entries[key] = {"size": size, "mtime_ns": mtime_ns, "sha256": digest}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.entries, fh, ensure_ascii=False, sort_keys=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Row model shared by local-scan and Drive-inventory modes
# --------------------------------------------------------------------------- #

@dataclass
class Row:
    name: str
    item_id: str
    path: str
    type_label: str
    item_type: str  # file | folder
    size: str
    size_int: int
    modified: str
    shared: str
    readable: str
    section: str
    confidence: str
    reason: str
    sensitive: bool
    digest: str
    digest_kind: str
    archive_members: str = ""
    trashed: bool = False
    duplicate_group: str = ""
    matched_terms: list[str] = field(default_factory=list)
    top_folder: str = ""
    extension: str = ""
    read_status: str = "unread"
    unread_reason: str = ""

    def to_csv(self) -> list[str]:
        return [
            self.name,
            self.item_id,
            self.path,
            self.type_label,
            self.size,
            self.modified,
            self.shared,
            self.readable,
            self.section,
            self.confidence,
            self.reason,
            "بله" if self.sensitive else "خیر",
            (
                f"{self.digest_kind}:{self.digest}"
                if self.digest else ""
            ),
            self.duplicate_group,
            self.archive_members,
            "بله" if self.trashed else "خیر",
        ]


def iso_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def local_id(rel_path: str) -> str:
    return "local-" + hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# Local filesystem scan
# --------------------------------------------------------------------------- #

@dataclass
class ScanOptions:
    max_content_chars: int = 2_000_000
    pdf_pages: int = 20
    use_hash: bool = True
    hash_max_bytes: int = 0  # 0 = unlimited


def analyze_local_file(
    path: Path,
    rel_path: str,
    kw: KeywordSet,
    opts: ScanOptions,
    cache: HashCache,
    archive_rows: list[ArchiveMember],
) -> Row:
    name = path.name
    suffix = path.suffix.lower()
    mime = mimetypes.guess_type(name)[0] or (
        "folder" if path.is_dir() else f"file{suffix}"
    )
    try:
        stat = path.stat()
        size_int = int(stat.st_size) if path.is_file() else 0
        modified = iso_time(stat.st_mtime)
        mtime_ns = int(stat.st_mtime_ns)
    except OSError as exc:
        size_int, modified, mtime_ns = 0, "", 0
        ev = Evidence(
            name_strong=find_terms(normalise(name + " " + rel_path), kw.strong),
            name_weak=find_terms(normalise(name + " " + rel_path), kw.weak),
        )
        section, confidence, reason = classify(
            item_type="file", sensitive=False, read_status="unread",
            unread_reason=f"خطای دسترسی به متادیتا: {type(exc).__name__}", ev=ev,
        )
        return Row(
            name=name, item_id=local_id(rel_path), path=rel_path,
            type_label=mime, item_type="file", size="", size_int=0,
            modified="", shared="نامشخص", readable="خیر", section=section,
            confidence=confidence, reason=reason, sensitive=False,
            digest="", digest_kind="", extension=suffix,
            read_status="unread",
            unread_reason=f"خطای دسترسی: {type(exc).__name__}",
        )

    sensitive = is_sensitive(name, rel_path, kw)
    joined = normalise(name + " " + rel_path)
    ev = Evidence(
        name_strong=find_terms(joined, kw.strong),
        name_weak=find_terms(joined, kw.weak),
    )

    if path.is_dir():
        section, confidence, reason = classify(
            item_type="folder", sensitive=False, read_status="folder",
            unread_reason="", ev=ev,
        )
        return Row(
            name=name, item_id=local_id(rel_path), path=rel_path,
            type_label=FOLDER_MIME, item_type="folder", size="",
            size_int=0, modified=modified, shared="نامشخص",
            readable="پوشه — محتوای متنی ندارد", section=section,
            confidence=confidence, reason=reason, sensitive=False,
            digest="", digest_kind="", top_folder=_top_folder(rel_path),
            matched_terms=sorted(set(ev.name_strong) | set(ev.name_weak)),
            read_status="folder",
        )

    read_status = "unread"
    unread_reason = ""
    readable_label = "خیر"
    member_count = ""
    text = ""

    if sensitive:
        unread_reason = "حساس — عمداً خوانده نشد"
    elif suffix in GOOGLE_SHORTCUT_EXTENSIONS:
        unread_reason = "میانبر Google است و محتوای سند محلی نیست"
    elif suffix in AUDIO_EXTENSIONS:
        unread_reason = "صوت — ابزار شنیدن/رونویسی ندارد"
    elif suffix in VIDEO_EXTENSIONS:
        unread_reason = "ویدیو — ابزار مشاهده ندارد"
    elif suffix in IMAGE_EXTENSIONS:
        unread_reason = "تصویر — OCR انجام نمی‌شود"
    elif archive_kind(path) is not None:
        members, error = list_archive(path)
        if error:
            unread_reason = error
        else:
            read_status = "archive_listed"
            readable_label = "فقط فهرست اعضا؛ محتوای اعضا خوانده نشد"
            member_count = str(len(members))
            for member in members:
                member.archive = rel_path
                archive_rows.append(member)
            member_text = normalise(" ".join(m.member for m in members))
            ev.archive_strong = find_terms(member_text, kw.strong)
            ev.archive_weak = find_terms(member_text, kw.weak)
    elif suffix == ".pdf":
        text, status, error = extract_pdf_text(
            path, opts.max_content_chars, opts.pdf_pages
        )
        if status == "unread":
            unread_reason = error
        else:
            read_status = status
            readable_label = "بله (استخراج متن PDF)"
    elif suffix in DOCUMENT_EXTENSIONS:
        text, status = extract_office_text(path, opts.max_content_chars)
        if status == "unread" or not text:
            unread_reason = "سند آفیس/کتاب الکترونیکی بدون متن قابل استخراج"
        else:
            read_status = status
            readable_label = "بله (استخراج متن سند)"
    elif suffix in TEXT_EXTENSIONS or _sniff_text(path):
        try:
            text, status = read_plain_text(path, opts.max_content_chars)
            read_status = status
            readable_label = "بله" if status == "full" else "بله، ناقص (رسیدن به سقف خواندن)"
        except OSError as exc:
            unread_reason = f"خطای خواندن فایل: {type(exc).__name__}"
    else:
        unread_reason = f"باینری/فرمت پشتیبانی‌نشده ({suffix or 'بدون پسوند'})"

    if text and read_status in ("full", "partial"):
        normalised_text = normalise(text)
        ev.content_strong = find_terms(normalised_text, kw.strong)
        ev.content_weak = find_terms(normalised_text, kw.weak)
    # Never keep extracted text beyond this function: outputs contain only
    # keyword names and counts, never snippets.
    del text

    digest = ""
    digest_kind = ""
    if opts.use_hash and not sensitive:
        if opts.hash_max_bytes and size_int > opts.hash_max_bytes:
            unread_reason = unread_reason or ""
        else:
            try:
                cached = cache.get(rel_path, size_int, mtime_ns)
                digest = cached or sha256_file(path)
                if not cached:
                    cache.put(rel_path, size_int, mtime_ns, digest)
                digest_kind = "sha256"
            except OSError as exc:
                if read_status == "unread" and not unread_reason:
                    unread_reason = f"خطای خواندن برای هش: {type(exc).__name__}"

    section, confidence, reason = classify(
        item_type="file", sensitive=sensitive, read_status=read_status,
        unread_reason=unread_reason, ev=ev,
    )
    if read_status == "partial":
        reason += "؛ متن تا سقف مجاز خوانده شد و ادامه‌اش خوانده نشد"
    if sensitive:
        readable_label = "خیر — حساس، عمداً خوانده نشد"
    elif read_status == "unread":
        readable_label = f"خیر — {unread_reason}" if unread_reason else "خیر"

    matched = sorted(
        set(ev.name_strong) | set(ev.name_weak) | set(ev.content_strong)
        | set(ev.content_weak) | set(ev.archive_strong) | set(ev.archive_weak)
    )
    return Row(
        name=name, item_id=local_id(rel_path), path=rel_path,
        type_label=mime, item_type="file", size=str(size_int),
        size_int=size_int, modified=modified, shared="نامشخص",
        readable=readable_label, section=section, confidence=confidence,
        reason=reason, sensitive=sensitive, digest=digest,
        digest_kind=digest_kind, archive_members=member_count,
        matched_terms=matched, top_folder=_top_folder(rel_path),
        extension=suffix, read_status=read_status,
        unread_reason=unread_reason,
    )


def _sniff_text(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return looks_like_text(fh.read(8192))
    except OSError:
        return False


def _top_folder(rel_path: str) -> str:
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
    if len(parts) <= 1:
        return "(ریشه)"
    return parts[0]


def scan_local_tree(
    root: Path, kw: KeywordSet, opts: ScanOptions, cache: HashCache,
    *, include_folders: bool, offset: int, limit: int,
    batch_size: int, progress: bool,
) -> tuple[list[Row], list[ArchiveMember], dict[str, Any]]:
    entries: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        current = Path(dirpath)
        if include_folders:
            for dirname in list(dirnames):
                entries.append(current / dirname)
        for filename in filenames:
            entries.append(current / filename)

    def sort_key(p: Path) -> str:
        return p.relative_to(root).as_posix()

    entries.sort(key=sort_key)
    total_seen = len(entries)
    selected = entries[offset:]
    if limit > 0:
        selected = selected[:limit]

    rows: list[Row] = []
    archive_rows: list[ArchiveMember] = []
    for index, entry in enumerate(selected, start=1):
        rel = entry.relative_to(root).as_posix()
        rows.append(analyze_local_file(entry, rel, kw, opts, cache, archive_rows))
        if progress and batch_size > 0 and index % batch_size == 0:
            print(f"[progress] processed {offset + index}/{total_seen}", file=sys.stderr)
    meta = {
        "mode": "local",
        "source": str(root),
        "total_seen": total_seen,
        "processed_offset": offset,
        "processed_count": len(rows),
        "processed_end": offset + len(rows),
        "partial": (offset + len(rows)) < total_seen,
    }
    return rows, archive_rows, meta


# --------------------------------------------------------------------------- #
# Drive metadata-inventory mode (no file bytes available)
# --------------------------------------------------------------------------- #

def _extract_file_lists(obj: Any, sink: list[dict[str, Any]]) -> None:
    """Recursively collect Drive ``files`` lists, including connector
    responses where the JSON payload is nested inside a text field."""
    if isinstance(obj, dict):
        files = obj.get("files")
        if isinstance(files, list) and files and all(isinstance(f, dict) for f in files):
            sink.extend(files)
            return
        if "Path" in obj and ("Name" in obj or "IsDir" in obj):
            sink.append(obj)
            return
        if "id" in obj and ("name" in obj or "mimeType" in obj or "mime_type" in obj):
            sink.append(obj)
            return
        for value in obj.values():
            _extract_file_lists(value, sink)
    elif isinstance(obj, list):
        if obj and all(
            isinstance(v, dict) and ("id" in v or "Path" in v) for v in obj
        ):
            sink.extend(v for v in obj if isinstance(v, dict))
            return
        for value in obj:
            _extract_file_lists(value, sink)
    elif isinstance(obj, str):
        stripped = obj.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                _extract_file_lists(json.loads(stripped), sink)
            except Exception:
                pass


def _first(item: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return default


def load_drive_inventory(path: Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig") as fh:
        raw = json.load(fh)
    items: list[dict[str, Any]] = []
    _extract_file_lists(raw, items)
    deduped: dict[str, dict[str, Any]] = {}
    unnamed: list[dict[str, Any]] = []
    for item in items:
        item_id = str(
            _first(item, "id", "ID", "Path", default="") or ""
        )
        if item_id:
            deduped[item_id] = item
        else:
            unnamed.append(item)
    return list(deduped.values()) + unnamed


def analyze_drive_inventory(
    items: list[dict[str, Any]], kw: KeywordSet, *, include_folders: bool,
    offset: int, limit: int,
) -> tuple[list[Row], dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = str(_first(item, "id", default="") or "")
        if item_id:
            by_id[item_id] = item

    path_cache: dict[str, str] = {}

    def resolve_path(item: dict[str, Any], seen: Optional[set[str]] = None) -> str:
        item_id = str(_first(item, "id", default="") or "")
        if item_id and item_id in path_cache:
            return path_cache[item_id]
        seen = set() if seen is None else seen
        name = str(_first(item, "name", default="") or "")
        parents = _first(item, "parents", default=[]) or []
        if not isinstance(parents, list) or not parents:
            result = name
        else:
            parent_id = str(parents[0])
            parent = by_id.get(parent_id)
            if parent is None or parent_id in seen:
                result = name
            else:
                seen.add(parent_id)
                parent_path = resolve_path(parent, seen)
                result = f"{parent_path}/{name}" if parent_path else name
        if item_id:
            path_cache[item_id] = result
        return result

    normalised: list[tuple[dict[str, Any], str, str]] = []
    for item in items:
        rclone_path = _first(item, "Path", default=None)
        if rclone_path is not None:
            item_type = "folder" if bool(item.get("IsDir")) else "file"
            normalised.append((item, str(rclone_path).strip("/"), item_type))
            continue
        mime = str(_first(item, "mimeType", "mime_type", default="") or "")
        item_type = "folder" if mime == FOLDER_MIME else "file"
        normalised.append((item, resolve_path(item), item_type))
    if not include_folders:
        normalised = [x for x in normalised if x[2] == "file"]
    normalised.sort(key=lambda x: x[1])
    total_seen = len(normalised)
    selected = normalised[offset:]
    if limit > 0:
        selected = selected[:limit]

    rows: list[Row] = []
    for item, path_str, item_type in selected:
        name = str(_first(item, "name", "Name", default="") or "")
        if not name:
            name = Path(path_str).name
        sensitive = is_sensitive(name, path_str, kw)
        joined = normalise(name + " " + path_str)
        ev = Evidence(
            name_strong=find_terms(joined, kw.strong),
            name_weak=find_terms(joined, kw.weak),
        )
        shared_raw = _first(item, "shared", default=None)
        shared = "نامشخص" if shared_raw is None else ("بله" if shared_raw else "خیر")
        size_raw = _first(item, "size", "Size", default="")
        try:
            size_int = int(size_raw) if str(size_raw).strip() else 0
        except (TypeError, ValueError):
            size_int = 0
        read_status = "folder" if item_type == "folder" else "unread"
        unread_reason = (
            "" if item_type == "folder"
            else "حالت فهرست متادیتا — بایت فایل در دسترس نیست"
        )
        section, confidence, reason = classify(
            item_type=item_type, sensitive=sensitive,
            read_status=read_status, unread_reason=unread_reason, ev=ev,
        )
        md5 = str(_first(item, "md5Checksum", "md5_checksum", default="") or "")
        rclone_hashes = item.get("Hashes")
        if not md5 and isinstance(rclone_hashes, dict):
            md5 = str(rclone_hashes.get("md5") or "")
        if sensitive:
            md5 = ""
        rows.append(Row(
            name=name,
            item_id=str(_first(item, "id", "ID", default="") or ""),
            path=path_str,
            type_label=str(
                FOLDER_MIME if item.get("IsDir") else
                _first(item, "mimeType", "mime_type", "MimeType", default="") or ""
            ),
            item_type=item_type,
            size="" if item_type == "folder" else str(size_raw or ""),
            size_int=size_int,
            modified=str(
                _first(
                    item, "modifiedTime", "modified_time", "ModTime",
                    default="",
                ) or ""
            ),
            shared=shared,
            readable=(
                "پوشه — محتوای متنی ندارد" if item_type == "folder"
                else (
                    "خیر — حساس، عمداً خوانده نشد" if sensitive
                    else f"خیر — {unread_reason}"
                )
            ),
            section=section,
            confidence=confidence,
            reason=reason,
            sensitive=sensitive,
            digest=md5,
            digest_kind="md5" if md5 else "",
            trashed=bool(_first(item, "trashed", default=False)),
            matched_terms=sorted(set(ev.name_strong) | set(ev.name_weak)),
            top_folder=_top_folder(path_str),
            extension=Path(name).suffix.lower(),
            read_status=read_status,
            unread_reason=unread_reason,
        ))
    meta = {
        "mode": "drive-inventory-json",
        "source": "",
        "total_seen": total_seen,
        "processed_offset": offset,
        "processed_count": len(rows),
        "processed_end": offset + len(rows),
        "partial": (offset + len(rows)) < total_seen,
    }
    return rows, meta


# --------------------------------------------------------------------------- #
# Duplicate candidates, empty folders, summaries
# --------------------------------------------------------------------------- #

def assign_duplicate_groups(rows: list[Row]) -> list[list[Row]]:
    groups: dict[tuple[str, str, int], list[Row]] = defaultdict(list)
    for row in rows:
        if (
            row.item_type == "file" and row.digest and not row.sensitive
        ):
            groups[(row.digest_kind, row.digest, row.size_int)].append(row)
    duplicate_groups: list[list[Row]] = []
    counter = 0
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        counter += 1
        label = f"D{counter:03d}"
        for member in members:
            member.duplicate_group = label
        duplicate_groups.append(sorted(members, key=lambda r: r.path))
    return duplicate_groups


def empty_folders(rows: list[Row]) -> list[Row]:
    child_counts: Counter[str] = Counter()
    for row in rows:
        parent = str(Path(row.path).parent.as_posix())
        if parent not in (".", ""):
            child_counts[parent] += 1
    result = [
        row for row in rows
        if row.item_type == "folder" and child_counts.get(row.path, 0) == 0
    ]
    return sorted(result, key=lambda r: r.path)


def write_csv(path: Path, headers: list[str], records: Iterable[list[str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for record in records:
            writer.writerow(record)


def build_summary(
    rows: list[Row],
    archive_rows: list[ArchiveMember],
    duplicate_groups: list[list[Row]],
    meta: dict[str, Any],
    expected_files: Optional[int],
    expected_folders: Optional[int],
) -> str:
    live = [r for r in rows if not r.trashed]
    trashed = [r for r in rows if r.trashed]
    files = [r for r in live if r.item_type == "file"]
    folders = [r for r in live if r.item_type == "folder"]
    lines: list[str] = []
    lines.append("# گزارش خلاصهٔ تحلیل FarsiVoiceAI")
    lines.append("")
    lines.append(f"- زمان تولید (UTC): {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- حالت منبع: {meta.get('mode')}")
    lines.append(f"- منبع: `{meta.get('source') or '(فایل فهرست JSON)'}`")
    lines.append(
        f"- شمارنده پردازش: {meta.get('processed_offset')} تا "
        f"{meta.get('processed_end')} از {meta.get('total_seen')}"
        + (" — **ناقص (بازه محدود شده است)**" if meta.get("partial") else "")
    )
    lines.append("")
    lines.append("## شمارش و تطابق")
    lines.append("")
    lines.append("| مورد | تعداد |")
    lines.append("|---|---:|")
    lines.append(f"| فایل زنده | {len(files)} |")
    lines.append(f"| پوشه زنده | {len(folders)} |")
    lines.append(f"| جمع زنده | {len(live)} |")
    lines.append(
        f"| سطل زباله فایل | {sum(1 for r in trashed if r.item_type == 'file')} |"
    )
    lines.append(
        f"| سطل زباله پوشه | {sum(1 for r in trashed if r.item_type == 'folder')} |"
    )
    lines.append(f"| کل ردیف‌ها | {len(rows)} |")
    if meta.get("mode") == "local":
        lines.append("| سطل زباله درایو در کپی محلی | نامشخص — از فایل‌سیستم محلی قابل مشاهده نیست |")
    if expected_files is not None:
        verdict = "✓ برابر" if expected_files == len(files) else "✗ نابرابر"
        lines.append(f"| انتظار فایل (ورودی کاربر) | {expected_files} — {verdict} |")
    if expected_folders is not None:
        verdict = "✓ برابر" if expected_folders == len(folders) else "✗ نابرابر"
        lines.append(f"| انتظار پوشه (ورودی کاربر) | {expected_folders} — {verdict} |")
    section_total = sum(Counter(r.section for r in files).values())
    lines.append("")
    lines.append(f"جمع بخش‌های فایل‌ها: {section_total} "
                 f"({'✓ با تعداد فایل‌ها برابر است' if section_total == len(files) else '✗ برابر نیست'})")

    for title, subset in (("بخش‌بندی فایل‌ها", files), ("بخش‌بندی پوشه‌ها", folders)):
        lines.append("")
        lines.append(f"## {title}")
        counts = Counter(r.section for r in subset)
        for section in (SECTION_DEFINITE, SECTION_PROBABLE, SECTION_UNRELATED, SECTION_UNKNOWN):
            lines.append(f"- {section}: {counts.get(section, 0)}")
        confidence = Counter(r.confidence for r in subset)
        lines.append(
            "- اطمینان — بالا: "
            f"{confidence.get('بالا', 0)}؛ متوسط: {confidence.get('متوسط', 0)}؛ "
            f"پایین: {confidence.get('پایین', 0)}"
        )

    lines.append("")
    lines.append("## خوانایی فایل‌ها")
    status_counts = Counter(r.read_status for r in files)
    for key in ("full", "partial", "archive_listed", "unread"):
        if status_counts.get(key):
            lines.append(f"- {key}: {status_counts[key]}")
    reasons = Counter(r.unread_reason for r in files if r.read_status == "unread")
    if reasons:
        lines.append("")
        lines.append("دلایل خوانده‌نشدن:")
        for reason, count in reasons.most_common():
            lines.append(f"- {reason or 'نامشخص'}: {count}")

    extension_counts = Counter((r.extension or "(بدون پسوند)") for r in files)
    if extension_counts:
        lines.append("")
        lines.append("## پسوند فایل‌ها")
        for ext, count in extension_counts.most_common(30):
            lines.append(f"- `{ext}`: {count}")

    top_counts = Counter(r.top_folder for r in files)
    if top_counts:
        lines.append("")
        lines.append("## فایل‌ها بر اساس پوشه سطح اول")
        for name, count in sorted(top_counts.items()):
            lines.append(f"- {name}: {count}")

    term_counts: Counter[str] = Counter()
    term_examples: dict[str, list[str]] = defaultdict(list)
    for row in files:
        for term in row.matched_terms:
            term_counts[term] += 1
            if len(term_examples[term]) < 3:
                term_examples[term].append(row.name)
    if term_counts:
        lines.append("")
        lines.append("## سرنخ‌های موضوعی (فقط نام کلیدواژه، بدون بازگوکردن محتوا)")
        for term, count in term_counts.most_common(30):
            examples = "، ".join(term_examples[term])
            lines.append(f"- `{term}`: {count} فایل — مثال نام فایل: {examples}")

    folders_empty = empty_folders(live)
    lines.append("")
    lines.append(f"## پوشه‌های خالی ({len(folders_empty)})")
    for row in folders_empty:
        lines.append(f"- {row.path}")

    big = sorted((r for r in files if r.size_int > 100 * 1000 * 1000),
                 key=lambda r: -r.size_int)
    lines.append("")
    lines.append(f"## فایل‌های بزرگ‌تر از ۱۰۰ مگابایت ({len(big)})")
    for row in big:
        lines.append(f"- {row.path} — {row.size} بایت — خوانایی: {row.readable}")

    sensitive_rows = [r for r in live if r.sensitive]
    lines.append("")
    lines.append(f"## حساس‌ها ({len(sensitive_rows)})")
    lines.append("محتوای این موارد خوانده و بازگو نشده است؛ مقصد پیشنهادی در طرح نهایی فقط «۰۸_نیازمند_بازبینی» است.")
    for row in sensitive_rows:
        lines.append(f"- حساس: {row.path}")

    lines.append("")
    lines.append(f"## کاندیدهای تکراری ({len(duplicate_groups)} گروه)")
    lines.append("فقط گروه‌هایی با هش و حجم یکسان گزارش شده‌اند؛ هیچ حذفی پیشنهاد یا انجام نمی‌شود.")
    for group in duplicate_groups:
        first = group[0]
        lines.append(
            f"- {first.duplicate_group}: {len(group)} فایل؛ حجم {first.size}؛ "
            + "؛ ".join(r.path for r in group)
        )

    lines.append("")
    lines.append(f"## آرشیوها")
    lines.append(f"- ردیف‌های فهرست داخلی آرشیو: {len(archive_rows)}")
    lines.append("- آرشیو حساس فهرست‌برداری نشده است.")
    lines.append("")
    lines.append("## محدودیت‌های این اجرا")
    lines.append("- این ابزار به Google Drive وصل نمی‌شود؛ روی کپی محلی یا فهرست JSON کار می‌کند.")
    lines.append("- در حالت محلی، اشتراکی‌بودن و مالکیت درایو «نامشخص» است.")
    lines.append("- در حالت JSON، بایت فایل‌ها در دسترس نیست؛ پس همه فایل‌ها از نظر محتوا «خوانده نشد» هستند.")
    lines.append("- صوت، ویدیو و تصویر تحلیل محتوایی نمی‌شوند.")
    lines.append("- محتوای اعضای داخل آرشیو استخراج نمی‌شود؛ فقط فهرست اعضا خوانده می‌شود.")
    lines.append("")
    lines.append("وضعیت: " + ("ناقص (بازه پردازش محدود بود)" if meta.get("partial") else "انجام شد"))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "ابزار فقط‌خواندنی تحلیل کپی محلی Google Drive یا فهرست JSON "
            "برای پروژه FarsiVoiceAI"
        )
    )
    parser.add_argument("source", help="پوشه محلی یا فایل JSON فهرست درایو")
    parser.add_argument("--out", required=True, help="پوشه خروجی (بیرون از پوشه منبع)")
    parser.add_argument("--config", default=None, help="مسیر keywords.json")
    parser.add_argument(
        "--files-only", action="store_true",
        help="در خروجی اصلی فقط فایل‌ها باشند، نه پوشه‌ها",
    )
    parser.add_argument("--offset", type=int, default=0,
                        help="ایندکس شروع پردازش در فهرست مرتب‌شده")
    parser.add_argument("--limit", type=int, default=0,
                        help="حداکثر تعداد آیتم این اجرا (۰ = همه)")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="هر چند آیتم یک پیام پیشرفت چاپ شود")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--no-hash", action="store_true",
                        help="محاسبه SHA-256 محلی انجام نشود")
    parser.add_argument("--hash-max-mb", type=float, default=0,
                        help="سقف حجم برای هش به مگابایت (۰ = نامحدود)")
    parser.add_argument("--max-content-chars", type=int, default=2_000_000,
                        help="سقف نویسه متن استخراج‌شده برای هر فایل")
    parser.add_argument("--pdf-pages", type=int, default=20,
                        help="سقف صفحات PDF (۰ = همه)")
    parser.add_argument("--expected-files", type=int, default=None)
    parser.add_argument("--expected-folders", type=int, default=None)
    parser.add_argument("--allow-output-inside-source", action="store_true",
                        help="اجازه خروجی داخل پوشه منبع (پیش‌فرض برای ایمنی ممنوع است)")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    started = time.time()
    source = Path(args.source).expanduser()
    out_dir = Path(args.out).expanduser()
    if not source.exists():
        print(f"[error] منبع پیدا نشد: {source}", file=sys.stderr)
        return 2
    if args.offset < 0 or args.limit < 0:
        print("[error] --offset و --limit نمی‌توانند منفی باشند", file=sys.stderr)
        return 2

    config_path = Path(args.config) if args.config else SCRIPT_DIR / "keywords.json"
    kw = KeywordSet.load(config_path if config_path.is_file() else None)

    include_folders = not args.files_only
    archive_rows: list[ArchiveMember] = []
    cache: Optional[HashCache] = None

    if source.is_dir():
        source_resolved = source.resolve()
        out_resolved = out_dir.resolve()
        inside = out_resolved == source_resolved or source_resolved in out_resolved.parents
        if inside and not args.allow_output_inside_source:
            print(
                "[error] پوشه خروجی داخل پوشه منبع است و ممکن است با Drive همگام شود؛ "
                "خروجی بیرونی انتخاب کنید یا --allow-output-inside-source بدهید.",
                file=sys.stderr,
            )
            return 2
        out_dir.mkdir(parents=True, exist_ok=True)
        cache = HashCache(out_dir / ".fva_hash_cache.json")
        opts = ScanOptions(
            max_content_chars=args.max_content_chars,
            pdf_pages=args.pdf_pages,
            use_hash=not args.no_hash,
            hash_max_bytes=int(args.hash_max_mb * 1000 * 1000),
        )
        rows, archive_rows, meta = scan_local_tree(
            source, kw, opts, cache,
            include_folders=include_folders,
            offset=args.offset,
            limit=args.limit,
            batch_size=args.batch_size,
            progress=not args.no_progress,
        )
    elif source.is_file() and source.suffix.lower() == ".json":
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            items = load_drive_inventory(source)
        except Exception as exc:
            print(f"[error] JSON خوانده نشد: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        rows, meta = analyze_drive_inventory(
            items, kw, include_folders=include_folders,
            offset=args.offset, limit=args.limit,
        )
        meta["source"] = str(source)
    else:
        print("[error] منبع باید پوشه یا فایل .json باشد", file=sys.stderr)
        return 2

    duplicate_groups = assign_duplicate_groups(rows)

    relevance_path = out_dir / "FVA_relevance_map_2026-10-23.csv"
    write_csv(relevance_path, HEADERS, (row.to_csv() for row in rows))

    write_csv(
        out_dir / "archive_contents.csv",
        ["آرشیو", "عضو", "حجم عضو", "حجم فشرده عضو", "نوع", "یادداشت"],
        (
            [m.archive, m.member, m.size, m.compressed_size, m.kind, m.note]
            for m in archive_rows
        ),
    )
    write_csv(
        out_dir / "duplicate_candidates.csv",
        ["گروه", "نوع هش", "هش", "حجم", "نام", "مسیر فعلی", "شناسه"],
        (
            [row.duplicate_group, row.digest_kind, row.digest, row.size,
             row.name, row.path, row.item_id]
            for group in duplicate_groups for row in group
        ),
    )
    unread = [
        row for row in rows
        if row.item_type == "file" and (row.read_status == "unread" or row.sensitive)
    ]
    write_csv(
        out_dir / "unread_and_sensitive.csv",
        HEADERS,
        (row.to_csv() for row in unread),
    )
    write_csv(
        out_dir / "empty_folders.csv",
        HEADERS,
        (row.to_csv() for row in empty_folders([r for r in rows if not r.trashed])),
    )

    summary = build_summary(
        rows, archive_rows, duplicate_groups, meta,
        args.expected_files, args.expected_folders,
    )
    with open(out_dir / "summary.md", "w", encoding="utf-8") as fh:
        fh.write(summary)

    state = dict(meta)
    state.update({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - started, 3),
        "files_live": sum(1 for r in rows if r.item_type == "file" and not r.trashed),
        "folders_live": sum(1 for r in rows if r.item_type == "folder" and not r.trashed),
        "trashed_items": sum(1 for r in rows if r.trashed),
        "rows_written": len(rows),
        "outputs": [
            "FVA_relevance_map_2026-10-23.csv",
            "archive_contents.csv",
            "duplicate_candidates.csv",
            "unread_and_sensitive.csv",
            "empty_folders.csv",
            "summary.md",
            "state.json",
        ],
    })
    with open(out_dir / "state.json", "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)

    if cache is not None:
        cache.save()

    print(
        f"[done] rows={len(rows)} files_live={state['files_live']} "
        f"folders_live={state['folders_live']} output={out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
