import os
import time
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('OBS_TEST_X11') != '1', reason='requires Xvfb and a window manager')


def test_real_active_window(qtbot):
    from Xlib import X, display, protocol
    from monitors import X11Monitor
    connection = display.Display()
    root = connection.screen().root
    window = root.create_window(20, 20, 200, 100, 0, connection.screen().root_depth,
                                X.InputOutput, X.CopyFromParent, background_pixel=0)
    monitor = X11Monitor()
    try:
        window.set_wm_class('integration', 'OBSIntegration')
        window.map()
        connection.sync()
        def activate():
            event = protocol.event.ClientMessage(window=window,
                client_type=connection.intern_atom('_NET_ACTIVE_WINDOW'), data=(32, [1, X.CurrentTime, 0, 0, 0]))
            root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            connection.flush()
            return monitor.read() == 'OBSIntegration'
        qtbot.waitUntil(activate, timeout=5000)
        window.destroy()
        connection.sync()
        qtbot.waitUntil(lambda: monitor.read() != 'OBSIntegration', timeout=5000)
    finally:
        monitor.close()
        connection.close()
