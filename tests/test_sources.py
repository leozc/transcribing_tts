"""Unit + adversarial tests for source classification & resolution.

Network/auth-dependent backends (S3, Drive download, YouTube) are exercised via
their pure parsing/dispatch logic and with monkeypatched fakes — no live calls.
"""
from pathlib import Path

import pytest

from tts_serve import sources
from tts_serve.sources import (
    GDriveProvider, LocalFileProvider, S3Provider, SourceOpts,
    classify, get_provider, parse_gdrive_id, resolve,
)


# ---------- provider abstraction ----------

@pytest.mark.parametrize("src,provider_cls", [
    ("/abs/a.wav", LocalFileProvider),
    ("relative.mp3", LocalFileProvider),
    ("s3://b/k.m4a", S3Provider),
    ("https://drive.google.com/file/d/ID/view", GDriveProvider),
    ("gdrive://ID", GDriveProvider),
])
def test_get_provider(src, provider_cls):
    assert isinstance(get_provider(src), provider_cls)

def test_local_provider_is_fallback():
    # an unrecognized bare string falls back to the local file provider
    assert isinstance(get_provider("just-a-name"), LocalFileProvider)


# ---------- classify ----------

@pytest.mark.parametrize("src,expected", [
    ("meeting.wav", "file"),
    ("/abs/path/a.mp3", "file"),
    ("./rel/b.mp4", "file"),
    ("s3://bucket/key.m4a", "s3"),
    ("https://www.youtube.com/watch?v=abc123", "youtube"),
    ("https://youtu.be/abc123", "youtube"),
    ("http://youtube.com/watch?v=x", "youtube"),
    ("https://drive.google.com/file/d/ID/view", "gdrive"),
    ("https://drive.google.com/drive/folders/FID", "gdrive"),
    ("gdrive://FILEID", "gdrive"),
    ("https://example.com/audio.m4a", "url"),
    ("https://cdn.example.com/path/to/clip.mp3?token=xyz", "url"),
])
def test_classify(src, expected):
    assert classify(src) == expected

def test_classify_adversarial_youtube_in_path_is_still_youtube():
    # a non-youtube host that merely contains 'youtube.com/' substring
    assert classify("https://notyoutube.com/watch") == "url"  # no youtube.com/ or youtu.be/ token...
    # but a real youtube.com/ substring classifies as youtube (documented behavior)
    assert classify("https://m.youtube.com/watch?v=z") == "youtube"

def test_classify_s3_takes_priority():
    assert classify("s3://b/k") == "s3"


# ---------- parse_gdrive_id ----------

@pytest.mark.parametrize("src,kind,gid", [
    ("https://drive.google.com/drive/folders/1aaXYZ", "folder", "1aaXYZ"),
    ("https://drive.google.com/file/d/ABC_123-x/view", "file", "ABC_123-x"),
    ("gdrive://PLAINID", "file", "PLAINID"),
    ("https://drive.google.com/open?id=QQ9", "file", "QQ9"),
    ("https://drive.google.com/uc?id=DL7&export=download", "file", "DL7"),
])
def test_parse_gdrive_id(src, kind, gid):
    assert parse_gdrive_id(src) == (kind, gid)

def test_parse_gdrive_id_folder_beats_file_pattern():
    # folder URLs must resolve as folders even if a /d/ appears nowhere
    assert parse_gdrive_id("https://drive.google.com/drive/folders/FOLDER1")[0] == "folder"

def test_parse_gdrive_id_no_id_raises():
    with pytest.raises(ValueError):
        parse_gdrive_id("https://drive.google.com/")


# ---------- resolve: local file ----------

def test_resolve_local_file(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"RIFF....")
    rs = resolve(str(f), tmp_path / "work")
    assert rs.origin == "file" and rs.local_path == f and rs.is_temp is False

def test_resolve_missing_local_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve(str(tmp_path / "nope.wav"), tmp_path / "work")


# ---------- resolve: youtube (monkeypatched subprocess) ----------

def test_resolve_youtube_dispatch(tmp_path, monkeypatch):
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = str(tmp_path / "work" / "yt_abc123.m4a")
        stderr = ""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        Path(FakeProc.stdout).parent.mkdir(parents=True, exist_ok=True)
        Path(FakeProc.stdout).write_bytes(b"audio")
        return FakeProc()

    monkeypatch.setattr(sources.subprocess, "run", fake_run)
    rs = resolve("https://youtu.be/abc123", tmp_path / "work")
    assert rs.origin == "youtube"
    assert rs.label == "youtube:abc123"
    assert rs.is_temp is True
    assert "yt_dlp" in captured["cmd"]

