# Third-Party Notices — Osvanta Bus Log Analyzer

Osvanta Bus Log Analyzer itself is licensed under the Mozilla Public License 2.0 (see `LICENSE`).
This file covers the third-party components redistributed with the portable build.

Full licence texts for every component listed here are in the `licenses/` directory
next to this file. Nothing in this document modifies or limits the terms of those
licences; where this summary and a licence text disagree, the licence text governs.

Generated from the contents of the portable build itself: every component below is in the
build, and nothing in the build is missing from it. Last reviewed 2026-10-09.

---

## 1. Components under the GNU Lesser General Public License

The following components are used under the **LGPL**. Osvanta Bus Log Analyzer does not modify any
of them — they are redistributed exactly as published by their upstream projects.

| Component | Version | Licence | Project |
|---|---|---|---|
| `asammdf` | 8.8.27 | LGPL-3.0-or-later | [link](https://github.com/danielhrisca/asammdf) |
| `PySide6` | 6.11.2 | LGPL-3.0-only | [link](https://pyside.org) |
| `PySide6_Addons` | 6.11.2 | LGPL-3.0-only | [link](https://pyside.org) |
| `PySide6_Essentials` | 6.11.2 | LGPL-3.0-only | [link](https://pyside.org) |
| `python-can` | 4.6.1 | LGPL-3.0-only | [link](https://github.com/hardbyte/python-can) |
| `shiboken6` | 6.11.2 | LGPL-3.0-only | [link](https://pyside.org) |

`PySide6`, `PySide6_Essentials`, `PySide6_Addons` and `shiboken6` are the Qt for Python
bindings and the Qt libraries they wrap. Qt is offered under the LGPL-3.0, the GPL-2.0, the
GPL-3.0 or a commercial licence; **Osvanta Bus Log Analyzer uses Qt under the LGPL-3.0 only.**
The build contains no Qt module that Qt makes available under the GPL only (such as Qt Charts,
Qt Data Visualization, Qt Graphs, Qt Quick 3D or Qt Virtual Keyboard): the build refuses to
complete if one appears.

LGPL-3.0 is written as a set of additional permissions on top of the GNU GPL-3.0, so
both texts are required and both are provided:

- `licenses/GNU-LGPL-3.0.txt` (also reproduced at the end of this file)
- `licenses/GNU-GPL-3.0.txt`

### Your right to replace these libraries

The LGPL gives you the right to run Osvanta Bus Log Analyzer with a modified version of any of
these libraries. The portable build supports this without any special tool:

- **Qt and PySide6** ship as separate files: the Qt libraries (`Qt6Core.dll`, `Qt6Gui.dll`,
  `Qt6Widgets.dll`, …) and the bindings (`QtCore.pyd`, …) in `_internal\PySide6\`, and
  `shiboken6` in `_internal\shiboken6\`. Replace them with interface-compatible builds of
  your own and the application loads yours.
- **python-can** and **asammdf** ship as compiled Python files (`.pyc`) in `_internal\can\`
  and `_internal\asammdf\`, outside the executable. To use a modified version, put your `.py`
  file beside the `.pyc` it replaces — Python imports the `.py` in its place — or replace the
  folder with your own build of the same version. asammdf's compiled extension modules
  (`.pyd`) in the same folder can be rebuilt from its source.

### Obtaining the source

Every LGPL component above is unmodified and its complete source for the exact version
shipped is available from its project page linked in the table, and from PyPI at
`https://pypi.org/project/<name>/<version>/#files`.

If you would prefer to receive the corresponding source directly, open an issue at
https://github.com/osvanta/bus-log-analyzer/issues and it will be provided.

### Third-party code inside Qt

Qt itself contains third-party code under its own licences — among them zlib, PCRE2,
FreeType, HarfBuzz and libpng. The list for each Qt version, with every licence, is at
https://doc.qt.io/qt-6/licenses-used-in-qt.html (this build ships Qt 6.11.2).

FreeType is used under the FreeType Project License, which asks for this credit:
Portions of this software are copyright © 2026 The FreeType Project (www.freetype.org).
All rights reserved.

---

## 2. The Python runtime and Microsoft's runtime libraries

The build contains the CPython 3.12 runtime and parts of its standard library,
under the **Python Software Foundation License**. Its licence, `licenses/Python-3.12.txt`,
also carries the notices for the libraries the Windows build of Python includes (such as
OpenSSL, libffi, zlib and bzip2) and Microsoft's terms for the Visual C++ runtime it uses.

The build also contains Microsoft's Visual C++ and Universal C runtime libraries
(`vcruntime140.dll`, `ucrtbase.dll` and the `api-ms-win-*.dll` forwarders), which Microsoft
permits applications to redistribute alongside themselves.

---

## 3. Components under permissive licences

| Component | Version | Licence | Project |
|---|---|---|---|
| `attrs` | 26.1.0 | MIT | [link](https://github.com/python-attrs/attrs) |
| `bitstruct` | 8.23.0 | MIT | [link](https://github.com/eerimoq/bitstruct) |
| `canmatrix` | 1.2 | BSD-2-Clause | [link](http://github.com/ebroecker/canmatrix) |
| `cantools` | 44.2.1 | MIT | [link](https://github.com/cantools/cantools) |
| `chardet` | 7.6.0 | 0BSD | [link](https://github.com/chardet/chardet) |
| `charset-normalizer` | 3.5.2 | MIT | [link](https://charset-normalizer.readthedocs.io/) |
| `colorama` | 0.4.6 | BSD-3-Clause | [link](https://github.com/tartley/colorama) |
| `deflate` | 0.9.0 | MIT | [link](https://github.com/dcwatson/deflate) |
| `et_xmlfile` | 2.0.0 | MIT | [link](https://foss.heptapod.net/openpyxl/et_xmlfile) |
| `isal` | 1.8.0 | PSF-2.0 | [link](https://github.com/pycompression/python-isal) |
| `Jinja2` | 3.1.6 | BSD-3-Clause | [link](https://github.com/pallets/jinja/) |
| `lark` | 1.3.1 | MIT | [link](https://github.com/lark-parser/lark) |
| `ldfparser` | 0.26.0 | MIT | [link](https://github.com/c4deszes/ldfparser) |
| `lxml` | 6.1.3 | BSD-3-Clause | [link](https://github.com/lxml/lxml) |
| `lz4` | 4.4.5 | BSD-3-Clause | [link](https://github.com/python-lz4/python-lz4) |
| `MarkupSafe` | 3.0.4 | BSD-3-Clause | [link](https://github.com/pallets/markupsafe/) |
| `numexpr` | 2.14.2 | MIT | [link](https://github.com/pydata/numexpr) |
| `numpy` | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [link](https://numpy.org) |
| `openpyxl` | 3.1.5 | MIT | [link](https://foss.heptapod.net/openpyxl/openpyxl) |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause | [link](https://github.com/pypa/packaging) |
| `pandas` | 3.0.6 | BSD-3-Clause | [link](https://pandas.pydata.org) |
| `pyqtgraph` | 0.14.0 | MIT | [link](https://github.com/pyqtgraph/pyqtgraph) |
| `python-dateutil` | 2.9.0.post0 | Apache-2.0 AND BSD-3-Clause | [link](https://github.com/dateutil/dateutil) |
| `PyYAML` | 6.0.3 | MIT | [link](https://github.com/yaml/pyyaml) |
| `setuptools` | 84.0.0 | MIT | [link](https://github.com/pypa/setuptools) |
| `six` | 1.17.0 | MIT | [link](https://github.com/benjaminp/six) |
| `textparser` | 0.26.2 | MIT | [link](https://github.com/cantools/textparser) |
| `typing_extensions` | 4.16.0 | PSF-2.0 | [link](https://github.com/python/typing_extensions) |
| `tzdata` | 2026.5 | Apache-2.0 | [link](https://github.com/python/tzdata) |
| `wrapt` | 1.17.3 | BSD-2-Clause | [link](https://wrapt.readthedocs.io/) |
| `zstd` | 1.5.7.2 | BSD-3-Clause | [link](https://github.com/sergey-dryabzhinsky/python-zstd) |

---

## 4. Build tooling

The portable build is produced with **PyInstaller** 6.22.3, licensed
GPL-2.0-or-later with an explicit exception that permits building and distributing non-free
programs. Two parts of it are inside the shipped application: its bootloader and loader, which that
exception covers, and its run-time hooks, which are licensed under the Apache License 2.0 — as are
the run-time hooks of `pyinstaller-hooks-contrib` 2026.8.
Both licence files are in `licenses/`. No other part of PyInstaller is redistributed.

---

## 5. Licence texts

| File | Covers |
|---|---|
| `licenses/GNU-LGPL-3.0.txt` | LGPL-3.0 components in section 1 |
| `licenses/GNU-GPL-3.0.txt` | Required by, and incorporated into, LGPL-3.0 |
| `licenses/MPL-2.0.txt` | Osvanta Bus Log Analyzer itself |
| `licenses/Python-3.12.txt` | The Python runtime (section 2) |
| `licenses/<name>-<version>.txt` | The licence text shipped by that component |

---

## Appendix: GNU Lesser General Public License, version 3

```text
                   GNU LESSER GENERAL PUBLIC LICENSE
                       Version 3, 29 June 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.


  This version of the GNU Lesser General Public License incorporates
the terms and conditions of version 3 of the GNU General Public
License, supplemented by the additional permissions listed below.

  0. Additional Definitions.

  As used herein, "this License" refers to version 3 of the GNU Lesser
General Public License, and the "GNU GPL" refers to version 3 of the GNU
General Public License.

  "The Library" refers to a covered work governed by this License,
other than an Application or a Combined Work as defined below.

  An "Application" is any work that makes use of an interface provided
by the Library, but which is not otherwise based on the Library.
Defining a subclass of a class defined by the Library is deemed a mode
of using an interface provided by the Library.

  A "Combined Work" is a work produced by combining or linking an
Application with the Library.  The particular version of the Library
with which the Combined Work was made is also called the "Linked
Version".

  The "Minimal Corresponding Source" for a Combined Work means the
Corresponding Source for the Combined Work, excluding any source code
for portions of the Combined Work that, considered in isolation, are
based on the Application, and not on the Linked Version.

  The "Corresponding Application Code" for a Combined Work means the
object code and/or source code for the Application, including any data
and utility programs needed for reproducing the Combined Work from the
Application, but excluding the System Libraries of the Combined Work.

  1. Exception to Section 3 of the GNU GPL.

  You may convey a covered work under sections 3 and 4 of this License
without being bound by section 3 of the GNU GPL.

  2. Conveying Modified Versions.

  If you modify a copy of the Library, and, in your modifications, a
facility refers to a function or data to be supplied by an Application
that uses the facility (other than as an argument passed when the
facility is invoked), then you may convey a copy of the modified
version:

   a) under this License, provided that you make a good faith effort to
   ensure that, in the event an Application does not supply the
   function or data, the facility still operates, and performs
   whatever part of its purpose remains meaningful, or

   b) under the GNU GPL, with none of the additional permissions of
   this License applicable to that copy.

  3. Object Code Incorporating Material from Library Header Files.

  The object code form of an Application may incorporate material from
a header file that is part of the Library.  You may convey such object
code under terms of your choice, provided that, if the incorporated
material is not limited to numerical parameters, data structure
layouts and accessors, or small macros, inline functions and templates
(ten or fewer lines in length), you do both of the following:

   a) Give prominent notice with each copy of the object code that the
   Library is used in it and that the Library and its use are
   covered by this License.

   b) Accompany the object code with a copy of the GNU GPL and this license
   document.

  4. Combined Works.

  You may convey a Combined Work under terms of your choice that,
taken together, effectively do not restrict modification of the
portions of the Library contained in the Combined Work and reverse
engineering for debugging such modifications, if you also do each of
the following:

   a) Give prominent notice with each copy of the Combined Work that
   the Library is used in it and that the Library and its use are
   covered by this License.

   b) Accompany the Combined Work with a copy of the GNU GPL and this license
   document.

   c) For a Combined Work that displays copyright notices during
   execution, include the copyright notice for the Library among
   these notices, as well as a reference directing the user to the
   copies of the GNU GPL and this license document.

   d) Do one of the following:

       0) Convey the Minimal Corresponding Source under the terms of this
       License, and the Corresponding Application Code in a form
       suitable for, and under terms that permit, the user to
       recombine or relink the Application with a modified version of
       the Linked Version to produce a modified Combined Work, in the
       manner specified by section 6 of the GNU GPL for conveying
       Corresponding Source.

       1) Use a suitable shared library mechanism for linking with the
       Library.  A suitable mechanism is one that (a) uses at run time
       a copy of the Library already present on the user's computer
       system, and (b) will operate properly with a modified version
       of the Library that is interface-compatible with the Linked
       Version.

   e) Provide Installation Information, but only if you would otherwise
   be required to provide such information under section 6 of the
   GNU GPL, and only to the extent that such information is
   necessary to install and execute a modified version of the
   Combined Work produced by recombining or relinking the
   Application with a modified version of the Linked Version. (If
   you use option 4d0, the Installation Information must accompany
   the Minimal Corresponding Source and Corresponding Application
   Code. If you use option 4d1, you must provide the Installation
   Information in the manner specified by section 6 of the GNU GPL
   for conveying Corresponding Source.)

  5. Combined Libraries.

  You may place library facilities that are a work based on the
Library side by side in a single library together with other library
facilities that are not Applications and are not covered by this
License, and convey such a combined library under terms of your
choice, if you do both of the following:

   a) Accompany the combined library with a copy of the same work based
   on the Library, uncombined with any other library facilities,
   conveyed under the terms of this License.

   b) Give prominent notice with the combined library that part of it
   is a work based on the Library, and explaining where to find the
   accompanying uncombined form of the same work.

  6. Revised Versions of the GNU Lesser General Public License.

  The Free Software Foundation may publish revised and/or new versions
of the GNU Lesser General Public License from time to time. Such new
versions will be similar in spirit to the present version, but may
differ in detail to address new problems or concerns.

  Each version is given a distinguishing version number. If the
Library as you received it specifies that a certain numbered version
of the GNU Lesser General Public License "or any later version"
applies to it, you have the option of following the terms and
conditions either of that published version or of any later version
published by the Free Software Foundation. If the Library as you
received it does not specify a version number of the GNU Lesser
General Public License, you may choose any version of the GNU Lesser
General Public License ever published by the Free Software Foundation.

  If the Library as you received it specifies that a proxy can decide
whether future versions of the GNU Lesser General Public License shall
apply, that proxy's public statement of acceptance of any version is
permanent authorization for you to choose that version for the
Library.
```
