"""Tolerant readers for run artifacts: json / jsonl / csv / ipynb timestamps / log tails.

* ``candidate_evaluations.jsonl`` is rewritten in place by the runtime (non-atomic), so a reader can see a truncated
  last line or a half-written file: unparsable lines are skipped and counted (the next poll retries).
* Non-finite floats (``NaN``/``Infinity`` are accepted by Python's json) become ``None`` so every payload is strict JSON.
* No function here takes a path from a request: callers pass paths built by :mod:`scanner`.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any

MAX_JSON_BYTES = 64 << 20  # a single artifact json larger than this is refused (largest real file: 1.4 MB ipynb)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def clean(value: Any) -> Any:
    """Recursively replace NaN/Infinity by ``None`` (strict JSON); containers are copied."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


_OVERFLOW = re.compile(r"\d[eE][+-]?\d{3,}")


def loads(text: str) -> Any:
    """``json.loads`` where the literals NaN / Infinity / -Infinity become ``None`` while parsing (no second pass over the
    document); a number that overflows to infinity (``1e999``) is caught by a cheap regex and cleaned in a second pass."""
    obj = json.loads(text, parse_constant=lambda _name: None)
    return clean(obj) if _OVERFLOW.search(text) else obj


def finite(value: Any) -> float | None:
    """``float(value)`` when it is a finite real number (bool excluded), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def read_json(path: str) -> tuple[Any, str | None]:
    """(parsed json with non-finite floats removed, None) or (None, reason)."""
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return None, "file too large"
        with open(path, "r", encoding="utf-8") as fh:
            return loads(fh.read()), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, UnicodeDecodeError) as exc:  # json.JSONDecodeError is a ValueError
        return None, f"unreadable: {type(exc).__name__}"


def read_text(path: str, max_bytes: int = 1 << 20) -> tuple[str | None, str | None]:
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, f"unreadable: {type(exc).__name__}"
    if len(data) > max_bytes:
        return None, "file too large"
    return data.decode("utf-8", errors="replace"), None


def read_jsonl(path: str) -> tuple[list[dict[str, Any]], int, str | None]:
    """(object rows in file order, number of skipped lines, error). A final line without newline that fails to parse is a
    partial write and counts as skipped."""
    rows: list[dict[str, Any]] = []
    bad = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
                else:
                    bad += 1
    except FileNotFoundError:
        return [], 0, "missing"
    except OSError as exc:
        return [], 0, f"unreadable: {type(exc).__name__}"
    return rows, bad, None


def read_csv_rows(path: str, limit: int = 100000) -> tuple[list[dict[str, str]], str | None]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            out = []
            for i, row in enumerate(csv.DictReader(fh)):
                if i >= limit:
                    break
                out.append(row)
            return out, None
    except FileNotFoundError:
        return [], "missing"
    except (OSError, csv.Error) as exc:
        return [], f"unreadable: {type(exc).__name__}"


def tail_lines(path: str, n: int, max_chars: int = 2000, strip_ansi: bool = False) -> tuple[list[str], str | None]:
    """Last ``n`` lines (each cut to ``max_chars``); reads at most the last 1 MiB of the file."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - (1 << 20)))
            data = fh.read()
    except FileNotFoundError:
        return [], "missing"
    except OSError as exc:
        return [], f"unreadable: {type(exc).__name__}"
    text = data.decode("utf-8", errors="replace")
    if strip_ansi:
        text = _ANSI.sub("", text)
    lines = text.splitlines()[-n:]
    return [ln[:max_chars] for ln in lines], None


def iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_ts(value: Any) -> str | None:
    """Normalize an ipynb execution timestamp (``2026-09-30T16:05:24.292968Z``) to second resolution ``...Z``; other
    parsable ISO strings are converted to UTC, naive ones are assumed UTC; garbage -> None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _epoch(value: Any) -> float | None:
    """Epoch seconds of an ISO timestamp with sub-second precision (naive = UTC); None when unparsable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


_STAGE_RE = re.compile(r"^\s*#\s*\[(P\d+(?:-\d+)?)\]")


