"""Build API-shaped match metadata from a stored replay.

The replay's post-match stat snapshots equal the community API's metadata
stats (verified on fixture 120851860: net worth, kills, creep kills, hero and
objective damage identical at every snapshot; final last hits identical).
So when the API doesn't have a match yet, a replay can stand in for it and
the summary code runs unchanged.

Not in the replay: shots hit/missed, so the accuracy metric is null.
Final kills/assists come from the last snapshot, which can miss an event in
the last seconds (seen once: 2 kills vs the API's 3). The kills table is
worse: it over-counts assists for most players, so it isn't used for totals.
Players other than the requester get placeholder account IDs (-1 .. -12).
"""

from __future__ import annotations

import polars as pl

from backend.analysis.replay_data import ReplayData


def metadata_from_replay(rd: ReplayData) -> dict:
    if rd.stat_snapshots is None or rd.stat_snapshots.is_empty() or rd.match is None:
        raise LookupError(f"match {rd.match_id} was parsed before stat snapshots were stored; parse it again")
    players = []
    for p in rd.players.sort("slot").iter_rows(named=True):
        slot = p["slot"]
        snaps = rd.stat_snapshots.filter(pl.col("slot") == slot).sort("time_s")
        last = snaps.row(-1, named=True)
        buys = rd.purchases.filter(pl.col("slot") == slot).sort("tick")
        items = []
        for b in buys.filter(pl.col("change").is_in(["purchased", "upgraded"])).iter_rows(named=True):
            sold = buys.filter((pl.col("item_id") == b["item_id"]) & (pl.col("change") == "sold") & (pl.col("tick") > b["tick"]))
            items.append({"item_id": b["item_id"], "game_time_s": int(b["game_s"]),
                          "sold_time_s": int(sold["game_s"][0]) if sold.height else 0})
        deaths = rd.kills.filter(pl.col("victim_slot") == slot)["game_s"].to_list()
        players.append({
            "account_id": p["account_id"] if p["is_requester"] else -(slot + 1),
            "team": p["team"], "hero_id": p["hero_id"],
            "kills": last["kills"], "deaths": last["deaths"], "assists": last["assists"],
            "net_worth": last["net_worth"], "last_hits": p["last_hits"] or 0, "denies": last["denies"],
            "player_rank_data": {"initial_display_rank": p["rank_badge"]} if p["rank_badge"] else None,
            "stats": [{"time_stamp_s": s["time_s"], "net_worth": s["net_worth"], "creep_kills": s["creep_kills"],
                       "player_damage": s["player_damage"], "boss_damage": s["boss_damage"],
                       "shots_hit": 0, "shots_missed": 0} for s in snaps.iter_rows(named=True)],
            "items": items,
            "death_details": [{"game_time_s": int(t)} for t in deaths],
        })
    return {
        "match_id": rd.match_id,
        "duration_s": int(round(rd.match["duration_s"])),
        "winning_team": rd.match["winning_team"],
        "players": players,
        "source": "replay",
    }
