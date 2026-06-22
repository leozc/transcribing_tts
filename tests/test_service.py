"""Service tests — store + API, no GPU (transcription is simulated)."""
import importlib
import io
import sqlite3
import time
import zipfile

import pytest

CID = {"X-Client-Id": "alice"}   # default caller for owned ops


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


# ---------- store ----------

def test_store_single_concurrency_fifo(svc):
    store, _, _ = svc
    a = store.create("s3://b/a.wav", "s3", {"reid": True}, client_id="t1")
    b = store.create("https://youtu.be/x", "youtube", {}, client_id="t1")
    assert store.get(a)["options"] == {"reid": True} and store.get(a)["client_id"] == "t1"
    assert store.claim_next_queued()["id"] == a          # FIFO
    assert store.claim_next_queued() is None              # single concurrency
    store.update(a, status="done", stage="done")
    assert store.claim_next_queued()["id"] == b
    assert store.claim_next_queued() is None


def test_reclaim_stale(svc):
    store, _, _ = svc
    a = store.create("s3://b/a.wav", "s3", {})
    store.claim_next_queued()
    assert store.get(a)["status"] == "running"
    assert store.reclaim_stale() == 1
    assert store.get(a)["status"] == "queued"
    assert store.claim_next_queued()["id"] == a


def test_purge_old(svc):
    store, _, _ = svc
    keep = store.create("s://k", "s3", {}, client_id="t1")
    old = store.create("s://o", "s3", {}, client_id="t1")
    store.update(old, status="done")
    with store._conn() as c:  # backdate to 10 days ago
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 10 * 86400, old))
    assert store.purge_old(7) == 1
    assert store.get(old) is None and store.get(keep) is not None  # queued never purged


def test_migration_adds_client_id(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_SERVE_DATA", str(tmp_path))
    from tts_serve.service import store as store_mod
    importlib.reload(store_mod)
    store_mod.DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(store_mod.DB)  # OLD schema, no client_id
    con.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT, stage TEXT, source TEXT,"
                " source_type TEXT, options_json TEXT, error TEXT, created_at REAL, updated_at REAL)")
    con.execute("INSERT INTO tasks(id,status) VALUES('old1','done')")
    con.commit(); con.close()
    store_mod.init()  # should ALTER TABLE ADD COLUMN client_id
    assert store_mod.get("old1")["client_id"] is None
    tid = store_mod.create("s", "file", {}, client_id="x")
    assert store_mod.get(tid)["client_id"] == "x"


# ---------- API: create + identity ----------

def test_create_via_url_and_status(svc):
    _, _, client = svc
    r = client.post("/v1/tasks", json={"source": "https://youtu.be/abc", "client_id": "alice",
                                       "speakers": 2, "reid": True})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    tid = r.json()["task_id"]
    s = client.get(f"/v1/tasks/{tid}", headers=CID).json()
    assert s["status"] == "queued" and s["source_type"] == "youtube" and s["client_id"] == "alice"
    assert s["options"]["speakers"] == 2 and s["options"]["reid"] is True


def test_create_via_upload(svc):
    store, _, client = svc
    r = client.post("/v1/tasks/upload",
                    files={"file": ("meeting.wav", b"RIFFxxxx", "audio/wav")},
                    data={"client_id": "bob", "speakers": "2", "reid": "true"})
    assert r.status_code == 200
    t = store.get(r.json()["task_id"])
    assert t["client_id"] == "bob" and t["source"].endswith("input.wav")
    assert t["options"]["speakers"] == 2 and t["options"]["reid"] is True


def test_create_requires_source_and_client_id(svc):
    _, _, client = svc
    assert client.post("/v1/tasks", json={}).status_code == 422                       # both missing
    assert client.post("/v1/tasks", json={"source": "x://y"}).status_code == 422       # client_id missing
    assert client.post("/v1/tasks", json={"client_id": "a"}).status_code == 422        # source missing


# ---------- API: ownership ----------

