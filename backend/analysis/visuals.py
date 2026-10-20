"""Data for the report's charts: souls over time vs the bracket, and where the player died.

Map placement (verified 2026-10-21 against the walker positions in a stored replay and the
relative objective positions from /v1/assets/map, 6 of 6 within 0.0001):
    left = 0.45 + x / (2 * radius),  top = 0.45 - y / (2 * radius)
as fractions of the square minimap image; radius comes from the map asset.
"""

from __future__ import annotations

from backend.analysis.benchmarks import Benchmarks
from backend.analysis.brackets import brackets
from backend.analysis.metrics.replay import ALONE_DISTANCE, death_contexts
from backend.analysis.replay_data import ReplayData
from backend.ingest import DeadlockApiClient
from backend.static_data import StaticData

MAP_OFFSET = 0.45   # see module docstring


def map_info(client: DeadlockApiClient) -> dict | None:
    try:
        m = client.get_json("/v1/assets/map", max_age_s=24 * 3600)
    except Exception:  # the map is decoration; charts still work without it
        return None
    return {"image": m["images"].get("minimap") or m["images"].get("plain"), "radius": m["radius"], "offset": MAP_OFFSET}


def souls_chart(meta: dict, account_id: int, bench: Benchmarks, static: StaticData, hero_id: int,
                bracket_label: str) -> dict:
    me = next(p for p in meta["players"] if p["account_id"] == account_id)
    points = sorted((s["time_stamp_s"], s["net_worth"]) for s in me.get("stats") or [])
    bracket_id = next((b.id for b in brackets(static) if b.label == bracket_label), None)
    curve = bench.souls_curve(hero_id, bracket_id)
    scope = None
    if curve:
        who = static.hero_name(hero_id) if curve["hero_id"] else "all heroes"
        where = next((b.label for b in brackets(static) if b.id == curve["bracket"]), "all ranks")
        scope = f"{who}, {where}"
    return {"player": [{"t": t, "souls": nw} for t, nw in points],
            "bracket": curve["points"] if curve else [], "bracket_scope": scope,
            "duration_s": meta["duration_s"]}


def deaths_map(meta: dict, account_id: int, replay: ReplayData | None) -> list[dict]:
    """Every death with position; 'alone' only when a replay shows where teammates were."""
    me = next(p for p in meta["players"] if p["account_id"] == account_id)
    api = me.get("death_details") or []
    out = []
    if replay is not None:
        for d in death_contexts(replay):
            near = min(api, key=lambda a: abs(a["game_time_s"] - d.game_s), default=None)
            killer = near.get("killer_pos") if near and abs(near["game_time_s"] - d.game_s) <= 3 else None
            out.append({"t": round(d.game_s, 1), "x": d.x, "y": d.y,
                        "alone": d.nearest_ally is None or d.nearest_ally > ALONE_DISTANCE,
                        "nearest_ally": None if d.nearest_ally is None else round(d.nearest_ally),
                        "killer": {"x": killer["x"], "y": killer["y"]} if killer else None})
        return out
    for a in api:
        pos = a.get("death_pos")
        if pos:
            k = a.get("killer_pos")
            out.append({"t": a["game_time_s"], "x": pos["x"], "y": pos["y"], "alone": None, "nearest_ally": None,
                        "killer": {"x": k["x"], "y": k["y"]} if k else None})
    return out
