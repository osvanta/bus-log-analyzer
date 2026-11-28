# -*- mode: python ; coding: utf-8 -*-
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys

if sys.platform == "win32":
    import pefile    # comes with PyInstaller on Windows; reads what a DLL links to

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# PyInstaller executes spec files without defining __file__.
# Resolve the project root from the current working directory, which is
# expected to be the project root when build.bat or build.sh runs pyinstaller.
project_root = Path(os.getcwd()).resolve()
icon_file = project_root / "resources" / "app_icon.ico"

# One spec for the Windows, Linux and macOS folders. Windows takes the
# executable's icon from the .ico; a Linux executable carries no icon, and a
# macOS one would need an .icns, so neither gets one here.
_exe_icon = str(icon_file) if sys.platform == "win32" else None

datas = []
if icon_file.exists():
    datas.append((str(icon_file), "resources"))

# Always bundle the splash screen image
splash_file = project_root / "resources" / "splashscreen.png"
if splash_file.exists():
    datas.append((str(splash_file), "resources"))

# Bundle app icon PNG for runtime window icon
icon_png = project_root / "resources" / "app_icon.png"
if icon_png.exists():
    datas.append((str(icon_png), "resources"))

# Licence documentation. The LGPL-3.0 components bundled here (Qt via PySide6,
# python-can, asammdf) require that the licence texts and a notice accompany
# the distributed application, so these are not optional extras — a build
# missing them is not redistributable. See THIRD_PARTY_NOTICES.md. These copies
# go into _internal, where the About dialog reads them; the same files are also
# copied beside the executables once the folder is assembled (end of file).
_LICENCE_FILES = ("LICENSE", "LICENSE-MIT", "THIRD_PARTY_NOTICES.md")
for _licence_file in _LICENCE_FILES:
    _p = project_root / _licence_file
    if _p.exists():
        datas.append((str(_p), "."))

_licence_dir = project_root / "licenses"
if _licence_dir.is_dir():
    for _p in sorted(_licence_dir.glob("*.txt")):
        datas.append((str(_p), "licenses"))

# asammdf asks canmatrix to load database format handlers by module name at
# runtime. PyInstaller cannot discover those dynamic imports automatically;
# without them the packaged app abandons native one-pass MF4 extraction and
# falls back to the slow frame-by-frame reader.
canmatrix_format_imports = collect_submodules("canmatrix.formats")

# ldfparser ships its lark grammars and jinja2 templates as package *data*,
# which PyInstaller does not collect automatically. Without these an LDF parses
# correctly in a source run and fails only inside the frozen build — the worst
# place to discover it. canmatrix's .ldf handler imports ldfparser at module
# load, so a missing grammar takes LIN decoding down with it.
datas += collect_data_files("ldfparser")

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "pyqtgraph",
    "can.io.blf",
    "can.io.asc",
    "asammdf",
    "canmatrix",
    "cantools.database",
    "cantools.database.can.formats.arxml",
    "lxml",
    # LIN database support: canmatrix.formats.ldf -> ldfparser -> lark/jinja2
    "ldfparser",
    "lark",
    "jinja2",
] + canmatrix_format_imports

block_cipher = None

# Qt is used under the LGPL, through PySide6 only. Kept out of the build:
_EXCLUDES = [
    # Every other Qt binding, and matplotlib, which pandas, asammdf and
    # pyqtgraph import only optionally.
    "PyQt5", "PyQt6", "PySide2", "shiboken2", "sip", "qtpy", "matplotlib",
    # Test tooling, reached only through numexpr's and pandas' own test
    # helpers. CI's build job never installs it; a local build that finds it
    # installed would otherwise ship pytest, and Pygments with it.
    "pytest", "_pytest",
    # asammdf's own viewer application. asammdf.signal imports it lazily, for
    # Signal.plot(), which this application never calls. Followed, it brings in
    # QtWebEngine, which imports QtQuick and QtQml, whose hook then collects
    # every QML module's DLLs — Qt Charts, Data Visualization, Graphs, Quick 3D,
    # Quick Timeline and Virtual Keyboard among them, which Qt licenses under the
    # GPL only. That made 120 Qt DLLs (300 MB) out of the 7 the app uses.
    "asammdf.gui",
    # Qt modules this application does not use, kept out by name as well so that
    # another dependency cannot pull the QML tree back in unnoticed.
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebView", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2", "PySide6.QtQuick3D",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets", "PySide6.QtVirtualKeyboard", "PySide6.QtNetworkAuth",
    "PySide6.QtHttpServer", "PySide6.QtMultimedia", "PySide6.QtLocation",
    "PySide6.QtPositioning", "PySide6.QtPdf", "PySide6.Qt3DCore",
    # The application makes no network connections. Without this, the QtNetwork
    # hook adds Qt's TLS plugins, and with them whatever OpenSSL DLLs the build
    # PC happens to have on PATH (Git for Windows' copy, on the dev PC).
    "PySide6.QtNetwork",
]

