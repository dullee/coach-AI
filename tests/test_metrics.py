from __future__ import annotations

import polars as pl
import pytest

from backend.analysis.brackets import ALL, bracket_of, brackets, next_bracket_up, tier_to_bracket_map
from backend.analysis.frame import SCHEMA, from_metadata
from backend.analysis.metrics import METRICS, compute_all
from backend.analysis.metrics.base import value_at
from tests.conftest import FIXTURES, fixture_metadata

MATCH_IDS = [f["match_id"] for f in FIXTURES]


# ---- brackets ---------------------------------------------------------------

def test_brackets_cover_every_ranked_tier_once(static):
    bs = brackets(static)
    tiers = [t for b in bs for t in b.tiers]
    assert sorted(tiers) == sorted(t for t in static.ranks if t > 0)
    assert len(tiers) == len(set(tiers))
    assert all(len(b.tiers) in (2, 3) for b in bs)
    assert len(bs[-1].tiers) == 3  # top tier merged into the bracket below
    for b in bs:  # labels come from static data, not code
        assert static.ranks[b.tiers[0]] in b.label and static.ranks[b.tiers[-1]] in b.label


def test_bracket_lookup(static):
    bs = brackets(static)
    assert bracket_of(0, static) is None and bracket_of(None, static) is None
    assert bracket_of(11, static) == bs[0]            # tier 1, subrank 1
    assert bracket_of(116, static) == bs[-1]          # top tier
    assert next_bracket_up(bs[0], static) == bs[1]
    assert next_bracket_up(bs[-1], static) == bs[-1]
    assert ALL not in tier_to_bracket_map(static).values()


# ---- frame ------------------------------------------------------------------

@pytest.mark.parametrize("match_id", MATCH_IDS)
def test_frame_from_real_metadata(match_id):
    meta = fixture_metadata(match_id)
    f = from_metadata(meta)
    assert f.height == 12
    assert set(SCHEMA) <= set(f.columns)
    for team in (0, 1):
        t = f.filter(pl.col("team") == team)
        enemy = f.filter(pl.col("team") == 1 - team)
        assert t["team_kills"].unique().to_list() == [t["kills"].sum()]
        assert t["enemy_deaths"].unique().to_list() == [enemy["deaths"].sum()]
    assert f["account_id"].to_list() == [p["account_id"] for p in meta["players"]]  # row order kept
    assert f.filter(pl.col("won"))["team"].unique().to_list() == [meta["winning_team"]]


def test_lake_and_metadata_frames_have_same_schema(lake_frame):
    meta_frame = from_metadata(fixture_metadata(MATCH_IDS[0]))
    for col, dtype in meta_frame.schema.items():
        assert lake_frame.schema[col] == dtype, col


# ---- metric helpers -----------------------------------------------------------

def test_value_at_interpolates_between_snapshots():
    df = pl.DataFrame({"t": [[180, 360, 540, 720]], "v": [[100, 400, 700, 1000]]})
    assert value_at(df, "t", "v", 360)[0] == 400       # exact snapshot
    assert value_at(df, "t", "v", 600)[0] == pytest.approx(800)
    assert value_at(df, "t", "v", 800)[0] is None      # after the last snapshot
    assert value_at(df, "t", "v", 100)[0] is None      # before the first snapshot


@pytest.mark.parametrize("match_id", MATCH_IDS)
def test_metrics_on_real_matches(match_id, static):
    meta = fixture_metadata(match_id)
    f = from_metadata(meta)
    m = compute_all(f, static)
    assert m.height == 12 and set(m.columns) == set(METRICS)
    expected_spm = f["net_worth"] / (meta["duration_s"] / 60)
    assert (m["souls_per_min"] - expected_spm).abs().max() < 1e-6
    deaths_lane = [sum(1 for t in p["death_details"] if t["game_time_s"] < 600) for p in meta["players"]]
    assert m["deaths_0_10"].to_list() == deaths_lane
    kp = m["kill_participation"].drop_nulls()
    assert kp.is_between(0, 1).all()
    # Net worth only grows, so the 0-10 rate is below the 10-20 rate for most players.
    assert (m["souls_per_min_0_10"] < m["souls_per_min_10_20"]).sum() >= 9


def test_metrics_on_lake_sample_are_mostly_present(lake_frame, static):
    m = compute_all(lake_frame, static)
    for metric_id in METRICS:
        share = m[metric_id].is_not_null().mean()
        floor = 0.5 if metric_id in ("souls_per_min_10_20", "accuracy", "third_big_item_s") else 0.9
        assert share > floor, f"{metric_id} present in only {share:.0%} of rows"
    assert m["kill_participation"].drop_nulls().is_between(0, 1).all()
