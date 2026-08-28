# Third-Party Notices — Osvanta Bus Log Analyzer

Osvanta Bus Log Analyzer itself is licensed under the Mozilla Public License 2.0 (see `LICENSE`).
This file covers the third-party components redistributed with the portable build.

Full licence texts for every component listed here are in the `licenses/` directory
next to this file. Nothing in this document modifies or limits the terms of those
licences; where this summary and a licence text disagree, the licence text governs.

Generated for Osvanta Bus Log Analyzer v00.01.00 against the dependency set in `requirements.txt`.
Last reviewed 2026-08-12.

---

## 1. Components under the GNU Lesser General Public License

The following components are used under the **LGPL**. Osvanta Bus Log Analyzer does not modify any
of them — they are redistributed exactly as published by their upstream projects.

| Component | Version | License | Project |
|---|---|---|---|
| `asammdf` | 8.8.6 | LGPL-3.0-or-later | [link](https://github.com/danielhrisca/asammdf) |
| `chardet` | 7.4.3 | LGPL-2.1-or-later | [link](https://github.com/chardet/chardet) |
| `PySide6` | 6.11.0 | LGPL-3.0-only | [link](https://pyside.org) |
| `PySide6_Addons` | 6.11.0 | LGPL-3.0-only | [link](https://pyside.org) |
| `PySide6_Essentials` | 6.11.0 | LGPL-3.0-only | [link](https://pyside.org) |
| `python-can` | 4.6.1 | LGPL-3.0-only | [link](https://github.com/hardbyte/python-can) |
| `shiboken6` | 6.11.0 | LGPL-3.0-only | [link](https://pyside.org) |

`PySide6`, `PySide6_Essentials`, `PySide6_Addons` and `shiboken6` are the Qt for Python
bindings and the Qt libraries they wrap. Qt is dual-licensed; **Osvanta Bus Log Analyzer uses Qt under
the LGPL-3.0**, not under a commercial Qt licence and not under the GPL.

LGPL-3.0 is written as a set of additional permissions on top of the GNU GPL-3.0, so
both texts are required and both are provided:

- `licenses/GNU-LGPL-3.0.txt`
- `licenses/GNU-GPL-3.0.txt`
- `licenses/LGPL-2.1-or-later` terms for `chardet` are in `licenses/chardet-*.txt`

### Your right to replace these libraries

The LGPL gives you the right to run Osvanta Bus Log Analyzer against your own build of any of these
libraries. The portable build supports this:

- **Qt** ships as individual DLLs (`Qt6Core.dll`, `Qt6Gui.dll`, `Qt6Widgets.dll`, …) in
  the application directory. Replace them with interface-compatible builds of your own
  and the application will load yours instead.
- **python-can**, **asammdf** and **chardet** are pure Python. They can be replaced by
  placing your own copies in the application directory, which takes precedence over the
  bundled archive.

### Obtaining the source

Every LGPL component above is unmodified and its complete source for the exact version
shipped is available from its project page linked in the table, and from PyPI at
`https://pypi.org/project/<name>/<version>/#files`.

If you would prefer to receive the corresponding source directly, open an issue at
https://github.com/osvanta/bus-log-analyzer/issues and it will be provided.

---

## 2. Components under permissive licences

| Component | Version | License | Project |
|---|---|---|---|
| `argparse-addons` | 0.12.0 | MIT License | [link](https://github.com/eerimoq/argparse_addons) |
| `attrs` | 26.1.0 | MIT | [link](https://tidelift.com/subscription/pkg/pypi-attrs?utm_source=pypi-attrs&utm_medium=pypi) |
| `bitstruct` | 8.22.1 | MIT | [link](https://github.com/eerimoq/bitstruct) |
| `canmatrix` | 1.2 | BSD-2-Clause | [link](http://github.com/ebroecker/canmatrix) |
| `cantools` | 41.3.0 | MIT | [link](https://github.com/cantools/cantools) |
| `certifi` | 2025.10.5 | MPL-2.0 | [link](https://github.com/certifi/python-certifi) |
| `charset-normalizer` | 3.4.4 | see bundled text |  |
| `click` | 8.3.2 | BSD-3-Clause | [link](https://github.com/pallets/click/) |
| `colorama` | 0.4.6 | BSD-3-Clause | [link](https://github.com/tartley/colorama) |
| `crccheck` | 1.3.1 | MIT | [link](https://github.com/MartinScharrer/crccheck) |
| `deflate` | 0.8.1 | MIT License | [link](https://github.com/dcwatson/deflate) |
| `diskcache` | 5.6.3 | Apache-2.0 | [link](http://www.grantjenks.com/docs/diskcache/) |
| `et_xmlfile` | 2.0.0 | MIT | [link](https://foss.heptapod.net/openpyxl/et_xmlfile) |
| `idna` | 3.11 | BSD-3-Clause | [link](https://github.com/kjd/idna) |
| `isal` | 1.8.0 | PSF-2.0 | [link](https://github.com/pycompression/python-isal) |
| `lxml` | 6.0.4 | BSD-3-Clause | [link](https://lxml.de/) |
| `lz4` | 4.4.5 | BSD-3-Clause | [link](https://github.com/python-lz4/python-lz4) |
| `numexpr` | 2.14.1 | MIT | [link](https://github.com/pydata/numexpr) |
| `numpy` | 2.2.6 | BSD-3-Clause | [link](https://numpy.org) |
| `openpyxl` | 3.1.5 | MIT | [link](https://openpyxl.readthedocs.io) |
| `packaging` | 26.0 | Apache-2.0 OR BSD-2-Clause | [link](https://github.com/pypa/packaging) |
| `pandas` | 2.3.3 | BSD-3-Clause | [link](https://pandas.pydata.org) |
| `pyqtgraph` | 0.14.0 | MIT | [link](http://www.pyqtgraph.org) |
| `python-dateutil` | 2.9.0.post0 | Apache-2.0 AND BSD-3-Clause | [link](https://github.com/dateutil/dateutil) |
| `pytz` | 2025.2 | MIT | [link](http://pythonhosted.org/pytz) |
| `PyYAML` | 6.0.3 | MIT | [link](https://pyyaml.org/) |
| `requests` | 2.32.5 | Apache-2.0 | [link](https://requests.readthedocs.io) |
| `six` | 1.17.0 | MIT | [link](https://github.com/benjaminp/six) |
| `textparser` | 0.24.0 | MIT License | [link](https://github.com/eerimoq/textparser) |
| `typing_extensions` | 4.15.0 | PSF-2.0 | [link](https://github.com/python/typing_extensions) |
| `tzdata` | 2025.2 | Apache-2.0 | [link](https://github.com/python/tzdata) |
| `urllib3` | 2.5.0 | MIT |  |
| `wrapt` | 1.17.3 | BSD-2-Clause | [link](https://github.com/GrahamDumpleton/wrapt) |
| `zstd` | 1.5.7.3 | BSD-3-Clause | [link](https://github.com/sergey-dryabzhinsky/python-zstd) |

---

## 3. Licence texts

| File | Covers |
|---|---|
| `licenses/GNU-LGPL-3.0.txt` | LGPL-3.0 components in section 1 |
| `licenses/GNU-GPL-3.0.txt` | Required by, and incorporated into, LGPL-3.0 |
| `licenses/MPL-2.0.txt` | Osvanta Bus Log Analyzer itself, and `certifi` |
| `licenses/<name>-<version>.txt` | The licence text shipped by that component |

---

## 4. Build tooling (not redistributed)

The portable build is produced with **PyInstaller**, licensed GPL-2.0-or-later with an
explicit exception permitting the building and distribution of non-free programs.
PyInstaller's own code is not part of the shipped application beyond its bootloader,
which is covered by that exception.
