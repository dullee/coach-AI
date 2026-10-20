"""Progress across matches: recurring issues, stat trends, and "did you follow last match's focus".

No model calls: each match is analysed with the same code as the summary
(issues + every metric value) and cached in the match_analysis table.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from backend import db
from backend.analysis.benchmarks import Benchmarks
from backend.analysis.brackets import brackets, next_bracket_up
from backend.analysis.frame import from_metadata
from backend.analysis.issues import BY_ID, issue_label
from backend.analysis.metrics import METRICS, compute_all
from backend.ingest import DeadlockApiClient, NotFound, RateLimited
from backend.static_data import StaticData
from backend.summarize.builder import build_summary
from backend.summarize.sources import match_data

# Bump when detectors or metrics change, so cached analyses are recomputed.
ANALYSIS_VERSION = "2026-10-21.1"   # + replay-moment detectors

# Stats shown as trends (in this order).
TREND_METRICS = ["souls_per_min_0_10", "last_hits_0_10", "souls_per_min", "deaths_per_10_min",
                 "kill_participation", "damage_per_min", "objective_damage_per_min"]


def _date(meta: dict) -> str | None:
    t = meta.get("start_time")
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
    if isinstance(t, str) and len(t) >= 10:
        return t[:10]
    return None


def analyse_match(client: DeadlockApiClient, engine: Engine, static: StaticData, bench: Benchmarks,
                  account_id: int, match_id: int, fallback_badge: int | None) -> dict | None:
    """Issues and metric values for one of the player's matches (cached). None if no data."""
    built = bench.meta.get("built_at")
    t = db.match_analysis
    with engine.connect() as conn:
        row = conn.execute(t.select().where((t.c.match_id == match_id) & (t.c.account_id == account_id))
                           .order_by(t.c.id.desc())).mappings().first()
    if row and row["benchmarks_built_at"] == built and (row["info"] or {}).get("analysis_version") == ANALYSIS_VERSION:
        replay_now = _has_replay(engine, match_id, account_id)
        if not (replay_now and row["analysis_level"] == "free"):   # re-analyse once a replay appears
            return dict(row)
    try:
        meta, replay = match_data(client, match_id, account_id, engine)
    except (NotFound, RateLimited):
        return None
    if not any(p["account_id"] == account_id for p in meta["players"]):
        return None
    summary = build_summary(meta, account_id, static, bench, replay, fallback_badge)
    frame = from_metadata(meta)
    idx = frame["account_id"].to_list().index(account_id)
    values = compute_all(frame, static).row(idx, named=True)
    label_to_id = {b.label: b.id for b in brackets(static)}
    k, d, a = summary.key_stats.kda
    info = {"hero": summary.player.hero, "hero_id": int(frame["hero_id"][idx]), "result": summary.player.result,
            "date": _date(meta), "kda": f"{k}/{d}/{a}", "bracket": label_to_id.get(summary.player.rank_bracket),
            "rank_bracket": summary.player.rank_bracket, "analysis_version": ANALYSIS_VERSION}
    record = {"match_id": match_id, "account_id": account_id, "benchmarks_built_at": built,
              "analysis_level": summary.analysis_level, "info": info,
              "summary_json": summary.model_dump(mode="json", exclude_none=True), "metrics": values}
    with engine.begin() as conn:
        conn.execute(delete(t).where((t.c.match_id == match_id) & (t.c.account_id == account_id)))
        conn.execute(t.insert().values(**record))
    return record


def _has_replay(engine: Engine, match_id: int, account_id: int) -> bool:
    with engine.connect() as conn:
        return conn.execute(select(db.match_players.c.match_id).where(
            (db.match_players.c.match_id == match_id) & (db.match_players.c.account_id == account_id))).first() is not None


def player_match_ids(client: DeadlockApiClient, engine: Engine, account_id: int) -> list[int]:
    """The player's normal-mode matches the API knows plus every replay parsed for them, newest first."""
    ids = {m.match_id for m in client.match_history(account_id) if m.game_mode == 1}
    with engine.connect() as conn:
        ids |= {r[0] for r in conn.execute(select(db.match_players.c.match_id)
                                           .where(db.match_players.c.account_id == account_id))}
    return sorted(ids, reverse=True)   # match IDs grow over time


