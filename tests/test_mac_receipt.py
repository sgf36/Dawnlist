"""The Mac App Store receipt — the only entitlement route Apple permits.

These test the parts that can be tested off a Mac: where the receipt is looked
for, what happens when there is not one, and the exit code Apple requires. The
VALIDATION itself is the Worker's and is deliberately not here.
"""
from __future__ import annotations

import sys

from app.core import mac_receipt


def test_it_reports_not_a_bundle_rather_than_no_receipt(monkeypatch):
    """"No receipt" and "not a bundled Mac build" need opposite responses.

    The first means a store customer must be sent to fetch one; the second is a
    developer running from source, where demanding a receipt would make the app
    unrunnable. Collapsing them is how a build becomes untestable.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    assert mac_receipt.bundle_root() is None
    assert mac_receipt.receipt_path() is None
    assert mac_receipt.read_receipt() is None


def test_running_from_source_on_a_mac_is_not_a_bundle(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert mac_receipt.bundle_root() is None


def test_the_receipt_is_found_inside_the_app_bundle(monkeypatch, tmp_path):
    """PyInstaller puts the executable in Contents/MacOS, so the bundle is two
    levels up. Getting this path wrong reads as "no receipt" and sends a paying
    customer round a loop that never ends."""
    app = tmp_path / "Dawnlist.app"
    exe = app / "Contents" / "MacOS" / "Dawnlist"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    receipt = app / "Contents" / "_MASReceipt" / "receipt"
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(b"pkcs7-blob")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert mac_receipt.bundle_root() == app
    assert mac_receipt.read_receipt() == b"pkcs7-blob"


def test_an_empty_receipt_counts_as_absent(monkeypatch, tmp_path):
    """A zero-byte file is what a half-finished install leaves behind, and
    forwarding it would have the Worker reject something that was never a
    receipt."""
    app = tmp_path / "Dawnlist.app"
    exe = app / "Contents" / "MacOS" / "Dawnlist"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    r = app / "Contents" / "_MASReceipt" / "receipt"
    r.parent.mkdir(parents=True)
    r.write_bytes(b"")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert mac_receipt.read_receipt() is None


def test_the_missing_receipt_exit_code_is_apples(monkeypatch):
    """173 exactly. macOS reads that one code as "fetch a receipt and relaunch";
    any other non-zero code is just a crash, and the app never comes back."""
    assert mac_receipt.exit_code_for_missing_receipt() == 173
    assert mac_receipt.MISSING_RECEIPT_EXIT == 173


def test_the_app_never_looks_inside_the_receipt():
    """The client forms no view about validity — the Worker rules.

    A client-side validator is one a determined user patches out, and one that
    is subtly wrong fails OPEN while looking correct. This asserts the module
    exposes no parsing surface at all.
    """
    surface = {n for n in dir(mac_receipt) if not n.startswith("_")}
    for forbidden in ("parse", "verify", "validate", "decode", "is_valid"):
        assert not any(forbidden in n.lower() for n in surface), (
            f"{forbidden!r} appears in the receipt module's public surface; "
            "validation belongs on the server")
