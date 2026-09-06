#!/usr/bin/env python3

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import obsws_python as obs


CONFIG_DIR = Path.home() / ".config" / "obs-kwin-switcher"
CONFIG_FILE = CONFIG_DIR / "config.json"
ACTIVE_FILE = Path("/tmp/obs-active-window.txt")
MARKER = "OBS_ACTIVE_WINDOW_7b32:"

DEBOUNCE_SEC = 0.15
HEALTH_INTERVAL_SEC = 2.0
OBS_TIMEOUT = 3

DEFAULT_CONFIG = {
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "obs_password": "",
    "safe_scene": "Safe",
    "mappings": {
        "org.kde.konsole": "Konsole",
        "firefox": "Firefox",
        "code": "Code",
        "org.kde.dolphin": "Dolphin",
    },
}


def normalize(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9._+-]", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def merge_config(data: dict | None) -> dict:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if not data:
        return merged
    mappings = data.get("mappings")
    merged.update(data)
    if isinstance(mappings, dict):
        merged["mappings"] = {
            normalize(str(k)): str(v).strip()
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


def resolve_scene(window_value: str, mappings: dict, safe_scene: str) -> str:
    value = normalize(window_value) if window_value else "none"
    if not value:
        value = "none"
    return mappings.get(value, safe_scene)


def atomic_write(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def find_qdbus():
    for name in ("qdbus6", "qdbus-qt6", "qdbus"):
        path = shutil.which(name)
        if path:
            return path
    return None


class Debouncer:
    def __init__(self, delay, callback, timer_factory=threading.Timer):
        self.delay = delay
        self.callback = callback
        self.timer_factory = timer_factory
        self._lock = threading.Lock()
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
        self.health_thread = None

        self.obs_client = None
        self.last_scene = None
        self.desired_scene = None

        self.qdbus = None
        self.script_id = None
        self.script_path = None
        self.journal = None

        self.config = merge_config(None)
        self._obs_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self.debouncer = Debouncer(debounce_sec, self.apply_window)

    def emit(self, event_type, value=None):
        self.events.put((event_type, value))

    def log(self, text):
        print(text, flush=True)
        self.emit("log", text)

    def update_config(self, config):
        self.config = merge_config(config)

    def reset_obs_client(self):
        with self._obs_lock:
            self.obs_client = None
            self.last_scene = None

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

        value = normalize(raw_value)
        if not value:
            value = "none"

        try:
            atomic_write(ACTIVE_FILE, value)
        except Exception as e:
            self.log(f"/tmp write error: {e}")

        self.emit("window", value)

        cfg = self.config
        scene = resolve_scene(value, cfg["mappings"], cfg["safe_scene"])
        self.log(f"window -> {value}")
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

    def _health_loop(self):
        while self.running:
            time.sleep(HEALTH_INTERVAL_SEC)
            if not self.running:
                break
            try:
                self.health_tick()
            except Exception as e:
                self.log(f"health loop error: {e}")

    def make_kwin_script(self):
        js = f"""
function reportWindow(w) {{
    if (!w) {{
        print("{MARKER}");
        return;
    }}

    var value = w.resourceClass;

    if (!value || value.length === 0)
        value = w.resourceName;

    if (!value || value.length === 0)
        value = w.caption;

    print("{MARKER}" + value);
}}

reportWindow(workspace.activeWindow);

workspace.windowActivated.connect(function(w) {{
    reportWindow(w);
}});
"""
        f = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".js",
            prefix="obs-active-",
            delete=False,
            encoding="utf-8",
        )
        f.write(js)
        f.close()
        self.script_path = f.name

    def start(self, config):
        with self._lifecycle_lock:
            self.stop_locked()
            self.update_config(config)
            self.running = True
            self.last_scene = None
            self.desired_scene = self.config["safe_scene"]
            self.thread = threading.Thread(target=self.worker, daemon=True)
            self.thread.start()

    def worker(self):
        try:
            self.qdbus = find_qdbus()
            if not self.qdbus:
                raise RuntimeError(
                    "qdbus6 / qdbus-qt6 / qdbus が見つかりません"
                )

            self.emit("status", "Starting")

            try:
                atomic_write(ACTIVE_FILE, "safe")
            except Exception as e:
                self.log(f"/tmp write error: {e}")

            self.connect_obs()
            self.switch_scene(self.config["safe_scene"], force=True)

            self.make_kwin_script()

            result = subprocess.run(
                [
                    self.qdbus,
                    "org.kde.KWin",
                    "/Scripting",
                    "loadScript",
                    self.script_path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            self.script_id = result.stdout.strip()
            if not self.script_id:
                raise RuntimeError("KWin script load failed")

            self.log(f"KWin script id: {self.script_id}")

            self.journal = subprocess.Popen(
                [
                    "journalctl",
                    "--user",
                    "-f",
                    "-n",
                    "0",
                    "-u",
                    "plasma-kwin_wayland.service",
                    "-o",
                    "cat",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            subprocess.run(
                [
                    self.qdbus,
                    "org.kde.KWin",
                    f"/Scripting/Script{self.script_id}",
                    "run",
                ],
                check=True,
            )

            self.health_thread = threading.Thread(
                target=self._health_loop,
                daemon=True,
            )
            self.health_thread.start()

            self.emit("status", "Running")
            self.log("monitor started")

            assert self.journal.stdout is not None

            for line in self.journal.stdout:
                if not self.running:
                    break
                if MARKER not in line:
                    continue
                value = line.split(MARKER, 1)[1].strip()
                self.handle_window(value)

        except Exception as e:
            self.log(f"ERROR: {e}")
            self.emit("status", "Error")

        finally:
            self.cleanup()

    def cleanup(self):
        was_running = self.running
        self.running = False
        self.debouncer.cancel()

        if self.journal:
            try:
                self.journal.terminate()
            except Exception:
                pass
            try:
                self.journal.wait(timeout=2)
            except Exception:
                try:
                    self.journal.kill()
                except Exception:
                    pass
            self.journal = None

        if self.script_id and self.qdbus:
            try:
                subprocess.run(
                    [
                        self.qdbus,
                        "org.kde.KWin",
                        f"/Scripting/Script{self.script_id}",
                        "stop",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
            except Exception:
                pass

        self.script_id = None

        if self.script_path:
            try:
                os.unlink(self.script_path)
            except FileNotFoundError:
                pass
            self.script_path = None

        with self._obs_lock:
            self.obs_client = None

        if was_running:
            self.emit("status", "Stopped")

    def stop_locked(self):
        self.running = False
        self.debouncer.cancel()

        if self.journal:
            try:
                self.journal.terminate()
            except Exception:
                pass

        thread = self.thread
        if thread is not None and thread.is_alive():
            if threading.current_thread() is not thread:
                thread.join(timeout=5)

        health = self.health_thread
        if health is not None and health.is_alive():
            if threading.current_thread() is not health:
                health.join(timeout=3)

        self.thread = None
        self.health_thread = None

    def stop(self):
        with self._lifecycle_lock:
            self.stop_locked()
