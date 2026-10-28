"""WP-3B fix-round evidence (run: CUDA_VISIBLE_DEVICES="" runtime/venv/bin/python -B docs/internal/evidence/WP3/3B/wire_check.py):
real uvicorn (h11/httptools) gateway + stub core on ephemeral 127.0.0.1 ports, raw http.client requests.
Checks the WP-3B fix round on the real wire: path decoding once, percent-encoded relay, dot/control rejection,
latin-1 header bytes, Last-Event-ID filter, NaN refusal. No GPU, no fixed ports."""
import http.client, json, shutil, socket, sys, tempfile, threading, time
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[5] / "tests" / "_shared"))
import uvicorn
from stub_core import CORE_TOKEN, StubCore
from mbnas_server.api.app import create_app
from mbnas_server.config import GatewaySettings

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    assert p not in (8000, 8001, 8002, 5173, 5174, 5175); return p

stub = StubCore()
seen = []
def recorder(app):
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            seen.append((scope["method"], scope["path"], scope.get("raw_path"), dict(scope["headers"]).get(b"idempotency-key"),
                         dict(scope["headers"]).get(b"last-event-id")))
        await app(scope, receive, send)
    return wrapped

def serve(app, port, http_impl):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", http=http_impl, lifespan="on")
    srv = uvicorn.Server(cfg); t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(200):
        if srv.started: return srv, t
        time.sleep(0.05)
    raise RuntimeError("server did not start")

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))

for http_impl in ("h11", "httptools"):
    seen.clear(); stub.calls.clear()
    cport, gport = free_port(), free_port()
    tmp = tempfile.mkdtemp(prefix="wp3b-wire-")
    s = GatewaySettings(api_token="gw-user-token-0123456789", core_url=f"http://127.0.0.1:{cport}", core_token=CORE_TOKEN,
                        artifact_store=f"{tmp}/store", log_dir=f"{tmp}/logs", audit_log=f"{tmp}/logs/a.jsonl",
                        web_dist=f"{tmp}/nodist", manifest_cache_root=f"{tmp}/gw")
    csrv, ct = serve(recorder(stub.app), cport, http_impl)
    gsrv, gt = serve(create_app(s), gport, http_impl)
    AUTH = {"Authorization": "Bearer gw-user-token-0123456789"}
    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", gport, timeout=10)
        c.putrequest(method, path, skip_accept_encoding=True)
        for k, v in {**AUTH, **(headers or {})}.items():
            c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, data
    tag = f"[{http_impl}] "
    n = len(seen); st, data = req("GET", "/api/v1/runs/%252E%252E/snapshot")
    last = seen[-1] if len(seen) > n else None
    check(tag + "double-encoded dots reach core as literal %2E%2E (one segment)", st == 200 and last and last[1] == "/core/v1/console/runs/%2E%2E/snapshot", (st, last))
    n = len(seen); st, data = req("GET", "/api/v1/runs/a%3Fb%23c/snapshot")
    last = seen[-1] if len(seen) > n else None
    check(tag + "? and # stay inside the segment", st == 200 and last and last[1] == "/core/v1/console/runs/a?b#c/snapshot" and last[2] == b"/core/v1/console/runs/a%3Fb%23c/snapshot", (st, last))
    n = len(seen); st, data = req("GET", "/api/v1/runs/r1/artifacts/%2E%2E")
    check(tag + "dot segment -> gateway 404, no relay", st == 404 and json.loads(data)["location"] == "gateway" and len(seen) == n, (st, data))
    n = len(seen); st, data = req("GET", "/api/v1/runs/r1/artifacts/%00")
    check(tag + "NUL id -> gateway 404, no relay", st == 404 and json.loads(data)["code"] == "NOT_FOUND" and len(seen) == n, (st, data))
    n = len(seen); st, data = req("POST", "/api/v1/runs", b"{}", {"Content-Type": "application/json", "Idempotency-Key": b"key-\xe9-1"})
    last = seen[-1] if len(seen) > n else None
    check(tag + "latin-1 Idempotency-Key forwarded byte-exact", st == 200 and last and last[3] == b"key-\xe9-1", (st, last))
    n = len(seen); st, data = req("GET", "/api/v1/runs/r1/events?after_seq=0", None, {"Last-Event-ID": b"\xb9\xb2"})
    last = seen[-1] if len(seen) > n else None
    check(tag + "malformed Last-Event-ID dropped, stream relayed", st == 200 and b"id: 2" in data and last and last[4] is None, (st, last, data[:80]))
    n = len(seen); st, data = req("GET", "/api/v1/runs/r1/events?after_seq=0", None, {"Last-Event-ID": b"7"})
    last = seen[-1] if len(seen) > n else None
    check(tag + "decimal Last-Event-ID forwarded", st == 200 and last and last[4] == b"7", (st, last))
    n = len(seen); st, data = req("POST", "/api/v1/protocols/validate", b'{"protocol": {"x": NaN}}', {"Content-Type": "application/json"})
    check(tag + "NaN body -> 422 VALIDATION_FAILED, no relay, no echo", st == 422 and json.loads(data)["code"] == "VALIDATION_FAILED" and b"NaN" not in data and len(seen) == n, (st, data))
    gsrv.should_exit = True; csrv.should_exit = True; gt.join(10); ct.join(10)
    shutil.rmtree(tmp, ignore_errors=True)

print("TOTAL pass=%d fail=%d" % (sum(ok for _, ok in results), sum(not ok for _, ok in results)))
sys.exit(0 if all(ok for _, ok in results) else 1)
