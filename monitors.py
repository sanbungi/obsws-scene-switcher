"""Window monitors. No GUI imports; run() owns resources until stop is set."""
import ctypes
import os
from pathlib import Path, PureWindowsPath
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid


def monitor_kind(platform=None, env=None):
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == 'win32':
        return 'Windows'
    if platform != 'linux':
        raise RuntimeError(f'Unsupported OS: {platform}')
    if env.get('XDG_SESSION_TYPE') == 'wayland' or env.get('WAYLAND_DISPLAY'):
        if 'kde' in env.get('XDG_CURRENT_DESKTOP', '').lower().split(':'):
            return 'KDE Wayland'
        raise RuntimeError('Wayland monitoring requires KDE Plasma (GNOME/Sway/Hyprland unsupported)')
    if env.get('DISPLAY'):
        return 'X11'
    raise RuntimeError('No supported desktop session / DISPLAY')


def find_qdbus():
    for name in ('qdbus6', 'qdbus-qt6', '/usr/lib/qt6/bin/qdbus',
                 'qdbus-qt5', '/usr/lib/qt5/bin/qdbus', 'qdbus'):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError('KDE monitoring requires qdbus (Qt 5 or Qt 6)')


class PollMonitor:
    def run(self, stop, changed):
        previous = object()
        try:
            while not stop.is_set():
                value = self.read()
                if value != previous:
                    changed(value)
                    previous = value
                stop.wait(0.1)
        finally:
            self.close()

    def close(self):
        pass


class X11Monitor(PollMonitor):
    def __init__(self):
        try:
            from Xlib.display import Display
            from Xlib import error
        except ImportError as exc:
            raise RuntimeError('X11 monitoring requires python-xlib') from exc
        self.errors = error
        self.display = Display()
        self.root = self.display.screen().root
        self.active = self.display.intern_atom('_NET_ACTIVE_WINDOW')

    def read(self):
        try:
            prop = self.root.get_full_property(self.active, 0)
            if prop is None or not len(prop.value) or not prop.value[0]:
                return ''
            window = self.display.create_resource_object('window', int(prop.value[0]))
            classes = window.get_wm_class()
            return (classes[1] or classes[0]) if classes else ''
        except self.errors.BadWindow:
            return ''

    def close(self):
        self.display.close()


def executable_id(path):
    return PureWindowsPath(path).stem.lower() if path else ''


class WindowsMonitor(PollMonitor):
    def __init__(self):
        from ctypes import wintypes as w
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.GetForegroundWindow.argtypes = []
        self.user.GetForegroundWindow.restype = w.HWND
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL

    def read(self):
        from ctypes import wintypes as w
        hwnd = self.user.GetForegroundWindow()
        if not hwnd:
            return ''
        pid = w.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            return ''
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ''
        try:
            size = w.DWORD(32768)
            path = ctypes.create_unicode_buffer(size.value)
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                return ''
            return executable_id(path.value)
        finally:
            self.kernel.CloseHandle(handle)


def kwin_script(marker):
    return '''
function report(w) {
    print(MARKER + (w ? (w.resourceClass || w.resourceName || "") : ""));
}
if (workspace.windowActivated) {
    workspace.windowActivated.connect(report);
    report(workspace.activeWindow);
} else {
    workspace.clientActivated.connect(report);
    report(workspace.activeClient);
}
'''.replace('MARKER', repr(marker))


class KWinMonitor:
    def __init__(self, notification_timeout=5):
        self.notification_timeout = notification_timeout

    def run(self, stop, changed):
        qdbus = find_qdbus()
        if not shutil.which('journalctl'):
            raise RuntimeError('KDE monitoring requires journalctl and a readable user journal')
        name = 'obs-scene-' + uuid.uuid4().hex
        marker = name + ':'
        journal = None
        reader = None
        loaded = False
        def dbus(path, method, *args):
            return subprocess.run([qdbus, 'org.kde.KWin', path, method, *args],
                                  capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        with tempfile.TemporaryDirectory(prefix=name) as folder:
            script = Path(folder) / 'monitor.js'
            script.write_text(kwin_script(marker), encoding='utf-8')
            try:
                # Include a short history to avoid the journal follower startup race;
                # the unique marker excludes all old monitor instances.
                journal = subprocess.Popen(['journalctl', '--user', '-f', '--since', 'now', '-o', 'cat'],
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                lines = queue.Queue()
                def read_lines():
                    for line in journal.stdout:
                        lines.put(line)
                    lines.put(None)
                reader = threading.Thread(target=read_lines, daemon=True)
                reader.start()
                script_id = dbus('/Scripting', 'org.kde.kwin.Scripting.loadScript', str(script), name)
                loaded = True
                if not script_id.isdigit():
                    raise RuntimeError('KWin script load failed: ' + script_id)
                # Plasma 5 and 6 expose different script object paths.
                for path in (f'/Scripting/Script{script_id}', f'/{script_id}'):
                    try:
                        dbus(path, 'org.kde.kwin.Script.run')
                        break
                    except subprocess.CalledProcessError:
                        if path == f'/{script_id}':
                            raise
                deadline = time.monotonic() + self.notification_timeout
                health_at = time.monotonic() + 2
                confirmed = False
                while not stop.is_set():
                    if time.monotonic() >= health_at:
                        if dbus('/Scripting', 'org.kde.kwin.Scripting.isScriptLoaded', name).lower() != 'true':
                            raise RuntimeError('KWin monitoring script disappeared (KWin may have restarted)')
                        health_at = time.monotonic() + 2
                    try:
                        line = lines.get(timeout=0.1)
                    except queue.Empty:
                        if not confirmed and time.monotonic() > deadline:
                            raise RuntimeError('KWin notification test timed out. Enable KWin script debug logging and check the user journal.')
                        continue
                    if line is None:
                        raise RuntimeError('KWin journal monitor exited unexpectedly')
                    if marker in line:
                        confirmed = True
                        changed(line.split(marker, 1)[1].strip())
            finally:
                if journal:
                    journal.terminate()
                    try:
                        journal.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        journal.kill()
                        journal.wait()
                    if reader:
                        reader.join()
                    journal.stdout.close()
                if loaded:
                    try:
                        dbus('/Scripting', 'org.kde.kwin.Scripting.unloadScript', name)
                    except (OSError, subprocess.SubprocessError):
                        pass


def create_monitor():
    return {'Windows': WindowsMonitor, 'X11': X11Monitor, 'KDE Wayland': KWinMonitor}[monitor_kind()]()
