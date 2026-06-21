"""File providers: resolve an input SOURCE to a local media file.

A ``FileProvider`` knows how to recognize a kind of source and fetch it to a
local path. The barebone providers are **direct file**, **S3**, and **Google
Drive**; YouTube and plain HTTP URLs are included as extra providers that fit
the same interface.

    resolve("s3://bucket/call.m4a", workdir, opts) -> ResolvedSource(local_path=...)

Optional backends (boto3, googleapiclient, gdown) are imported lazily so each
provider only needs its deps when actually used.

Auth:
- S3:     AWS credential chain, or --aws-profile / --aws-* / env.
- GDrive: service-account JSON (--gdrive-credentials), Application Default
          Credentials, or a public "Anyone with the link" file/folder via gdown
          (--gdrive-public, also auto-tried when authed access is blocked).
- YouTube: optional --cookies for restricted videos.
"""
from __future__ import annotations

import re
import subprocess
import sys
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class SourceOpts:
    aws_profile: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str | None = None
    gdrive_credentials: str | None = None
    gdrive_public: bool = False  # fetch via gdown (public link), skip Drive API
    cookies: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ResolvedSource:
    local_path: Path
    origin: str   # file | s3 | gdrive | youtube | url
    label: str    # canonical source string for the output doc
    name: str     # stem used for naming outputs
    is_temp: bool  # whether local_path is a downloaded temp


# ---------------------------------------------------------------------------
# Source recognition
# ---------------------------------------------------------------------------

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
_GDRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
_GDRIVE_FILE_ID = re.compile(r"/d/([A-Za-z0-9_-]+)|[?&]id=([A-Za-z0-9_-]+)")
_GDRIVE_FOLDER_ID = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_MEDIA_MIME = re.compile(r"^(audio|video)/")
_MEDIA_EXTS = {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".opus",
               ".webm", ".mov", ".mkv", ".aac", ".wma", ".m4v"}
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def classify(source: str) -> str:
    """Return the provider name for a source string."""
    if source.startswith("s3://"):
        return "s3"
    if source.startswith("gdrive://"):
        return "gdrive"
    if source.startswith(("http://", "https://")):
        host = (urlparse(source).hostname or "").lower()
        if host in _YT_HOSTS:
            return "youtube"
        if host in _GDRIVE_HOSTS:
            return "gdrive"
        return "url"
    return "file"


def parse_gdrive_id(source: str) -> tuple[str, str]:
    """Return (kind, id) where kind is 'folder' or 'file'."""
    if source.startswith("gdrive://"):
        return "file", source[len("gdrive://") :]
    mf = _GDRIVE_FOLDER_ID.search(source)
    if mf:
        return "folder", mf.group(1)
    m = _GDRIVE_FILE_ID.search(source)
    if m:
        return "file", (m.group(1) or m.group(2))
    raise ValueError(f"could not extract a Google Drive file/folder id from: {source}")


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Google Drive helpers
# ---------------------------------------------------------------------------

_GDRIVE_AUTH_HINT = (
    "Google Drive access failed. Easiest fix: share the file/folder as "
    "'Anyone with the link' and re-run with --gdrive-public. "
    "Otherwise pass --gdrive-credentials <service-account.json> (and share with "
    "the service-account email)."
)


def _is_auth_error(e: Exception) -> bool:
    s = f"{type(e).__name__}: {e}".lower()
    return any(k in s for k in ("reauth", "refresh", "invalid_grant", "insufficient",
                                "scope", "401", "403", "unauthorized", "permission",
                                "blocked", "denied"))


def _drive_service(opts: SourceOpts):
    """Build a Drive v3 service, or None if no usable auth is available."""
    try:
        from googleapiclient.discovery import build  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Google Drive auth needs: pip install google-api-python-client google-auth"
        ) from e

    if opts.gdrive_credentials:
        from google.oauth2 import service_account  # type: ignore
        creds = service_account.Credentials.from_service_account_file(
            opts.gdrive_credentials, scopes=_DRIVE_SCOPES
        )
        _log("[gdrive] auth: service account")
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        import google.auth  # type: ignore
        creds, _ = google.auth.default(scopes=_DRIVE_SCOPES)
        _log("[gdrive] auth: application default credentials")
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:  # noqa: BLE001
        _log(f"[gdrive] no ADC ({type(e).__name__}); will try public gdown")
        return None


