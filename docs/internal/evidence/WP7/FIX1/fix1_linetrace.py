"""Scratch pytest plugin (evidence only): records executed line numbers of the files named in FIX1_TRACE_FILES."""
import json, os, sys, threading

_TARGETS = tuple(x for x in os.environ.get("FIX1_TRACE_FILES", "").split(",") if x)
_HITS: dict = {}


def _tracer(frame, event, arg):
    fn = frame.f_code.co_filename
    if fn.endswith(_TARGETS):
        if event == "line":
            _HITS.setdefault(fn, set()).add(frame.f_lineno)
        return _tracer
    return None


def pytest_configure(config):
    sys.settrace(_tracer)
    threading.settrace(_tracer)


def pytest_unconfigure(config):
    sys.settrace(None)
    out = os.environ.get("FIX1_TRACE_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump({k: sorted(v) for k, v in _HITS.items()}, fh)
