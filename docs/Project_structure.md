bus-log-analyzer/
├── app.py                        # Entry point, APP_NAME="Osvanta Bus Log Analyzer", APP_VERSION
├── config/
│   └── diagnostics/
│       ├── motor_control.yaml    # Fault rules for motor/inverter domain (user-editable)
│       └── README.md             # Rule authoring guide
├── core/
│   ├── models.py                 # RawFrame, DecodedSignalSample dataclasses
│   ├── bus_types.py              # BusType (CAN/LIN), (bus, channel) identity, label/sort/encode helpers
│   ├── channel_config.py         # ChannelConfig: {(bus, channel) → DBC/ARXML/LDF}, decoder cache, save/load .osvanta_ch (v3)
│   ├── signal_store.py           # SignalStore, SignalSeries (array.array storage)
│   ├── raw_frame_store.py        # Batched CAN+LIN Trace store: compact metadata + 64 B/frame mmap payload; error/remote/BRS flags, BusStatus records
│   ├── dbc_decoder.py            # DBCDecoder with 3-level cache — .dbc, .arxml, .ldf (via DBC conversion)
│   ├── vectorized_decoder.py     # NumPy DBC decode, sparse multiplex filtering, short frames decode only what fits, cantools fallback
│   ├── blf_reader.py             # BLF decompression into packed column batches; CAN + LIN objects
│   ├── export.py                 # CSV export
│   ├── mf4_writer.py             # Decoded signals → MF4 (asammdf); marks each group's bus channel so the file reopens with the same keys; plot markers become MF4 marker events
│   ├── converter.py              # Tools ▸ Convert files: join / cut / keep signals / resample; MF4·MDF via asammdf, BLF·ASC frames via python-can, TRC via its reader
│   ├── asammdf_fixes.py          # asammdf's bus-logging decode made to match the decoder: signed 8-bit off a byte boundary, J1939 "not available" kept, no −0.0
│   ├── measurement_info.py       # A file's own information: MF4 header, events, attachments; BLF start/stop; ASC date line; TRC version, start, connections, events
│   ├── signal_statistics.py      # Min / max / mean / std dev / RMS / samples of a signal over a time range
│   ├── trace_differences.py      # Trace Δt and changed bytes: each frame's previous frame of the same ID, channel and kind, indexed in chunks (4 B/frame)
│   ├── signal_selection.py       # Convert files' saved signal selections: match by name or pattern, read/write .txt lists
│   ├── formula_catalog.py        # Insert Function's list: every formula function, operator and constant, by category, with argument help and examples
│   ├── readers/
│   │   ├── __init__.py           # reader_factory(), dbc_required_for(), bus-tagged prescan_measurement()
│   │   ├── db_format.py          # SUPPORTED_DB_SUFFIXES (.dbc/.arxml/.ldf), LDF→DBC conversion, message lengths
│   │   ├── base.py               # MeasurementReader protocol
│   │   ├── blf_can_reader.py     # BLF packed-batch + DBC/ARXML vectorized pipeline
│   │   ├── blf_content.py        # Which buses a BLF holds; catches logs that read as empty
│   │   ├── asc_can_reader.py     # Direct classic CAN/CAN-FD/LIN ASC column-array parser
│   │   ├── trc_can_reader.py     # PEAK trace (.trc) 1.0–3.0 column-array parser, written from PEAK's published format
│   │   ├── mdf_reader.py         # Pre-decoded MDF batched arrays + CAN/LIN bus-logging probe
│   │   ├── mdf_can_reader.py     # Native asammdf CAN+LIN bus extraction; python-can raw fallback
│   │   └── csv_reader.py         # Wide and narrow CSV
│   └── diagnostics/              # AI-powered diagnostics engine (Ctrl+Shift+A)
│       ├── __init__.py
│       ├── config_loader.py      # YAML domain parser — infers rule type, auto-generates id/title
│       ├── context.py            # DiagnosticContext — read-only SignalStore adapter for rule processors
│       ├── engine.py             # DiagnosticEngine — orchestrates rule runs and evidence building
│       ├── evidence.py           # EvidenceBuilder — reduces large data to <5 KB LLM snippets
│       ├── models.py             # Finding, Severity, AnalysisResult dataclasses
│       ├── rules/
│       │   ├── __init__.py       # RULE_PROCESSORS dispatch table
│       │   ├── expression.py     # Free-form condition evaluation (>, <, =, !=, and, or)
│       │   ├── fault_signal.py   # fault_when operator evaluation
│       │   ├── range_check.py    # min/max boundary check
│       │   └── message_loss.py   # Gap detection between CAN samples
│       └── llm/
│           ├── __init__.py
│           ├── client.py         # GitHubModelsClient — streaming OpenAI-compatible REST
│           └── prompts.py        # Analysis and chat follow-up prompt builders
├── gui/
│   ├── main_window.py            # MainWindow, top row, config save/load, plot_finding()
│   ├── load_worker.py            # LoadWorker (QThread): native MDF arrays + batched CAN-raw vectorized decode paths
│   ├── plot_widget.py            # PlotPanel: normal / multi-axis / stacked, dual cursors, zoom_to_time(), signal table columns chosen from its header (Δ C2−C1, statistics), Table view, measurement events
│   ├── tabular_view.py           # Table view: plotted signals on the union of their sample times, a virtual model
│   ├── measurement_info.py       # File ▸ Measurement info dialog
│   ├── converter_dialog.py       # Tools ▸ Convert files dialog (runs core.converter off the GUI thread)
│   ├── user_settings.py          # One setting at a time in the user's settings file
│   ├── overflow_row.py           # OverflowButtonRow: plot buttons that do not fit move into a "»" menu
│   ├── plot_icons.py             # Monochrome plot button icons, drawn from SVG in the palette's text colour
│   ├── brand_logo.py             # BrandLogo: the Osvanta logo at the status bar's right end, in the light or dark theme's colours
│   ├── signal_tree.py            # SignalTreeWidget with live search
│   ├── raw_frame_dialog.py       # Sliding-window CAN/LIN Trace (RawFrameStore, no cap); Δt, changed-byte highlight, follows plot Cursor 1
│   ├── dbc_manager.py            # Database Manager dialog: per-bus-channel DBC/ARXML/LDF, match quality bars
│   ├── splash.py                 # SplashScreen — minimisable, taskbar-visible splash screen
│   ├── app_data.py               # Where logs and settings live: %LOCALAPPDATA%\Osvanta\BusLogAnalyzer when packaged, next to app.py from source
│   ├── crash_log.py              # BLA_crash.log: Qt fatal errors, native crashes, freezes, uncaught exceptions
│   ├── session_restore.py        # Last session: asks Save / Don't save / Cancel on close, keeps osvanta_last_session.json (refreshed each minute), offers it at start
│   ├── survey_prompt.py          # User survey request on the 10th start, and the top row's link after "Remind me later"; state in osvanta_user_settings.json
│   ├── top_row.py                # The top row's File and Tools menus and its Decode button: bold, its icon beside the word
│   ├── insert_function_dialog.py # Calculated Signal ▸ Insert function (Shift+F3): categories, functions, how to use each
│   ├── help_menu.py              # The top row's Help menu: Getting started (F1), keyboard shortcuts, GitHub Discussions and issues, the log folder, the survey, About
│   ├── release_test/             # Hidden --release-test: drives the built app through real measurements; --known-good adds timing against fixed limits (timing.py)
│   │   ├── plan.py               # Folder layout → measurements, databases, scenarios
│   │   ├── driver.py             # Works one scenario's toolbar steps inside the running app
│   │   ├── runner.py             # Restarts the app once per scenario, progress window
│   │   └── report.py             # Verdicts from exit codes and crash-log sessions; anonymised report
│   └── diagnostics/              # Diagnostics UI (non-modal window)
│       ├── activation.py         # Wires Ctrl+Shift+A shortcut in MainWindow
│       ├── window.py             # DiagnosticsWindow — domain selector, run controls, auto-plot on fault
│       ├── findings_panel.py     # Left panel — severity-coloured finding list + details pane
│       ├── chat_panel.py         # Right panel — streaming LLM chat with status label
│       └── worker.py             # AnalysisWorker / LLMWorker (background thread helpers)
├── resources/
│   ├── splashscreen.png          # 1635 × 962 splash image
│   ├── app_icon.png              # 1024 × 1024 app icon source
│   ├── app_icon.ico              # Multi-resolution ICO (256/128/64/48/32/16 px)
│   └── release_readme.txt        # README.txt beside the built executables
├── guide/                        # User guide (Markdown), published to GitHub Pages
│   ├── index.md                  # Front page; the Help menu links to it and to getting-started/:
│   ├── getting-started.md        #   never rename or move these two pages
│   ├── images/                   # Screenshots, light and dark, from a demo measurement
│   └── requirements.txt          # The guide's build tools, apart from the application's
├── mkdocs.yml                    # The guide's site: theme, navigation, address
├── requirements.txt              # What the application needs; LGPL libraries pinned
├── requirements-release.in       # requirements.txt + PyInstaller: the input of the release lock
├── requirements-release.txt      # The release lock: every package, version and hash CI installs
├── BusLogAnalyzer.spec           # PyInstaller spec, bundles resources/, config/ and ldfparser grammars;
│                                 #   builds BusLogAnalyzer.exe and appdebugger.exe (-X dev)
├── .github/workflows/build.yml  # On a v*.*.* tag: test, build, SBOM and SHA-256 sums; a separate job publishes
├── .github/workflows/pr.yml     # On a PR: protected paths, dependency audit, lint, tests, portable build
├── .github/workflows/guide.yml  # Builds the guide on a PR; publishes it to GitHub Pages from main
└── .github/dependabot.yml       # Monthly updates of the commit-pinned GitHub Actions


