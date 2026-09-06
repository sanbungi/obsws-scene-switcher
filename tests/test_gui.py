import json
import queue
import threading
from types import SimpleNamespace

from PySide6.QtWidgets import QDialog, QMessageBox
from main import App, MappingDialog


class Backend:
    def __init__(self, events):
        self.events = events
        self.running = False
        self.stopped = threading.Event()
    def update_config(self, config):
        self.config = config
    def start(self, config):
        self.running = True
        self.events.put(('status', 'Running'))
    def stop(self):
        self.running = False
        self.stopped.set()


def make_app(qtbot, tmp_path):
    app = App(tmp_path / 'config.json', Backend)
    qtbot.addWidget(app)
    app.show()
    return app


def test_settings_roundtrip(qtbot, tmp_path):
    app = make_app(qtbot, tmp_path)
    app.host_edit.setText('localhost')
    app.port_edit.setValue(4456)
    app.password_edit.setText('secret')
    app.safe_edit.setText('BRB')
    app.case_insensitive_check.setChecked(False)
    app.partial_match_check.setChecked(False)
    app.table.setRowCount(0)
    app.put_mapping('firefox', 'Browser')
    app.save_config()
    assert json.loads(app.config_file.read_text())['mappings'] == {'firefox': 'Browser'}
    assert app.config_data['matching'] == {
        'case_insensitive': False,
        'partial_match': False,
    }
    app.config_data = app.load_config()
    app.load_config_into_ui()
    assert app.get_config_from_ui() == app.config_data


def test_mapping_edit_delete_and_current(qtbot, tmp_path, monkeypatch):
    app = make_app(qtbot, tmp_path)
    app.table.setRowCount(0)
    def accept(dialog):
        dialog.class_edit.setText(dialog.class_edit.text() or 'Firefox')
        dialog.scene_edit.setText('Browser')
        dialog.ok()
        return QDialog.Accepted
    monkeypatch.setattr(MappingDialog, 'exec', accept)
    app.add_mapping()
    assert app.table.item(0, 0).text() == 'firefox'
    app.table.selectRow(0)
    app.edit_mapping()
    app.delete_mapping()
    assert app.table.rowCount() == 0
    app.labels['window'].setText('code')
    app.use_current()
    assert app.table.item(0, 0).text() == 'code'


def test_status_error_and_async_close(qtbot, tmp_path, monkeypatch):
    app = make_app(qtbot, tmp_path)
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[2]))
    app.start_monitor()
    app.process_events()
    assert app.labels['status'].text() == 'Running'
    assert not app.start_button.isEnabled()
    app.event_queue.put(('dialog', ('error', 'monitor failed')))
    app.process_events()
    assert errors == ['monitor failed']
    gate = threading.Event()
    app.backend.stop = lambda: gate.wait(2)
    app.close()
    assert app.isVisible()
    assert app.closing
    gate.set()
    qtbot.waitUntil(lambda: not app.isVisible())


def test_stop_prevents_restart_until_complete(qtbot, tmp_path):
    app = make_app(qtbot, tmp_path)
    app.start_monitor()
    app.process_events()
    gate = threading.Event()
    app.backend.stop = lambda: gate.wait(2)
    app.stop_monitor()
    app.start_monitor()
    assert not app.start_button.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: not app.stopping)
    assert app.start_button.isEnabled()