def test_blank_client_id_rejected(svc):
    _, _, client = svc
    assert client.post("/v1/tasks", json={"source": "u://x", "client_id": "   "}).status_code == 422
    r = client.post("/v1/tasks/upload",
                    files={"file": ("a.wav", b"x", "audio/wav")}, data={"client_id": "  "})
    assert r.status_code == 422  # not 500


def test_ownership_enforced(svc):
    _, _, client = svc
    tid = client.post("/v1/tasks", json={"source": "https://youtu.be/x", "client_id": "alice"}).json()["task_id"]
    # wrong / missing client_id -> 403
    assert client.get(f"/v1/tasks/{tid}").status_code == 403
    assert client.get(f"/v1/tasks/{tid}", headers={"X-Client-Id": "mallory"}).status_code == 403
    assert client.get(f"/v1/tasks/{tid}/artifact", headers={"X-Client-Id": "mallory"}).status_code == 403
    # right client_id (also via query param) -> ok
    assert client.get(f"/v1/tasks/{tid}", headers=CID).status_code == 200
    assert client.get(f"/v1/tasks/{tid}?client_id=alice").status_code == 200


def test_list_filters_by_client(svc):
    _, _, client = svc
    client.post("/v1/tasks", json={"source": "u://1", "client_id": "alice"})
    client.post("/v1/tasks", json={"source": "u://2", "client_id": "bob"})
    assert len(client.get("/v1/tasks", headers={"X-Client-Id": "alice"}).json()["tasks"]) == 1
    assert len(client.get("/v1/tasks").json()["tasks"]) == 2  # admin (no id) sees all


def test_queue_admin_and_delete(svc):
    store, _, client = svc
    a = client.post("/v1/tasks", json={"source": "https://youtu.be/a", "client_id": "alice"}).json()["task_id"]
    b = client.post("/v1/tasks", json={"source": "https://youtu.be/b", "client_id": "alice"}).json()["task_id"]
    q = client.get("/v1/queue").json()
    assert q["running"] is None and len(q["queued"]) == 2 and q["counts"]["queued"] == 2
    store.claim_next_queued()
    q = client.get("/v1/queue").json()
    assert q["running"]["task_id"] == a and len(q["queued"]) == 1
    assert client.delete(f"/v1/tasks/{b}", headers=CID).status_code == 200
    assert client.get(f"/v1/tasks/{b}", headers=CID).status_code == 404
    assert client.delete(f"/v1/tasks/{a}", headers=CID).status_code == 409  # running


def test_retry(svc):
    store, _, client = svc
    tid = client.post("/v1/tasks", json={"source": "https://youtu.be/x", "client_id": "alice"}).json()["task_id"]
    store.update(tid, status="failed", error="boom")
    assert client.post(f"/v1/tasks/{tid}/retry", headers=CID).status_code == 200
    assert client.get(f"/v1/tasks/{tid}", headers=CID).json()["status"] == "queued"
    assert client.post(f"/v1/tasks/{tid}/retry", headers=CID).status_code == 409


def test_unknown_task_404(svc):
    _, _, client = svc
    assert client.get("/v1/tasks/nope", headers=CID).status_code == 404
    assert client.get("/v1/tasks/nope/artifact", headers=CID).status_code == 404


def test_artifact_lifecycle(svc):
    store, _, client = svc
    tid = client.post("/v1/tasks", json={"source": "https://youtu.be/x", "client_id": "alice"}).json()["task_id"]
    assert client.get(f"/v1/tasks/{tid}/artifact", headers=CID).status_code == 409  # not done
    rdir = store.results_dir(tid)
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "transcript.txt").write_text("hello world")
    (rdir / "segments.json").write_text('{"segments":[]}')
    store.update(tid, status="done", stage="done")
    resp = client.get(f"/v1/tasks/{tid}/artifact", headers=CID)
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


def test_bearer_auth(tmp_path, monkeypatch):
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
    assert client.get("/v1/tasks/x").status_code == 401  # no token
    assert client.get("/v1/tasks/x", headers={"Authorization": "Bearer secret",
                                              "X-Client-Id": "alice"}).status_code == 404
    assert client.get("/healthz").status_code == 200
