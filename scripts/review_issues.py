"""Write a review sheet of detected issues for your own recent matches (Phase 2 "Done when").

    python scripts/review_issues.py --account-id <your account id> [--matches 10]

Uses Free-tier data (API only) for each match, plus the stored replay when the
match was parsed with python -m backend.parse. Output goes to
data/review/issues_<account>.md (data/ is private and gitignored).
For each issue, mark whether it is reasonable: [x] agree / [ ] disagree.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.analysis.benchmarks import Benchmarks  # noqa: E402
from backend.analysis.replay_data import ReplayData  # noqa: E402
from backend.db import get_engine  # noqa: E402
from backend.ingest import DeadlockApiClient, NotFound  # noqa: E402
from backend.static_data import load  # noqa: E402
from backend.summarize.builder import build_summary, estimate_tokens  # noqa: E402


def fmt_time(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-id", type=int, required=True)
    ap.add_argument("--matches", type=int, default=10)
    args = ap.parse_args()

    engine = get_engine()
    bench = Benchmarks.load()
    lines = [f"# Issue review: account {args.account_id}", "",
             f"Benchmarks: patch {bench.meta['patch_start'][:10]}, {bench.meta['matches']:,} ranked matches.",
             "Mark each issue: [x] reasonable, or leave [ ] and add a note.", ""]
    with DeadlockApiClient() as client:
        static = load(client=client)
        history = [m for m in client.match_history(args.account_id) if m.game_mode == 1][: args.matches]
        current_badge = client.player_rank(args.account_id).badge or None
        for h in history:
            try:
                meta = client.own_match_metadata(h.match_id)
            except NotFound:
                lines += [f"## Match {h.match_id}: metadata not available", ""]
                continue
            try:
                replay = ReplayData.load(h.match_id, h.hero_id, engine)
            except LookupError:
                replay = None
            s = build_summary(meta, args.account_id, static, bench, replay, fallback_badge=current_badge)
            when = datetime.fromtimestamp(h.start_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            k, d, a = s.key_stats.kda
            if s.player.bracket_basis == "estimated_from_stats":
                lvl = s.player.estimated_level or ""
                said = (f"this match's stats are below typical {lvl.removeprefix('below ')} players" if lvl.startswith("below ")
                        else f"this match's stats match {lvl} players")
                bracket = f"unranked; {said}, so compared with {s.player.rank_bracket} (just above)"
            else:
                bracket = s.player.rank_bracket
            lines += [
                f"## Match {s.match_id} ({when}): {s.player.hero}, {s.player.result}, {fmt_time(s.player.duration_s)}",
                f"Level: {s.analysis_level} · bracket: {bracket} · KDA {k}/{d}/{a} · "
                f"{s.key_stats.souls_per_min:.0f} souls/min · ~{estimate_tokens(s)} tokens", "",
            ]
            if not s.issues:
                lines.append("- no issues detected")
            for i in s.issues:
                e = i.evidence
                bench_txt = f" vs bracket median {e.bracket_p50:g}" if e.bracket_p50 is not None else ""
                pct = f", better than {e.percentile:.0%} of the bracket" if e.percentile is not None else ""
                times = f" at {', '.join(fmt_time(t) for t in i.timestamps_s)}" if i.timestamps_s else ""
                extra = "; ".join(f"{k}: {v}" for k, v in e.details.items() if k not in ("benchmark_scope", "benchmark_n"))
                lines.append(f"- [ ] **{i.id}** (severity {i.severity:.2f}): {e.player_value:g} {e.unit}{bench_txt}"
                             f" ({e.window}{pct}){times}{'; ' + extra if extra else ''}")
            if s.strengths:
                lines.append("- strengths: " + ", ".join(f"{st.label} ({st.evidence.percentile:.0%})" for st in s.strengths))
            lines.append("")

    out = config.data_dir() / "review" / f"issues_{args.account_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({len(history)} matches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
