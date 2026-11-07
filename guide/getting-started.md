# Getting started

From a measurement file to your first plot in five minutes. The pictures show a
short demo drive: a made-up vehicle with four CAN messages on channel 1.

!!! tip "Prefer to watch?"
    The same steps, and more, are shown in the tutorial videos on
    [YouTube](https://www.youtube.com/@osvanta).

## Install

1. Download `BusLogAnalyzer_<version>_Windows.zip` from the
   [latest release](https://github.com/osvanta/bus-log-analyzer/releases/latest).
2. Extract it to a folder you can write to, such as one in your Documents. The
   application keeps its log files and your settings beside itself, so avoid
   `C:\Program Files`.
3. Run `BusLogAnalyzer.exe`.

!!! note "Windows protected your PC"
    The application is not signed yet, so Windows SmartScreen may stop it the
    first time. Click **More info**, then **Run anyway**.

## The top row

Everything starts from the row at the top of the window, read from left to right
in the order of the work.

![The top row, its eight items numbered](images/top-row-light.webp#only-light)
![The top row, its eight items numbered](images/top-row-dark.webp#only-dark)

| | Item | What it does |
|---|---|---|
| 1 | **File** | Open configuration, Save configuration (++ctrl+s++), Export plotted signals (++ctrl+e++), Save decoded measurement as MF4, Measurement info (++ctrl+i++) |
| 2 | **Open Measurement** | Opens a BLF, ASC, TRC, MF4, MDF or CSV file (++ctrl+o++) |
| 3 | **Databases** | Assigns DBC, ARXML and LDF databases to the bus channels |
| 4 | **Decode** | Reads the measurement and decodes it into signals (++f9++) |
| 5 | **Calculated Signal** | Creates a signal calculated from others by a formula |
| 6 | **Trace** | Shows the raw CAN and LIN frames (++ctrl+shift+r++) |
| 7 | **Tools** | Convert files: join, cut, resample and convert measurements, open or not |
| 8 | **Help** | This guide (++f1++), the keyboard shortcuts, where to ask a question or report a problem, the log folder, the user survey and About |

Items you cannot use yet are greyed out. The status bar at the bottom of the
window always says what to do next.

## 1. Open a measurement

Click **Open Measurement** (++ctrl+o++) and choose your file.

For a raw bus log, the application scans the file for its CAN and LIN channels,
so that the next step can show them.

!!! tip "Already decoded?"
    An MF4, MDF or CSV file that holds decoded signals needs no database. Go
    straight to [step 3](#3-decode).

## 2. Assign the databases

Click **Databases**. The Database Manager lists the channels it found in the
measurement.

![The Database Manager with a DBC assigned to CAN 1 and a full match](images/databases-light.webp#only-light)
![The Database Manager with a DBC assigned to CAN 1 and a full match](images/databases-dark.webp#only-dark)

1. Click **+ Add Database…** and choose your DBC, ARXML or LDF files.
2. Under **Assigned to**, choose the channel each database belongs to. **All
   CAN** (or **All LIN**) applies a database to every channel of that bus that
   has no database of its own.
3. Check **Match quality**: it shows how well the database fits the frames on
   that channel. A low match usually means the wrong channel or the wrong
   database.
4. Click **OK**.

To use the same assignment again, click **Save Channel Config…**. Next time,
**Load Channel Config…** restores it in one step.

## 3. Decode

Click **Decode** (++f9++). The status bar shows the progress, and the
**Log** below the plot reports what was read.

When it is done, the signal tree on the left lists every channel, message and
signal.

![After Decode: the signal tree lists the channel, its messages and signals](images/decoded-light.webp#only-light)
![After Decode: the signal tree lists the channel, its messages and signals](images/decoded-dark.webp#only-dark)

Type in **Search signals…** to find a signal: the text can be anywhere in its
name, in any case. `*` stands for any text and `?` for one character, so
`veh*speed` finds VehicleSpeed.

Right-click a signal and choose **Properties…** to see what the database says
about it: its message and ID, start bit, length, byte order, factor, offset,
range, unit, value table and comment, and which database file it came from.

**File ▸ Measurement info** (++ctrl+i++) describes the file itself: its format,
size, start time and duration, and for an MF4 its header, events and
attachments.

## 4. Plot

Plot a signal from the tree in any of these ways:

- double-click it;
- select one or more and press ++space++;
- drag them onto the plot;
- right-click them and choose **Plot selected signal(s)**.

![Four signals in Stacked mode, both cursors on](images/plot-light.webp#only-light)
![Four signals in Stacked mode, both cursors on](images/plot-dark.webp#only-dark)

**Plot modes**, the first three buttons above the plot:

- **Stacked**, the default, gives each signal its own row and scale.
- **Multi-Axis** draws the signals over each other, with an axis for each unit.
- **MultiStack** has rows that hold several signals: drag signals into a row to
  overlay them.

**Table**, the fourth button, lists the plotted signals as a table beside the
plot, a row per sample time.

**Cursors.** A click on the plot places Cursor 1, and ++shift+++click places
Cursor 2. The line under the plot shows both times and the time between them,
and the signal table shows each signal's value at each cursor.

**Signal table columns.** Right-click the table header to show or hide a
column: the cursor values, **Hex** (Cursor 1's value as the raw value on the
bus, in hexadecimal), **Δ C2−C1** (Cursor 2's value minus Cursor 1's), the
unit, and the statistics **Min**, **Max**, **Mean**, **Std dev**, **RMS** and
**Samples**. The statistics are between the cursors when both are on, and over
the visible range otherwise.

**Hex** turns the value back into what the bus carried: (value − offset) /
factor from the database, padded to the signal's length, so an 11-bit signal
reads `0x7FF` and an 8-bit signed −1 reads `0xFF`. A float signal shows its
IEEE 754 bits. A signal no database defines (a calculated signal, or an MF4
channel the file decoded itself) shows its value in hex when it is a whole
number, and nothing otherwise.

**Signal properties.** Right-click a signal in the tree or in the signal table
and choose **Properties…** to see what its database says about it: start bit,
length, byte order, factor and offset, range, unit, value table, its message
and the database file it came from. **Copy** puts it all on the clipboard, for
a bug report.

**Markers.** Right-click the plot and choose **Add marker here**, or press ++m++
to add one at Cursor 1. Drag a marker to move it, double-click it to rename
it, and right-click it to move Cursor 1 there or delete it. **Go to marker**
in the plot's right-click menu lists them: click a name to bring that marker
into view, or the waste bin just after its name to delete it. The list stays
open, so several go in a row of clicks, and ++delete++ deletes the highlighted
one. **Delete Markers**, the marker and waste bin icon above the plot, offers
**Delete all markers**. Markers are saved in the configuration, and an MF4
export keeps them as events.

**Look.** Right-click a signal in the table to change its colour (++c++), give
it a line style (solid, dashed, dotted or dash-dot), group it with others,
hide it, or remove it. The line-mode button (++s++) switches between **Step
Mode**, where each value is held until the next sample, and **Interpolation
Mode**, where straight lines join the samples.

**Trace.** The **Δt** column is the time since the previous frame with the
same ID on the same channel, and the bytes that changed since that frame are
bold and coloured; right-click the header to turn either off. While
**⇄ Follow Cursor 1** is on, picking a frame moves Cursor 1 to it, and moving
Cursor 1 brings its frame into view.

**Moving around.** ++f++ fits the whole recording, and ++v++ fits the height
only. With **Rectangle Zoom** (++z++), drag a box to zoom into it, ++shift+++drag
to zoom out, and double-click to see everything again. ++ctrl+z++ undoes the
last plot action.

## 5. Keep your work

- **File ▸ Save configuration** (++ctrl+s++) saves the session to a `.json`
  file: the measurement, the databases, calculated signals, the plotted signals
  with their colours, groups and order, the axes and the cursors. **File ▸ Open
  configuration** brings it all back.
- Closing with changes you have not saved asks whether to save them. Whatever
  you answer, the application keeps the session, and the next start offers to
  reopen it, even after a crash.
- **File ▸ Export plotted signals** (++ctrl+e++) writes the plotted signals to
  CSV or Excel, on the time base you choose, or to MF4.
- **Tools ▸ Convert files** joins, cuts and converts measurements. In
  **Choose signals**, **Save as…** keeps the ticked signals as a named
  selection that works on any file, and **Import .txt…** and **Export .txt…**
  share a list with others: one signal name a line, `*` and `?` allowed.

## Next

- Learn the [keyboard shortcuts](keyboard-shortcuts.md).
- Something not working? See [Help and troubleshooting](help.md).
