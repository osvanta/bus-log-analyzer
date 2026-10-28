# Getting started

From a measurement file to your first plot in five minutes. The pictures show a
short demo drive: a made-up vehicle with four CAN messages on channel 1.

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

![The top row, its seven items numbered](images/top-row-light.webp#only-light)
![The top row, its seven items numbered](images/top-row-dark.webp#only-dark)

| | Item | What it does |
|---|---|---|
| 1 | **File** | Open configuration, Save configuration (++ctrl+s++), Export plotted signals (++ctrl+e++) |
| 2 | **Open Measurement** | Opens a BLF, ASC, TRC, MF4, MDF or CSV file (++ctrl+o++) |
| 3 | **Databases** | Assigns DBC, ARXML and LDF databases to the bus channels |
| 4 | **Decode** | Reads the measurement and decodes it into signals (++f9++) |
| 5 | **Calculated Signal** | Creates a signal calculated from others by a formula |
| 6 | **Trace** | Shows the raw CAN and LIN frames (++ctrl+shift+r++) |
| 7 | **Help** | This guide (++f1++), the keyboard shortcuts, and where to ask a question or report a problem |

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

## 4. Plot

Plot a signal from the tree in any of these ways:

- double-click it;
- select one or more and press ++space++;
- drag them onto the plot;
- right-click them and choose **Plot selected signal(s)**.

![Four signals in Stacked mode, both cursors on](images/plot-light.webp#only-light)
![Four signals in Stacked mode, both cursors on](images/plot-dark.webp#only-dark)

**Plot modes**, the three buttons above the plot:

- **Stacked**, the default, gives each signal its own row and scale.
- **Multi-Axis** draws the signals over each other, with an axis for each unit.
- **MultiStack** has rows that hold several signals: drag signals into a row to
  overlay them.

**Cursors.** A click on the plot places Cursor 1, and ++shift+++click places
Cursor 2. The line under the plot shows both times and the time between them,
and the signal table shows each signal's value at each cursor.

**Signal table columns.** Right-click the table header to show or hide a
column: the cursor values, **Δ C2−C1** (Cursor 2's value minus Cursor 1's),
the unit, and the statistics **Min**, **Max**, **Mean**, **Std dev**, **RMS**
and **Samples**. The statistics are between the cursors when both are on, and
over the visible range otherwise.

**Markers.** Right-click the plot and choose **Add marker here**, or press ++m++
to add one at Cursor 1. Drag a marker to move it, double-click it to rename
it, and right-click it to move Cursor 1 there or delete it. **Go to marker**
in the plot's right-click menu brings one into view. Markers are saved in the
configuration, and an MF4 export keeps them as events.

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
