#!/usr/bin/env python3

import json
import queue
import sys
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import obsws_python as obs


def config_dir(platform=None, env=None):
    env = os.environ if env is None else env
    if (platform or sys.platform) == "win32":
        return Path(env.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "obs-scene-switcher"
    return Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "obs-kwin-switcher"


CONFIG_DIR = config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
ACTIVE_FILE = Path(tempfile.gettempdir()) / "obs-active-window.txt"

DEBOUNCE_SEC = 0.15
HEALTH_INTERVAL_SEC = 2.0
OBS_TIMEOUT = 3

DEFAULT_CONFIG = {
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "obs_password": "",
    "safe_scene": "Safe",
    "matching": {
        "case_insensitive": True,
        "partial_match": True,
    },
    "mappings": {
        "org.kde.konsole": "Konsole",
        "firefox": "Firefox",
        "code": "Code",
        "org.kde.dolphin": "Dolphin",
    },
}


def normalize(value: str, case_insensitive=True) -> str:
    value = value.strip()
    if case_insensitive:
        value = value.lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^A-Za-z0-9._+-]", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def merge_config(data: dict | None) -> dict:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if not data:
        return merged
    matching = data.get("matching")
    if not isinstance(matching, dict):
        matching = {}
    merged["matching"].update({
        key: bool(matching[key])
        for key in ("case_insensitive", "partial_match")
        if key in matching
    })
    mappings = data.get("mappings")
    merged.update(data)
    merged["matching"] = {
        "case_insensitive": bool(matching.get("case_insensitive", True)),
        "partial_match": bool(matching.get("partial_match", True)),
    }
    if isinstance(mappings, dict):
        merged["mappings"] = {
            normalize(str(k), merged["matching"]["case_insensitive"]): str(v).strip()
            for k, v in mappings.items()
            if str(k).strip() and str(v).strip()
        }
    else:
        merged["mappings"] = dict(DEFAULT_CONFIG["mappings"])
    if not str(merged.get("safe_scene", "")).strip():
        merged["safe_scene"] = DEFAULT_CONFIG["safe_scene"]
    try:
        merged["obs_port"] = int(merged["obs_port"])
    except (TypeError, ValueError):
        merged["obs_port"] = DEFAULT_CONFIG["obs_port"]
    return merged


def resolve_scene(window_value: str, mappings: dict, safe_scene: str,
                  case_insensitive=True, partial_match=True) -> str:
    value = normalize(window_value, case_insensitive) if window_value else "none"
    if not value:
        value = "none"
    if value in mappings:
        return mappings[value]

    if partial_match:
        # Match whole ID components only. For example, "firefox" matches
        # "firefox_firefox", but "code" does not match "codec".
        matches = [key for key in mappings if re.search(
            rf"(?:^|[._+-]){re.escape(key)}(?:$|[._+-])", value
        )]
        if matches:
            return mappings[max(matches, key=len)]
    return safe_scene


def atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


