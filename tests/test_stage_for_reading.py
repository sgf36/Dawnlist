"""Staging a CV for the reader must survive a refusal to copy file metadata.

On a TestFlight Mac build a PDF failed with PermissionError and a .docx with
PackageNotFoundError. Both are what the worker thread raises when it is handed
the ORIGINAL path instead of a staged copy; the original is read outside the
sandbox's drag-and-drop grant. The staged copy had been made and then
discarded because `shutil.copy2` raised while copying extended attributes.
"""
import shutil

import pytest

pytest.importorskip("PySide6")

from app.ui import onboarding  # noqa: E402


def _cv(tmp_path, name="CV - Sept 2026.pdf", data=b"%PDF-1.4 hello"):
    f = tmp_path / name
    f.write_bytes(data)
    return f


def test_staged_copy_is_a_different_file_with_the_same_bytes(tmp_path):
    src = _cv(tmp_path)
    staged, staging = onboarding._stage_for_reading([src])
    try:
        assert staged[0] != src
        assert staged[0].read_bytes() == src.read_bytes()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_a_metadata_refusal_does_not_discard_the_copy(tmp_path, monkeypatch):
    """The sandbox refuses extended attributes; content must still be staged."""
    def refuse(*_a, **_k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(shutil, "copystat", refuse)
    monkeypatch.setattr(shutil, "copy2", lambda s, d, **k: (shutil.copyfile(s, d), refuse()))
    src = _cv(tmp_path)
    staged, staging = onboarding._stage_for_reading([src])
    try:
        assert staged[0] != src, "fell back to the original path"
        assert staged[0].read_bytes() == src.read_bytes()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_falls_back_to_reading_bytes_when_copyfile_is_refused(tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(shutil, "copyfile", refuse)
    src = _cv(tmp_path, "CV.docx", b"PK\x03\x04docx")
    staged, staging = onboarding._stage_for_reading([src])
    try:
        assert staged[0] != src
        assert staged[0].read_bytes() == b"PK\x03\x04docx"
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_an_unreadable_file_keeps_its_original_path(tmp_path):
    missing = tmp_path / "gone.pdf"
    staged, staging = onboarding._stage_for_reading([missing])
    try:
        assert staged == [missing]
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_two_files_with_one_name_are_both_kept(tmp_path):
    a = _cv(tmp_path / ".." / "a" if False else tmp_path, "CV.pdf", b"one")
    sub = tmp_path / "sub"
    sub.mkdir()
    b = sub / "CV.pdf"
    b.write_bytes(b"two")
    staged, staging = onboarding._stage_for_reading([a, b])
    try:
        assert {p.read_bytes() for p in staged} == {b"one", b"two"}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
