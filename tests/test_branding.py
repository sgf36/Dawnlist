"""Every window carries the app's icon, and keeps carrying it.

The bug this guards against is not "the icon is wrong". It is "somebody adds a
fifth place that constructs a QApplication and does not know the icon has to be
applied there too" — which is exactly how the first four ended up without one.
Nothing failed, nothing warned, and every screenshot for weeks carried Qt's
placeholder in the title bar because nobody looks at a title bar.
"""
import re
from pathlib import Path

from app.ui.branding import ICON, apply_icon

ROOT = Path(__file__).resolve().parents[1]
CREATES_QAPP = re.compile(r"QApplication\.instance\(\) or QApplication\(")


def source_files():
    for p in (ROOT / "app").rglob("*.py"):
        yield p


def test_the_icon_ships_in_the_bundle():
    """Read by path at runtime, so it must exist in the source tree AND be
    listed in build_exe.spec's datas. The spec half is checked below."""
    assert ICON.exists(), f"{ICON} is missing — every window falls back to Qt's placeholder"
    assert ICON.stat().st_size > 1000, "an icon this small is unlikely to be a real image"


def test_the_spec_ships_the_icon():
    """The window icon is NOT supplied by the exe icon. A build that sets one
    and forgets the other looks correct in Explorer and wrong on screen."""
    spec = (ROOT / "packaging" / "build_exe.spec").read_text(encoding="utf-8")
    assert '"app" / "resources" / "icon.png"' in spec, (
        "build_exe.spec does not ship app/resources/icon.png, so the packaged "
        "app will show Qt's placeholder however good the source tree looks")


def test_the_spec_refuses_to_build_without_an_exe_icon():
    """It used to fall back to PyInstaller's default silently, and shipped that
    way. A conditional that degrades to the WRONG artefact is worse than a
    missing file, because a missing file stops the build."""
    spec = (ROOT / "packaging" / "build_exe.spec").read_text(encoding="utf-8")
    assert "_require_icon" in spec
    # Match the EXECUTABLE form, not the prose. The first version of this
    # assertion searched for the old pattern anywhere in the file and tripped
    # on the comment that explains the old pattern — a test failing on its own
    # documentation, which teaches people to delete the documentation.
    code = [ln for ln in spec.split("\n") if not ln.lstrip().startswith("#")]
    assert not any('icon=str(icons_dir / "dawnlist.ico") if' in ln for ln in code), (
        "the silent icon fallback is back")


def test_every_qapplication_site_applies_the_icon():
    """The guard that matters. Setting the icon at one of four call sites fixes
    one of four windows."""
    missing = []
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        creations = len(CREATES_QAPP.findall(text))
        if not creations:
            continue
        applications = text.count("apply_icon(app)")
        if applications < creations:
            missing.append(
                f"{path.relative_to(ROOT)}: {creations} QApplication(s), "
                f"{applications} apply_icon call(s)")
    assert not missing, (
        "a QApplication is created without applying the window icon:\n  "
        + "\n  ".join(missing))


def test_apply_icon_never_raises_without_an_icon(monkeypatch, tmp_path):
    """Branding must never break a launch. A missing or unreadable icon
    degrades to the placeholder, which is ugly and harmless."""
    monkeypatch.setattr("app.ui.branding.ICON", tmp_path / "nope.png")

    class FakeApp:
        def setWindowIcon(self, icon):  # noqa: N802 - Qt's name
            raise AssertionError("must not be called when the icon is missing")

    assert apply_icon(FakeApp()) is False
