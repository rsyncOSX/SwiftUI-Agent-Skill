"""Discovery helpers for os_log messages and os_signpost intervals.

These let an agent locate a focus window (e.g. "after the log saying X",
"during signpost Y") before running the main lane analysis.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import xctrace, xml_utils

OS_LOG_SCHEMA = "os-log"
OS_SIGNPOST_SCHEMA = "os-signpost"


def list_logs(
    trace_path: Path,
    toc_schemas: frozenset[str],
    subsystem: str | None = None,
    category: str | None = None,
    message_contains: str | None = None,
    message_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return os_log entries, optionally filtered. Case-insensitive contains."""
    if OS_LOG_SCHEMA not in toc_schemas:
        return []
    xml_bytes = xctrace.export_schema(trace_path, OS_LOG_SCHEMA)
    stream = xml_utils.RowStream(xml_bytes)
    needle = message_contains.lower() if message_contains else None

    out: list[dict[str, Any]] = []
    for row in stream:
        time_el = row.get("time")
        if time_el is None:
            continue
        time_ns = xml_utils.int_text(stream.resolve(time_el))
        if time_ns is None:
            continue

        sub = _str_of(row, stream, "subsystem")
        cat = _str_of(row, stream, "category")
        typ = _str_of(row, stream, "message-type")
        fmt = _str_of(row, stream, "format-string")
        msg = _str_of(row, stream, "message") or fmt

        if subsystem and (sub or "") != subsystem:
            continue
        if category and (cat or "") != category:
            continue
        if message_type and (typ or "") != message_type:
            continue
        if needle and needle not in (msg or "").lower() and needle not in (fmt or "").lower():
            continue

        process_el = row.get("process")
        process = (
            xml_utils.extract_process(process_el, stream).get("name")
            if process_el is not None else None
        )

        out.append({
            "time_ns": time_ns,
            "time_ms": round(time_ns / 1_000_000, 3),
            "type": typ,
            "subsystem": sub,
            "category": cat,
            "process": process,
            "message": msg,
            "format_string": fmt,
        })
        if limit is not None and len(out) >= limit:
            break

    out.sort(key=lambda e: e["time_ns"])
    return out


def list_signposts(
    trace_path: Path,
    toc_schemas: frozenset[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return signpost intervals (paired begin/end) plus single-point events.

    Shape: { "intervals": [...], "events": [...] }. Intervals have
    start_ms/end_ms/duration_ms; events have a single time_ms.
    """
    if OS_SIGNPOST_SCHEMA not in toc_schemas:
        return {"intervals": [], "events": []}

    xml_bytes = xctrace.export_schema(trace_path, OS_SIGNPOST_SCHEMA)
    stream = xml_utils.RowStream(xml_bytes)

    pending: dict[tuple, dict] = {}
    intervals: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for row in stream:
        time_el = row.get("time") or row.get("start")
        if time_el is None:
            continue
        time_ns = xml_utils.int_text(stream.resolve(time_el))
        if time_ns is None:
            continue

        name = _str_of(row, stream, "name")
        subsystem = _str_of(row, stream, "subsystem")
        category = _str_of(row, stream, "category")
        event_type = _str_of(row, stream, "event-type") or _str_of(row, stream, "message-type")
        signpost_id = _str_of(row, stream, "signpost-id")
        process_el = row.get("process")
        process = (
            xml_utils.extract_process(process_el, stream).get("name")
            if process_el is not None else None
        )

        key = (process, subsystem, category, name, signpost_id)
        etype = (event_type or "").lower()

        if etype in ("begin", "interval begin", "start"):
            pending[key] = {"start_ns": time_ns, "name": name,
                            "subsystem": subsystem, "category": category,
                            "process": process, "signpost_id": signpost_id}
        elif etype in ("end", "interval end", "stop"):
            start = pending.pop(key, None)
            if start is not None:
                dur_ns = time_ns - start["start_ns"]
                intervals.append({
                    **start,
                    "end_ns": time_ns,
                    "duration_ns": dur_ns,
                    "start_ms": round(start["start_ns"] / 1_000_000, 3),
                    "end_ms": round(time_ns / 1_000_000, 3),
                    "duration_ms": round(dur_ns / 1_000_000, 3),
                })
            else:
                events.append(_point_event(time_ns, name, subsystem, category,
                                            process, signpost_id, event_type))
        else:
            events.append(_point_event(time_ns, name, subsystem, category,
                                        process, signpost_id, event_type))

    # Unclosed begins are returned as events so nothing is silently dropped.
    for key, info in pending.items():
        events.append(_point_event(info["start_ns"], info["name"],
                                    info["subsystem"], info["category"],
                                    info["process"], info["signpost_id"],
                                    "Begin (unclosed)"))

    intervals.sort(key=lambda i: i["start_ns"])
    events.sort(key=lambda e: e["time_ns"])
    return {"intervals": intervals, "events": events}


def _point_event(time_ns, name, subsystem, category, process, signpost_id, event_type):
    return {
        "time_ns": time_ns,
        "time_ms": round(time_ns / 1_000_000, 3),
        "name": name,
        "subsystem": subsystem,
        "category": category,
        "process": process,
        "signpost_id": signpost_id,
        "event_type": event_type,
    }


def _str_of(row, stream, key):
    el = row.get(key)
    if el is None:
        return None
    resolved = stream.resolve(el)
    txt = xml_utils.str_text(resolved) or resolved.get("fmt")
    return txt
