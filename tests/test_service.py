"""Service tests — store + API, no GPU (transcription is simulated).

Access control: client_id is attribution (who enqueued); the per-task pull_token
returned at create is the capability required to poll/pull/delete/retry.
"""
import importlib
import io
import sqlite3
import time
import zipfile

import pytest


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


def _enqueue(client, **body):
    body.setdefault("client_id", "alice")
    r = client.post("/v1/tasks", json={"source": "https://youtu.be/x", **body})
    assert r.status_code == 200, r.text
    return r.json()["task_id"], r.json()["pull_token"]


def tok(t):
    return {"X-Task-Token": t}


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
    store_mod.init()  # ALTER TABLE ADD client_id, token
    row = store_mod.get("old1")
    assert row["client_id"] is None and row["token"] is None  # legacy -> fail-closed


# ---------- API: create + token ----------

def test_create_returns_token_and_status(svc):
    _, _, client = svc
    tid, t = _enqueue(client, speakers=2, reid=True)
    assert t and isinstance(t, str)
    s = client.get(f"/v1/tasks/{tid}", headers=tok(t)).json()
    assert s["status"] == "queued" and s["client_id"] == "alice"
    assert s["options"]["speakers"] == 2 and s["options"]["reid"] is True


def test_create_via_upload(svc):
    store, _, client = svc
    r = client.post("/v1/tasks/upload",
                    files={"file": ("meeting.wav", b"RIFFxxxx", "audio/wav")},
                    data={"client_id": "bob", "speakers": "2"})
    assert r.status_code == 200 and r.json()["pull_token"]
    t = store.get(r.json()["task_id"])
    assert t["client_id"] == "bob" and t["token"] == r.json()["pull_token"]
    assert t["source"].endswith("input.wav") and t["options"]["speakers"] == 2


def test_create_requires_source_and_client_id(svc):
    _, _, client = svc
    assert client.post("/v1/tasks", json={}).status_code == 422
    assert client.post("/v1/tasks", json={"source": "x://y"}).status_code == 422
    assert client.post("/v1/tasks", json={"client_id": "a"}).status_code == 422


def test_blank_client_id_rejected(svc):
    _, _, client = svc
    assert client.post("/v1/tasks", json={"source": "u://x", "client_id": "   "}).status_code == 422
    r = client.post("/v1/tasks/upload",
                    files={"file": ("a.wav", b"x", "audio/wav")}, data={"client_id": "  "})
    assert r.status_code == 422


# ---------- API: token access control ----------

def test_token_required_for_access(svc):
    _, _, client = svc
    tid, t = _enqueue(client)
    assert client.get(f"/v1/tasks/{tid}").status_code == 403                       # no token
    assert client.get(f"/v1/tasks/{tid}", headers=tok("wrong")).status_code == 403  # bad token
    assert client.get(f"/v1/tasks/{tid}/artifact", headers=tok("wrong")).status_code == 403
    assert client.get(f"/v1/tasks/{tid}", headers=tok(t)).status_code == 200        # good token
    assert client.get(f"/v1/tasks/{tid}?token={t}").status_code == 200              # via query


def test_no_token_task_is_inaccessible(svc):
    store, _, client = svc
    tid = store.create("u://x", "url", {}, client_id="legacy")  # no token (fail closed)
    assert client.get(f"/v1/tasks/{tid}", headers=tok("anything")).status_code == 403


def test_admin_list_and_queue(svc):
    _, _, client = svc
    _enqueue(client, client_id="alice")
    _enqueue(client, client_id="bob")
    # no API key configured in tests -> admin views open
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
    assert client.get("/openapi.json").status_code == 200


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