def progress(client: DeadlockApiClient, engine: Engine, static: StaticData, bench: Benchmarks,
             account_id: int, last_n: int = 10) -> dict:
    badge = client.player_rank(account_id).badge or None
    analyses = []
    for mid in player_match_ids(client, engine, account_id)[:last_n]:
        a = analyse_match(client, engine, static, bench, account_id, mid, badge)
        if a:
            analyses.append(a)
    n = len(analyses)
    counts = Counter(i["id"] for a in analyses for i in a["summary_json"]["issues"])
    recurring = []
    for issue_id, count in counts.most_common():
        seen = [a for a in analyses if any(i["id"] == issue_id for i in a["summary_json"]["issues"])]
        sev = [i["severity"] for a in seen for i in a["summary_json"]["issues"] if i["id"] == issue_id]
        streak = 0
        for a in analyses:   # newest first: how many of the latest matches in a row
            if any(i["id"] == issue_id for i in a["summary_json"]["issues"]):
                streak += 1
            else:
                break
        recurring.append({"id": issue_id, "label": issue_label(issue_id), "category": BY_ID[issue_id].category if issue_id in BY_ID else "",
                          "count": count, "of": n, "avg_severity": round(sum(sev) / len(sev), 2), "latest_streak": streak,
                          "in_matches": [a["match_id"] for a in seen]})
    oldest_first = list(reversed(analyses))
    trends = []
    for m in TREND_METRICS:
        metric = METRICS[m]
        trends.append({"metric": m, "label": metric.label, "unit": metric.unit, "window": metric.window,
                       "higher_is_better": metric.higher_is_better,
                       "values": [a["metrics"].get(m) for a in oldest_first]})
    return {
        "matches": [{"match_id": a["match_id"], **a["info"], "analysis_level": a["analysis_level"],
                     "issues": [i["id"] for i in a["summary_json"]["issues"]]} for a in oldest_first],
        "recurring": recurring,
        "trends": trends,
    }


def follow_up(client: DeadlockApiClient, engine: Engine, static: StaticData, bench: Benchmarks,
              account_id: int, match_id: int) -> dict | None:
    """Compare this match with the focus of the player's previous coached match."""
    r = db.coaching_reports
    with engine.connect() as conn:
        prev = conn.execute(r.select().where((r.c.account_id == account_id) & (r.c.match_id < match_id) & r.c.valid)
                            .order_by(r.c.match_id.desc(), r.c.id.desc())).mappings().first()
    if prev is None:
        return None
    badge = client.player_rank(account_id).badge or None
    before = analyse_match(client, engine, static, bench, account_id, prev["match_id"], badge)
    now = analyse_match(client, engine, static, bench, account_id, match_id, badge)
    if before is None or now is None:
        return None
    now_issues = {i["id"]: i for i in now["summary_json"]["issues"]}
    cited = []
    for p in prev["report_json"]["priorities"]:
        for iid in p["issue_ids"]:
            if iid in BY_ID and iid not in cited:
                cited.append(iid)
    items = []
    for iid in cited:
        det = BY_ID[iid]
        item = {"issue_id": iid, "label": issue_label(iid)}
        if det.metric:
            metric = METRICS[det.metric]
            b, a = before["metrics"].get(det.metric), now["metrics"].get(det.metric)
            bracket = now["info"].get("bracket")
            if iid == "late_core_items":   # that detector compares one bracket up
                up = next_bracket_up(next((x for x in brackets(static) if x.id == bracket), None), static)
                bracket = up.id if up else bracket
            dist = bench.distribution(det.metric, now["info"]["hero_id"], bracket)
            item.update({"unit": metric.unit, "window": metric.window, "before": b, "after": a,
                         "median": dist.p50 if dist else None, "higher_is_better": metric.higher_is_better})
            if a is None or b is None:
                item["status"] = "no data"
            elif a == b:
                item["status"] = "same"
            else:
                item["status"] = "improved" if (a > b) == metric.higher_is_better else "worse"
        else:
            item["status"] = "still detected" if iid in now_issues else "not detected this match"
            if iid in now_issues:
                item.update({"after": now_issues[iid]["evidence"]["player_value"], "unit": now_issues[iid]["evidence"]["unit"]})
        items.append(item)
    return {"previous_match_id": prev["match_id"], "previous_hero": before["info"]["hero"],
            "same_hero": before["info"]["hero_id"] == now["info"]["hero_id"],
            "previous_focus": prev["report_json"]["next_match_focus"], "items": items}
