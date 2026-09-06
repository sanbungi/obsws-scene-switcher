import queue
from types import SimpleNamespace
from unittest.mock import MagicMock

from switcher import Debouncer, SwitcherBackend


class ImmediateTimer:
    def __init__(self, delay, fn, args=None, kwargs=None):
        self.fn = fn
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True
        if not self.cancelled:
            self.fn(*self.args, **self.kwargs)

    def cancel(self):
        self.cancelled = True


class FakeTimer:
    instances = []

    def __init__(self, delay, fn, args=None, kwargs=None):
        self.delay = delay
        self.fn = fn
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.daemon = False
        self.cancelled = False
        FakeTimer.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn(*self.args, **self.kwargs)


class FakeObs:
    def __init__(self, fail_set=0, fail_version=False):
        self.scenes = []
        self.fail_set = fail_set
        self.fail_version = fail_version
        self.version_calls = 0

    def get_version(self):
        self.version_calls += 1
        if self.fail_version:
            raise RuntimeError("disconnected")
        return SimpleNamespace(obs_version="31.0.0")

    def get_current_program_scene(self):
        return SimpleNamespace(current_program_scene_name="Safe")

    def set_current_program_scene(self, name):
        if self.fail_set > 0:
            self.fail_set -= 1
            raise RuntimeError("ws error")
        self.scenes.append(name)


def make_backend(obs_client=None):
    events = queue.Queue()
    backend = SwitcherBackend(events, debounce_sec=0)
    backend.debouncer = Debouncer(0, backend.apply_window, timer_factory=ImmediateTimer)
    backend.running = True
    backend.config = {
        "obs_host": "127.0.0.1",
        "obs_port": 4455,
        "obs_password": "",
        "safe_scene": "Safe",
        "mappings": {"firefox": "Firefox", "code": "Code"},
    }
    backend.obs_client = obs_client
    return backend, events


def drain(events):
    items = []
    while True:
        try:
            items.append(events.get_nowait())
        except queue.Empty:
            return items


def test_apply_window_maps_and_switches(tmp_path, monkeypatch):
    monkeypatch.setattr("switcher.ACTIVE_FILE", tmp_path / "active.txt")
    client = FakeObs()
    backend, events = make_backend(client)

    backend.apply_window("Firefox")

    assert client.scenes == ["Firefox"]
    assert backend.last_scene == "Firefox"
    assert backend.desired_scene == "Firefox"
    assert (tmp_path / "active.txt").read_text().strip() == "firefox"
    kinds = [k for k, _ in drain(events)]
    assert "window" in kinds
    assert "scene" in kinds


def test_apply_window_unmapped_goes_safe(tmp_path, monkeypatch):
    monkeypatch.setattr("switcher.ACTIVE_FILE", tmp_path / "active.txt")
    client = FakeObs()
    backend, _ = make_backend(client)

    backend.apply_window("unknown-app")

    assert client.scenes == ["Safe"]


def test_switch_scene_skips_duplicate_when_connected():
    client = FakeObs()
    backend, _ = make_backend(client)
    backend.last_scene = "Firefox"

    ok = backend.switch_scene("Firefox")

    assert ok is True
    assert client.scenes == []


def test_switch_scene_force_resends():
    client = FakeObs()
    backend, _ = make_backend(client)
    backend.last_scene = "Firefox"

    backend.switch_scene("Firefox", force=True)

    assert client.scenes == ["Firefox"]


def test_switch_scene_retries_after_ws_error(monkeypatch):
    first = FakeObs(fail_set=1)
    second = FakeObs()
    clients = iter([second])

    backend, _ = make_backend(first)

    def fake_connect():
        backend.obs_client = next(clients)
        return True

    monkeypatch.setattr(backend, "_connect_obs_locked", fake_connect)

    ok = backend.switch_scene("Code")

    assert ok is True
    assert second.scenes == ["Code"]
    assert backend.last_scene == "Code"


def test_handle_window_debounces_to_latest():
    FakeTimer.instances = []
    events = queue.Queue()
    backend = SwitcherBackend(events, debounce_sec=0.15)
    applied = []
    backend.apply_window = applied.append
    backend.debouncer = Debouncer(0.15, backend.apply_window, timer_factory=FakeTimer)

    backend.handle_window("firefox")
    backend.handle_window("code")

    assert len(FakeTimer.instances) == 2
    assert FakeTimer.instances[0].cancelled is True
    FakeTimer.instances[1].fire()
    assert applied == ["code"]


def test_health_tick_reconnects_and_restores_desired(monkeypatch):
    backend, _ = make_backend(FakeObs(fail_version=True))
    backend.desired_scene = "Firefox"
    restored = []

    monkeypatch.setattr(backend, "connect_obs", lambda: True)
    monkeypatch.setattr(
        backend,
        "switch_scene",
        lambda scene, force=False: restored.append((scene, force)) or True,
    )

    backend.health_tick()

    assert restored == [("Firefox", True)]


def test_stop_does_not_start_second_worker():
    events = queue.Queue()
    backend = SwitcherBackend(events)
    backend.worker = MagicMock()

    backend.start({"safe_scene": "Safe", "mappings": {}})
    first = backend.thread
    backend.start({"safe_scene": "Safe", "mappings": {}})
    second = backend.thread

    assert first is not second
    backend.running = False
    if second:
        second.join(timeout=1)