## Validated loading and decoding architecture — protected

The BLF, ASC, MF4, and MDF loading/decoding implementation described below is
fully tested and accepted. It must not be changed without explicit permission
from the project owner. See `AGENTS.md` for the complete protected-file list.

### Entry and format selection

1. `gui/main_window.py` owns file selection, lightweight channel/ID pre-scan,
   database configuration, and the **Decode** worker lifecycle.
2. `core/readers/__init__.py` detects the measurement format, distinguishes
   pre-decoded MDF from MDF bus logging, enforces DBC/ARXML requirements, and
   constructs the correct reader.
3. `core/channel_config.py` maps physical bus channels to DBC/ARXML/LDF files
   and caches decoders. Channels are identified by `(bus, number)` rather than a
   bare integer, because `CAN 1` and `LIN 1` can both exist in one MF4 and would
   otherwise collide. Channel `0` is the all-channels fallback **per bus**, so an
   All-LIN database is never handed to CAN extraction.

### LoadWorker dispatch

`gui/load_worker.py` is the central background pipeline and selects exactly
one accepted path. It sits in `gui/` because it is a Qt worker (a `QObject`
whose signals cross to the GUI thread); `core/` imports no Qt at all, which
`tests/test_core_imports_no_gui.py` enforces.

- **MF4/MDF bus logging:** `MDFCANReader.iter_decoded_channel_arrays()` calls
  `asammdf.MDF.extract_bus_logging()` once and bulk-imports decoded NumPy
  arrays. It preserves channel/message metadata and value-to-text conversion.
  The older raw-frame vectorized path remains a compatibility fallback.
