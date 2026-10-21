"""Death replays: the seconds before each of the player's deaths, rebuilt from the replay for a
top-down map animation in the report (Medium tier; no video needed).

For each death: every player's position and health at FPS frames per second, the player's camera
direction, the damage the player took and dealt, kills nearby, and a few facts computed from those.
Only the browser receives this; it never goes to a coaching model.

Not available: what the player could see. No Boon dataset records visibility, so the page shows
every enemy and says so.

Camera direction (verified 2026-10-21 on a stored replay): `yaw` is in degrees in the same frame
as world x/y, i.e. facing = (cos yaw, sin yaw). It matched the direction of movement with a
median error of 18 degrees over 681 moving samples; yaw +/- 90 or -yaw did not (80-134 degrees).
"""

from __future__ import annotations

import math
from pathlib import Path

import polars as pl
from sqlalchemy import select
from sqlalchemy.engine import Engine

from backend import config, db
from backend.analysis.metrics.replay import ALONE_DISTANCE
from backend.static_data import StaticData

WINDOW_BEFORE_S = 15.0
WINDOW_AFTER_S = 2.0
FPS = 8                       # frames per second sent to the browser (it interpolates between them)
FRAME_TOLERANCE_S = 0.25      # a frame takes the nearest timeline row within this much match time
RELEVANT_DISTANCE = 5000.0    # players this close to the player at some point are "in the scene"
HALF_HP = 50                  # "dropped below half health" threshold, percent

TIMELINE_COLUMNS = ("slot", "game_s", "x", "y", "yaw", "health", "max_health", "is_alive")


def _rows(conn, table, *where) -> list[dict]:
    return [dict(r) for r in conn.execute(select(table).where(table.c.match_id == where[0], *where[1:])).mappings()]


def death_replays(match_id: int, account_id: int, engine: Engine, static: StaticData,
                  data_dir: Path | None = None) -> dict:
    """All of the player's deaths in a stored replay. Raises LookupError if the replay of this
    match was not parsed for this account."""
    data_dir = Path(data_dir or config.data_dir())
    mp = db.match_players
    with engine.connect() as conn:
        players = _rows(conn, mp, match_id)
        me = next((p for p in players if p["account_id"] == account_id), None)
        if me is None:
            raise LookupError(f"no stored replay of match {match_id} parsed for account {account_id}")
        match = dict(conn.execute(select(db.matches).where(db.matches.c.match_id == match_id)).mappings().one())
        kills = sorted(_rows(conn, db.kills, match_id), key=lambda k: k["tick"])
        dmg = db.damage
        damage = [dict(r) for r in conn.execute(
            select(dmg.c.game_s, dmg.c.attacker_slot, dmg.c.victim_slot, dmg.c.damage, dmg.c.ability_id)
            .where((dmg.c.match_id == match_id) & (dmg.c.damage > 0)
                   & ((dmg.c.victim_slot == me["slot"]) | (dmg.c.attacker_slot == me["slot"])))).mappings()]

    slot = me["slot"]
    role = {p["slot"]: "you" if p["slot"] == slot else ("ally" if p["team"] == me["team"] else "enemy") for p in players}
    deaths = [k for k in kills if k["victim_slot"] == slot and k["game_s"] is not None]
    roster = [{"slot": p["slot"], "hero": static.hero_name(p["hero_id"]), "role": role[p["slot"]],
               "icon": (static.heroes[p["hero_id"]].minimap_image if p["hero_id"] in static.heroes else None)}
              for p in sorted(players, key=lambda p: p["slot"])]
    out = {"match_id": match_id, "window": [-WINDOW_BEFORE_S, WINDOW_AFTER_S], "fps": FPS,
           "players": roster, "deaths": []}
    if not deaths:
        return out

    windows = [(d["game_s"] - WINDOW_BEFORE_S - FRAME_TOLERANCE_S, d["game_s"] + WINDOW_AFTER_S + FRAME_TOLERANCE_S)
               for d in deaths]
    in_any = pl.any_horizontal([pl.col("game_s").is_between(lo, hi) for lo, hi in windows])
    timeline = (pl.scan_parquet(data_dir / match["timeline_path"]).filter(in_any)
                .select(*TIMELINE_COLUMNS).collect().sort("game_s"))
    slots = [p["slot"] for p in roster]
    for n, d in enumerate(deaths, 1):
        out["deaths"].append(_one_death(n, d, timeline, slots, role, slot, kills, damage, static))
    return out


def _item_name(static: StaticData, ability_id) -> str | None:
    it = static.items.get(int(ability_id)) if ability_id is not None else None
    return it.name if it else None


