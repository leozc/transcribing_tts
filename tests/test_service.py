"""Service tests — store + API, no GPU (transcription is simulated).

Access model: a registered client's X-Client-Key authenticates enqueue and scopes
"list my tasks" to that client; a single task is reachable by its owner
(X-Client-Key) OR the per-task pull_token returned at create (X-Task-Token).
"""
import importlib
import io
import itertools
import sqlite3
import time
import zipfile

import pytest

_ids = itertools.count()


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path))
    from tts_serve.service import store as store_mod
    importlib.reload(store_mod)
    from tts_serve.service import api as api_mod
    importlib.reload(api_mod)
    api_mod.store = store_mod
    store_mod.init()
    from fastapi.testclient import TestClient
    return store_mod, api_mod, TestClient(api_mod.app)


def _reg(client, client_id):
    r = client.post("/v1/clients", json={"client_id": client_id})
    assert r.status_code == 201, r.text
    return r.json()["client_key"]


def ckey(k):
    return {"X-Client-Key": k}


def tok(t):
    return {"X-Task-Token": t}


def _enqueue(client, client_id=None, key=None, **body):
    """Register a client (unless a key is supplied) and enqueue a task as it."""
    if client_id is None:
        client_id = f"c{next(_ids)}"
    if key is None:
        key = _reg(client, client_id)
    r = client.post("/v1/tasks", json={"source": "https://youtu.be/x", "client_id": client_id, **body},
                    headers=ckey(key))
    assert r.status_code == 200, r.text
    return r.json()["task_id"], r.json()["pull_token"]


# ---------- store ----------

def test_store_single_concurrency_fifo(svc):
    store, _, _ = svc
    a = store.create("s3://b/a.wav", "s3", {"reid": True}, client_id="t1", token="ta")
    b = store.create("https://youtu.be/x", "youtube", {}, client_id="t1", token="tb")
    assert store.get(a)["options"] == {"reid": True} and store.get(a)["token"] == "ta"
    assert store.claim_next_queued()["id"] == a
    assert store.claim_next_queued() is None
    store.update(a, status="done", stage="done")
    assert store.claim_next_queued()["id"] == b
    assert store.claim_next_queued() is None


def test_client_registration(svc):
    store, _, _ = svc
    key = store.create_client("alice")
    assert key and store.client_for_key(key) == "alice"
    assert store.create_client("alice") is None          # duplicate id
    assert store.client_for_key("nope") is None           # unknown key
    assert store.client_for_key(None) is None
    # the raw key is never stored, only its hash
    with store._conn() as c:
        row = c.execute("SELECT key_hash FROM clients WHERE client_id='alice'").fetchone()
    assert row["key_hash"] != key and len(row["key_hash"]) == 64


def test_recycle_should_spawn():
    from tts_serve.service.worker import _should_spawn
    # spawn only when there is pending work AND no child is running
    assert _should_spawn({"queued": 2}, child_alive=False) is True
    assert _should_spawn({"running": 1}, child_alive=False) is True   # crash-orphan -> respawn requeues
    assert _should_spawn({"queued": 3}, child_alive=True) is False    # one child at a time
    assert _should_spawn({"done": 5}, child_alive=False) is False     # nothing pending
    assert _should_spawn({}, child_alive=False) is False


def test_reclaim_stale(svc):
    store, _, _ = svc
    a = store.create("s3://b/a.wav", "s3", {})
    store.claim_next_queued()
    assert store.reclaim_stale() == 1
    assert store.get(a)["status"] == "queued"


def test_purge_old(svc):
    store, _, _ = svc
    keep = store.create("s://k", "s3", {})
    old = store.create("s://o", "s3", {})
    store.update(old, status="done")
    with store._conn() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 10 * 86400, old))
    assert store.purge_old(7) == 1
    assert store.get(old) is None and store.get(keep) is not None


