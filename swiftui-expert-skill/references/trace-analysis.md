# Instruments Trace Analysis

Use this reference whenever the user references an Xcode Instruments `.trace`
file. A target SwiftUI source file is **optional** — if provided, you can
cite specific lines; without one, the trace still surfaces view names,
hot symbols, and high-severity events that tell the user where to look.

The bundled parser reads four lanes for SwiftUI responsiveness (Time
Profiler, Hangs, Animation Hitches, SwiftUI updates) and exposes two
discovery modes (`--list-logs`, `--list-signposts`) plus a `--window` flag
so the agent can focus analysis on a precise slice of the trace.

## When to invoke

Any of these signals:

- Message contains a path ending in `.trace`.
- User mentions "hangs", "hitches", "jank", "slow view", or performance
  issues alongside an Instruments recording.
- User asks to focus analysis "after / before / between / during" a log
  message or signpost.

Triggering does **not** require a SwiftUI source file. If one is present
you'll ground recommendations in specific lines; if not, base them on the
view names and symbols the trace reveals.

## The three CLI modes

The scripts live alongside this skill at `scripts/` and need only the
Python 3 stdlib + `xctrace` (ships with Xcode at `/usr/bin/xctrace`).

### 1. Full analysis (default)

```bash
python3 "${SKILL_DIR}/scripts/analyze_trace.py" \
  --trace "/path/to/file.trace" \
  --top 10 --top-hitches 5 \
  [--window START_MS:END_MS] \
  --json-only
```

- `--json-only` gives you structured data; omit for JSON + markdown
  summary; `--markdown-only` is for pasting a digest into the chat.
- `--output <path>` writes `<path>.json` and `<path>.md` instead of stdout.
- `--window START_MS:END_MS` (optional) restricts every lane and every
  correlation to that time slice.

### 2. `--list-logs` — find os_log timestamps

```bash
python3 "${SKILL_DIR}/scripts/analyze_trace.py" --trace <path> --list-logs \
  [--log-subsystem com.myapp.net] \
  [--log-category "Network"] \
  [--log-type Fault] \
  [--log-message-contains "loaded feed"] \
  [--log-limit 10] \
  [--window START_MS:END_MS]
```

Returns JSON `{ "logs": [...], "count": N }` where each log entry includes
`time_ms`, `type`, `subsystem`, `category`, `process`, and the formatted
`message` (with args substituted) + raw `format_string`. All filters are
AND-combined; `--log-message-contains` is case-insensitive substring match.

### 3. `--list-signposts` — find signpost intervals

```bash
python3 "${SKILL_DIR}/scripts/analyze_trace.py" --trace <path> --list-signposts \
  [--signpost-name-contains "ImageDecode"] \
  [--signpost-subsystem com.myapp.feed] \
  [--signpost-category "Rendering"] \
  [--window START_MS:END_MS]
```

Returns JSON `{ "intervals": [...], "events": [...] }`. Intervals are
paired `begin`/`end` signposts with `start_ms`, `end_ms`, `duration_ms`,
`name`, `subsystem`, `category`, `process`, `signpost_id`. Single-point
events (and any unpaired begins) go into `events`. All filters are
AND-combined; `--signpost-name-contains` is case-insensitive substring
match.

## Composition pattern — scoping to a slice

When the user says something like "focus on X", "between A and B", or
"during signpost Y", compose the three modes:

1. **Discover** — call `--list-logs` or `--list-signposts` with filters
   that match the user's description. Pick the right entries.
2. **Build the window** — take `time_ms` (logs) or `start_ms`/`end_ms`
   (intervals) and form `--window START:END`.
3. **Analyse** — call the default mode with `--window`.

Examples:

- *"Focus on the section after the log saying 'loaded feed'."*
  → `--list-logs --log-message-contains "loaded feed"`, take the entry's
  `time_ms`, set window = `[that_ms, end_of_trace_ms]` (or use the trace
  `duration_s × 1000`).
- *"Between the 'begin-sync' log and the 'done-sync' log."*
  → Two `--list-logs` calls (or one with a broader filter), pick the two
  timestamps, set window = `[first, second]`.
- *"During the signpost 'ImageDecode'."*
  → `--list-signposts --signpost-name-contains "ImageDecode"`, pick the
  interval, set window = `[start_ms, end_ms]`.

## JSON shape

```json
{
  "trace": "...",
  "xctrace_version": "26.4 (...)",
  "template": "SwiftUI",
  "duration_s": 14.83,
  "schemas_available": [...],
  "lanes": [
    { "lane": "time-profiler", "available": true, "schema_used": "time-profile",
      "metrics": { "total_samples": N, "total_weight_ms": ms, "processes": [...] },
      "top_offenders": [ { "symbol", "weight_ms", "percent", "samples", "thread" } ] },
    { "lane": "hangs", "available": true, "schema_used": "potential-hangs",
      "metrics": { "count", "total_duration_ms", "worst_duration_ms",
                   "severity_buckets": {"lt_250ms","250ms_1s","gt_1s"} },
      "top_offenders": [ { "start_ms", "duration_ms", "hang_type", "thread" } ] },
    { "lane": "hitches", "available": true, "schema_used": "hitches",
      "metrics": { "count", "total_hitch_ms", "worst_hitch_ms",
                   "narrative_breakdown": {...}, "system_hitches", "app_hitches" },
      "top_offenders": [ { "start_ms", "hitch_duration_ms", "narrative", "is_system" } ] },
    { "lane": "swiftui", "available": true, "schemas_used": [...],
      "metrics": { "total_events", "unique_views", "total_duration_ms",
                   "severity_breakdown": {"Very Low":N,"Moderate":N,"High":N},
                   "update_type_breakdown": {"View Body Updates":N, ...} },
      "top_offenders": [ { "view", "total_ms", "count", "avg_ms" } ],
      "high_severity_events": [ { "view", "severity", "duration_ms", "category",
                                   "update_type", "description" } ] }
  ],
  "correlations": [
    {
      "trigger": { "lane": "hangs"|"hitches", "start_ms", "end_ms", "duration_ms",
                   "hang_type"|"frame_duration_ms" },
      "time_profiler_main_thread": {
        "samples_in_window": N, "samples_on_main": M,
        "main_running_coverage_pct": 0–100,
        "hot_symbols": [ { "symbol", "samples", "weight_ms", "percent_of_main" } ]
      },
      "swiftui_overlapping_updates": [ { "view", "duration_ms", "start_ms" } ]
    }
  ]
}
```

