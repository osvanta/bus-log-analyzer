# Security and data handling

This page is for whoever approves the tool for use with confidential data, such
as an information security officer working to TISAX (VDA ISA). It says what the
application does with your measurements and databases, what it writes to disk,
and what it sends over the network.

It describes the application as published on GitHub Releases. If something here
no longer matches what the application does, that is a bug: please report it
as the [security policy](https://github.com/osvanta/bus-log-analyzer/blob/main/SECURITY.md)
describes.

## Summary

| Question | Answer |
|---|---|
| Does it send data anywhere? | **No.** It opens no network connection of its own. |
| Telemetry, usage statistics, update checks? | **None.** |
| Does it change the files I open? | **No.** A damaged MF4 is repaired in a temporary copy, never in place. |
| Where does it write? | A folder of your own under `%LOCALAPPDATA%`, the temp folder, and wherever you save. See below. |
| Does it run code from the files it opens? | **No.** Calculated-signal formulas are parsed and checked, never run as Python. |
| Does it need administrator rights? | **No.** It runs from any folder you can write to. |

## Network

The application makes no network connection by itself. A few links open a web
page in your default browser, and only when you click them:

- **Help ▸ Getting started** (F1): this guide, on `osvanta.github.io`;
- **Help ▸ Ask a question or share an idea** and **Help ▸ Report a problem or
  request a feature**: GitHub;
- **Help ▸ Take the user survey**, the survey link in **About**, and the survey
  request shown once after a few starts: a Microsoft Forms page.

Nothing about your measurements, databases or computer is passed to that page.
Where browsing is not allowed, ignore those entries: the application works the
same without them.

## Files the application writes

### In your own folder: `%LOCALAPPDATA%\Osvanta\BusLogAnalyzer`

| File | What it holds |
|---|---|
| `logs\busloganalyzer_applog.log` | Every line of the Log panel, including **the full path and name of each measurement and database you open**, and warnings and errors. Rotated at 1 MB. |
| `logs\BLA_crash.log` | Why the application ended when it crashed or froze: stack traces and the messages Qt printed, which can name a file being opened. |
| `osvanta_user_settings.json` | Preferences, and whether the survey was answered. |
| `osvanta_temp_plot_config.json` | The last plot layout, so it can be restored: **signal and message names from your database**, colours and axes. |
| `osvanta_last_session.json` | The session the application was last closed with, so the next start can offer to reopen it: **the full path of the measurement and databases**, the plotted signals, calculated signals and layout. Rewritten once a minute while you work. |

Windows lets only you, and the computer's administrators, read this folder, so
the files stay private even when the application itself runs from a shared
folder or a network drive. **Help ▸ Open the log folder** opens it. If it cannot
be written, the two logs go to your temp folder instead.

To keep the folder somewhere else, for example on a drive your IT department
manages, set the environment variable `OSVANTA_DATA_DIR` to that folder.

Treat these files as being as confidential as the measurements and databases
you open: the names they hold can identify a project, an ECU or a supplier.
Keep them on an encrypted drive, and check the logs before you send them to
anyone.

Versions up to v00.02.02 kept these files next to `BusLogAnalyzer.exe`. The
first start of a later version moves them into your folder.

### In the temp folder (`%TEMP%`)

| File | What it is | Removed |
|---|---|---|
| `osvanta_*.rawdata` | The CAN Trace and plotted samples of the open measurement | By Windows when the application closes it, even after a crash |
| `osvanta_recovered_*.mf4` | A repaired copy of a damaged MF4 | When the measurement is closed; a copy left by a crash at the next start |

### Where you choose

Configurations (**File ▸ Save configuration**) hold the paths of the
measurement and databases and your plot layout. Exports hold the signal data you
export. Both go only where you save them.

## Opening untrusted files

Measurements and databases are input data: the application reads them, it never
runs code from them. Calculated-signal formulas, including those in a
configuration file someone sends you, are parsed into a fixed set of arithmetic,
comparison and function operations and rejected otherwise; they are never
handed to Python's `eval`.

The main risk from a hostile file is the same as for any parser: a malformed
file that crashes the application or makes it use a great deal of memory. If you
find one, report it privately as the
[security policy](https://github.com/osvanta/bus-log-analyzer/blob/main/SECURITY.md)
describes.

## Where the application comes from

- The source is public under MPL-2.0 at
  [github.com/osvanta/bus-log-analyzer](https://github.com/osvanta/bus-log-analyzer).
- Each release is built by GitHub Actions from a version tag, after the test
  suite passes, and published on the repository's Releases page. Download it only
  from there.
- Every library is installed at a fixed version and checked against its
  published hash, so a release contains exactly the library versions it lists.
  Each change to the source is scanned for known-vulnerable libraries, security
  bugs (CodeQL) and leaked credentials.
- Releases after v00.02.02 come with a `SHA256SUMS.txt` file: in PowerShell,
  `Get-FileHash` on the downloaded zip must print the hash it lists.
- They also come with a software bill of materials (`sbom.cdx.json`,
  CycloneDX): every library the release was built with, and its version, for
  your vulnerability management.
- **The executable is not code-signed yet**, so Windows SmartScreen warns on
  first start.
- The third-party libraries it ships, and their versions and licences, are in
  `THIRD_PARTY_NOTICES.md` and the `licenses` folder of the download.

## Removing the application

Delete its folder, and `%LOCALAPPDATA%\Osvanta\BusLogAnalyzer` for its logs and
settings. Nothing is written to the registry, and nothing else outside those two
folders, the temp folder and the files you saved yourself.