def test_reclaim_inputs(svc):
    store, _, _ = svc
    tid = store.create("u://x", "url", {})
    d = store.task_dir(tid)
    (d / "input.m4a").write_bytes(b"x" * 1000)        # downloaded source ("inbox")
    (d / "audio_16k.wav").write_bytes(b"y" * 2000)    # intermediate working copy
    rdir = store.results_dir(tid); rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "transcript.txt").write_text("keep me")   # result ("outbox")
    # not done -> default sweep (done-only) skips it (retry/debug keep their input)
    assert store.reclaim_inputs() == (0, 0)
    assert (d / "input.m4a").exists()
    # done -> sweep drops input media, keeps results
    store.update(tid, status="done")
    swept, freed = store.reclaim_inputs()
    assert swept == 1 and freed >= 3000
    assert not (d / "input.m4a").exists() and not (d / "audio_16k.wav").exists()
    assert (rdir / "transcript.txt").read_text() == "keep me"
    assert store.reclaim_inputs() == (0, 0)           # idempotent


def test_migration_adds_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path))
    from tts_serve.service import store as store_mod
    importlib.reload(store_mod)
    store_mod.DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(store_mod.DB)  # OLD schema: no client_id, no token
    con.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT, stage TEXT, source TEXT,"
                " source_type TEXT, options_json TEXT, error TEXT, created_at REAL, updated_at REAL)")
    con.execute("INSERT INTO tasks(id,status) VALUES('old1','done')")
    con.commit(); con.close()
    store_mod.init()  # ALTER TABLE ADD client_id, token + CREATE clients
    row = store_mod.get("old1")
    assert row["client_id"] is None and row["token"] is None  # legacy -> fail-closed
    assert store_mod.create_client("x")  # clients table created by the migration


def test_split_brain_guard(tmp_path, monkeypatch):
    import importlib
    # a canonical state DB exists, but TTS_SERVE_DATA is unset -> init() must refuse
    # to silently use the <repo>/data fallback (would split-brain the queue).
    state = tmp_path / "state" / "tts_serve"
    state.mkdir(parents=True)
    (state / "tasks.db").write_text("x")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TTS_SERVE_DATA", raising=False)
    from tts_serve.service import store as store_mod
    importlib.reload(store_mod)
    with pytest.raises(RuntimeError, match="split-brain"):
        store_mod.init()
    # explicit TTS_SERVE_DATA -> no guard
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path / "explicit"))
    importlib.reload(store_mod)
    store_mod.init()  # must not raise


def test_setup_logging_idempotent_and_none_guard(tmp_path, monkeypatch):
    import importlib
    import logging as _logging
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path))
    monkeypatch.setenv("TTS_SERVE_LOG_DIR", "none")  # children/file-less path
    root = _logging.getLogger()
    saved = root.handlers[:]
    try:
        from tts_serve.service import logconf
        importlib.reload(logconf)          # reset _CONFIGURED
        logconf.setup_logging("worker")
        n1 = len(root.handlers)
        logconf.setup_logging("worker")    # second call is a no-op (no duplicate handlers)
        assert len(root.handlers) == n1
        assert not any(type(h).__name__ == "RotatingFileHandler" for h in root.handlers)
    finally:
        root.handlers[:] = saved
        importlib.reload(logconf)          # leave module fresh for other tests


# ---------- API: register + enqueue ----------

def test_register_and_create_returns_token(svc):
    _, _, client = svc
    tid, t = _enqueue(client, client_id="alice", speakers=2, reid=True)
    assert t and isinstance(t, str)
    s = client.get(f"/v1/tasks/{tid}", headers=tok(t)).json()
    assert s["status"] == "queued" and s["client_id"] == "alice"
    assert s["options"]["speakers"] == 2 and s["options"]["reid"] is True


def test_register_duplicate_and_blank(svc):
    _, _, client = svc
    assert client.post("/v1/clients", json={"client_id": "alice"}).status_code == 201
    assert client.post("/v1/clients", json={"client_id": "alice"}).status_code == 409
    assert client.post("/v1/clients", json={"client_id": "  "}).status_code == 422
    assert client.post("/v1/clients", json={}).status_code == 422