- **Pre-decoded MF4/MDF:** `MDFReader.iter_channel_arrays()` batches channels
  by MDF channel group and imports arrays directly without a DBC.
- **ASC + database:** `ASCCANReader.iter_raw_batches()` parses classic CAN,
  CAN-FD and LIN ASC text directly into packed column batches without
  allocating a `can.Message` or `RawFrame` per record.
- **TRC + database:** `TRCCANReader.iter_raw_batches()` does the same for PEAK
  trace files, every version from 1.0 to 3.0, choosing the line layout from the
  `$FILEVERSION` (and, from 2.0, the `$COLUMNS`) header line. python-can's
  `TRCReader` is not used: it fails on 1.2, 3.0 and 1.0 files with error frames.
  Times are absolute (`$STARTTIME`, local wall clock, as BLF's start) from 1.1
  on. What the store cannot hold — CAN XL frames, CAN XL protocol exceptions,
  J1939 messages over 64 bytes, unreadable lines — is counted in
  `load_warnings`. Other programs save `.trc` files too (text logs starting
  `@ TEXT @`, for one): a file none of whose first 20 lines of data reads as a PEAK trace
  line is refused when its header is read, so Load, Measurement info and
  Convert files say why instead of loading nothing.
- **BLF + database:** `BLFReaderService.iter_raw_batches()` uses python-can for
  BLF container decompression and emits packed column batches without
  per-frame tuples at the LoadWorker boundary. LIN objects, which python-can
  skips, are parsed by `_PackedBLFReader` itself.

The bulk vectorised decode reads the bus back out of the raw-frame flags and
keys decoders by `(bus, channel)` through `ChannelConfig.bus_decoder_map()`.
The remaining per-frame loops still match against `RawFrame.channel`, a bare
integer, and keep using `ChannelConfig.can_decoder_map()`: handing them the
bus-tagged mapping would fill the lookup with tuple keys no integer can match,
and decoding would silently yield nothing instead of raising.

### LIN bus logging (MF4/MDF, BLF and ASC)

MF4/MDF decodes LIN through the same `extract_bus_logging()` call as CAN, with
both databases passed in one `database_files={"CAN": [...], "LIN": [...]}`
argument. BLF and ASC take a different route to the same result — see
**LIN from BLF and ASC** below.

- Bus type is **discovered from the file, never chosen by the user**: CAN groups
  appear as `CAN_DataFrame`, LIN groups as `LIN_Frame`, each with its own
  `BusChannel` numbering. `MDFCANReader._decoded_group_metadata()` recovers the
  bus from asammdf's acquisition source (`LIN{n}.LIN_Frame.ID=0x{id}`), so a
  database's file extension is never used to infer the bus — a `.dbc` legitimately
  describes a LIN cluster.
- `.ldf` support requires `ldfparser`, which is what makes `canmatrix.formats.ldf`
  importable. `db_format.ldf_support_available()` checks this up front because a
  missing ldfparser otherwise surfaces as a bare `KeyError` from inside asammdf.
- LIN frames share the CAN Trace store rather than a separate one: a 6-bit LIN ID
  fits the uint32 ID column, a LIN payload is at most 8 of 64 bytes, and the bus
  is carried in a previously unused flag bit. The 64-byte record layout is
  unchanged.
### LIN from BLF and ASC

python-can models CAN only: its BLF reader *skips* LIN objects and its ASC
reader has no LIN handling at all, so a LIN-only Vector log used to load as an
empty measurement with no error and nothing to point at.

- **The LDF becomes DBC text, and the existing pipeline does the rest.**
  `db_format.ldf_to_dbc_string()` reads the LDF with canmatrix/ldfparser and
  dumps DBC into memory; `load_database_file()` hands that to cantools. A LIN
  signal is laid out inside its frame exactly as a CAN signal is, so past that
  point there is no LIN-specific decoding — `DBCDecoder`, `VectorizedDBC`, the
  match bar and the debug inspector all apply unchanged. The conversion
  preserves frame IDs, bit layouts, scaling and value tables, and produces the
  same names and values as asammdf's own LDF handling, which is what keeps a
  signal key such as `LIN1::DoorCmd::WindowPos` stable across containers.
- **The bus rides in the flags byte.** `_PackedBLFReader` and the ASC batch
  parser already emit the byte that *is* the `RawFrameStore` flags column, so
  setting `FLAG_LIN` carries the bus end to end without widening a batch tuple
  or changing a dtype.
- **`_PackedBLFReader` parses `LIN_MESSAGE2` (57) and `LIN_MESSAGE` (11).**
  Type 57's field offsets are verified against a real 7,275-object CANoe log;
  type 11 is the pre-2005 layout, implemented from the documented struct and
  unverified. Other LIN objects — errors, sleep, wakeup, schedule changes — are
  counted and skipped: they are bus events, not frames.
- **LIN channel numbers are used as stored.** BLF holds them 1-indexed; the CAN
  path adds one only to undo python-can's own `channel - 1`, which the packed
  reader bypasses.
- **The bulk decode groups on `(bus, channel, arb_id)`**, with the bus in bit 40.
  LIN IDs are 6-bit and overlap the low CAN range, so CAN 1 and LIN 1 can carry
  the same frame ID; grouping without the bus merges them and lets one
  database decode both. `ChannelConfig.bus_decoder_map()` is the bus-tagged
  counterpart to `can_decoder_map()` for this one loop.
- **Match scoring uses frame length as well as frame ID for LIN.** Every LIN
  cluster numbers from 0, so unrelated LDFs routinely declare the same IDs — in
  the CANoe sample, `Door.ldf` scores a perfect ID match against *both* LIN
  channels and belongs to one. `PrescanResult.lengths_per_channel` carries the
  observed lengths; it is empty for CAN, so CAN scoring is unchanged.
- **A short frame yields only the signals that fit in its bytes.**
  `LoadWorker` hands `MessageVectorDecoder.decode` each frame's length, so a
  signal past the end of a frame gets no sample from it, as cantools
  (`allow_truncated`) and the CAN Trace decode it, instead of a value read from
  the padding of the fixed 64-byte record. `LoadWorker` counts frames shorter
  than the message they matched and reports the total — the visible symptom of
  a database on the wrong channel. A bus-logging MF4 is decoded by asammdf,
  which reads the padding; `MDFCANReader` finds the frames shorter than their
  message from `DataLength` (keeping only those, so memory follows their
  number), matches them to asammdf's samples by time stamp, and leaves out
  the same samples in the lazy and the eager read, reporting the count among
  the load warnings. LIN too: asammdf gathers a LIN frame ID's frames from
  every LIN bus into each bus's group, so `MDFCANReader` also leaves out the
  samples from another bus's frames, and reads the LIN bus channels from the
  frames themselves (asammdf's LIN keys are frame IDs, not channels).
- **A value table names raw values; a signal plots its physical values.**
  Each sample's label is the table's text for its raw value, or the physical
  value where the table has none, as cantools gives it with
  `decode_choices=True`. The decoder's labels (`ChoiceLabels`,
  `DBCDecoder.decode_frame`) turn the physical value back into its raw one
  with the signal's scale and offset before the lookup. asammdf writes a
  bus-logging MF4's table as a range-to-text conversion whose default is the
  scaling; `MDFReader` reads such a channel raw, takes the default's result as
  its values and the conversion's text as its labels (`TableLabels`, worked
  out when shown). A table with a text default, as this application's own
  MF4 export writes, keeps the stored value.
