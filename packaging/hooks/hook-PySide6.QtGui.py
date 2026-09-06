"""Only ship desktop plugins used by this Qt Widgets application."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import pyside6_library_info

hiddenimports, binaries, datas = pyside6_library_info.collect_module('PySide6.QtGui')
# Avoid optional PDF, virtual keyboard, QML and embedded-device plugins.
allowed = {
    'libqxcb.so', 'libqwayland.so', 'libqwayland-egl.so', 'libqoffscreen.so', 'libqminimal.so',
    'qwindows.dll', 'qoffscreen.dll', 'qminimal.dll',
    'libqjpeg.so', 'libqico.so', 'libqsvg.so', 'qjpeg.dll', 'qico.dll', 'qsvg.dll',
    'libcomposeplatforminputcontextplugin.so', 'libibusplatforminputcontextplugin.so',
}
binaries = [(source, dest) for source, dest in binaries
            if Path(source).name in allowed or 'wayland-' in dest]
hiddenimports = [name for name in hiddenimports if name in
                 {'PySide6.QtCore', 'PySide6.QtDBus', 'PySide6.QtNetwork', 'PySide6.QtSvg'}]
