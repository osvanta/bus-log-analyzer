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
│   ├── load_worker.py            # QThread: native MDF arrays + batched CAN-raw vectorized decode paths
│   ├── signal_store.py           # SignalStore, SignalSeries (array.array storage)
│   ├── raw_frame_store.py        # Batched CAN+LIN Trace store: compact metadata + 64 B/frame mmap payload
│   ├── dbc_decoder.py            # DBCDecoder with 3-level cache — .dbc, .arxml, .ldf (via DBC conversion)
│   ├── vectorized_decoder.py     # NumPy DBC decode, sparse multiplex filtering, cantools fallback
│   ├── blf_reader.py             # BLF decompression into packed column batches; CAN + LIN objects
│   ├── export.py                 # CSV export
│   ├── readers/
│   │   ├── __init__.py           # reader_factory(), dbc_required_for(), bus-tagged prescan_measurement()
│   │   ├── db_format.py          # SUPPORTED_DB_SUFFIXES (.dbc/.arxml/.ldf), LDF→DBC conversion, message lengths
│   │   ├── base.py               # MeasurementReader protocol
│   │   ├── blf_can_reader.py     # BLF packed-batch + DBC/ARXML vectorized pipeline
│   │   ├── blf_content.py        # Which buses a BLF holds; catches logs that read as empty
│   │   ├── asc_can_reader.py     # Direct classic CAN/CAN-FD/LIN ASC column-array parser
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
│   ├── main_window.py            # MainWindow, toolbar, config save/load, plot_finding()
│   ├── plot_widget.py            # PlotPanel: normal / multi-axis / stacked, dual cursors, zoom_to_time()
│   ├── overflow_row.py           # OverflowButtonRow: plot buttons that do not fit move into a "»" menu
│   ├── plot_icons.py             # Monochrome plot button icons, drawn from SVG in the palette's text colour
│   ├── signal_tree.py            # SignalTreeWidget with live search
│   ├── raw_frame_dialog.py       # Sliding-window CAN/LIN Trace (RawFrameStore, no cap)
│   ├── dbc_manager.py            # Database Manager dialog: per-bus-channel DBC/ARXML/LDF, match quality bars
│   ├── splash.py                 # SplashScreen — minimisable, taskbar-visible splash screen
│   ├── crash_log.py              # BLA_crash.log: Qt fatal errors, native crashes, freezes, uncaught exceptions
│   ├── survey_prompt.py          # User survey request on the 10th start, and the top row's link after "Remind me later"; state in osvanta_user_settings.json
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
├── requirements.txt
├── BusLogAnalyzer.spec           # PyInstaller spec, bundles resources/, config/ and ldfparser grammars;
│                                 #   builds BusLogAnalyzer.exe and appdebugger.exe (-X dev)
└── .github/workflows/build.yml  # Auto-build on v*.*.* tag push


## Validated loading and decoding architecture — protected

The BLF, ASC, MF4, and MDF loading/decoding implementation described below is
fully tested and accepted. It must not be changed without explicit permission
from the project owner. See `AGENTS.md` for the complete protected-file list.

### Entry and format selection

1. `gui/main_window.py` owns file selection, lightweight channel/ID pre-scan,
   database configuration, and the **Load + Decode** worker lifecycle.
2. `core/readers/__init__.py` detects the measurement format, distinguishes
   pre-decoded MDF from MDF bus logging, enforces DBC/ARXML requirements, and
   constructs the correct reader.
3. `core/channel_config.py` maps physical bus channels to DBC/ARXML/LDF files
   and caches decoders. Channels are identified by `(bus, number)` rather than a
   bare integer, because `CAN 1` and `LIN 1` can both exist in one MF4 and would
   otherwise collide. Channel `0` is the all-channels fallback **per bus**, so an
   All-LIN database is never handed to CAN extraction.

### LoadWorker dispatch

`core/load_worker.py` is the central background pipeline and selects exactly
one accepted path:

- **MF4/MDF bus logging:** `MDFCANReader.iter_decoded_channel_arrays()` calls
  `asammdf.MDF.extract_bus_logging()` once and bulk-imports decoded NumPy
  arrays. It preserves channel/message metadata and value-to-text conversion.
  The older raw-frame vectorized path remains a compatibility fallback.
- **Pre-decoded MF4/MDF:** `MDFReader.iter_channel_arrays()` batches channels
  by MDF channel group and imports arrays directly without a DBC.
- **ASC + database:** `ASCCANReader.iter_raw_batches()` parses classic CAN,
  CAN-FD and LIN ASC text directly into packed column batches without
  allocating a `can.Message` or `RawFrame` per record.
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
- **A short frame decodes from padding rather than failing**, because
  `VectorizedDBC` slices a fixed 64-byte record. `LoadWorker` counts frames
  shorter than the message they matched and reports the total — the visible
  symptom of a database on the wrong channel.
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
   Unsupported layouts use the verified cantools fallback.
3. `SignalStore.add_series_bulk()` receives complete timestamp/value arrays.
   Do not alter `core/signal_store.py` as part of loading/decoding work without
   separate explicit permission.
4. The final tree payload and partial/completion signals are delivered to the
   GUI only through the existing LoadWorker signal wiring.

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