def _drive_download(svc, file_id: str, dst: Path) -> Path:
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore
    meta = svc.files().get(fileId=file_id, fields="name", supportsAllDrives=True).execute()
    dst = dst.parent / meta.get("name", dst.name)
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dst, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    return dst


def _drive_pick_from_folder(svc, folder_id: str) -> str:
    """List a folder and return the id of its single media file (else raise)."""
    resp = svc.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,mimeType,size,modifiedTime)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        orderBy="modifiedTime desc",
    ).execute()
    files = resp.get("files", [])
    media = [f for f in files if _MEDIA_MIME.match(f.get("mimeType", ""))]
    if not media:
        raise RuntimeError(
            f"folder {folder_id} has no audio/video files (found {len(files)} items)"
        )
    if len(media) > 1:
        listing = "\n".join(f"  gdrive://{f['id']}  {f['name']} ({f['mimeType']})" for f in media)
        raise RuntimeError(f"folder has {len(media)} media files — pass one explicitly:\n{listing}")
    _log(f"[gdrive] folder → {media[0]['name']}")
    return media[0]["id"]


def _gdown_fetch(kind: str, gid: str, workdir: Path) -> ResolvedSource:
    """Fetch a public ('Anyone with the link') Drive file or folder via gdown."""
    try:
        import gdown  # type: ignore
    except ImportError as e:
        raise RuntimeError("Public Google Drive download needs `pip install gdown`.") from e

    if kind == "folder":
        paths = gdown.download_folder(id=gid, output=str(workdir), quiet=False) or []
        media = [Path(p) for p in paths if Path(p).suffix.lower() in _MEDIA_EXTS]
        if not media:
            raise RuntimeError(
                f"public folder {gid}: no audio/video found "
                "(is it shared as 'Anyone with the link'?)"
            )
        if len(media) > 1:
            listing = "\n".join(f"  {p}" for p in media)
            raise RuntimeError(f"folder has {len(media)} media files — pass one file link:\n{listing}")
        p = media[0]
        return ResolvedSource(p, "gdrive", f"gdrive:{gid}", p.stem, is_temp=True)

    dst = workdir / f"gdrive_{gid}"
    out = gdown.download(id=gid, output=str(dst), quiet=False)
    if not out:
        raise RuntimeError(_GDRIVE_AUTH_HINT)
    return ResolvedSource(Path(out), "gdrive", f"gdrive:{gid}", Path(out).stem, is_temp=True)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class FileProvider(ABC):
    """Recognizes a kind of source and fetches it to a local file."""

    name: str

    @abstractmethod
    def matches(self, source: str) -> bool: ...

    @abstractmethod
    def fetch(self, source: str, workdir: Path, opts: SourceOpts) -> ResolvedSource: ...


class LocalFileProvider(FileProvider):
    name = "file"

    def matches(self, source: str) -> bool:
        return classify(source) == "file"

    def fetch(self, source: str, workdir: Path, opts: SourceOpts) -> ResolvedSource:
        p = Path(source).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"local file not found: {p}")
        return ResolvedSource(p, "file", str(p), p.stem, is_temp=False)