- **asammdf decodes a bus-logging MF4 as the decoder decodes the same frames.**
  `core/asammdf_fixes.py`, installed with the overrun guard by
  `core/readers/__init__.py`, corrects asammdf 8.8.27 where it differs: a
  signed 8-bit signal off a byte boundary is sign-extended (0xFC is −4, not
  252); a J1939 signal "not available" in every sample is kept, as BLF and ASC
  show it, since the application never applies asammdf's J1939 invalidation
  marks; and a linear conversion with a negative factor and no offset gives
  0.0 for raw 0, not −0.0 (MDF3 too). Rerun `tests/test_asammdf_fixes.py`
  after any asammdf upgrade.
- `blf_content.py` scans object types — stopping at the first CAN object, so a
  normal CAN log costs one container — and `reader_factory` refuses a BLF
  holding *neither* CAN nor LIN. That check runs **before** the database check:
  no database can help a file with no decodable frames.
- An `.ldf` is still refused for raw CAN CSV, which has no bus dimension.

### CAN-raw storage and decode

1. `RawFrameStore.append_raw_batch()` bulk-appends compact metadata and writes
   packed 64-byte payload records. After sealing, the payload file is memory
   mapped for CAN Trace access and vectorized decoding.
2. `VectorizedDBC` groups frames by `(channel, arbitration_id)` and performs
   NumPy signal extraction. Simple little-endian multiplexing is vectorized;
   inactive multiplex branches and failed rows are not inserted as samples.
   A frame whose multiplexer value the database does not define keeps its
   multiplexer and non-multiplexed signals, as asammdf does; only that
   branch's signals get no value (`dbc_decoder.decode_known_branches` where
   cantools would reject the frame, in the CAN Trace too).
   Unsupported layouts use the verified cantools fallback.