def notebook_stages(nb: Any) -> list[dict[str, Any]]:
    """Stage timeline from an executed notebook: every code cell whose first line is ``# [P<n>-<m>] ...`` gives
    ``{label, started_at, ended_at, duration_s}`` from ``metadata.execution`` (``iopub.execute_input`` ->
    ``shell.execute_reply``). Cells without timestamps give nulls."""
    out: list[dict[str, Any]] = []
    cells = nb.get("cells") if isinstance(nb, dict) else None
    for cell in cells or []:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        src = cell.get("source")
        first = (src[0] if isinstance(src, list) and src else (src or "")).split("\n", 1)[0] if src else ""
        m = _STAGE_RE.match(first if isinstance(first, str) else "")
        if not m:
            continue
        ex = (cell.get("metadata") or {}).get("execution") or {}
        started, ended = norm_ts(ex.get("iopub.execute_input")), norm_ts(ex.get("shell.execute_reply"))
        t0, t1 = _epoch(ex.get("iopub.execute_input")), _epoch(ex.get("shell.execute_reply"))
        dur = round(t1 - t0, 3) if t0 is not None and t1 is not None and t1 >= t0 else None
        out.append({"label": m.group(1), "started_at": started, "ended_at": ended, "duration_s": dur})
    return out


def notebook_span(nb: Any) -> tuple[str | None, str | None]:
    """(first execute_input, last shell.execute_reply) over all code cells, second resolution."""
    starts, ends = [], []
    for cell in (nb.get("cells") if isinstance(nb, dict) else None) or []:
        if not isinstance(cell, dict):
            continue
        ex = (cell.get("metadata") or {}).get("execution") or {}
        a, b = norm_ts(ex.get("iopub.execute_input")), norm_ts(ex.get("shell.execute_reply"))
        if a:
            starts.append(a)
        if b:
            ends.append(b)
    return (min(starts) if starts else None), (max(ends) if ends else None)


_NOTE_MODE = re.compile(r'"mode"\s*:\s*"([^"]*)"')
_NOTE_PARENTS = re.compile(r'"parents"\s*:\s*\[([^\]]*)\]')
_NOTE_CACHE = re.compile(r'"cache_hit"\s*:\s*(?:"([^"]*)"|(true|false|null))')
_NOTE_ERR = re.compile(r'"error"\s*:\s*"((?:[^"\\]|\\.)*)')
_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def parse_notes(notes: Any) -> dict[str, Any]:
    """Lineage facts from a row's ``notes`` (JSON string cut to 500 chars by the runtime).

    Returns ``{mode, parents, mutation, cache_hit, error, parse}`` with ``parse`` in ``ok|truncated|missing``. A cut or
    otherwise invalid JSON falls back to regular expressions for mode / parents / cache_hit / error; whatever cannot be
    recovered stays ``None`` (never guessed). A notes string that is plain text (not JSON at all) is the error text."""
    base: dict[str, Any] = {"mode": None, "parents": [], "mutation": None, "cache_hit": None, "error": None,
                            "parse": "missing"}
    if not isinstance(notes, str) or not notes.strip():
        return base
    try:
        obj = loads(notes)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        parents = obj.get("parents")
        cache = obj.get("cache_hit")
        base.update(
            mode=obj.get("mode") if isinstance(obj.get("mode"), str) else None,
            parents=[str(p) for p in parents] if isinstance(parents, list) else [],
            mutation=clean(obj["mutation"]) if isinstance(obj.get("mutation"), dict) else None,
            cache_hit=cache if isinstance(cache, str) and cache else None,
            error=str(obj["error"]) if obj.get("error") else None, parse="ok")
        return base
    base["parse"] = "truncated"
    m = _NOTE_MODE.search(notes)
    base["mode"] = m.group(1) if m else None
    m = _NOTE_PARENTS.search(notes)
    if m:
        base["parents"] = [s for s in _STR.findall(m.group(1))]
    m = _NOTE_CACHE.search(notes)
    if m and m.group(1):
        base["cache_hit"] = m.group(1)
    m = _NOTE_ERR.search(notes)
    if m and m.group(1):
        base["error"] = m.group(1)[:300]
    elif base["mode"] is None and not notes.lstrip().startswith("{"):
        base["error"] = notes[:300]  # plain text note (runtime error message cut to 500 chars)
    return base
