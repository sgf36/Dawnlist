"""The tray icon, and what closing the window means.

The daily run happens only while Dawnlist is running, so a person who closes
the window at night and expects a shortlist at seven has closed the thing that
would have made one. With "keep running" switched on, closing hides the window
to the tray instead, and the run happens on time. With it off, closing quits
exactly as it always did — keeping a process alive is a decision the user
makes, never one taken for them.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from app.core import schedule
from app.i18n import tr


class TrayPresence(QObject):
    def __init__(self, window, conn, controller, *,
                 available: Callable[[], bool] | None = None,
                 quit_app: Callable[[], None] | None = None,
                 parent=None):
        super().__init__(parent or window)
        self._window = window
        self._conn = conn
        self._controller = controller
        self._quit = quit_app or QApplication.quit
        self.icon: QSystemTrayIcon | None = None

        if (available or QSystemTrayIcon.isSystemTrayAvailable)():
            app = QApplication.instance()
            self.icon = QSystemTrayIcon(app.windowIcon() if app else window.windowIcon(),
                                        self)
            self.icon.setToolTip(tr("menu.app"))
            self.menu = QMenu()
            self.act_open = QAction(tr("tray.open"), self.menu)
            self.act_run = QAction(tr("run.now"), self.menu)
            self.act_quit = QAction(tr("tray.quit"), self.menu)
            self.act_open.triggered.connect(self.open)
            self.act_run.triggered.connect(controller.run_now)
            self.act_quit.triggered.connect(self._quit)
            for action in (self.act_open, self.act_run):
                self.menu.addAction(action)
            self.menu.addSeparator()
            self.menu.addAction(self.act_quit)
            self.icon.setContextMenu(self.menu)
            self.icon.activated.connect(self._activated)
            controller.changed.connect(self.refresh)
            self.refresh()
            self.icon.show()
            # Closing is decided in `eventFilter`. Left to Qt, hiding the last
            # window and later dismissing any dialog would end the process
            # behind the user's back.
            QApplication.setQuitOnLastWindowClosed(False)

        window.installEventFilter(self)

    @property
    def available(self) -> bool:
        return self.icon is not None

    def refresh(self) -> None:
        if self.icon is not None:
            self.act_run.setEnabled(self._controller.offers_run_now())

    def open(self) -> None:
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open()

    def notify(self, text: str) -> None:
        if self.icon is not None and text:
            self.icon.showMessage(tr("menu.app"), text)

    def start_hidden(self) -> bool:
        """Launched at sign-in: stay in the tray. False if there is no tray.

        If the run time has already passed with nothing run today, say so from
        the tray — the launch rule offers, it never spends, and an offer nobody
        can see is not one.
        """
        if self.icon is None:
            return False
        if self._controller.offers_run_now():
            from app.ui.scheduler import format_clock
            self.notify(tr("run.offer",
                           time=format_clock(self._controller.run_time())))
        return True

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        if watched is self._window and event.type() == QEvent.Type.Close:
            return self._closing(event)
        return False

    def _closing(self, event) -> bool:
        if self.icon is None:
            return False        # Qt's own behaviour: last window closed, quit
        if schedule.load_flag(self._conn, schedule.KEEP_RUNNING_KEY):
            event.ignore()
            self._window.hide()
            if not schedule.load_flag(self._conn, schedule.TRAY_NOTICE_KEY):
                self.icon.showMessage(tr("tray.still_running_title"),
                                      tr("tray.still_running"))
                schedule.save_flag(self._conn, schedule.TRAY_NOTICE_KEY, True)
            return True
        # Quitting is ours to do now that Qt has been told not to.
        self._quit()
        return False
