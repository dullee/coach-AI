"""Find the player's downloaded Deadlock replays on this PC.

In-game downloads go to <Steam library>\\steamapps\\common\\Deadlock\\game\\citadel\\replays\\<match_id>.dem
(verified 2026-10-20). Set COACH_REPLAYS_DIR to point elsewhere; otherwise
common Steam library locations on every drive are checked.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

from backend import config

SUFFIX = Path("steamapps") / "common" / "Deadlock" / "game" / "citadel" / "replays"


def replay_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("COACH_REPLAYS_DIR"):
        dirs.append(Path(os.environ["COACH_REPLAYS_DIR"]))
    for drive in string.ascii_uppercase:
        root = Path(f"{drive}:/")
        if not root.exists():
            continue
        for lib in ("SteamLibrary", "Steam", "Program Files (x86)/Steam", "Program Files/Steam", "Games/Steam"):
            d = root / lib / SUFFIX
            if d.is_dir():
                dirs.append(d)
    # Not data/demos: it holds the test fixtures (other players' matches).
    dirs.append(config.data_dir() / "uploads")
    return list(dict.fromkeys(dirs))


def local_replays() -> dict[int, Path]:
    """match_id -> .dem path (complete downloads only; .dem.partial files are skipped)."""
    found: dict[int, Path] = {}
    for d in replay_dirs():
        if not d.is_dir():
            continue
        for f in d.glob("*.dem"):
            head = f.stem.split("_")[0]
            if head.isdigit():
                found.setdefault(int(head), f)
    return found
