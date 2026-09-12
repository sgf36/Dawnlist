"""Starting Dawnlist when the user signs in. Opt-in on every build.

WHY IT IS NEEDED AT ALL
-----------------------
The daily run happens only while Dawnlist is running. A person who restarts
their computer and does not think to open the app has no run that morning, and
the promise of a shortlist every morning quietly becomes a promise kept on the
days they remembered.

WHY IT IS OFF UNTIL SWITCHED ON
-------------------------------
Apple's guideline 2.4.5(iii) forbids a Mac App Store app from launching at
login "without consent", and Microsoft reviews startup tasks the same way. So
the manifest ships the task disabled and nothing here runs unless the user
ticks the box.

ONE MECHANISM PER BUILD, because each channel allows exactly one:

  store   (MSIX)  a `windows.startupTask` in AppxManifest.xml, switched through
                  Windows.ApplicationModel.StartupTask. A packaged app cannot
                  write the Run key for itself, and the user can switch the
                  task off in Task Manager, after which ONLY they can switch it
                  back on — so that case opens Startup apps for them.
  direct  Windows the per-user Run key. Needs no identity and no dependency.
          macOS   SMAppService.mainApp, as the Mac App Store build does.
  mas             SMAppService.mainApp, the API Apple provides for this.

Every platform call is behind an object passed in, so the decisions are tested
on any machine and the Windows suite never imports PyObjC.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

#: Must match `uap5:StartupTask TaskId` in packaging/msix/AppxManifest.xml.
#: Changing it after release orphans the task a user already switched on.
TASK_ID = "DawnlistStartAtSignIn"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Dawnlist"

#: Put on the direct build's Run command, and accepted on any build, so a
#: launch at sign-in opens in the tray instead of putting a window in front of
#: whatever the person signed in to do.
BACKGROUND_ARG = "--background"

#: Windows.ApplicationModel.Activation.ActivationKind.StartupTask. Compared as
#: a number so the check does not need the activation projection's enum.
STARTUP_TASK_ACTIVATION = 1020

WINDOWS_STARTUP_APPS = "ms-settings:startupapps"
MAC_LOGIN_ITEMS = "mac-login-items"

#: Windows.ApplicationModel.StartupTaskState, by value.
STORE_STATES = {0: "disabled", 1: "disabled_by_user", 2: "enabled",
                3: "disabled_by_policy", 4: "enabled_by_policy"}

#: SMAppServiceStatus, by value.
MAC_STATES = {0: "not_registered", 1: "enabled", 2: "requires_approval",
              3: "not_found"}


@dataclass(frozen=True)
class Change:
    """What switching it did, and what the user must still do themselves."""
    enabled: bool
    #: A system page to open, because only the user may make this change there.
    open_page: str = ""
    #: 'policy' when an administrator has decided, 'unavailable' when the
    #: platform call could not be made, or the platform's own error text.
    problem: str = ""


def mechanism(build: str, platform: str | None = None) -> str | None:
    """'store', 'registry', 'mac' or None where this build cannot do it."""
    platform = platform or sys.platform
    windows = platform.startswith("win")
    mac = platform == "darwin"
    if build == "store" and windows:
        return "store"
    if build == "mas" and mac:
        return "mac"
    if build == "direct":
        return "registry" if windows else "mac" if mac else None
    return None


def supported(build: str, platform: str | None = None) -> bool:
    return mechanism(build, platform) is not None


def launch_command(executable: str | None = None, *, frozen: bool | None = None) -> str:
    executable = executable or sys.executable
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        return f'"{executable}" {BACKGROUND_ARG}'
    # A source checkout has no Dawnlist.exe; this is what a developer testing
    # the direct build's switch would otherwise have to type.
    return f'"{executable}" -m app.main {BACKGROUND_ARG}'


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def set_enabled(build: str, on: bool, *, platform: str | None = None,
                store=None, registry=None, mac=None,
                command: str | None = None) -> Change:
    kind = mechanism(build, platform)
    if kind == "store":
        return _store_change(on, store)
    if kind == "registry":
        return _registry_change(on, registry, command or launch_command())
    if kind == "mac":
        return _mac_change(on, mac)
    return Change(False, problem="unavailable")


def _store_change(on: bool, task) -> Change:
    try:
        task = task or StoreStartupTask()
        state = task.state()
    except Exception:  # noqa: BLE001 - no projection, or no package identity
        # Startup apps can still do it, whichever way the user wanted it.
        return Change(False, open_page=WINDOWS_STARTUP_APPS, problem="unavailable")

    if on:
        if state in ("enabled", "enabled_by_policy"):
            return Change(True)
        if state == "disabled_by_policy":
            return Change(False, problem="policy")
        if state == "disabled":
            try:
                state = task.request_enable()
            except Exception:  # noqa: BLE001
                state = "unavailable"
            if state in ("enabled", "enabled_by_policy"):
                return Change(True)
        # DisabledByUser: Windows lets only the user undo their own choice.
        return Change(False, open_page=WINDOWS_STARTUP_APPS)

    if state == "enabled_by_policy":
        return Change(True, problem="policy")
    if state == "enabled":
        try:
            task.disable()
        except Exception:  # noqa: BLE001
            return Change(True, open_page=WINDOWS_STARTUP_APPS)
    return Change(False)


def _registry_change(on: bool, registry, command: str) -> Change:
    try:
        registry = registry or RunRegistry()
        if not on:
            registry.remove()
            return Change(False)
        registry.write(command)
        # Read back rather than trusted: a value that did not land is a
        # sign-in that will not happen, and the box must not say it will.
        if registry.read() != command:
            return Change(False, problem="the setting did not save")
        return Change(True)
    except Exception as exc:  # noqa: BLE001
        return Change(False, problem=str(exc) or type(exc).__name__)


def _mac_change(on: bool, service) -> Change:
    try:
        service = service or MainAppService()
        if not on:
            service.unregister()
            return Change(False)
        status = service.register()
    except Exception as exc:  # noqa: BLE001
        return Change(False, problem=str(exc) or type(exc).__name__)
    if status == "enabled":
        return Change(True)
    if status == "requires_approval":
        # Registered, but macOS has put it to the user in System Settings.
        return Change(False, open_page=MAC_LOGIN_ITEMS)
    return Change(False, problem=status)


def is_enabled(build: str, *, platform: str | None = None, store=None,
               registry=None, mac=None) -> bool:
    """The system's answer, not a stored preference.

    The user can switch it off in Task Manager or System Settings without
    opening Dawnlist, and a box that still said "on" would be wrong from then
    on.
    """
    kind = mechanism(build, platform)
    try:
        if kind == "store":
            return (store or StoreStartupTask()).state() in (
                "enabled", "enabled_by_policy")
        if kind == "registry":
            return (registry or RunRegistry()).read() is not None
        if kind == "mac":
            return (mac or MainAppService()).status() == "enabled"
    except Exception:  # noqa: BLE001
        return False
    return False


def launched_at_sign_in(build: str, argv, *, platform: str | None = None,
                        activation_kind=None, mac_login_launch=None) -> bool:
    if BACKGROUND_ARG in (argv or ()):
        return True
    kind = mechanism(build, platform)
    try:
        if kind == "store":
            return (activation_kind or _store_activation_kind)() == \
                STARTUP_TASK_ACTIVATION
        if kind == "mac":
            return bool((mac_login_launch or _mac_launched_as_login_item)())
    except Exception:  # noqa: BLE001
        # Failing towards a visible window: a window nobody wanted is a small
        # annoyance, an app that starts invisibly when opened by hand is not.
        return False
    return False


def open_page(page: str, *, opener=None, mac=None) -> bool:
    try:
        if page == WINDOWS_STARTUP_APPS:
            import os
            (opener or os.startfile)(page)
            return True
        if page == MAC_LOGIN_ITEMS:
            (mac or MainAppService()).open_login_items()
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


# ---------------------------------------------------------------------------
# The platform calls. Imported lazily, and exercised only on their platform.
# ---------------------------------------------------------------------------

def _wait(make_operation):
    """Block on a WinRT async operation. Called off the UI thread."""
    import asyncio

    async def wait():
        return await make_operation()

    return asyncio.run(wait())


class StoreStartupTask:
    def __init__(self):
        from winrt.windows.applicationmodel import StartupTask

        self._task = _wait(lambda: StartupTask.get_async(TASK_ID))

    def state(self) -> str:
        return STORE_STATES.get(int(self._task.state), "unknown")

    def request_enable(self) -> str:
        return STORE_STATES.get(int(_wait(self._task.request_enable_async)),
                                "unknown")

    def disable(self) -> None:
        self._task.disable()


def _store_activation_kind() -> int | None:
    from winrt.windows.applicationmodel import AppInstance
    try:
        # Registers the activation argument types, so `.kind` is readable.
        import winrt.windows.applicationmodel.activation  # noqa: F401
    except ImportError:
        pass
    args = AppInstance.get_activated_event_args()
    return None if args is None else int(args.kind)


class RunRegistry:
    def read(self) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return winreg.QueryValueEx(key, RUN_VALUE)[0]
        except FileNotFoundError:
            return None

    def write(self, command: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)

    def remove(self) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
        except FileNotFoundError:
            pass


class MainAppService:
    def __init__(self):
        from ServiceManagement import SMAppService

        self._cls = SMAppService
        self._service = SMAppService.mainAppService()

    def status(self) -> str:
        return MAC_STATES.get(int(self._service.status()), "unknown")

    def register(self) -> str:
        # PyObjC returns (ok, NSError). The status afterwards is the answer
        # either way: a failed register that left it awaiting approval is the
        # ordinary first-time case, not an error.
        self._service.registerAndReturnError_(None)
        return self.status()

    def unregister(self) -> None:
        self._service.unregisterAndReturnError_(None)

    def open_login_items(self) -> None:
        self._cls.openSystemSettingsLoginItems()


def _fourcc(code: bytes) -> int:
    return int.from_bytes(code, "big")


def _mac_launched_as_login_item() -> bool:
    """Whether the launch Apple event says loginwindow opened this app.

    Only answers while that event is being handled, and Qt handles it inside
    its own event loop — so on a real Mac this may read nothing and the window
    opens. NOT VERIFIED on macOS hardware; see the report on this change.
    """
    from Foundation import NSAppleEventManager

    event = NSAppleEventManager.sharedAppleEventManager().currentAppleEvent()
    if event is None:
        return False
    prop = event.paramDescriptorForKeyword_(_fourcc(b"prdt"))
    return prop is not None and prop.enumCodeValue() == _fourcc(b"lgit")