def _one_death(n: int, death: dict, timeline: pl.DataFrame, slots: list[int], role: dict[int, str], me: int,
               kills: list[dict], damage: list[dict], static: StaticData) -> dict:
    t0 = death["game_s"]
    n_frames = int(round((WINDOW_BEFORE_S + WINDOW_AFTER_S) * FPS)) + 1
    rel = [round(-WINDOW_BEFORE_S + i / FPS, 3) for i in range(n_frames)]
    grid = (pl.DataFrame({"frame": list(range(n_frames)), "game_s": [t0 + r for r in rel]})
            .join(pl.DataFrame({"slot": slots}, schema={"slot": timeline.schema["slot"]}), how="cross")
            .sort("game_s"))
    near = timeline.filter(pl.col("game_s").is_between(t0 - WINDOW_BEFORE_S - FRAME_TOLERANCE_S,
                                                       t0 + WINDOW_AFTER_S + FRAME_TOLERANCE_S))
    frames = (grid.join_asof(near, on="game_s", by="slot", strategy="nearest", tolerance=FRAME_TOLERANCE_S,
                            check_sortedness=False)   # both sides are sorted on game_s above
              .sort("slot", "frame"))

    tracks = {}
    for s in slots:
        f = frames.filter(pl.col("slot") == s)
        alive = [bool(a) if a is not None else False for a in f["is_alive"].to_list()]
        xs, ys = f["x"].to_list(), f["y"].to_list()
        hp = [None if not a or h is None or not mh else max(0, min(100, round(100 * h / mh)))
              for a, h, mh in zip(alive, f["health"].to_list(), f["max_health"].to_list())]
        track = {"slot": s, "x": [None if v is None else round(v) for v in xs],
                 "y": [None if v is None else round(v) for v in ys], "hp": hp}
        if s == me:
            track["yaw"] = [None if v is None else round(v) for v in f["yaw"].to_list()]
        tracks[s] = track

    lo, hi = t0 - WINDOW_BEFORE_S, t0 + WINDOW_AFTER_S

    def when(g: float) -> float:
        return round(g - t0, 2)

    taken = [{"t": when(h["game_s"]), "from": h["attacker_slot"], "dmg": int(h["damage"]),
              "what": _item_name(static, h["ability_id"])}
             for h in damage if h["victim_slot"] == me and lo <= h["game_s"] <= t0 + 0.05]
    dealt = [{"t": when(h["game_s"]), "to": h["victim_slot"], "dmg": int(h["damage"]),
              "what": _item_name(static, h["ability_id"])}
             for h in damage if h["attacker_slot"] == me and h["victim_slot"] is not None and lo <= h["game_s"] <= hi]
    nearby_kills = [{"t": when(k["game_s"]), "victim": k["victim_slot"], "killer": k["attacker_slot"]}
                    for k in kills if k["game_s"] is not None and lo <= k["game_s"] <= hi]
    taken.sort(key=lambda h: h["t"]); dealt.sort(key=lambda h: h["t"])

    # who was in the scene: near the player while both were alive, or exchanged damage, or got the kill
    involved = {h["from"] for h in taken} | {h["to"] for h in dealt} | {death["attacker_slot"]} \
        | set(death.get("assister_slots") or [])
    mine = tracks[me]
    for s, tr in tracks.items():
        close = any(
            x is not None and mx is not None and hp is not None and mhp is not None
            and math.hypot(x - mx, y - my) <= RELEVANT_DISTANCE
            for x, y, hp, mx, my, mhp in zip(tr["x"], tr["y"], tr["hp"], mine["x"], mine["y"], mine["hp"]))
        tr["in_scene"] = s == me or close or s in involved   # drawn
        tr["involved"] = s == me or s in involved             # the map zooms to these

    return {"n": n, "t": round(t0, 1), "killer": death["attacker_slot"],
            "assisters": list(death.get("assister_slots") or []),
            "tracks": [tracks[s] for s in slots], "taken": taken, "dealt": dealt, "kills": nearby_kills,
            "facts": _facts(tracks, role, me, taken, dealt)}


def _facts(tracks: dict[int, dict], role: dict[int, str], me: int, taken: list[dict], dealt: list[dict]) -> dict:
    """Plain numbers for the summary under the animation (all from the replay)."""
    i = int(WINDOW_BEFORE_S * FPS) - 1          # the last frame before the death
    mine = tracks[me]
    mx, my = mine["x"][i], mine["y"][i]
    ally_d, enemies_near = [], 0
    if mx is not None:
        for s, tr in tracks.items():
            if s == me or tr["hp"][i] is None or tr["x"][i] is None:
                continue
            dist = math.hypot(tr["x"][i] - mx, tr["y"][i] - my)
            if role[s] == "ally":
                ally_d.append(dist)
            elif dist <= ALONE_DISTANCE:
                enemies_near += 1
    by_source: dict = {}
    for h in taken:
        by_source[h["from"]] = by_source.get(h["from"], 0) + h["dmg"]
    # seconds between last dropping below half health and dying (None if never at/above half in the window)
    below_half_s = None
    for j in range(i, -1, -1):
        v = mine["hp"][j]
        if v is not None and v >= HALF_HP:
            below_half_s = round((i + 1 - j) / FPS, 1) if j < i else 0.0
            break
    start_hp = next((v for v in mine["hp"] if v is not None), None)
    return {
        "damage_taken": sum(h["dmg"] for h in taken),
        "damage_by_source": [{"from": s, "dmg": v} for s, v in sorted(by_source.items(), key=lambda kv: -kv[1])],
        "damage_dealt": sum(h["dmg"] for h in dealt if h["t"] <= 0),
        "below_half_hp_s": below_half_s,
        "nearest_ally": round(min(ally_d)) if ally_d else None,
        "alone": not ally_d or min(ally_d) > ALONE_DISTANCE,
        "enemies_near": enemies_near,
        "hp_at_start": start_hp,
        "alone_distance": ALONE_DISTANCE,
    }