## Interpretation guide

### `main_running_coverage_pct` is the key diagnostic

Time Profiler samples the main thread every ~1ms. For a correlation window
of `N` ms, you'd expect ~`N` main-thread running samples if main were fully
CPU-bound. Coverage is the ratio of observed main-thread samples to that
expectation.

- **< 25% coverage** → main thread was **blocked** (I/O, lock, sync XPC,
  `Task.sleep`, waiting on an actor-isolated call). The `hot_symbols` you
  do see are the moments main *was* executing — look there for the code
  that *initiates* the blocking work, not the work itself. Common fix:
  move to a background executor / `nonisolated` / `Task.detached`.
- **≥ 75% coverage** → main was **CPU-bound** the whole time. `hot_symbols`
  point directly at the expensive work. Common fixes: hoist computation
  out of view bodies, cache derived values, avoid per-frame allocation,
  debounce `onChange`.
- **25–75%** → mix. Usually computation plus intermittent I/O; show both
  hot symbols and note that main was partially blocked.

### High-severity SwiftUI events → reference routing

When `swiftui.high_severity_events[].description` is one of:

| description      | Likely cause              | Route to                            |
|------------------|---------------------------|-------------------------------------|
| `onChange`       | Expensive `.onChange` body | `references/performance-patterns.md`, `references/state-management.md` |
| `Gesture`        | Heavy gesture handler     | `references/performance-patterns.md` |
| `Action Callback`| Button/tap handler work   | `references/performance-patterns.md` |
| `Update`         | View body recomputation   | `references/view-structure.md`, `references/performance-patterns.md` |
| `Creation`       | View init cost            | `references/view-structure.md`      |
| `Layout`         | GeometryReader churn      | `references/layout-best-practices.md` |

### Mapping trace findings to source code

If the user gave you a specific file, use it to confirm/cite. If they didn't, the trace itself tells you which views and symbols to look up.

1. **From `swiftui.top_offenders` and `high_severity_events`**, use the
   `view` string as your search key. If a target file is open, grep it;
   if not, recommend the user grep their project for that type or the
   module name. A partial match (prefix / generic stripping) means it's
   probably a subview.
2. **From `correlations[].time_profiler_main_thread.hot_symbols`**, treat
   symbols starting with the user's module name (or in Swift free-function
   form) as candidates. System frames (`swift_`, `dyld`, `objc_`, `CA*`,
   `CF*`, `NS*`, `__open`, `pthread*`) identify *what* the code was doing
   but the user-code caller one frame up is typically what to fix — say
   so and, if you can, suggest searching the project for callers of the
   equivalent Swift API (e.g. `__open` → `FileHandle` / `Data(contentsOf:)` /
   `JSONDecoder.decode(from: Data)` sites).
3. **From `hitches[].narrative`**, Apple pre-attributes each hitch. The
   string `"Potentially expensive app update(s)"` means SwiftUI blamed the
   app (so user code is in scope); absence of narrative usually means it
   was a system hitch or below the threshold.
4. **Correlating hitches with SwiftUI updates**: the
   `swiftui_overlapping_updates` list on each hitch names the views that
   were actively rendering when the frame dropped. Prioritise those.

### Picking targets from a full-trace analysis

Prioritise from most actionable to least:

1. **Any `hangs` with `main_running_coverage_pct < 25%`** — these are
   blocking-I/O smells; nearly always fixable by moving work off-main.
2. **Any `hangs` with `main_running_coverage_pct ≥ 75%`** — CPU-bound
   main-thread work; fix the top `hot_symbols`.
3. **`hitches` with `narrative == "Potentially expensive app update(s)"`**
   and overlapping `swiftui_overlapping_updates` — specific views to
   restructure.
4. **`swiftui.high_severity_events`** — `onChange`, `Gesture`, or `Action
   Callback` with `duration_ms > ~16` are frame-dropping handlers.
5. **`swiftui.top_offenders`** — heaviest views by total body time, even
   without triggering hitches; candidates for view extraction or
   memoisation (`equatable`, `@ViewBuilder` extraction).

## Recommended output format for the user

After running the parser, structure your response as:

1. **One-line summary** — "Found N hangs, worst Wms; K hitches; J high-severity SwiftUI updates."
2. **Root-cause findings** — per prioritised target (see above), one paragraph with the trace evidence (coverage %, hot symbol, overlapping view) and a citation from `references/…` for the fix pattern.
3. **Plan** — numbered, file-specific edits. Cite line numbers in the user's Swift file when you know them. Don't edit the file unless the user asked for edits.
