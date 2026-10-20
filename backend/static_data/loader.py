"""Hero, item, ability and rank data from the community API's static assets.

Nothing about heroes or items is hard-coded (CLAUDE.md, patch-proof rule).
Assets are stored per game client version under data/static/<version>/ and
refreshed when the stored copy is older than max_age_h or a newer client
version appears.

IDs verified in Phase 0: Boon's hero_id, item/ability IDs and ability
class names all join to these assets.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend import config
from backend.ingest.client import DeadlockApiClient

FILES = ("heroes", "items", "ranks")


@dataclass(frozen=True)
class Hero:
    id: int
    name: str
    class_name: str
    playable: bool
    # The hero's four abilities, in order (class names of items with type "ability").
    abilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    """Anything in /v1/assets/items: shop items ("upgrade"), abilities, weapons."""

    id: int
    name: str
    class_name: str
    type: str
    slot_type: str | None
    tier: int | None
    cost: int | None
    ability_type: str | None = None  # for abilities: "signature", "ultimate", ...


class StaticData:
    def __init__(self, client_version: int, heroes: list[dict], items: list[dict], ranks: list[dict]):
        self.client_version = client_version
        self.heroes = {h["id"]: _hero(h) for h in heroes}
        self.items = {i["id"]: _item(i) for i in items}
        self._by_class = {i.class_name: i for i in self.items.values()}
        self.ranks = {r["tier"]: r["name"] for r in ranks}

    # ---- lookups --------------------------------------------------------

    def hero(self, hero_id: int) -> Hero:
        return self.heroes[hero_id]

    def hero_name(self, hero_id: int) -> str:
        h = self.heroes.get(hero_id)
        return h.name if h else f"unknown hero {hero_id}"

    def playable_heroes(self) -> list[Hero]:
        return sorted((h for h in self.heroes.values() if h.playable), key=lambda h: h.name)

    def item(self, item_id: int) -> Item:
        return self.items[item_id]

    def item_by_class_name(self, class_name: str) -> Item | None:
        return self._by_class.get(class_name)

    def rank_name(self, badge: int) -> str:
        """Badge = tier * 10 + subrank (e.g. 104 -> 'Ascendant 4'). 0 = unranked."""
        tier, sub = divmod(int(badge), 10)
        name = self.ranks.get(tier, f"tier {tier}")
        return name if sub == 0 else f"{name} {sub}"

    # ---- storage --------------------------------------------------------

    @classmethod
    def load_dir(cls, directory: Path) -> StaticData:
        directory = Path(directory)
        parts = {n: json.loads((directory / f"{n}.json").read_text(encoding="utf-8")) for n in FILES}
        return cls(int(directory.name), parts["heroes"], parts["items"], parts["ranks"])


def _hero(h: dict) -> Hero:
    # Not filtering on in_development: heroes with that flag do appear in
    # ranked matches (verified: 2 such heroes in hero-stats and our replays).
    playable = bool(h.get("player_selectable")) and not h.get("disabled")
    slots = h.get("items") or {}
    abilities = tuple(slots[k] for k in sorted(slots) if k.startswith("signature"))
    return Hero(id=h["id"], name=h["name"], class_name=h["class_name"], playable=playable, abilities=abilities)


def _item(i: dict) -> Item:
    return Item(
        id=i["id"],
        name=i.get("name") or i["class_name"],
        class_name=i["class_name"],
        type=i.get("type", "unknown"),
        slot_type=i.get("item_slot_type"),
        tier=i.get("item_tier"),
        cost=i.get("cost"),
        ability_type=i.get("ability_type"),
    )


def _write_json(path: Path, body: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    os.replace(tmp, path)


def stored_versions(root: Path | None = None) -> list[int]:
    root = Path(root or config.static_dir())
    if not root.exists():
        return []
    return sorted(int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit() and (p / "meta.json").exists())


def refresh(client: DeadlockApiClient, root: Path | None = None) -> Path:
    """Download assets for the newest client version and store them. Returns the folder."""
    root = Path(root or config.static_dir())
    version = max(client.client_versions())
    directory = root / str(version)
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "heroes.json", client.heroes(version))
    _write_json(directory / "items.json", client.items(version))
    _write_json(directory / "ranks.json", [r.model_dump() for r in client.ranks(version)])
    _write_json(directory / "meta.json", {"client_version": version, "fetched_at": time.time()})
    return directory


def load(
    version: int | None = None,
    *,
    root: Path | None = None,
    client: DeadlockApiClient | None = None,
    max_age_h: float = 24.0,
) -> StaticData:
    """Load static data.

    version=None loads the newest stored version, refreshing first if a client
    is given and the stored copy is missing or older than max_age_h.
    An explicit version loads exactly that stored version (for old matches).
    """
    root = Path(root or config.static_dir())
    if version is not None:
        return StaticData.load_dir(root / str(version))
    versions = stored_versions(root)
    if client is not None and (not versions or _age_h(root / str(versions[-1])) > max_age_h):
        refresh(client, root)
        versions = stored_versions(root)
    if not versions:
        raise FileNotFoundError(f"no static data in {root}; run: python -m backend.static_data refresh")
    return StaticData.load_dir(root / str(versions[-1]))


def _age_h(directory: Path) -> float:
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    return (time.time() - meta["fetched_at"]) / 3600