class S3Provider(FileProvider):
    name = "s3"

    def matches(self, source: str) -> bool:
        return classify(source) == "s3"

    def fetch(self, source: str, workdir: Path, opts: SourceOpts) -> ResolvedSource:
        bucket, _, key = source[len("s3://"):].partition("/")
        if not bucket or not key:
            raise ValueError(f"bad s3 uri (want s3://bucket/key): {source}")
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError("S3 source needs `pip install boto3`") from e
        session_kw = {}
        if opts.aws_profile:
            session_kw["profile_name"] = opts.aws_profile
        if opts.aws_access_key_id and opts.aws_secret_access_key:
            session_kw["aws_access_key_id"] = opts.aws_access_key_id
            session_kw["aws_secret_access_key"] = opts.aws_secret_access_key
        if opts.aws_region:
            session_kw["region_name"] = opts.aws_region
        session = boto3.Session(**session_kw)
        dst = workdir / Path(key).name
        _log(f"[s3] downloading s3://{bucket}/{key}")
        session.client("s3").download_file(bucket, key, str(dst))
        return ResolvedSource(dst, "s3", source, dst.stem, is_temp=True)


class GDriveProvider(FileProvider):
    name = "gdrive"

    def matches(self, source: str) -> bool:
        return classify(source) == "gdrive"

    def fetch(self, source: str, workdir: Path, opts: SourceOpts) -> ResolvedSource:
        kind, gid = parse_gdrive_id(source)
        svc = None if opts.gdrive_public else _drive_service(opts)
        if svc is not None:
            try:
                file_id = _drive_pick_from_folder(svc, gid) if kind == "folder" else gid
                dst = _drive_download(svc, file_id, workdir / f"gdrive_{file_id}")
                return ResolvedSource(dst, "gdrive", f"gdrive:{file_id}", dst.stem, is_temp=True)
            except RuntimeError:
                raise  # already actionable (multiple/no media files)
            except Exception as e:  # noqa: BLE001
                if _is_auth_error(e):
                    _log("[gdrive] authed access blocked; trying public gdown fallback")
                else:
                    raise
        return _gdown_fetch(kind, gid, workdir)


class YouTubeProvider(FileProvider):
    name = "youtube"

    def matches(self, source: str) -> bool:
        return classify(source) == "youtube"

    def fetch(self, source: str, workdir: Path, opts: SourceOpts) -> ResolvedSource:
        out_tmpl = str(workdir / "yt_%(id)s.%(ext)s")
        cmd = [sys.executable, "-m", "yt_dlp", "-f", "140/bestaudio/best",
               "-o", out_tmpl, "--no-playlist", "--print", "after_move:filepath"]
        if opts.cookies:
            cmd += ["--cookies", opts.cookies]
        cmd.append(source)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {res.stderr[-500:]}")
        path = Path(res.stdout.strip().splitlines()[-1])
        vid = re.search(r"[?&]v=([\w-]+)", source) or re.search(r"youtu\.be/([\w-]+)", source)
        label = f"youtube:{vid.group(1)}" if vid else f"youtube:{path.stem}"
        return ResolvedSource(path, "youtube", label, path.stem, is_temp=True)


class HttpUrlProvider(FileProvider):
    name = "url"

    def matches(self, source: str) -> bool:
        return classify(source) == "url"

    def fetch(self, source: str, workdir: Path, opts: SourceOpts) -> ResolvedSource:
        name = Path(source.split("?")[0]).name or "download"
        dst = workdir / name
        _log(f"[url] downloading {source}")
        with urllib.request.urlopen(source) as r, open(dst, "wb") as f:  # noqa: S310
            while chunk := r.read(1 << 20):
                f.write(chunk)
        return ResolvedSource(dst, "url", source, dst.stem, is_temp=True)


# Order matters: specific schemes first, local file as the fallback.
PROVIDERS: list[FileProvider] = [
    S3Provider(),
    GDriveProvider(),
    YouTubeProvider(),
    HttpUrlProvider(),
    LocalFileProvider(),
]


def get_provider(source: str) -> FileProvider:
    for p in PROVIDERS:
        if p.matches(source):
            return p
    return LocalFileProvider()


def resolve(source: str, workdir: Path, opts: SourceOpts | None = None) -> ResolvedSource:
    opts = opts or SourceOpts()
    workdir.mkdir(parents=True, exist_ok=True)
    provider = get_provider(source)
    _log(f"[source] {provider.name}: {source}")
    return provider.fetch(source, workdir, opts)
