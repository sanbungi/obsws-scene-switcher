#!/usr/bin/env python3
import json
import queue
import sys
import threading
from pathlib import Path

import obsws_python as obs
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QApplication, QMainWindow, QDialog, QDialogButtonBox,
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit, QSpinBox,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPlainTextEdit, QMessageBox)
from PySide6.QtWidgets import QCheckBox
from monitors import monitor_kind
from switcher import CONFIG_FILE, SwitcherBackend, merge_config, normalize

APP_NAME = 'OBS Scene Switcher'


class MappingDialog(QDialog):
    def __init__(self, parent, window_class='', scene='', case_insensitive=True):
        super().__init__(parent)
        self.setWindowTitle('Scene Mapping')
        self.result_mapping = None
        self.case_insensitive = case_insensitive
        form = QFormLayout(self)
        self.class_edit = QLineEdit(window_class)
        self.scene_edit = QLineEdit(scene)
        form.addRow('Window / App ID', self.class_edit)
        form.addRow('OBS Scene', self.scene_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.ok)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def ok(self):
        key = normalize(self.class_edit.text(), self.case_insensitive)
        scene = self.scene_edit.text().strip()
        if not key or not scene:
            QMessageBox.warning(self, APP_NAME, 'Window / App ID と OBS Scene を入力してください。')
            return
        self.result_mapping = key, scene
        self.accept()


