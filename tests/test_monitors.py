import queue
import threading
from types import SimpleNamespace

import pytest

from monitors import PollMonitor, X11Monitor, executable_id, kwin_script, monitor_kind
from switcher import SwitcherBackend, config_dir


@pytest.mark.parametrize('platform,env,expected', [
    ('win32', {}, 'Windows'), ('linux', {'DISPLAY': ':0'}, 'X11'),
    ('linux', {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0', 'XDG_CURRENT_DESKTOP': 'KDE'}, 'KDE Wayland'),
    ('linux', {'XDG_SESSION_TYPE': 'wayland', 'XDG_CURRENT_DESKTOP': 'KDE'}, 'KDE Wayland')])
def test_detection(platform, env, expected):
    assert monitor_kind(platform, env) == expected


@pytest.mark.parametrize('platform,env', [('darwin', {}), ('linux', {}),
    ('linux', {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0', 'XDG_CURRENT_DESKTOP': 'GNOME'})])
def test_unsupported_never_falls_back(platform, env):
    with pytest.raises(RuntimeError):
        monitor_kind(platform, env)


def test_config_locations(tmp_path):
    assert config_dir('linux', {'XDG_CONFIG_HOME': str(tmp_path)}) == tmp_path / 'obs-kwin-switcher'
    assert config_dir('win32', {'APPDATA': str(tmp_path)}) == tmp_path / 'obs-scene-switcher'


def test_windows_identifier():
    assert executable_id(r'C:\Program Files\Firefox\FIREFOX.EXE') == 'firefox'
    assert executable_id('') == ''


def test_poll_changes_only_and_closes():
    stop = threading.Event()
    values = iter(['app', 'app', '', '', 'other'])
    class Monitor(PollMonitor):
        closed = False
        def read(self):
            value = next(values)
            if value == 'other':
                stop.set()
            return value
        def close(self):
            self.closed = True
    monitor = Monitor()
    received = []
    monitor.run(stop, received.append)
    assert received == ['app', '', 'other']
    assert monitor.closed


def test_poll_error_propagates_and_closes():
    class Monitor(PollMonitor):
        closed = False
        def read(self):
            raise RuntimeError('lost display')
        def close(self):
            self.closed = True
    monitor = Monitor()
    with pytest.raises(RuntimeError, match='lost display'):
        monitor.run(threading.Event(), lambda _: None)
    assert monitor.closed


def test_x11_missing_focus_and_destroyed_window():
    class BadWindow(Exception): pass
    monitor = X11Monitor.__new__(X11Monitor)
    monitor.errors = SimpleNamespace(BadWindow=BadWindow)
    monitor.active = 1
    monitor.root = SimpleNamespace(get_full_property=lambda *_: None)
    assert monitor.read() == ''
    monitor.root.get_full_property = lambda *_: SimpleNamespace(value=[123])
    def destroyed(*_): raise BadWindow()
    monitor.display = SimpleNamespace(create_resource_object=destroyed)
    assert monitor.read() == ''
    monitor.display.create_resource_object = lambda *_: SimpleNamespace(get_wm_class=lambda: ('instance', 'Firefox'))
    assert monitor.read() == 'Firefox'


def test_kwin_supports_both_apis_without_caption():
    script = kwin_script('unique:')
    assert 'workspace.activeClient' in script
    assert 'workspace.activeWindow' in script
    assert 'workspace.clientActivated' in script
    assert 'workspace.windowActivated' in script
    assert 'caption' not in script


def test_failure_goes_safe_and_remains_error(monkeypatch):
    backend = SwitcherBackend(queue.Queue(), debounce_sec=0)
    scenes = []
    monkeypatch.setattr(backend, 'switch_scene', lambda scene, **_: scenes.append(scene))
    monkeypatch.setattr('monitors.monitor_kind', lambda: 'X11')
    class Broken:
        def run(self, stop, changed):
            raise RuntimeError('lost display')
    monkeypatch.setattr('monitors.create_monitor', Broken)
    backend.start({'safe_scene': 'Safe'})
    backend.thread.join(2)
    assert not backend.thread.is_alive()
    assert scenes == ['Safe', 'Safe']
    events = list(backend.events.queue)
    assert ('status', 'Error') in events
    assert ('status', 'Stopped') not in events
    assert ('dialog', ('error', 'lost display')) in events


def test_stop_restart_ignores_late_callback(monkeypatch):
    backend = SwitcherBackend(queue.Queue(), debounce_sec=0)
    scenes = []
    callbacks = []
    ready = threading.Event()
    monkeypatch.setattr(backend, 'switch_scene', lambda scene, **_: scenes.append(scene))
    monkeypatch.setattr('monitors.monitor_kind', lambda: 'X11')
    class Monitor:
        def run(self, stop, changed):
            callbacks.append(changed)
            ready.set()
            stop.wait()
            changed('late')
    monkeypatch.setattr('monitors.create_monitor', Monitor)
    for _ in range(2):
        ready.clear()
        backend.start({'safe_scene': 'Safe', 'mappings': {'late': 'BAD'}})
        assert ready.wait(1)
        if len(callbacks) == 2:
            callbacks[0]('late')
        backend.stop()
    assert scenes == ['Safe', 'Safe']


@pytest.mark.parametrize('foreground,open_ok,query_ok,expected', [
    (0, True, True, ''), (42, False, True, ''),
    (42, True, False, ''), (42, True, True, 'firefox')])
def test_win32_acquisition_and_handle_cleanup(foreground, open_ok, query_ok, expected):
    from monitors import WindowsMonitor
    closed = []
    monitor = WindowsMonitor.__new__(WindowsMonitor)
    def pid(hwnd, pointer):
        pointer._obj.value = 123
        return 1
    def query(handle, flags, buffer, size):
        buffer.value = r'C:\Apps\FIREFOX.EXE'
        return query_ok
    monitor.user = SimpleNamespace(GetForegroundWindow=lambda: foreground, GetWindowThreadProcessId=pid)
    monitor.kernel = SimpleNamespace(OpenProcess=lambda *_: 99 if open_ok else 0,
        QueryFullProcessImageNameW=query, CloseHandle=closed.append)
    assert monitor.read() == expected
    assert closed == ([99] if foreground and open_ok else [])


def test_no_focus_always_safe_even_with_legacy_none_mapping(monkeypatch, tmp_path):
    from test_backend import make_backend, FakeObs
    client = FakeObs()
    backend, _ = make_backend(client)
    backend.config['mappings']['none'] = 'Idle'
    monkeypatch.setattr('switcher.ACTIVE_FILE', tmp_path / 'active')
    backend.apply_window('')
    assert client.scenes == ['Safe']


@pytest.mark.parametrize('legacy_path', [False, True])
def test_kwin_notification_and_cleanup(monkeypatch, legacy_path):
    import io
    import subprocess
    from monitors import KWinMonitor
    import monitors
    stopped = threading.Event()
    calls = []
    monkeypatch.setattr(monitors.uuid, 'uuid4', lambda: SimpleNamespace(hex='test'))
    monkeypatch.setattr(monitors.shutil, 'which', lambda name: name)
    process = SimpleNamespace(stdout=io.StringIO('js: obs-scene-test:Firefox\n'),
        terminate=lambda: calls.append('terminate'), wait=lambda **_: 0)
    monkeypatch.setattr(monitors.subprocess, 'Popen', lambda *_, **__: process)
    def run(command, **kwargs):
        calls.append(command)
        if command[3].endswith('.loadScript'):
            return SimpleNamespace(stdout='7')
        if legacy_path and command[2] == '/Scripting/Script7':
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout='true')
    monkeypatch.setattr(monitors.subprocess, 'run', run)
    values = []
    def changed(value):
        values.append(value)
        stopped.set()
    KWinMonitor().run(stopped, changed)
    assert values == ['Firefox']
    assert 'terminate' in calls
    assert any(isinstance(call, list) and call[3].endswith('.unloadScript') for call in calls)
    if legacy_path:
        assert any(isinstance(call, list) and call[2] == '/7' for call in calls)


def test_kwin_notification_timeout_cleans_up(monkeypatch):
    import monitors
    done = threading.Event()
    class SilentStream:
        def __iter__(self):
            done.wait(2)
            return iter(())
        def close(self): pass
    calls = []
    process = SimpleNamespace(stdout=SilentStream(), terminate=done.set, wait=lambda **_: 0)
    monkeypatch.setattr(monitors.shutil, 'which', lambda name: name)
    monkeypatch.setattr(monitors.subprocess, 'Popen', lambda *_, **__: process)
    def run(command, **kwargs):
        calls.append(command[3])
        return SimpleNamespace(stdout='7' if command[3].endswith('.loadScript') else 'true')
    monkeypatch.setattr(monitors.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='timed out'):
        monitors.KWinMonitor(notification_timeout=0.01).run(threading.Event(), lambda _: None)
    assert done.is_set()
    assert 'org.kde.kwin.Scripting.unloadScript' in calls


@pytest.mark.parametrize('original', [None, '/custom/lib'])
def test_system_command_environment(monkeypatch, original):
    import monitors
    monkeypatch.setattr(monitors.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(monitors.sys, '_MEIPASS', '/opt/app/_internal', raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/opt/app/_internal')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    if original is not None:
        monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    monkeypatch.setenv('QT_PLUGIN_PATH', '/opt/app/_internal/PySide6/Qt/plugins')
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')
    env = monitors.system_command_env()
    assert env.get('LD_LIBRARY_PATH') == original
    assert 'QT_PLUGIN_PATH' not in env
    assert env['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/run/user/1000/bus'
    assert monitors.os.environ['LD_LIBRARY_PATH'] == '/opt/app/_internal'


def test_unfrozen_environment_unchanged(monkeypatch):
    import monitors
    monkeypatch.setattr(monitors.sys, 'frozen', False, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/custom/lib')
    assert monitors.system_command_env() == dict(monitors.os.environ)


def test_journal_exit_reports_diagnostics_and_uses_host_environment(monkeypatch):
    import io
    import monitors
    commands = []
    environment = {'PATH': '/usr/bin'}
    monkeypatch.setattr(monitors, 'system_command_env', lambda: environment)
    monkeypatch.setattr(monitors.shutil, 'which', lambda name: name)
    def popen(command, **kwargs):
        assert kwargs['env'] == environment
        return SimpleNamespace(stdout=io.StringIO('journalctl: symbol lookup error: libsystemd.so\n'),
            terminate=lambda: None, wait=lambda **_: 127)
    def run(command, **kwargs):
        assert kwargs['env'] == environment
        commands.append(command[3])
        return SimpleNamespace(stdout='7')
    monkeypatch.setattr(monitors.subprocess, 'Popen', popen)
    monkeypatch.setattr(monitors.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='exit 127') as exc:
        monitors.KWinMonitor().run(threading.Event(), lambda _: None)
    assert 'symbol lookup error' in str(exc.value)
    assert 'org.kde.kwin.Scripting.unloadScript' in commands
