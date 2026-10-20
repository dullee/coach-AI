"""Fetch a match replay (.dem) by match ID.

Route (verified in Phase 0): /v1/matches/{id}/salts -> demo_url on Valve's
replay servers. The file is named .dem.bz2 but is actually zstd-compressed;
we detect the real format from its first bytes. Replays exist only for some
matches and only for roughly the first 2-3 weeks.

Downloads are ~200 MB, so call this from a background job, not a web request.
"""

from __future__ import annotations

import bz2
import hashlib
import logging
import os
import shutil
from pathlib import Path

import httpx
import zstandard

from backend import config
from backend.ingest.client import DeadlockApiClient, NotFound

log = logging.getLogger(__name__)

DEMO_MAGIC = b"PBDEMS2\x00"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
BZ2_MAGIC = b"BZh"
CHUNK = 1 << 20


class ReplayUnavailable(Exception):
    """The API has no replay link for this match, or Valve no longer has the file."""


class InvalidReplay(Exception):
    pass


def detect_format(head: bytes) -> str:
    if head.startswith(DEMO_MAGIC):
        return "dem"
    if head.startswith(ZSTD_MAGIC):
        return "zstd"
    if head.startswith(BZ2_MAGIC):
        return "bz2"
    raise InvalidReplay(f"unknown replay format (first bytes {head[:8]!r})")


def decompress_to(src: Path, dest: Path) -> None:
    """Write the plain .dem for src (raw, zstd or bz2) to dest."""
    with open(src, "rb") as f:
        fmt = detect_format(f.read(8))
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        if fmt == "dem":
            shutil.copyfileobj(fin, fout, CHUNK)
        elif fmt == "zstd":
            zstandard.ZstdDecompressor().copy_stream(fin, fout, read_size=CHUNK, write_size=CHUNK)
        else:
            with bz2.open(fin) as b:
                shutil.copyfileobj(b, fout, CHUNK)
    with open(tmp, "rb") as f:
        ok = f.read(8).startswith(DEMO_MAGIC)
    if not ok:  # unlink after closing: Windows can't delete open files
        tmp.unlink(missing_ok=True)
        raise InvalidReplay(f"{src.name} did not decompress to a Source 2 demo")
    os.replace(tmp, dest)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def fetch_replay(
    match_id: int,
    *,
    client: DeadlockApiClient,
    dest_dir: Path | None = None,
    http: httpx.Client | None = None,
) -> Path:
    """Download and decompress the replay for match_id; return the .dem path.

    Returns the existing file without downloading if it is already present.
    Raises ReplayUnavailable when there is no replay to fetch.
    """
    dest_dir = Path(dest_dir or config.demos_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(dest_dir.glob(f"{match_id}_*.dem"))
    if existing:
        return existing[0]

    try:
        salts = client.match_salts(match_id)
    except NotFound as e:
        raise ReplayUnavailable(f"match {match_id} is unknown to the community API") from e
    if not salts.demo_url:
        raise ReplayUnavailable(f"the community API has no replay link for match {match_id}")

    name = f"{match_id}_{salts.replay_salt}"
    download = dest_dir / f"{name}.download"
    own_http = http is None
    http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True)
    try:
        log.info("downloading replay %s", salts.demo_url)
        with http.stream("GET", salts.demo_url) as resp:
            if resp.status_code == 404:
                raise ReplayUnavailable(f"replay for match {match_id} has expired on Valve's servers")
            resp.raise_for_status()
            with open(download, "wb") as out:
                for chunk in resp.iter_bytes(CHUNK):
                    out.write(chunk)
        dem = dest_dir / f"{name}.dem"
        decompress_to(download, dem)
        return dem
    finally:
        download.unlink(missing_ok=True)
        if own_http:
            http.close()