3. `SignalStore.add_series_bulk()` receives complete timestamp/value arrays.
   Do not alter `core/signal_store.py` as part of loading/decoding work without
   separate explicit permission.
4. The final tree payload and partial/completion signals are delivered to the
   GUI only through the existing LoadWorker signal wiring.
5. Frames that are not messages are kept, flagged, and never decoded:
   `FLAG_ERROR` (error frame) and `FLAG_REMOTE` (remote frame; its DLC is the
   length it asked for) group apart from the data frames of their ID
   (`_frame_keys()` bit 42) and are skipped by the decode. `FLAG_BRS` marks a
   CAN FD frame sent with bit rate switch. Readers set them where the file
   records them: BLF (RTR bit, BRS bit, `CAN_ERROR`, `CAN_ERROR_EXT`,
   `CAN_FD_ERROR_64`), ASC (`ErrorFrame`, `r` frames, the CAN FD BRS column),
   TRC (`ER`/`Error` lines, `RR`/`RTR` frames, the `FB`/`BI` types),
   MF4 bus logging (`CAN_ErrorFrame`, `CAN_RemoteFrame`, `CAN_DataFrame.BRS`)
   and raw CAN CSV (`BRS`, `RTR` columns).
6. What a logger records about the bus rather than a frame becomes a
   `BusStatus` record on `RawFrameStore.bus_status`, on the frames' time base:
   BLF driver statistics, driver errors (TEC/REC) and overload frames; ASC
   `Statistic:` and controller status lines; TRC hardware status (`ST`,
   `Warng`), error counters (`EC`, and the counters of an error frame) and
   overload frames (`OF`). Rare, so a plain list.