def test_enqueue_requires_client_key(svc):
    _, _, client = svc
    key = _reg(client, "alice")
    body = {"source": "https://youtu.be/x", "client_id": "alice"}
    assert client.post("/v1/tasks", json=body).status_code == 401                       # no key
    assert client.post("/v1/tasks", json=body, headers=ckey("bad")).status_code == 401   # invalid key
    # key authenticates alice but body claims bob -> mismatch
    assert client.post("/v1/tasks", json={**body, "client_id": "bob"},
                       headers=ckey(key)).status_code == 403
    assert client.post("/v1/tasks", json=body, headers=ckey(key)).status_code == 200


def test_create_via_upload(svc):
    store, _, client = svc
    key = _reg(client, "bob")
    r = client.post("/v1/tasks/upload",
                    files={"file": ("meeting.wav", b"RIFFxxxx", "audio/wav")},
                    data={"client_id": "bob", "speakers": "2"}, headers=ckey(key))
    assert r.status_code == 200 and r.json()["pull_token"]
    t = store.get(r.json()["task_id"])
    assert t["client_id"] == "bob" and t["token"] == r.json()["pull_token"]
    assert t["source"].endswith("input.wav") and t["options"]["speakers"] == 2
    # upload without a key is refused
    assert client.post("/v1/tasks/upload",
                       files={"file": ("a.wav", b"x", "audio/wav")},
                       data={"client_id": "bob"}).status_code == 401


def test_create_requires_source_and_client_id(svc):
    _, _, client = svc
    # body validation (422) happens regardless of auth
    assert client.post("/v1/tasks", json={}).status_code == 422
    assert client.post("/v1/tasks", json={"source": "x://y"}).status_code == 422
    assert client.post("/v1/tasks", json={"client_id": "a"}).status_code == 422
    assert client.post("/v1/tasks", json={"source": "u://x", "client_id": "   "}).status_code == 422


# ---------- API: access control ----------

def test_token_required_for_access(svc):
    _, _, client = svc
    tid, t = _enqueue(client)
    assert client.get(f"/v1/tasks/{tid}").status_code == 403                       # nothing
    assert client.get(f"/v1/tasks/{tid}", headers=tok("wrong")).status_code == 403  # bad token
    assert client.get(f"/v1/tasks/{tid}/artifact", headers=tok("wrong")).status_code == 403
    assert client.get(f"/v1/tasks/{tid}", headers=tok(t)).status_code == 200        # good token
    assert client.get(f"/v1/tasks/{tid}?token={t}").status_code == 200              # via query


def test_owner_key_accesses_own_task_not_others(svc):
    _, _, client = svc
    a_key = _reg(client, "alice")
    b_key = _reg(client, "bob")
    tid, _t = _enqueue(client, client_id="alice", key=a_key)
    # alice reaches her task with just her client key (no pull_token)
    assert client.get(f"/v1/tasks/{tid}", headers=ckey(a_key)).status_code == 200
    # bob's key does not reach alice's task
    assert client.get(f"/v1/tasks/{tid}", headers=ckey(b_key)).status_code == 403


def test_client_lists_only_own_tasks(svc):
    _, _, client = svc
    a_key = _reg(client, "alice")
    b_key = _reg(client, "bob")
    _enqueue(client, client_id="alice", key=a_key)
    _enqueue(client, client_id="alice", key=a_key)
    _enqueue(client, client_id="bob", key=b_key)
    alice = client.get("/v1/tasks", headers=ckey(a_key)).json()["tasks"]
    bob = client.get("/v1/tasks", headers=ckey(b_key)).json()["tasks"]
    assert len(alice) == 2 and all(t["client_id"] == "alice" for t in alice)
    assert len(bob) == 1 and bob[0]["client_id"] == "bob"


