# Osvanta Bus Log Analyzer

A portable Windows tool for automotive measurements. Open a CAN or LIN bus log,
decode it with your DBC, ARXML or LDF databases, and plot the signals.

![Four signals of a demo drive plotted in Stacked mode, with both cursors](images/plot-light.webp#only-light)
![Four signals of a demo drive plotted in Stacked mode, with both cursors](images/plot-dark.webp#only-dark)

## Where to start

- **New to the tool?** [Getting started](getting-started.md) takes you from a
  measurement file to your first plot in five minutes.
- **Looking for a key?** See the [keyboard shortcuts](keyboard-shortcuts.md).
- **Stuck, or found a problem?** See [Help and troubleshooting](help.md).

## Get the application

Download `BusLogAnalyzer_<version>_Windows.zip` from the
[latest release](https://github.com/osvanta/bus-log-analyzer/releases/latest),
extract it, and run `BusLogAnalyzer.exe`. There is nothing to install, and no
Python is needed.

## What it opens

| Measurement | Content | Database |
|---|---|---|
| BLF (`.blf`) | Raw CAN and LIN frames | DBC or ARXML for CAN, LDF for LIN |
| ASC (`.asc`) | Raw CAN and LIN frames | DBC or ARXML for CAN, LDF for LIN |
| MDF4 and MDF3 (`.mf4`, `.mdf`) | Raw CAN or LIN bus logging, or decoded signals | Only for raw bus logging |
| CSV (`.csv`) | A raw CAN export, or signals in columns | Only for raw CAN data |

The application finds out for itself what an MDF or CSV file holds.

## What it does

- Decodes large measurements fast, channel by channel, with a database for each channel.
- Lists every channel, message and signal in a tree you can search, with `*` and `?` wildcards.
- Plots signals in Stacked, Multi-Axis or MultiStack mode, with two cursors and Rectangle Zoom.
- Calculates new signals from others with a formula.
- Shows the **Trace**: the raw CAN and LIN frames, in order.
- Exports plotted signals to CSV or Excel, and saves the whole session as a configuration.
