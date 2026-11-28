#!/bin/sh
# Build the application folder on Linux or macOS, the counterpart of
# build_portable.bat. The output is dist/BusLogAnalyzer/, with the
# BusLogAnalyzer and appdebugger executables beside _internal.
cd "$(dirname "$0")" || exit 1

if [ -x .venv/bin/python ]; then
    PYEXE=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PYEXE=python3
else
    echo "Python was not found. Create .venv first or install Python 3.12 and add it to PATH."
    exit 1
fi

"$PYEXE" -m PyInstaller BusLogAnalyzer.spec --noconfirm || {
    echo
    echo "Build failed."
    exit 1
}

if [ ! -x dist/BusLogAnalyzer/BusLogAnalyzer ]; then
    echo
    echo "Build completed but dist/BusLogAnalyzer/BusLogAnalyzer was not found."
    exit 1
fi

echo
echo "Build successful."
echo "Output: dist/BusLogAnalyzer/"
