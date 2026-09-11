"""Start at sign-in: the decisions, with every platform call replaced.

None of these touch the registry, WinRT or PyObjC. The real adapters are thin
and can only be exercised on their own platform and build; what can go wrong in
a way a test can catch is the choosing, and that is all tested here.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.core import sign_in
from app.core.sign_in import (BACKGROUND_ARG, MAC_LOGIN_ITEMS,
                              STARTUP_TASK_ACTIVATION, WINDOWS_STARTUP_APPS,
                              Change)


class FakeTask:
    def __init__(self, state, after_request="enabled"):
        self._state = state
        self.after_request = after_request
        self.requested = self.disabled = 0

    def state(self):
        return self._state

    def request_enable(self):
        self.requested += 1
        self._state = self.after_request
        return self._state

    def disable(self):
        self.disabled += 1
        self._state = "disabled"


class Unavailable:
    def state(self):
        raise OSError("this process has no package identity")


class FakeRegistry:
    def __init__(self, sticks=True):
        self.value = None
        self.sticks = sticks

    def read(self):
        return self.value

    def write(self, command):
        if self.sticks:
            self.value = command

    def remove(self):
        self.value = None


class FakeService:
    def __init__(self, after_register="enabled"):
        self._status = "not_registered"
        self.after_register = after_register
        self.opened = 0

    def status(self):
        return self._status

    def register(self):
        self._status = self.after_register
        return self._status

    def unregister(self):
        self._status = "not_registered"

    def open_login_items(self):
        self.opened += 1


# -- which mechanism --------------------------------------------------------

@pytest.mark.parametrize("build, platform, expected", [
    ("store", "win32", "store"),
    ("direct", "win32", "registry"),
    ("direct", "darwin", "mac"),
    ("mas", "darwin", "mac"),
    ("store", "darwin", None),
    ("mas", "win32", None),
    ("none", "win32", None),
    ("ambiguous", "darwin", None),
])
def test_each_build_has_exactly_its_own_mechanism(build, platform, expected):
    assert sign_in.mechanism(build, platform) == expected
    assert sign_in.supported(build, platform) is (expected is not None)


# -- Microsoft Store: the startup task --------------------------------------

def test_store_an_enabled_task_is_left_alone():
    task = FakeTask("enabled")
    assert sign_in.set_enabled("store", True, platform="win32", store=task) == Change(True)
    assert task.requested == 0


def test_store_a_disabled_task_is_requested():
    task = FakeTask("disabled")
    assert sign_in.set_enabled("store", True, platform="win32", store=task) == Change(True)
    assert task.requested == 1


def test_store_a_task_the_user_switched_off_sends_them_to_startup_apps():
    """Windows lets only the user undo their own choice, so asking again would
    do nothing and the box would claim it had worked."""
    task = FakeTask("disabled_by_user")
    change = sign_in.set_enabled("store", True, platform="win32", store=task)
    assert change == Change(False, open_page=WINDOWS_STARTUP_APPS)
    assert task.requested == 0


def test_store_a_refused_request_sends_them_to_startup_apps():
    task = FakeTask("disabled", after_request="disabled_by_user")
    change = sign_in.set_enabled("store", True, platform="win32", store=task)
    assert change == Change(False, open_page=WINDOWS_STARTUP_APPS)


def test_store_an_administrators_decision_is_reported_not_fought():
    assert sign_in.set_enabled("store", True, platform="win32",
                               store=FakeTask("disabled_by_policy")) == \
        Change(False, problem="policy")
    assert sign_in.set_enabled("store", False, platform="win32",
                               store=FakeTask("enabled_by_policy")) == \
        Change(True, problem="policy")


def test_store_switching_off_disables_the_task():
    task = FakeTask("enabled")
    assert sign_in.set_enabled("store", False, platform="win32", store=task) == Change(False)
    assert task.disabled == 1


def test_store_without_winrt_falls_back_to_startup_apps():
    change = sign_in.set_enabled("store", True, platform="win32", store=Unavailable())
    assert change == Change(False, open_page=WINDOWS_STARTUP_APPS,
                            problem="unavailable")


# -- direct download on Windows: the Run key --------------------------------

def test_direct_windows_writes_and_removes_the_run_value():
    reg = FakeRegistry()
    command = '"C:\\Apps\\Dawnlist.exe" --background'
    assert sign_in.set_enabled("direct", True, platform="win32", registry=reg,
                               command=command) == Change(True)
    assert reg.value == command
    assert sign_in.is_enabled("direct", platform="win32", registry=reg)

    assert sign_in.set_enabled("direct", False, platform="win32",
                               registry=reg) == Change(False)
    assert reg.value is None
    assert not sign_in.is_enabled("direct", platform="win32", registry=reg)


def test_direct_windows_a_value_that_did_not_land_is_not_reported_on():
    change = sign_in.set_enabled("direct", True, platform="win32",
                                 registry=FakeRegistry(sticks=False), command="x")
    assert change.enabled is False and change.problem


def test_the_run_command_starts_in_the_tray():
    frozen = sign_in.launch_command(r"C:\Apps\Dawnlist.exe", frozen=True)
    assert frozen == f'"C:\\Apps\\Dawnlist.exe" {BACKGROUND_ARG}'
    source = sign_in.launch_command(r"C:\py\python.exe", frozen=False)
    assert source.endswith(f"-m app.main {BACKGROUND_ARG}")


# -- macOS: SMAppService.mainApp ---------------------------------------------

@pytest.mark.parametrize("build", ["mas", "direct"])
def test_mac_registers_the_main_app(build):
    service = FakeService()
    assert sign_in.set_enabled(build, True, platform="darwin", mac=service) == Change(True)
    assert sign_in.is_enabled(build, platform="darwin", mac=service)
    assert sign_in.set_enabled(build, False, platform="darwin", mac=service) == Change(False)
    assert not sign_in.is_enabled(build, platform="darwin", mac=service)


def test_mac_awaiting_approval_opens_login_items():
    change = sign_in.set_enabled("mas", True, platform="darwin",
                                 mac=FakeService("requires_approval"))
    assert change == Change(False, open_page=MAC_LOGIN_ITEMS)


def test_mac_a_bundle_macos_cannot_find_is_a_problem_not_a_success():
    change = sign_in.set_enabled("mas", True, platform="darwin",
                                 mac=FakeService("not_found"))
    assert change == Change(False, problem="not_found")


def test_the_mac_decisions_never_import_pyobjc():
    """The Windows suite must pass with no macOS module installed."""
    sign_in.set_enabled("mas", True, platform="darwin", mac=FakeService())
    assert "ServiceManagement" not in sys.modules or sys.platform == "darwin"


# -- an unsupported build ----------------------------------------------------

def test_an_unmarked_build_changes_nothing():
    assert sign_in.set_enabled("none", True, platform="win32") == \
        Change(False, problem="unavailable")
    assert sign_in.is_enabled("none", platform="win32") is False


def test_a_failing_check_reads_as_off():
    assert sign_in.is_enabled("store", platform="win32", store=Unavailable()) is False


# -- recognising a launch at sign-in ----------------------------------------

def test_the_background_flag_means_sign_in_on_any_build():
    for build in ("store", "direct", "mas", "none"):
        assert sign_in.launched_at_sign_in(build, [BACKGROUND_ARG], platform="win32")


def test_store_recognises_its_startup_task_activation():
    assert sign_in.launched_at_sign_in(
        "store", [], platform="win32",
        activation_kind=lambda: STARTUP_TASK_ACTIVATION)
    assert not sign_in.launched_at_sign_in(
        "store", [], platform="win32", activation_kind=lambda: 0)


def test_a_launch_that_cannot_be_read_opens_the_window():
    def broken():
        raise ImportError("winrt")
    assert not sign_in.launched_at_sign_in("store", [], platform="win32",
                                           activation_kind=broken)
    assert not sign_in.launched_at_sign_in("mas", [], platform="darwin",
                                           mac_login_launch=broken)


def test_a_plain_direct_launch_is_not_a_sign_in():
    assert not sign_in.launched_at_sign_in("direct", [], platform="win32")


def test_mac_asks_the_launch_event():
    assert sign_in.launched_at_sign_in("mas", [], platform="darwin",
                                       mac_login_launch=lambda: True)


# -- the pages only the user may change --------------------------------------

def test_startup_apps_is_opened_by_its_settings_address():
    opened = []
    assert sign_in.open_page(WINDOWS_STARTUP_APPS, opener=opened.append)
    assert opened == ["ms-settings:startupapps"]


def test_login_items_is_opened_through_the_service():
    service = FakeService()
    assert sign_in.open_page(MAC_LOGIN_ITEMS, mac=service)
    assert service.opened == 1


def test_a_page_that_will_not_open_says_so():
    def refuse(_page):
        raise OSError("no handler")
    assert sign_in.open_page(WINDOWS_STARTUP_APPS, opener=refuse) is False


# -- the manifest ------------------------------------------------------------

MANIFEST = Path(__file__).resolve().parent.parent / "packaging" / "msix" / "AppxManifest.xml"
UAP5 = "http://schemas.microsoft.com/appx/manifest/uap/windows10/5"


def test_the_manifest_declares_the_startup_task_switched_off():
    """Off in the package, so installing never adds Dawnlist to Startup apps,
    and under the id the code switches — a mismatch is a switch that silently
    does nothing on the one build that needs it."""
    root = ET.parse(MANIFEST).getroot()
    tasks = root.findall(f".//{{{UAP5}}}StartupTask")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.get("TaskId") == sign_in.TASK_ID
    assert task.get("Enabled") == "false"

    extension = root.find(f".//{{{UAP5}}}Extension")
    assert extension.get("Category") == "windows.startupTask"
    assert extension.get("Executable") == "Dawnlist.exe"
    assert "uap5" in root.get("IgnorableNamespaces").split()
