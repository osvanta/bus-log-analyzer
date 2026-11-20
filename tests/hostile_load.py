# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Copyright (c) 2025-2026 Dinakaran Ganesan

"""Open a measurement the way the application does, in a child process.

For files built to make a reader loop for ever: a regression then fails the
test when the time limit kills the child, instead of freezing the test run.
The child runs what Open Measurement and Decode run (the channel pre-scan,
then the load worker), and reports what came out as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Enough for the child's imports on a busy machine; a hang needs for ever.
TIME_LIMIT_S = 90
#: A reader that hangs may also take memory without end; the child gets this.
MEMORY_LIMIT_MB = 1024

_CHILD = r"""
import json, sys
from PySide6.QtCore import QCoreApplication
app = QCoreApplication.instance() or QCoreApplication([])

import core.readers as readers
from core.channel_config import ChannelConfig
from gui.load_worker import LoadWorker

measurement = sys.argv[1]
database = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
out = {}

scan = readers.prescan_measurement(measurement)
out["channels"] = [[bus.name, number] for bus, number in scan.channels]
out["ids"] = sorted({i for ids in scan.ids_per_channel.values() for i in ids})

done = {}
config = ChannelConfig.from_single_dbc(database) if database else None
worker = LoadWorker(measurement, config)
worker.finished.connect(lambda store: done.__setitem__("store", store))
worker.failed.connect(lambda text: done.__setitem__("failed", text))
worker.run()

store = done.get("store")
if "failed" in done:
    out["outcome"] = "refused"
    out["message"] = done["failed"][:2000]
elif store is None:
    out["outcome"] = "nothing"
else:
    out["outcome"] = "loaded"
    out["signals"] = {}
    for key in store.all_keys():
        series = store.get_series(key)
        out["signals"][str(getattr(series, "name", key))] = len(series.numpy_timestamps())
    trace = getattr(store, "raw_frame_store", None)
    out["frames"] = len(trace) if trace is not None else 0
    out["warnings"] = list(getattr(store, "load_warnings", ()) or ())
    store.close()
print("RESULT " + json.dumps(out), flush=True)
"""


def load_in_child(path: Path, database: Path | None, work: Path,
                  time_limit: float = TIME_LIMIT_S) -> dict:
    """Pre-scan and load *path*; return the child's report.

    Fails the calling test when the child does not finish in *time_limit*
    seconds or ends without a report.
    """
    temp = work / "child_temp"
    data = work / "child_data"
    for folder in (temp, data):
        folder.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ, PYTHONPATH=str(REPO_ROOT), QT_QPA_PLATFORM="offscreen",
        TEMP=str(temp), TMP=str(temp), OSVANTA_DATA_DIR=str(data),
    )
    stdout, stderr, code = run_capped(
        ["-c", _CHILD, str(path), str(database or "")], env, time_limit,
        f"opening {path.name}",
    )
    for line in stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    raise AssertionError(
        f"the load of {path.name} ended without a result (exit {code}): "
        f"{stderr[-3000:]}"
    )


def run_capped(arguments: list[str], env: dict | None = None,
               time_limit: float = TIME_LIMIT_S,
               what: str = "the child") -> tuple[str, str, int]:
    """Run Python with *arguments* under the time and memory limits.

    Returns its output, error output and exit code; fails the calling test
    when the time limit ends it.
    """
    env = env or dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    process = subprocess.Popen(
        [sys.executable, *arguments], cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    job = _limit_memory(process)
    try:
        stdout, stderr = process.communicate(timeout=time_limit)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, _stderr = process.communicate()
        raise AssertionError(
            f"{what} did not finish in {time_limit} s: a reader hangs. "
            f"Output before it: {stdout[-500:]!r}"
        ) from None
    finally:
        _release(job)
    return stdout, stderr, process.returncode


def _limit_memory(process: subprocess.Popen):
    """Put *process* in a Windows job with a memory limit; None elsewhere."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("Basic", Basic),
            ("Io", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.LimitFlags = 0x100 | 0x2000   # process memory, kill on close
    info.ProcessMemoryLimit = MEMORY_LIMIT_MB * 1024 * 1024
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    kernel32.AssignProcessToJobObject(job, int(process._handle))
    return kernel32, job


def _release(job) -> None:
    if job is not None:
        kernel32, handle = job
        kernel32.CloseHandle(handle)
