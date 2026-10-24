# Help and troubleshooting

## Ask, or tell us

- **A question, an idea, or anything else about the tool:** write in
  [GitHub Discussions](https://github.com/osvanta/bus-log-analyzer/discussions).
- **A problem, or a feature you need:** open an
  [issue on GitHub](https://github.com/osvanta/bus-log-analyzer/issues). We read
  every one.

The application's **Help** menu has both links, and **Help ▸ Take the user
survey** asks what you use the tool for, in three or four minutes.

!!! warning "GitHub is public"
    Do not attach measurements, databases or screenshots you are not allowed to
    share. Describe them instead: the format, the size, the bus, and how many
    channels and databases.

## What to put in a problem report

1. The version: **Help ▸ About** shows it.
2. What you did, step by step, what you expected, and what happened.
3. The two log files. **Help ▸ Open the log folder** shows them:
    - `busloganalyzer_applog.log`: every line of the **Log** panel, and the
      warnings and errors behind them;
    - `BLA_crash.log`: why the application ended, when it crashed or froze.

    The folder is `%LOCALAPPDATA%\Osvanta\BusLogAnalyzer\logs`: type that in the
    Explorer address bar if the application does not start. The logs name the
    files you opened, so check them before you send them. Versions up to
    v00.02.02 kept them next to `BusLogAnalyzer.exe`.

!!! tip "If it crashes"
    `appdebugger.exe`, next to `BusLogAnalyzer.exe`, is the same application with
    memory checks. It runs slower, but it records far more about a crash. Repeat
    the same steps with it, then send `BLA_crash.log`.

## Common problems

??? question "Windows says it protected my PC"
    The application is not signed yet, so SmartScreen stops it the first time.
    Click **More info**, then **Run anyway**.

??? question "Decode is greyed out"
    Open a measurement first, with **Open Measurement** (++ctrl+o++). The status
    bar at the bottom always says what to do next.

??? question "Decode finished, but there are no signals"
    A raw bus log needs a database for each channel. Click **Databases** and
    check that each channel has one, and that its **Match quality** is high. The
    **Log** panel shows how many message groups were decoded, for example
    `4/4 groups`. A low match usually means the database is on the wrong channel.

??? question "Trace is greyed out, or says the Trace is unavailable"
    The Trace is ready once a measurement has been decoded. It shows raw CAN and
    LIN frames, so it needs a raw bus log: an MF4, MDF or CSV file that holds only
    decoded signals has no frames to show, and clicking **Trace** says so.

??? question "A signal I expect is not in the tree"
    Check the search box above the tree: a search hides every signal that does
    not match. Then check **Databases**: a message no database describes is not
    decoded.
