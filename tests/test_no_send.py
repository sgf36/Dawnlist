"""Invariant 1, as a property of the build rather than a policy.

"Never sends" is worth nothing if it depends on nobody importing smtplib later.
This walks the shipped source and fails if the sending machinery appears at all.
"""
import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Modules that can put a message on the wire, or that would require a mailbox
# credential and drag the product back into CASA/OAuth territory.
FORBIDDEN_IMPORTS = {"smtplib", "imaplib", "poplib", "email.smtpd", "aiosmtplib",
                     "yagmail", "sendgrid"}
FORBIDDEN_CALLS = {"sendmail", "send_message", "starttls"}


def python_files():
    return sorted(APP.rglob("*.py"))


def test_no_sending_module_is_imported_anywhere_in_the_app():
    offenders = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        offenders.append(f"{path.name}: import {a.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.name}: from {node.module} import ...")
    assert offenders == [], (
        "No SMTP in the binary. Permanent. Found: " + "; ".join(offenders))


def test_no_send_call_appears_in_the_app():
    offenders = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in FORBIDDEN_CALLS:
                    offenders.append(f"{path.name}:{node.lineno} {name}()")
    assert offenders == [], "Found a send call: " + "; ".join(offenders)
