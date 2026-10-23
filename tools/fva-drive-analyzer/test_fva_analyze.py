#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic end-to-end tests for fva_analyze.py (stdlib unittest only)."""

import codecs
import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "fva_analyze.py"
SECRET = "UNIT_TEST_FAKE_SECRET_VALUE_123456"
LINKED_SECRET = "UNIT_TEST_LINKED_SECRET_VALUE_987654"


def load_module():
    spec = importlib.util.spec_from_file_location("fva_analyze", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load analyzer module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_tool(source: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), "--out", str(out), *extra],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class LocalScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "drive"
        out = Path(cls.tmp.name) / "out"
        cls.root, cls.out = root, out

        (root / "02_FarsiVoiceAI").mkdir(parents=True)
        (root / "misc").mkdir()
        (root / "empty_folder").mkdir()

        (root / "02_FarsiVoiceAI" / "note.md").write_text(
            "FarsiVoiceAI برای کتاب پاسخ به تاریخ؛ تلفظ و مکث با Edge TTS بررسی شد.",
            encoding="utf-8",
        )
        (root / "misc" / "cooking.txt").write_text(
            "A recipe for soup with rice and herbs.", encoding="utf-8"
        )
        long_text = ("x " * 120000) + " FarsiVoiceAI OCR تلفظ"
        (root / "misc" / "long.txt").write_text(long_text, encoding="utf-8")

        # Byte-identical duplicate pair (same content in two places).
        (root / "misc" / "a.bin").write_bytes(b"same-bytes-for-dup-test" * 10)
        (root / "02_FarsiVoiceAI" / "b.bin").write_bytes(b"same-bytes-for-dup-test" * 10)

        # Same name, different bytes: must NOT be reported as duplicates.
        (root / "misc" / "same-name.zip").write_bytes(b"not really a zip A")
        (root / "02_FarsiVoiceAI" / "same-name.zip").write_bytes(b"not really a zip B")

        # Archive whose member names are project-related (contents unread).
        with zipfile.ZipFile(root / "02_FarsiVoiceAI" / "code.zip", "w") as zf:
            zf.writestr("fva.py", "print('x')\n")
            zf.writestr("backend/ocr.py", "# code\n")
        # A nested archive member is listed but not opened.
        nested = Path(cls.tmp.name) / "inner.zip"
        with zipfile.ZipFile(nested, "w") as zf:
            zf.writestr("hidden.txt", "hidden")
        with zipfile.ZipFile(root / "misc" / "outer.zip", "w") as zf:
            zf.write(nested, arcname="inner.zip")

        # Sensitive: content must never appear anywhere in the outputs and
        # a takeout-named archive must not even be listed.
        (root / "misc" / "settings.cfg").write_text(SECRET, encoding="utf-8")
        with zipfile.ZipFile(root / "misc" / "takeout-fake.zip", "w") as zf:
            zf.writestr("Mail/personal.txt", "private body")

        (root / "02_FarsiVoiceAI" / "golden_test.mp3").write_bytes(b"ID3 fake audio")
        (root / "misc" / "mystery.rar").write_bytes(b"Rar! fake")
        # A bare ``fva`` token is only a weak clue, not decisive evidence.
        (root / "misc" / "fva_notes.txt").write_text(
            "ordinary shopping notes", encoding="utf-8"
        )

        linked_target = Path(cls.tmp.name) / "outside-secret.txt"
        linked_target.write_text(LINKED_SECRET, encoding="utf-8")
        cls.has_symlink = hasattr(os, "symlink")
        if cls.has_symlink:
            os.symlink(linked_target, root / "misc" / "innocent.txt")

        # A synthetic .docx (zip + word/document.xml).
        with zipfile.ZipFile(root / "misc" / "chapter.docx", "w") as zf:
            zf.writestr(
                "word/document.xml",
                "<w:document xmlns:w='urn:x'><w:body><w:p><w:r>"
                "<w:t>پاسخ به تاریخ FarsiVoiceAI OCR</w:t>"
                "</w:r></w:p></w:body></w:document>",
            )

        result = run_tool(root, out)
        assert result.returncode == 0, result.stdout + result.stderr
        cls.rows = read_rows(out / "FVA_relevance_map_2026-10-23.csv")
        cls.by_name = {r["نام"]: r for r in cls.rows}
        cls.archives = read_rows(out / "archive_contents.csv")
        cls.dups = read_rows(out / "duplicate_candidates.csv")
        cls.summary = (out / "summary.md").read_text(encoding="utf-8")
        cls.state = json.loads((out / "state.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_content_related_is_definite_high_confidence(self) -> None:
        row = self.by_name["note.md"]
        self.assertTrue(row["بخش"].startswith("الف"))
        self.assertEqual(row["اطمینان"], "بالا")

    def test_unrelated_read_text(self) -> None:
        row = self.by_name["cooking.txt"]
        self.assertTrue(row["بخش"].startswith("ج"))
        self.assertEqual(row["اطمینان"], "بالا")

    def test_long_text_keyword_at_end_is_found_by_chunked_reading(self) -> None:
        row = self.by_name["long.txt"]
        self.assertFalse(row["بخش"].startswith("ج"))

    def test_docx_extracted(self) -> None:
        row = self.by_name["chapter.docx"]
        self.assertIn("بله", row["قابل‌خواندن؟"])
        self.assertFalse(row["بخش"].startswith("ج"))

    def test_unread_is_never_declared_unrelated(self) -> None:
        row = self.by_name["mystery.rar"]
        self.assertTrue(row["بخش"].startswith("د"))
        audio = self.by_name["golden_test.mp3"]
        self.assertIn("صوت", audio["قابل‌خواندن؟"] + audio["دلیل (با شاهد)"])

    def test_archive_listing_and_nested_archive(self) -> None:
        members = {r["عضو"] for r in self.archives if r["آرشیو"].endswith("code.zip")}
        self.assertIn("fva.py", members)
        nested = [r for r in self.archives if r["عضو"] == "inner.zip"]
        self.assertEqual(len(nested), 1)
        self.assertIn("تو در تو", nested[0]["یادداشت"])
        self.assertNotIn("hidden.txt", {r["عضو"] for r in self.archives})

    def test_sensitive_never_opened_or_leaked(self) -> None:
        row = self.by_name["settings.cfg"]
        self.assertEqual(row["حساس؟"], "بله")
        self.assertTrue(row["بخش"].startswith("د"))
        for path in self.out.iterdir():
            if path.is_file():
                self.assertNotIn(SECRET, path.read_text(encoding="utf-8", errors="ignore"))
        self.assertNotIn(
            "takeout-fake.zip", {Path(r["آرشیو"]).name for r in self.archives}
        )

    def test_bare_fva_is_not_strong_evidence(self) -> None:
        row = self.by_name["fva_notes.txt"]
        self.assertTrue(row["بخش"].startswith("د"))
        self.assertEqual(row["اطمینان"], "پایین")

    def test_symlink_is_never_followed_or_hashed(self) -> None:
        if not self.has_symlink:
            self.skipTest("symlinks unavailable")
        row = self.by_name["innocent.txt"]
        self.assertEqual(row["نوع"], "symlink")
        self.assertEqual(row["هش (SHA-256/MD5)"], "")
        self.assertIn("پیوند نمادین", row["قابل‌خواندن؟"] + row["دلیل (با شاهد)"])
        for path in self.out.iterdir():
            if path.is_file():
                self.assertNotIn(
                    LINKED_SECRET,
                    path.read_text(encoding="utf-8", errors="ignore"),
                )

    def test_duplicates_only_by_hash(self) -> None:
        paths = {Path(r["مسیر فعلی"]).name for r in self.dups}
        self.assertEqual(paths, {"a.bin", "b.bin"})

    def test_empty_folder_and_reconciliation(self) -> None:
        empty = read_rows(self.out / "empty_folders.csv")
        self.assertIn("empty_folder", {r["نام"] for r in empty})
        files = [r for r in self.rows if r["نوع"] != "application/vnd.google-apps.folder"]
        self.assertEqual(self.state["files_live"], len(files))
        self.assertIn("با تعداد فایل‌ها برابر است", self.summary)

    def test_source_tree_was_not_modified(self) -> None:
        # The analyzer must not create anything inside the source tree.
        generated = {"summary.md", "state.json", "FVA_relevance_map_2026-10-23.csv"}
        for path in self.root.rglob("*"):
            self.assertNotIn(path.name, generated)


class SafetyAndDecodingTest(unittest.TestCase):
    def test_utf16_requires_bom_and_cp1256_fallback(self) -> None:
        module = load_module()
        bom_text, bom_eof = module.extract_text_from_stream(
            iter([codecs.BOM_UTF16_LE + "سلام".encode("utf-16-le")]), 100
        )
        self.assertEqual(bom_text, "سلام")
        self.assertTrue(bom_eof)

        no_bom, _ = module.extract_text_from_stream(
            iter([b"a\x00b\x00"]), 100
        )
        self.assertIn("\x00", no_bom)
        self.assertNotEqual(no_bom, "ab")

        # Windows-1256 stores Arabic yeh; normalise() maps it to Farsi yeh.
        persian = "پاسخ به تاريخ و تلفظ فارسي براي كتاب صوتي"
        cp_text, _ = module.extract_text_from_stream(
            iter([persian.encode("cp1256")]), 1000
        )
        self.assertIn("پاسخ", cp_text)
        self.assertNotIn("\ufffd", cp_text)

    def test_oversize_office_member_is_skipped_before_read(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            docx = Path(tmp) / "huge.docx"
            huge_xml = (
                "<w:document xmlns:w='urn:x'><w:body><w:p><w:r><w:t>"
                + ("x" * 1_100_000)
                + "</w:t></w:r></w:p></w:body></w:document>"
            )
            with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("word/document.xml", huge_xml)
            text, status = module.extract_office_text(docx, 100)
            self.assertEqual(text, "")
            self.assertEqual(status, "unread")

    def test_partial_run_skips_global_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "drive"
            root.mkdir()
            (root / "empty").mkdir()
            (root / "a.txt").write_text("same", encoding="utf-8")
            (root / "b.txt").write_text("same", encoding="utf-8")
            out = Path(tmp) / "partial-out"
            result = run_tool(root, out, "--limit", "1", "--no-progress")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((out / "duplicate_candidates.csv").exists())
            self.assertFalse((out / "empty_folders.csv").exists())
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("در اجرای ناقص محاسبه نشد", summary)
            state = json.loads((out / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["global_reports_skipped"])
            self.assertNotIn("duplicate_candidates.csv", state["outputs"])

    def test_report_symlink_is_not_followed(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "drive"
            out = base / "out"
            victim = base / "victim.txt"
            root.mkdir()
            out.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")
            victim.write_text("do-not-overwrite", encoding="utf-8")
            os.symlink(victim, out / "FVA_relevance_map_2026-10-23.csv")
            result = run_tool(root, out, "--no-progress")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(victim.read_text(encoding="utf-8"), "do-not-overwrite")


class DriveInventoryTest(unittest.TestCase):
    def test_connector_shaped_json(self) -> None:
        payload = {
            "files": [
                {"id": "root07", "name": "۰۷_بکاپ_و_آرشیو",
                 "mime_type": "application/vnd.google-apps.folder",
                 "parents": ["ROOT"], "trashed": False,
                 "shared": False, "owned_by_me": True},
                {"id": "f1", "name": "01.zip", "mime_type": "application/zip",
                 "parents": ["root07"], "size": "100", "trashed": False,
                 "shared": False, "owned_by_me": True,
                 "md5_checksum": "abc", "modified_time": "2026-10-22T00:00:00Z"},
                {"id": "f2", "name": "01-copy.zip", "mime_type": "application/zip",
                 "parents": ["root07"], "size": "100", "trashed": True,
                 "shared": True, "owned_by_me": True,
                 "md5_checksum": "abc", "modified_time": "2026-10-22T01:00:00Z"},
                {"id": "f3", "name": "FarsiVoiceAI_code.zip",
                 "mime_type": "application/zip",
                 "parents": ["root07"], "size": "200", "trashed": False,
                 "shared": False, "owned_by_me": True,
                 "md5_checksum": "def", "modified_time": "2026-10-22T02:00:00Z"},
            ]
        }
        nested = {"content": [{"type": "text", "text": json.dumps(payload)}]}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "inventory.json"
            out = Path(tmp) / "out"
            source.write_text(json.dumps(nested), encoding="utf-8")
            result = run_tool(source, out)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = read_rows(out / "FVA_relevance_map_2026-10-23.csv")
            by_name = {r["نام"]: r for r in rows}
            self.assertEqual(by_name["01.zip"]["مسیر فعلی"],
                             "۰۷_بکاپ_و_آرشیو/01.zip")
            self.assertEqual(by_name["01-copy.zip"]["سطل زباله؟"], "بله")
            self.assertEqual(by_name["01-copy.zip"]["اشتراکی؟"], "بله")
            # Inventory mode never claims high confidence: bytes unread.
            self.assertNotIn("بالا", {r["اطمینان"] for r in rows})
            dups = read_rows(out / "duplicate_candidates.csv")
            self.assertEqual({r["نام"] for r in dups}, {"01.zip", "01-copy.zip"})
            state = json.loads((out / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["trashed_items"], 1)

    def test_rclone_lsjson_shape(self) -> None:
        payload = [
            {"Path": "۰۲_FarsiVoiceAI", "Name": "۰۲_FarsiVoiceAI",
             "Size": -1, "MimeType": "inode/directory", "IsDir": True,
             "ModTime": "2026-10-22T00:00:00Z", "ID": "dir-id"},
            {"Path": "۰۲_FarsiVoiceAI/fva.py", "Name": "fva.py",
             "Size": 12, "MimeType": "text/x-python", "IsDir": False,
             "ModTime": "2026-10-22T01:00:00Z", "ID": "file-id",
             "Hashes": {"md5": "0123456789abcdef"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "rclone.json"
            out = Path(tmp) / "out"
            source.write_text(json.dumps(payload), encoding="utf-8")
            result = run_tool(source, out)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = read_rows(out / "FVA_relevance_map_2026-10-23.csv")
            by_name = {r["نام"]: r for r in rows}
            self.assertEqual(by_name["fva.py"]["شناسه"], "file-id")
            self.assertEqual(
                by_name["fva.py"]["مسیر فعلی"], "۰۲_FarsiVoiceAI/fva.py"
            )
            self.assertIn("md5:0123456789abcdef", by_name["fva.py"]["هش (SHA-256/MD5)"])

    def test_output_inside_source_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "drive"
            root.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")
            result = run_tool(root, root / "reports")
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