# Qt modules that Qt licenses under the GPL-3 (or commercially) only, never the
# LGPL: doc.qt.io/qt-6/licensing.html, plus the Qt Charts and Qt Data
# Visualization module pages. PySide6_Addons ships them all in one package.
# Matched against library names (see _library_name); a match anywhere in the
# finished folder fails the build (end of file). Lottie Animation's library is
# Qt6Bodymovin.
_GPL_ONLY_QT = re.compile(
    r"^(qt6(charts|datavisualization|graphs|quick3d|quicktimeline|virtualkeyboard|"
    r"coap|httpserver|bodymovin|mqtt|networkauth|grpc|qmlcompiler|waylandcompositor|"
    r"canvaspainter)\w*|qtvirtualkeyboardplugin|qtvkb\w*|qmldbg_quick3d\w*)$"
)

# Qt libraries nothing in this application loads, dropped for size (19 MB), not
# for licensing: all are LGPL. Quick and the four Qml libraries are there only
# for the Virtual Keyboard plugin dropped below; Pdf only for the PDF image
# format plugin (qpdf), and Network only for Pdf, Qml, Quick and the TUIO
# network touch plugin. On Linux the VNC platform plugin (qvnc) is the one
# other user of Network. The end of this file checks that nothing left needs them.
_UNUSED_QT = {
    "qt6quick", "qt6qml", "qt6qmlmodels", "qt6qmlmeta",
    "qt6qmlworkerscript", "qt6pdf", "qt6network",
    "qpdf", "qtuiotouchplugin", "qvnc",
}

# What a shared library or executable is called on each OS.
_LIBRARY_SUFFIXES = {".dll", ".pyd", ".exe", ".so", ".dylib"}


def _library_name(file_name):
    """Qt6Quick.dll, libQt6Quick.so.6 and libQt6Quick.6.dylib are all qt6quick."""
    stem = file_name.lower().split(".")[0]
    if stem.startswith("lib") and len(stem) > 3:
        stem = stem[3:]
    return stem


def _is_library(path):
    return bool(_LIBRARY_SUFFIXES.intersection(s.lower() for s in path.suffixes))


def _dropped(file_name):
    library = _library_name(file_name)
    return bool(_GPL_ONLY_QT.match(library)) or library in _UNUSED_QT


