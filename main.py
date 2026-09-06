#!/usr/bin/env python3

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import obsws_python as obs

from switcher import (
    CONFIG_DIR,
    CONFIG_FILE,
    SwitcherBackend,
    merge_config,
    normalize,
)


APP_NAME = "OBS KWin Scene Switcher"


class MappingDialog(tk.Toplevel):
    def __init__(self, parent, window_class="", scene=""):
        super().__init__(parent)

        self.title("Scene Mapping")
        self.resizable(False, False)

        self.result = None

        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")

        ttk.Label(
            frame,
            text="Window class",
        ).grid(row=0, column=0, sticky="w", pady=4)

        self.class_var = tk.StringVar(value=window_class)

        ttk.Entry(
            frame,
            textvariable=self.class_var,
            width=35,
        ).grid(row=0, column=1, pady=4)

        ttk.Label(
            frame,
            text="OBS Scene",
        ).grid(row=1, column=0, sticky="w", pady=4)

        self.scene_var = tk.StringVar(value=scene)

        ttk.Entry(
            frame,
            textvariable=self.scene_var,
            width=35,
        ).grid(row=1, column=1, pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="e",
            pady=(12, 0),
        )

        ttk.Button(
            buttons,
            text="Cancel",
            command=self.destroy,
        ).pack(side="right")

        ttk.Button(
            buttons,
            text="OK",
            command=self.ok,
        ).pack(side="right", padx=6)

        self.transient(parent)
        self.grab_set()

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def ok(self):
        window_class = normalize(self.class_var.get())
        scene = self.scene_var.get().strip()

        if not window_class or not scene:
            messagebox.showwarning(
                APP_NAME,
                "Window class と OBS Scene を入力してください。",
                parent=self,
            )
            return

        self.result = (window_class, scene)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry("760x650")
        self.minsize(650, 550)

        self.event_queue = queue.Queue()
        self.backend = SwitcherBackend(self.event_queue)

        self.config_data = self.load_config()

        self.create_ui()
        self.load_config_into_ui()

        self.after(100, self.process_events)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        if not CONFIG_FILE.exists():
            return merge_config(None)

        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return merge_config(data)
        except Exception:
            return merge_config(None)

    def save_config(self):
        config = self.get_config_from_ui()

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        CONFIG_FILE.write_text(
            json.dumps(
                config,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self.config_data = config
        self.backend.update_config(config)

    def create_ui(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        obs_frame = ttk.LabelFrame(
            outer,
            text="OBS WebSocket",
            padding=10,
        )
        obs_frame.pack(fill="x")

        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.password_var = tk.StringVar()

        ttk.Label(obs_frame, text="Host").grid(
            row=0,
            column=0,
            sticky="w",
        )

        ttk.Entry(
            obs_frame,
            textvariable=self.host_var,
            width=18,
        ).grid(
            row=0,
            column=1,
            padx=(4, 12),
        )

        ttk.Label(obs_frame, text="Port").grid(
            row=0,
            column=2,
            sticky="w",
        )

        ttk.Entry(
            obs_frame,
            textvariable=self.port_var,
            width=8,
        ).grid(
            row=0,
            column=3,
            padx=(4, 12),
        )

        ttk.Label(obs_frame, text="Password").grid(
            row=0,
            column=4,
            sticky="w",
        )

        ttk.Entry(
            obs_frame,
            textvariable=self.password_var,
            show="•",
        ).grid(
            row=0,
            column=5,
            sticky="ew",
            padx=(4, 0),
        )

        obs_frame.columnconfigure(5, weight=1)

        status_frame = ttk.LabelFrame(
            outer,
            text="Status",
            padding=10,
        )
        status_frame.pack(fill="x", pady=(10, 0))

        self.status_var = tk.StringVar(value="Stopped")
        self.obs_status_var = tk.StringVar(value="Disconnected")
        self.window_var = tk.StringVar(value="-")
        self.scene_var = tk.StringVar(value="-")

        labels = [
            ("Monitor", self.status_var),
            ("OBS", self.obs_status_var),
            ("Active window", self.window_var),
            ("OBS scene", self.scene_var),
        ]

        for row, (name, var) in enumerate(labels):
            ttk.Label(
                status_frame,
                text=name + ":",
            ).grid(
                row=row,
                column=0,
                sticky="w",
                pady=2,
            )

            ttk.Label(
                status_frame,
                textvariable=var,
            ).grid(
                row=row,
                column=1,
                sticky="w",
                padx=(8, 0),
                pady=2,
            )

        safe_frame = ttk.Frame(outer)
        safe_frame.pack(fill="x", pady=(10, 0))

        ttk.Label(
            safe_frame,
            text="Fallback / Safe scene:",
        ).pack(side="left")

        self.safe_var = tk.StringVar()

        ttk.Entry(
            safe_frame,
            textvariable=self.safe_var,
            width=30,
        ).pack(side="left", padx=8)

        map_frame = ttk.LabelFrame(
            outer,
            text="Window → Scene",
            padding=8,
        )
        map_frame.pack(
            fill="both",
            expand=True,
            pady=(10, 0),
        )

        self.tree = ttk.Treeview(
            map_frame,
            columns=("class", "scene"),
            show="headings",
            height=8,
        )

        self.tree.heading("class", text="Window class")
        self.tree.heading("scene", text="OBS Scene")

        self.tree.column(
            "class",
            width=280,
            anchor="w",
        )

        self.tree.column(
            "scene",
            width=250,
            anchor="w",
        )

        self.tree.pack(
            side="left",
            fill="both",
            expand=True,
        )

        scrollbar = ttk.Scrollbar(
            map_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        scrollbar.pack(side="left", fill="y")

        self.tree.configure(
            yscrollcommand=scrollbar.set
        )

        map_buttons = ttk.Frame(map_frame)
        map_buttons.pack(
            side="left",
            fill="y",
            padx=(8, 0),
        )

        ttk.Button(
            map_buttons,
            text="Add",
            command=self.add_mapping,
        ).pack(fill="x")

        ttk.Button(
            map_buttons,
            text="Edit",
            command=self.edit_mapping,
        ).pack(fill="x", pady=4)

        ttk.Button(
            map_buttons,
            text="Delete",
            command=self.delete_mapping,
        ).pack(fill="x")

        ttk.Button(
            map_buttons,
            text="Use current",
            command=self.use_current,
        ).pack(fill="x", pady=(12, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(10, 0))

        self.start_button = ttk.Button(
            controls,
            text="Start",
            command=self.start_monitor,
        )
        self.start_button.pack(side="left")

        self.stop_button = ttk.Button(
            controls,
            text="Stop",
            command=self.stop_monitor,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=6)

        ttk.Button(
            controls,
            text="Save",
            command=self.save_clicked,
        ).pack(side="left")

        ttk.Button(
            controls,
            text="Reconnect",
            command=self.reconnect_obs,
        ).pack(side="right", padx=(0, 6))

        ttk.Button(
            controls,
            text="Test OBS",
            command=self.test_obs,
        ).pack(side="right")

        log_frame = ttk.LabelFrame(
            outer,
            text="Log",
            padding=8,
        )
        log_frame.pack(
            fill="both",
            expand=True,
            pady=(10, 0),
        )

        self.log_text = tk.Text(
            log_frame,
            height=8,
            wrap="word",
            state="disabled",
        )

        self.log_text.pack(
            fill="both",
            expand=True,
        )

    def load_config_into_ui(self):
        cfg = self.config_data

        self.host_var.set(cfg["obs_host"])
        self.port_var.set(str(cfg["obs_port"]))
        self.password_var.set(cfg["obs_password"])
        self.safe_var.set(cfg["safe_scene"])

        for item in self.tree.get_children():
            self.tree.delete(item)

        for window_class, scene in cfg["mappings"].items():
            self.tree.insert(
                "",
                "end",
                values=(window_class, scene),
            )

    def get_config_from_ui(self):
        mappings = {}

        for item in self.tree.get_children():
            window_class, scene = self.tree.item(
                item,
                "values",
            )
            mappings[window_class] = scene

        try:
            port = int(self.port_var.get())
        except ValueError:
            port = 4455

        return merge_config({
            "obs_host": self.host_var.get().strip(),
            "obs_port": port,
            "obs_password": self.password_var.get(),
            "safe_scene": self.safe_var.get().strip(),
            "mappings": mappings,
        })

    def add_mapping(self):
        dialog = MappingDialog(self)
        self.wait_window(dialog)

        if dialog.result:
            self.tree.insert(
                "",
                "end",
                values=dialog.result,
            )

    def edit_mapping(self):
        selected = self.tree.selection()

        if not selected:
            return

        item = selected[0]

        old_class, old_scene = self.tree.item(
            item,
            "values",
        )

        dialog = MappingDialog(
            self,
            old_class,
            old_scene,
        )

        self.wait_window(dialog)

        if dialog.result:
            self.tree.item(
                item,
                values=dialog.result,
            )

    def delete_mapping(self):
        for item in self.tree.selection():
            self.tree.delete(item)

    def use_current(self):
        current = self.window_var.get().strip()

        if not current or current == "-":
            return

        dialog = MappingDialog(
            self,
            current,
            "",
        )

        self.wait_window(dialog)

        if dialog.result:
            self.tree.insert(
                "",
                "end",
                values=dialog.result,
            )

    def append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start_monitor(self):
        try:
            self.save_config()
        except Exception as e:
            messagebox.showerror(
                APP_NAME,
                f"設定保存失敗:\n{e}",
            )
            return

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        self.backend.start(self.config_data)

    def stop_monitor(self):
        self.backend.stop()

        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def save_clicked(self):
        self.save_config()
        self.append_log(f"saved: {CONFIG_FILE}")

    def reconnect_obs(self):
        config = self.get_config_from_ui()
        self.backend.update_config(config)

        def run():
            self.backend.reset_obs_client()
            if self.backend.connect_obs():
                scene = (
                    self.backend.desired_scene
                    or self.backend.config.get("safe_scene")
                )
                if scene:
                    self.backend.switch_scene(scene, force=True)

        threading.Thread(
            target=run,
            daemon=True,
        ).start()

    def test_obs(self):
        config = self.get_config_from_ui()

        def run():
            try:
                client = obs.ReqClient(
                    host=config["obs_host"],
                    port=config["obs_port"],
                    password=config["obs_password"],
                    timeout=3,
                )

                version = client.get_version()

                self.event_queue.put(
                    (
                        "dialog",
                        (
                            "info",
                            f"OBS connected\nVersion: {version.obs_version}",
                        ),
                    )
                )

            except Exception as e:
                self.event_queue.put(
                    (
                        "dialog",
                        (
                            "error",
                            f"OBS connection failed:\n{e}",
                        ),
                    )
                )

        threading.Thread(
            target=run,
            daemon=True,
        ).start()

    def process_events(self):
        try:
            while True:
                event, value = self.event_queue.get_nowait()

                if event == "log":
                    self.append_log(value)

                elif event == "status":
                    self.status_var.set(value)

                    if value in ("Stopped", "Error"):
                        self.start_button.configure(
                            state="normal"
                        )
                        self.stop_button.configure(
                            state="disabled"
                        )

                elif event == "obs_status":
                    self.obs_status_var.set(value)

                elif event == "window":
                    self.window_var.set(value)

                elif event == "scene":
                    self.scene_var.set(value)

                elif event == "dialog":
                    kind, message = value

                    if kind == "info":
                        messagebox.showinfo(
                            APP_NAME,
                            message,
                        )
                    else:
                        messagebox.showerror(
                            APP_NAME,
                            message,
                        )

        except queue.Empty:
            pass

        self.after(100, self.process_events)

    def on_close(self):
        try:
            self.save_config()
        except Exception:
            pass

        self.backend.stop()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
