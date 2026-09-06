# Third-party software

This distribution includes CPython, Qt / PySide6 / Shiboken (LGPLv3 or
commercial terms), obsws-python, websocket-client, and, on Linux, python-xlib
and six. PyInstaller's bootloader uses its GPL exception for bundled programs.
The adjacent package directories contain upstream license and notice files;
manifest.json records exact versions and upstream project links.

Qt and Python sources and license information:
- https://download.qt.io/official_releases/QtForPython/
- https://code.qt.io/cgit/qt/
- https://www.python.org/downloads/source/
- https://doc.qt.io/qt-6/lgpl.html

The directory installation uses shared Qt libraries. Replacement libraries
must be ABI-compatible. The Windows portable executable extracts shared
libraries into a temporary directory; use the directory installation when
replacing libraries. Rebuild using the matching source checkout, uv.lock,
and packaging/build.py. No restriction on reverse engineering for debugging
modifications to LGPL components is imposed by this application.
