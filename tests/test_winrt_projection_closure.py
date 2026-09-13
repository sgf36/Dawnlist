"""Every Windows namespace the Store build touches at run time is installed.

The live 1.1.2 Microsoft Store build showed "could not read the add-on: No
module named 'winrt.windows.foundation.collections'" (Spencer, 2026-09-13).
The WinRT projection imports a namespace only when a call RETURNS one of its
types, from inside compiled code, so the import audit, which reads Python
imports, could not see it. Reproduced on Windows with exactly the locked
packages; fixed by installing Windows.Foundation.Collections.

These tests make the calls rather than reading the imports. Outside an
installed package the Store answers "unavailable", which is fine: the
projection still has to build the result objects, and that is where the
missing module raised.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Namespace -> the distribution in the lock that provides it. Every namespace
#: a Store or start-at-sign-in call can hand back must be here.
RUNTIME_NAMESPACES = {
    "winrt.runtime.interop": "winrt-runtime",
    "winrt.windows.foundation": "winrt-windows-foundation",
    "winrt.windows.foundation.collections": "winrt-windows-foundation-collections",
    "winrt.windows.services.store": "winrt-windows-services-store",
    "winrt.windows.applicationmodel": "winrt-windows-applicationmodel",
    "winrt.windows.applicationmodel.activation": "winrt-windows-applicationmodel-activation",
}


def test_every_runtime_namespace_is_in_the_lock():
    lock = "\n" + (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").lower()
    missing = [dist for dist in RUNTIME_NAMESPACES.values() if f"\n{dist}==" not in lock]
    assert not missing, f"not installed by the lock: {missing}"


@pytest.mark.skipif(sys.platform != "win32", reason="WinRT exists only on Windows")
def test_every_runtime_namespace_imports():
    import importlib

    pytest.importorskip("winrt.windows.services.store")
    for name in RUNTIME_NAMESPACES:
        importlib.import_module(name)


PROBE = textwrap.dedent("""
    from winrt.windows.services.store import StoreContext
    ctx = StoreContext.get_default()
    result = ctx.get_store_products_async(["Durable", "Subscription"], []).get()
    list(result.products.items())
    licence = ctx.get_app_license_async().get()
    list(licence.add_on_licenses.items())
    print("PROJECTED")
""")


@pytest.mark.skipif(sys.platform != "win32", reason="WinRT exists only on Windows")
def test_the_store_queries_the_app_makes_build_their_results():
    """In a child process, because a Store call can wait on a service that a
    CI machine lacks; a hang there says nothing about a missing module."""
    pytest.importorskip("winrt.windows.services.store")
    try:
        done = subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                              text=True, timeout=90)
    except subprocess.TimeoutExpired:
        pytest.skip("the Store service did not answer on this machine")
    output = done.stdout + done.stderr
    assert "ModuleNotFoundError" not in output, output
    if "PROJECTED" not in output:
        pytest.skip(f"the Store refused outside a package: {output[-300:]}")
