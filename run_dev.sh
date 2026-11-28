#!/bin/sh
# Start the application from source on Linux or macOS, the counterpart of
# run_dev.bat. Uses the project's .venv when there is one. What the console
# shows also goes to busloganalyzer_applog.log next to app.py.
cd "$(dirname "$0")" || exit 1

if [ -x .venv/bin/python ]; then
    PYEXE=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PYEXE=python3
else
    echo "Python was not found. Create .venv first or install Python 3.12 and add it to PATH."
    exit 1
fi

exec "$PYEXE" app.py "$@"