a = Analysis(
    [str(project_root / "app.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # cantools no longer depends on diskcache, so neither the cache nor its old
    # Dependabot alert (CVE, diskcache <=5.6.3) is in the build any more.
    excludes=_EXCLUDES,
    # python-can and asammdf are LGPL-3, so a user must be able to run the
    # application with a modified version of either. Inside the PYZ archive they
    # could not be replaced: the frozen importer looks there first. Collected as
    # .pyc files in _internal/can and _internal/asammdf instead, they can — a .py
    # placed beside a .pyc is imported in its place.
    module_collection_mode={"can": "pyc", "asammdf": "pyc"},
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# The QtGui hook collects every platform input context plugin, and with it the
# Virtual Keyboard (GPL-only, unused here). Drop anything GPL-only by name, and
# the Qt libraries nothing loads.
a.binaries = [entry for entry in a.binaries if not _dropped(Path(entry[0]).name)]
a.datas = [entry for entry in a.datas if not _dropped(Path(entry[0]).name)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BusLogAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_exe_icon,
)

# The same application with Python's memory checks on (-X dev): a buffer
# overrun is reported where it happens instead of crashing a later load. A
# packaged exe ignores PYTHONDEVMODE and PYTHONMALLOC, so a second exe is the
# only way to get them. It shares _internal with BusLogAnalyzer.exe and adds
# about 25 MB. The release test runs with it (gui/release_test), and a user
# can reproduce a crash with it for a bug report. gui/release_test/runner.py
# looks for it by this name.
memcheck_exe = EXE(
    pyz,
    a.scripts,
    [('X dev', None, 'OPTION')],
    exclude_binaries=True,
    name="appdebugger",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=_exe_icon,
)

coll = COLLECT(
    exe,
    memcheck_exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BusLogAnalyzer",
)

# A README beside the two executables, where the user sees it. Data files go
# into _internal, so it is copied after the folder is assembled.
_install_dir = Path(coll.name)
shutil.copyfile(project_root / "resources" / "release_readme.txt",
                _install_dir / "README.txt")

# The licence texts and the third-party notice beside the executables too, so
# whoever receives the folder sees them without opening _internal.
for _licence_file in _LICENCE_FILES:
    if (project_root / _licence_file).exists():
        shutil.copyfile(project_root / _licence_file, _install_dir / _licence_file)
if _licence_dir.is_dir():
    shutil.copytree(_licence_dir, _install_dir / "licenses", dirs_exist_ok=True)

# Refuse to produce a folder that ships a Qt module licensed under the GPL only,
# or any QML module: QML modules carry plugin DLLs this name check cannot
# classify, and the application uses no QML.
_gpl_only = sorted(p.relative_to(_install_dir).as_posix()
                   for p in _install_dir.rglob("*")
                   if p.is_file() and _GPL_ONLY_QT.match(_library_name(p.name)))
_qml = sorted(p.relative_to(_install_dir).as_posix()
              for p in _install_dir.rglob("qml") if p.is_dir())
if _gpl_only or _qml:
    raise SystemExit(
        "Build refused: the folder contains Qt modules licensed under the GPL only, "
        "or a QML tree. Qt is used under the LGPL, so these must not ship.\n  "
        + "\n  ".join(_gpl_only + _qml)
        + "\nFind what imports them (build/BusLogAnalyzer/xref-BusLogAnalyzer.html) "
        "and exclude it in BusLogAnalyzer.spec."
    )

# Refuse a folder in which something still links to a library dropped above:
# the OS would refuse to load it at run time, so the failure would otherwise
# reach a user first. Windows binaries are read with pefile, which comes with
# PyInstaller there; Linux ones with readelf (binutils) and macOS ones with
# otool, where the tool is installed. Without the tool the check is skipped
# and says so, rather than failing a build over a missing developer tool.


def _linked_libraries(binary):
    """The library names *binary* links to, or None if they cannot be read."""
    if sys.platform == "win32":
        try:
            _pe = pefile.PE(str(binary), fast_load=True)
        except pefile.PEFormatError:
            return []
        _pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        needed = [entry.dll.decode() for entry in getattr(_pe, "DIRECTORY_ENTRY_IMPORT", [])]
        _pe.close()
        return needed
    tool = ["otool", "-L"] if sys.platform == "darwin" else ["readelf", "-d"]
    if shutil.which(tool[0]) is None:
        return None
    result = subprocess.run(tool + [str(binary)], capture_output=True, text=True,
                            errors="replace")
    if result.returncode != 0:
        return []      # not a binary of this kind, for example a text file
    if sys.platform == "darwin":
        return [line.split()[0].rsplit("/", 1)[-1]
                for line in result.stdout.splitlines()[1:] if line.strip()]
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", result.stdout)


_needs_dropped = []
_link_check_skipped = False
for _binary in sorted(_install_dir.rglob("*")):
    if not _binary.is_file() or not _is_library(_binary):
        continue
    _needed_libraries = _linked_libraries(_binary)
    if _needed_libraries is None:
        _link_check_skipped = True
        break
    for _needed in _needed_libraries:
        if _dropped(_needed):
            _needs_dropped.append(
                f"{_binary.relative_to(_install_dir).as_posix()} needs {_needed}")
if _link_check_skipped:
    print("BusLogAnalyzer.spec: readelf/otool not found, so whether anything still "
          "links to a dropped Qt library was not checked.")
if _needs_dropped:
    raise SystemExit(
        "Build refused: something in the folder links to a library this spec drops.\n  "
        + "\n  ".join(_needs_dropped)
        + "\nTake that library out of _UNUSED_QT in BusLogAnalyzer.spec."
    )