class App(QMainWindow):
    def __init__(self, config_file=CONFIG_FILE, backend_factory=SwitcherBackend):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(760, 720)
        self.config_file = Path(config_file)
        self.event_queue = queue.Queue()
        self.backend = backend_factory(self.event_queue)
        self.jobs = []
        self.stopping = False
        self.closing = False
        self.closed_ready = False
        self.config_data = self.load_config()
        self.create_ui()
        self.load_config_into_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_events)
        self.timer.start(50)

    def load_config(self):
        try:
            data = json.loads(self.config_file.read_text(encoding='utf-8'))
            return merge_config(data if isinstance(data, dict) else None)
        except (OSError, ValueError):
            return merge_config(None)

    def create_ui(self):
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        connection = QGroupBox('OBS WebSocket')
        form = QFormLayout(connection)
        self.host_edit = QLineEdit()
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        for title, widget in [('Host', self.host_edit), ('Port', self.port_edit), ('Password', self.password_edit)]:
            form.addRow(title, widget)
        layout.addWidget(connection)
        status = QGroupBox('Status')
        form = QFormLayout(status)
        self.labels = {key: QLabel(value) for key, value in
            [('status', 'Stopped'), ('obs_status', 'Disconnected'), ('window', '-'), ('scene', '-') ]}
        try:
            method = monitor_kind()
        except RuntimeError as exc:
            method = str(exc)
        self.labels['method'] = QLabel(method)
        for key, title in [('method', 'Monitoring method'), ('status', 'Monitor'), ('obs_status', 'OBS'), ('window', 'Window / App ID'), ('scene', 'OBS scene')]:
            self.labels[key].setWordWrap(True)
            form.addRow(title, self.labels[key])
        layout.addWidget(status)
        safe = QFormLayout()
        self.safe_edit = QLineEdit()
        safe.addRow('Fallback / Safe scene', self.safe_edit)
        layout.addLayout(safe)
        matching = QGroupBox('Window / App ID matching')
        matching_layout = QHBoxLayout(matching)
        self.case_insensitive_check = QCheckBox('Ignore case')
        self.case_insensitive_check.setToolTip(
            'Treat Firefox and firefox as the same ID.'
        )
        self.partial_match_check = QCheckBox('Match ID components')
        self.partial_match_check.setToolTip(
            'Allow firefox to match firefox_firefox. Exact matches win.'
        )
        matching_layout.addWidget(self.case_insensitive_check)
        matching_layout.addWidget(self.partial_match_check)
        matching_layout.addStretch()
        layout.addWidget(matching)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['Window / App ID', 'OBS Scene'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_mapping())
        layout.addWidget(self.table, 2)
        mappings = QHBoxLayout()
        for title, callback in [('Add', self.add_mapping), ('Edit', self.edit_mapping), ('Delete', self.delete_mapping), ('Use current', self.use_current)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            mappings.addWidget(button)
        layout.addLayout(mappings)
        controls = QHBoxLayout()
        for name, title, callback in [('start', 'Start', self.start_monitor), ('stop', 'Stop', self.stop_monitor), ('save', 'Save', self.save_clicked), ('test', 'Test OBS', self.test_obs), ('reconnect', 'Reconnect', self.reconnect_obs)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            setattr(self, name + '_button', button)
            controls.addWidget(button)
        self.stop_button.setEnabled(False)
        layout.addLayout(controls)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(1000)
        layout.addWidget(self.log_text, 1)

    def load_config_into_ui(self):
        cfg = self.config_data
        self.host_edit.setText(cfg['obs_host'])
        self.port_edit.setValue(cfg['obs_port'])
        self.password_edit.setText(cfg['obs_password'])
        self.safe_edit.setText(cfg['safe_scene'])
        self.case_insensitive_check.setChecked(cfg['matching']['case_insensitive'])
        self.partial_match_check.setChecked(cfg['matching']['partial_match'])
        self.table.setRowCount(0)
        for key, scene in cfg['mappings'].items():
            self.put_mapping(key, scene)

    def put_mapping(self, key, scene, row=None):
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
        for col, value in enumerate((key, scene)):
            self.table.setItem(row, col, QTableWidgetItem(value))

    def get_config_from_ui(self):
        return merge_config(dict(obs_host=self.host_edit.text().strip(), obs_port=self.port_edit.value(),
            obs_password=self.password_edit.text(), safe_scene=self.safe_edit.text().strip(),
            matching={
                'case_insensitive': self.case_insensitive_check.isChecked(),
                'partial_match': self.partial_match_check.isChecked(),
            },
            mappings={self.table.item(row, 0).text(): self.table.item(row, 1).text() for row in range(self.table.rowCount())}))

    def save_config(self):
        config = self.get_config_from_ui()
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.config_file)
        self.config_data = config
        self.backend.update_config(config)

    def save_clicked(self):
        try:
            self.save_config()
            self.append_log(f'saved: {self.config_file}')
            return True
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f'設定保存失敗: {exc}')
            return False

    def mapping_dialog(self, key='', scene='', row=None):
        dialog = MappingDialog(
            self, key, scene,
            self.case_insensitive_check.isChecked(),
        )
        if dialog.exec() == QDialog.Accepted:
            self.put_mapping(*dialog.result_mapping, row=row)

    def add_mapping(self):
        self.mapping_dialog()

    def edit_mapping(self):
        row = self.table.currentRow()
        if row >= 0:
            self.mapping_dialog(self.table.item(row, 0).text(), self.table.item(row, 1).text(), row)

    def delete_mapping(self):
        for row in sorted({item.row() for item in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(row)

    def use_current(self):
        key = self.labels['window'].text()
        if key not in ('', '-', 'none'):
            self.mapping_dialog(key)

    def append_log(self, text):
        self.log_text.appendPlainText(str(text))

    def launch(self, callback):
        thread = threading.Thread(target=callback, daemon=True)
        self.jobs.append(thread)
        thread.start()

    def start_monitor(self):
        if self.stopping or self.backend.running or not self.save_clicked():
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.backend.start(self.config_data)

    def stop_monitor(self):
        if self.stopping:
            return
        self.stopping = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.labels['status'].setText('Stopping')
        def stop():
            self.backend.stop()
            self.event_queue.put(('stopped', None))
        self.launch(stop)

    def reconnect_obs(self):
        if self.stopping:
            return
        self.backend.update_config(self.get_config_from_ui())
        self.reconnect_button.setEnabled(False)
        def reconnect():
            try:
                self.backend.reset_obs_client()
                if self.backend.connect_obs() and self.backend.running:
                    self.backend.switch_scene(self.backend.desired_scene or self.backend.config['safe_scene'], force=True)
            finally:
                self.event_queue.put(('reconnected', None))
        self.launch(reconnect)

    def test_obs(self):
        cfg = self.get_config_from_ui()
        self.test_button.setEnabled(False)
        def test():
            client = None
            try:
                client = obs.ReqClient(host=cfg['obs_host'], port=cfg['obs_port'], password=cfg['obs_password'], timeout=3)
                value = ('info', f'OBS connected\nVersion: {client.get_version().obs_version}')
            except Exception as exc:
                value = ('error', f'OBS connection failed: {exc}')
            finally:
                if client:
                    try:
                        client.disconnect()
                    except Exception:
                        pass
            self.event_queue.put(('tested', value))
        self.launch(test)

    def process_events(self):
        while True:
            try:
                kind, value = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.append_log(value)
            elif kind in self.labels:
                if not self.stopping:
                    self.labels[kind].setText(str(value) or 'none')
                    if kind == 'status':
                        self.start_button.setEnabled(value in ('Stopped', 'Error'))
                        self.stop_button.setEnabled(value not in ('Stopped', 'Error'))
            elif kind == 'stopped':
                self.stopping = False
                self.labels['status'].setText('Stopped')
                self.labels['obs_status'].setText('Disconnected')
                self.start_button.setEnabled(not self.closing)
            elif kind == 'reconnected':
                self.reconnect_button.setEnabled(True)
            elif kind in ('dialog', 'tested'):
                if kind == 'tested':
                    self.test_button.setEnabled(True)
                if not self.closing:
                    method = QMessageBox.information if value[0] == 'info' else QMessageBox.critical
                    method(self, APP_NAME, value[1])
        self.jobs = [job for job in self.jobs if job.is_alive()]
        if self.closing and not self.stopping and not self.jobs:
            self.closed_ready = True
            self.close()

    def closeEvent(self, event):
        if self.closed_ready:
            self.timer.stop()
            event.accept()
            return
        event.ignore()
        if not self.closing:
            if not self.save_clicked():
                return
            self.closing = True
            self.centralWidget().setEnabled(False)
            self.stop_monitor()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = App()
    window.show()
    if '--smoke-test' in sys.argv:
        QTimer.singleShot(500, window.close)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