7. An MF4 written by this application (`core.mf4_writer`) names each channel
   group's bus channel in its acquisition source ("CAN1"), with the application
   as the source's name. `MDFSampleSource.group_channels` reads it back and the
   load worker gives those signals their channel, so the keys
   (`CH1::Message::Signal`) are the original ones. MDF files from any other
   tool keep the `CH?` keys they always had.

### Accepted behavioral invariants

- Physical channel numbering remains consistent across BLF, ASC, and MDF, and is
  now qualified by bus so `CAN 1` and `LIN 1` stay distinct.
- Signal keys keep their historical `CH<n>::Message::Signal` form for CAN. They are
  persisted in saved session configs, so changing them would break every stored
  plot; LIN uses `LIN<n>::` and the friendlier `CAN 1` / `LIN 1` labels are display
  only.
- Channel configs written before version 3 have bare integer keys, predate LIN
  support, and load as CAN.
- All decoded timestamps share one recording-wide zero origin.
- DBC/ARXML channel-specific mappings and all-channel fallback semantics remain
  unchanged.
- MF4/MDF native extraction, ASC direct-array parsing, BLF packed batches,
  sparse multiplex handling, fallback behavior, and progress logging remain
  unchanged.
- BLF/ASC continue to populate CAN Trace through `RawFrameStore`, now including
  LIN frames, tagged by flag bit rather than by a widened record.
- LIN channel numbers in BLF are used as stored (1-indexed); only the CAN path
  compensates for python-can's `channel - 1`.
- An LDF decodes to the same message names, signal names and values whether the
  measurement is an MF4 (asammdf) or a BLF/ASC (LDF→DBC conversion). A saved
  plot configuration must keep working when the same cluster is recorded in a
  different container.
- MF4/MDF native fast loading builds CAN Trace from whole bus-logging channel
  groups in bulk. The groups arrive one bus channel at a time, so the rows are
  then put in timestamp order (`RawFrameStore.sort_by_time()`, stable) — the
  trace dialog's jump-to-time is a binary search that relies on it. BLF, ASC
  and CSV traces keep file order and are never sorted. The compatibility
  fallback (python-can's `MF4Reader`, which already merges groups by time)
  remains unchanged.
- Signal names, message names/IDs, units, enum display values, decoded sample
  counts, and tree hierarchy must remain stable for the validated fixtures.