def test_no_token_task_is_inaccessible(svc):
    store, _, client = svc
    tid = store.create("u://x", "url", {}, client_id="legacy")  # no token (fail closed)
    assert client.get(f"/v1/tasks/{tid}", headers=tok("anything")).status_code == 403


def test_admin_list_and_queue(svc):
    _, _, client = svc
    _enqueue(client, client_id="alice")
    _enqueue(client, client_id="bob")
    # no API key configured in tests -> admin views open (no X-Client-Key -> see all)
    assert len(client.get("/v1/tasks").json()["tasks"]) == 2
    assert len(client.get("/v1/tasks?client_id=alice").json()["tasks"]) == 1
    q = client.get("/v1/queue").json()
    assert q["counts"]["queued"] == 2 and q["running"] is None


def test_queue_and_delete(svc):
    store, _, client = svc
    a, ta = _enqueue(client)
    b, tb = _enqueue(client)
    store.claim_next_queued()  # a -> running
    assert client.get("/v1/queue").json()["running"]["task_id"] == a
    assert client.delete(f"/v1/tasks/{b}", headers=tok(tb)).status_code == 200
    assert client.get(f"/v1/tasks/{b}", headers=tok(tb)).status_code == 404
    assert client.delete(f"/v1/tasks/{a}", headers=tok(ta)).status_code == 409  # running


def test_retry(svc):
    store, _, client = svc
    tid, t = _enqueue(client)
    store.update(tid, status="failed", error="boom")
    assert client.post(f"/v1/tasks/{tid}/retry", headers=tok(t)).status_code == 200
    assert client.get(f"/v1/tasks/{tid}", headers=tok(t)).json()["status"] == "queued"
    assert client.post(f"/v1/tasks/{tid}/retry", headers=tok(t)).status_code == 409


def test_unknown_task_404(svc):
    _, _, client = svc
    assert client.get("/v1/tasks/nope", headers=tok("x")).status_code == 404
    assert client.get("/v1/tasks/nope/artifact", headers=tok("x")).status_code == 404


def test_artifact_lifecycle(svc):
    store, _, client = svc
    tid, t = _enqueue(client)
    # queued/running -> 202 Accepted ("more coming", not an error)
    r = client.get(f"/v1/tasks/{tid}/artifact", headers=tok(t))
    assert r.status_code == 202 and r.json()["status"] == "queued"
    # failed/cancelled -> 409 (no artifact will ever exist)
    store.update(tid, status="failed", error="boom")
    assert client.get(f"/v1/tasks/{tid}/artifact", headers=tok(t)).status_code == 409
    rdir = store.results_dir(tid)
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "transcript.txt").write_text("hello world")
    (rdir / "segments.json").write_text('{"segments":[]}')
    store.update(tid, status="done", stage="done")
    resp = client.get(f"/v1/tasks/{tid}/artifact", headers=tok(t))
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    assert set(z.namelist()) == {"transcript.txt", "segments.json"}
    assert z.read("transcript.txt") == b"hello world"


def test_agent_info_and_openapi(svc):
    _, _, client = svc
    r = client.get("/agent_info").json()
    assert r["service"] == "tts_serve" and r["workflow"] and r["spec"]["openapi"] == "/openapi.json"
    assert "identity" in r
    spec = client.get("/openapi.json").json()
    assert "/v1/clients" in spec["paths"]  # registration is discoverable


def test_admin_endpoints_require_bearer_when_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path))
    monkeypatch.setenv("TTS_SERVE_API_KEY", "secret")
    from tts_serve.service import store as store_mod
    importlib.reload(store_mod)
    from tts_serve.service import api as api_mod
    importlib.reload(api_mod)
    api_mod.store = store_mod
    store_mod.init()
    from fastapi.testclient import TestClient
    client = TestClient(api_mod.app)
    h = {"Authorization": "Bearer secret"}
    assert client.get("/v1/queue").status_code == 401              # no bearer at all
    assert client.get("/v1/tasks", headers=h).status_code == 200   # admin ok
    assert client.get("/healthz").status_code == 200