def test_resolve_youtube_failure_raises(tmp_path, monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "ERROR: video unavailable"
    monkeypatch.setattr(sources.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(RuntimeError, match="yt-dlp failed"):
        resolve("https://youtu.be/x", tmp_path / "work")


# ---------- resolve: gdrive folder picking (fake Drive service) ----------

class _FakeFiles:
    def __init__(self, listing): self._listing = listing
    def files(self): return self
    def list(self, **kw): return self  # noqa: D401
    def get(self, **kw): self._get = kw; return self
    def get_media(self, **kw): return ("media", kw)
    def execute(self): return {"files": self._listing}

def test_gdrive_folder_single_media(monkeypatch, tmp_path):
    files = [
        {"id": "F1", "name": "talk.m4a", "mimeType": "audio/mp4"},
        {"id": "D1", "name": "notes.txt", "mimeType": "text/plain"},
    ]
    monkeypatch.setattr(sources, "_drive_service", lambda opts: _FakeFiles(files))
    assert sources._drive_pick_from_folder(_FakeFiles(files), "FID") == "F1"

def test_gdrive_folder_multiple_media_raises(monkeypatch):
    files = [
        {"id": "F1", "name": "a.m4a", "mimeType": "audio/mp4"},
        {"id": "F2", "name": "b.mp4", "mimeType": "video/mp4"},
    ]
    with pytest.raises(RuntimeError, match="media files"):
        sources._drive_pick_from_folder(_FakeFiles(files), "FID")

def test_gdrive_folder_no_media_raises():
    files = [{"id": "D1", "name": "notes.txt", "mimeType": "text/plain"}]
    with pytest.raises(RuntimeError, match="no audio/video"):
        sources._drive_pick_from_folder(_FakeFiles(files), "FID")

def test_gdrive_no_auth_falls_back_to_gdown(monkeypatch, tmp_path):
    # with no Drive service, resolve should attempt the public gdown path
    monkeypatch.setattr(sources, "_drive_service", lambda opts: None)
    called = {}
    def fake_gdown(kind, gid, workdir):
        called["args"] = (kind, gid)
        p = workdir / "got.m4a"; p.write_bytes(b"x")
        return sources.ResolvedSource(p, "gdrive", f"gdrive:{gid}", "got", True)
    monkeypatch.setattr(sources, "_gdown_fetch", fake_gdown)
    rs = resolve("https://drive.google.com/drive/folders/FID", tmp_path / "w")
    assert called["args"] == ("folder", "FID") and rs.origin == "gdrive"

def test_gdrive_public_flag_skips_drive_api(monkeypatch, tmp_path):
    # --gdrive-public must NOT call _drive_service at all
    def boom(opts): raise AssertionError("_drive_service should not be called")
    monkeypatch.setattr(sources, "_drive_service", boom)
    monkeypatch.setattr(sources, "_gdown_fetch",
                        lambda kind, gid, wd: sources.ResolvedSource(wd / "f", "gdrive", gid, "f", True))
    rs = resolve("https://drive.google.com/file/d/ABC/view", tmp_path / "w",
                 sources.SourceOpts(gdrive_public=True))
    assert rs.origin == "gdrive"

def test_gdrive_auth_error_falls_back_to_gdown(monkeypatch, tmp_path):
    # authed access blocked -> should fall back to gdown, not raise
    class FakeSvc:
        def files(self): return self
        def get(self, **k): return self
        def execute(self): raise Exception("403 insufficient permission")
    monkeypatch.setattr(sources, "_drive_service", lambda opts: FakeSvc())
    monkeypatch.setattr(sources, "_gdown_fetch",
                        lambda kind, gid, wd: sources.ResolvedSource(wd / "f", "gdrive", gid, "f", True))
    rs = resolve("https://drive.google.com/file/d/ABC/view", tmp_path / "w")
    assert rs.origin == "gdrive"

def test_is_auth_error_classification():
    class RefreshError(Exception): pass
    assert sources._is_auth_error(RefreshError("reauth required"))
    assert sources._is_auth_error(Exception("403 insufficient scopes"))
    assert not sources._is_auth_error(ValueError("bad uri"))


# ---------- resolve: s3 dispatch (fake boto3) ----------

def test_s3_bad_uri_raises(tmp_path):
    with pytest.raises(ValueError, match="bad s3 uri"):
        resolve("s3://bucket-only", tmp_path / "w")
