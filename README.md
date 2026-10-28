# Osvanta Bus Log Analyzer

> A portable Windows application for loading automotive measurements, decoding CAN signals, inspecting raw frames, and plotting signal data interactively.

> **User guide:** [osvanta.github.io/bus-log-analyzer](https://osvanta.github.io/bus-log-analyzer/),
> or **Help ▸ Getting started** (`F1`) in the application.

> Formerly released as **CANScope**. The repository moved to
> [github.com/osvanta/bus-log-analyzer](https://github.com/osvanta/bus-log-analyzer)
> in August 2026.

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?logo=windows)
![License](https://img.shields.io/badge/License-MPL--2.0-green)
![Release](https://img.shields.io/github/v/release/osvanta/bus-log-analyzer)

## Screenshots

Stacked plot:
<img width="2299" height="1588" alt="Stacked plot" src="https://github.com/user-attachments/assets/57baedc2-0382-4f63-a7e9-cc786e18ded8" />


Multiple Axis plot:
<img width="2214" height="1590" alt="multi-axis plot" src="https://github.com/user-attachments/assets/7d3ef351-1b02-45e0-85f5-4dfd1217ad6e" />

Trace, the raw CAN and LIN frames:
<img width="1543" height="1019" alt="CANScope_RawCAN Frame" src="https://github.com/user-attachments/assets/69ad9749-ecec-445d-a31f-4b47901855cf" />

## Supported formats

| Measurement | Content | Database |
|---|---|---|
| BLF (`.blf`) | Raw CAN frames | DBC or ARXML required |
| ASC (`.asc`) | Raw CAN frames | DBC or ARXML required |
| TRC (`.trc`) | Raw CAN frames (PEAK trace, versions 1.0 to 3.0) | DBC or ARXML required |
| MDF4/MDF3 (`.mf4`, `.mdf`) | Raw CAN bus logging or pre-decoded signals | Required only for raw bus logging |
| CSV (`.csv`) | Raw CAN export, narrow signals, or wide signals | Required only for raw CAN data |

MDF and CSV content is detected automatically. Database Manager supports per-channel DBC/ARXML assignments, mixed database formats, decode-coverage indicators, and a channel-0 fallback.


## Current capabilities

- Fast bulk loading and decoding for BLF, ASC, TRC, MDF/MF4, and CSV measurements.
- Searchable channel/message/signal tree with multi-select plotting and drag-and-drop.
- Indexed **Trace** of the raw CAN and LIN frames, with filtering and on-demand signal decode. Its **Δt** column shows the time since the previous frame with the same ID on the same channel, the bytes that changed since that frame are highlighted, and it can follow plot Cursor 1 both ways.
- Formula-based **Calculated Signal** with arithmetic, comparisons, logical expressions, temporal operations, and a stateful diagnostic counter; **Insert function** (`Shift+F3`) lists every function by category, with how to use each. The definitions are saved in configurations.
- **Statistics** in the signal table: Min, Max, Mean, Std dev, RMS and Samples, over the visible range or between the cursors, and the **Δ C2−C1** difference between the cursors. Right-click the table header to choose the columns.
- **Table**: the plotted signals as a table, right of the plot, a row per sample time.
- Named **markers** on the plot: add one where you right-click or at Cursor 1 (`M`), drag, rename, go to; they are saved in configurations and written into MF4 exports as events.
- **File ▸ Measurement info** (`Ctrl+I`): the file's format, size, start time and duration, and for MF4 its header, events and attachments. MF4 events can be drawn on the plot.
- Export of the plotted signals to CSV or Excel (`.xlsx`), on a selected signal's timestamps or a recurrence time you enter, or to MF4; **File ▸ Save decoded measurement as MF4** writes every decoded signal.
- **Tools ▸ Convert files**: join, cut and resample measurements, keep chosen signals, and convert between BLF, ASC, MF4 and CSV (TRC as an input). Signal selections can be saved by name and reused on other files, or shared as text files.
- JSON configuration save/load for measurement paths, database mapping, calculated signals, plotted order, groups, colors, visibility, axes, cursors, markers, and display settings.
- YAML rule-based diagnostics for expressions, ranges, fault flags, and message loss; optional GitHub Models analysis is available through `Ctrl+Shift+A`.

### Plotting

- **Stacked** is the default mode and gives each signal its own lane and Y scale.
- **Multi-Axis** overlays the signals, with one axis per unit and an optional individual axis per signal.
- **MultiStack** has lanes that each hold several signals; drag signals into a lane to overlay them there.
- Dual draggable cursors provide values and delta time; a click on the plot places Cursor 1 there and a Shift+click places Cursor 2. A cursor switched on always appears inside the visible window, and cursors are black on a light plot background and white on a dark one.
- Fit-to-window and vertical-fit work across all plot modes while preserving the relevant view range.
- After the plot mode buttons, Fit to Window, Fit Vertical, the cursors, the line mode, Show Data Points and Hide Line are black-and-white icons; pointing at one shows its name.
- Signals can be grouped, reordered by dragging, recolored, hidden, or removed.
- The line-mode button (`S`) switches between **Step Mode**, the default, where each value is held until the next sample, and **Interpolation Mode**, where straight lines join the samples. Its icon shows the mode the plot is in.
- **Show Data Points** uses adaptive markers and thinner lines.
- **Hide Line** is enabled only when data points are shown; when ON, it displays points without signal lines.

## Install and run

### Portable release

Download the latest Windows ZIP from [Releases](https://github.com/osvanta/bus-log-analyzer/releases), extract it, and run `BusLogAnalyzer.exe`. No installer or Python installation is required.

### From source

```powershell
git clone https://github.com/osvanta/bus-log-analyzer.git
cd bus-log-analyzer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

The portable build and CI release build use Python 3.12.

## Basic workflow

1. Select **Open Measurement** and choose a supported measurement.
2. If the file contains raw CAN frames, select **Databases** and configure DBC/ARXML mapping.
3. Select **Decode** (`F9`).
4. Search the signal tree, then double-click, drag, press `Space`, or use the context menu to plot signals.
5. Use cursors, plot modes, data points, grouping, and fit controls as needed.
6. In the **File** menu, select **Save configuration** to preserve the session, or **Export plotted signals** to write them to CSV, Excel or MF4.
7. Open the **Trace** when the source contains raw CAN frames.
8. The **Help** menu has Getting started (`F1`), the keyboard shortcuts, GitHub Discussions for questions and ideas, GitHub issues for problems and feature requests, and About.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `F1` | Getting started, in the user guide |
| `Ctrl+O` | Open Measurement |
| `F9` | Decode |
| `Ctrl+S` | Save configuration |
| `Ctrl+E` | Export plotted signals |
| `Ctrl+I` | Measurement info |
| `Ctrl+Shift+R` | Open the Trace |
| `Shift+F3` | Insert function, in Calculated Signal |
| `F` | Fit X and Y to all visible data |
| `V` | Fit Y to the current X range |
| `Z` / `Esc` | Rectangle Zoom on or off / leave it |
| `S` | Step Mode or Interpolation Mode |
| `Space` | Plot selected signal-tree entries |
| `C` | Change the selected signal color |
| `R` | Toggle both cursors |
| `M` | Add a marker at Cursor 1 |
| `Delete` | Remove selected plotted signals |
| `Ctrl+Z` | Undo the last plot action |
| `Ctrl+Shift+-` / `Ctrl+Shift++` | Collapse / expand signal-tree messages |


## Test and build

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Build the portable application with:

```powershell
.\build_portable.bat
```

Output is written to `dist\BusLogAnalyzer\`. Pushing a `v*.*.*` tag runs the Windows build workflow and publishes a release ZIP.

## Repository layout

```text
app.py                 Application entry point and version
core/readers/          BLF, ASC, TRC, MDF/MF4, and CSV readers
core/                  Decode, signal storage, export, and generated-signal logic
core/diagnostics/      Rule engine and optional AI-assisted diagnostics
gui/                   Main window, plots, database manager, CAN Trace, diagnostics
config/diagnostics/    User-editable diagnostic rules and documentation
resources/             Application icon and splash assets
guide/, mkdocs.yml     User guide, published to GitHub Pages
tests/                 Pytest regression suite
```

Runtime dependencies are maintained in [requirements.txt](requirements.txt).

## Security and license

Release binaries are currently unsigned, so Windows SmartScreen may show a warning on first launch.

Osvanta Bus Log Analyzer is licensed under the [Mozilla Public License 2.0](LICENSE).

MPL-2.0 is a file-level copyleft license. You may use it freely, including
inside commercial and proprietary products, and you may combine these files with
files of your own under any terms you choose. The one obligation is reciprocal:
if you distribute a modified version of a file that carries the MPL notice, the
source of that modified file must be made available under the MPL.

Releases up to and including `v00.00.59` were published under the MIT License and
remain available under those terms — see [LICENSE-MIT](LICENSE-MIT). The change to
MPL-2.0 applies from `v00.00.60` onward.

Osvanta Bus Log Analyzer bundles third-party components under their own licenses, including
LGPL-3.0 components (Qt via PySide6, python-can, asammdf, chardet). They are used
unmodified and remain available from their upstream projects. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full component list, and
`licenses/` for the complete license texts — both ship inside the portable build.

"Osvanta", "Osvanta Bus Log Analyzer", and the earlier names "CAN Scope" and
"CANScope" are names used by this project. The license grants rights to the
code, not to the project name.