class Debouncer:
    def __init__(self, delay, callback, timer_factory=threading.Timer):
        self.delay = delay
        self.callback = callback
        self.timer_factory = timer_factory
        self._lock = threading.RLock()
        self._timer = None

    def trigger(self, value):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = self.timer_factory(self.delay, self._fire, args=(value,))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _fire(self, value):
        with self._lock:
            self._timer = None
        self.callback(value)

    def cancel(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class SwitcherBackend:
    def __init__(self, event_queue, debounce_sec=DEBOUNCE_SEC):
        self.events = event_queue
        self.running = False
        self.thread = None

        self.obs_client = None
        self.last_scene = None
        self.desired_scene = None

        self.config = merge_config(None)
        self._obs_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self.debouncer = Debouncer(debounce_sec, self.apply_window)
        self._stop = threading.Event()
        self._pending = queue.Queue()

    def emit(self, event_type, value=None):
        self.events.put((event_type, value))

    def log(self, text):
        if sys.stdout is not None:
            print(text, flush=True)
        self.emit("log", text)

    def update_config(self, config):
        self.config = merge_config(config)

    def reset_obs_client(self):
        with self._obs_lock:
            if self.obs_client is not None:
                try:
                    self.obs_client.disconnect()
                except Exception:
                    pass
            self.obs_client = None
            self.last_scene = None
            self.emit("obs_status", "Disconnected")

    def connect_obs(self):
        with self._obs_lock:
            return self._connect_obs_locked()

    def _connect_obs_locked(self):
        try:
            cfg = self.config
            self.obs_client = obs.ReqClient(
                host=cfg["obs_host"],
                port=int(cfg["obs_port"]),
                password=cfg["obs_password"],
                timeout=OBS_TIMEOUT,
            )
            version = self.obs_client.get_version()
            self.log(f"OBS connected: {version.obs_version}")
            self.emit("obs_status", "Connected")
            try:
                current = self.obs_client.get_current_program_scene()
                scene = (
                    getattr(current, "scene_name", None)
                    or getattr(current, "current_program_scene_name", None)
                )
                if scene:
                    self.emit("scene", scene)
            except Exception:
                pass
            return True
        except Exception as e:
            self.obs_client = None
            self.emit("obs_status", "Disconnected")
            self.log(f"OBS connection failed: {e}")
            return False

    def _ensure_obs_locked(self):
        if self.obs_client is None:
            return self._connect_obs_locked()
        return True

    def switch_scene(self, scene_name, force=False):
        if not scene_name:
            return False

        self.desired_scene = scene_name

        if not force and scene_name == self.last_scene and self.obs_client is not None:
            return True

        with self._obs_lock:
            if not self._ensure_obs_locked():
                return False
            try:
                self.obs_client.set_current_program_scene(scene_name)
                self.last_scene = scene_name
                self.emit("scene", scene_name)
                self.log(f"scene -> {scene_name}")
                return True
            except Exception as e:
                self.log(f"OBS websocket error: {e}")
                self.emit("obs_status", "Disconnected")
                self.obs_client = None
                self.last_scene = None
                if self._connect_obs_locked():
                    try:
                        self.obs_client.set_current_program_scene(scene_name)
                        self.last_scene = scene_name
                        self.emit("scene", scene_name)
                        self.log(f"scene -> {scene_name}")
                        return True
                    except Exception as e2:
                        self.log(f"OBS websocket retry failed: {e2}")
                        self.obs_client = None
                        self.last_scene = None
                return False

    def apply_window(self, raw_value):
        if not self.running:
            return

        matching = self.config.get("matching", DEFAULT_CONFIG["matching"])
        case_insensitive = matching["case_insensitive"]
        value = normalize(raw_value, case_insensitive)
        if not value:
            value = "none"

        try:
            atomic_write(ACTIVE_FILE, value)
        except Exception as e:
            self.log(f"/tmp write error: {e}")

        self.emit("window", value)

        cfg = self.config
        scene = (
            cfg["safe_scene"]
            if not normalize(raw_value, case_insensitive)
            else resolve_scene(
                value, cfg["mappings"], cfg["safe_scene"],
                case_insensitive=case_insensitive,
                partial_match=matching["partial_match"],
            )
        )
        self.log(f"window -> {value}")
        self.log(f"mapping -> {scene}")
        self.switch_scene(scene)

    def handle_window(self, raw_value):
        self.debouncer.trigger(raw_value)

    def ping_obs(self):
        with self._obs_lock:
            if self.obs_client is None:
                return False
            try:
                self.obs_client.get_version()
                return True
            except Exception:
                self.obs_client = None
                self.last_scene = None
                self.emit("obs_status", "Disconnected")
                return False

    def health_tick(self):
        if not self.running:
            return
        if self.ping_obs():
            return
        self.log("OBS health check failed, reconnecting")
        if self.connect_obs():
            scene = self.desired_scene or self.config.get("safe_scene")
            if scene:
                self.switch_scene(scene, force=True)

    def start(self, config):
        with self._lifecycle_lock:
            self.stop_locked()
            self.update_config(config)
            self._stop = threading.Event()
            self._pending = queue.Queue()
            self.running = True
            self.last_scene = None
            self.desired_scene = self.config["safe_scene"]
            self.thread = threading.Thread(target=self.worker, daemon=True)
            self.thread.start()

    def worker(self):
        from monitors import create_monitor, monitor_kind
        monitor_thread = None
        failed = False
        pending = self._pending
        stop = self._stop
        try:
            self.emit("status", "Starting")
            self.switch_scene(self.config["safe_scene"], force=True)
            if stop.is_set():
                return
            self.emit("method", monitor_kind())
            def watch():
                try:
                    create_monitor().run(stop, lambda value: pending.put(("window", value)))
                    if not stop.is_set():
                        pending.put(("error", "Window monitor exited unexpectedly"))
                except Exception as exc:
                    pending.put(("error", str(exc)))
            monitor_thread = threading.Thread(target=watch, daemon=True)
            monitor_thread.start()
            value = None
            deadline = None
            health_at = time.monotonic() + HEALTH_INTERVAL_SEC
            confirmed = False
            while not stop.is_set():
                try:
                    kind, data = pending.get(timeout=0.05)
                    if kind == "error":
                        raise RuntimeError(data)
                    if not confirmed:
                        self.emit("status", "Running")
                        confirmed = True
                    value = data
                    deadline = time.monotonic() + self.debouncer.delay
                except queue.Empty:
                    pass
                if stop.is_set():
                    break
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    self.apply_window(value)
                    deadline = None
                if now >= health_at:
                    self.health_tick()
                    health_at = time.monotonic() + HEALTH_INTERVAL_SEC
        except Exception as exc:
            if not stop.is_set():
                failed = True
                self.switch_scene(self.config["safe_scene"], force=True)
                self.log(f"ERROR: {exc}")
                self.emit("dialog", ("error", str(exc)))
        finally:
            stop.set()
            self.running = False
            self.debouncer.cancel()
            if monitor_thread:
                monitor_thread.join()
            self.reset_obs_client()
            self.emit("status", "Error" if failed else "Stopped")

    def stop_locked(self):
        self.running = False
        self._stop.set()
        self.debouncer.cancel()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join()
        self.thread = None

    def stop(self):
        with self._lifecycle_lock:
            self.stop_locked()
