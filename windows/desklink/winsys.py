"""Windows-only bits: global hotkey, start-with-Windows, firewall rule. Safe no-ops elsewhere."""
from __future__ import annotations

import os
import sys
import threading
from typing import Callable

IS_WINDOWS = sys.platform == "win32"

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

# (label, modifiers, virtual key). Ctrl+Shift+S was avoided on purpose: it is "Save As" in most apps.
HOTKEYS: dict[str, tuple[str, int, int]] = {
    "ctrl+alt+c": ("Ctrl + Alt + C", MOD_CONTROL | MOD_ALT, 0x43),
    "ctrl+shift+alt+c": ("Ctrl + Shift + Alt + C", MOD_CONTROL | MOD_SHIFT | MOD_ALT, 0x43),
    "win+alt+c": ("Win + Alt + C", MOD_WIN | MOD_ALT, 0x43),
    "ctrl+alt+p": ("Ctrl + Alt + P", MOD_CONTROL | MOD_ALT, 0x50),
    "off": ("Off", 0, 0),
}


def hotkey_label(name: str) -> str:
    return HOTKEYS.get(name, HOTKEYS["ctrl+alt+c"])[0]


class GlobalHotkey:
    """Registers one system-wide hotkey on a background thread with its own message loop."""

    def __init__(self, callback: Callable[[], None]):
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0

    def set(self, name: str) -> bool:
        """(Re)register a hotkey. Returns False if another app already owns it."""
        self.stop()
        mods, vk = HOTKEYS.get(name, HOTKEYS["ctrl+alt+c"])[1:]
        if not IS_WINDOWS or not vk:
            return name == "off" or not IS_WINDOWS
        result: dict[str, bool] = {}
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(mods, vk, result, ready), daemon=True)
        self._thread.start()
        ready.wait(2)
        return result.get("ok", False)

    def stop(self) -> None:
        if IS_WINDOWS and self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            if self._thread:
                self._thread.join(1)
        self._thread, self._thread_id = None, 0

    def _run(self, mods: int, vk: int, result: dict, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        result["ok"] = bool(user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
        ready.set()
        if not result["ok"]:
            self._thread_id = 0
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self._callback()
                except Exception:
                    pass
        user32.UnregisterHotKey(None, 1)


# ---------- start with Windows ----------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "DeskLink"


def _launch_command() -> str:
    launcher = os.environ.get("DESKLINK_LAUNCHER")  # set by the portable package's DeskLink.exe
    if launcher and os.path.exists(launcher):
        return f'"{launcher}" --tray'
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    script = os.path.abspath(sys.argv[0])
    return f'"{pyw if os.path.exists(pyw) else exe}" "{script}" --tray'


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, _RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---------- firewall ----------
def allow_through_firewall() -> bool:
    """Ask for admin rights once and add an inbound rule for this program on Private networks."""
    if not IS_WINDOWS:
        return False
    import ctypes
    program = sys.executable
    args = (
        '/c netsh advfirewall firewall delete rule name="DeskLink" >nul 2>&1 & '
        f'netsh advfirewall firewall add rule name="DeskLink" dir=in action=allow '
        f'program="{program}" enable=yes profile=private,domain'
    )
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", args, None, 0)
    return rc > 32
