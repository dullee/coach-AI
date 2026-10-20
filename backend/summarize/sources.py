"""Where a user's match data comes from.

Order: the community API's stored metadata (has everything, incl. shot
accuracy) -> the stored replay (identical stats except accuracy) -> asking
the API to fetch the match from Steam (only 3 requests/hour per IP).
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from backend.analysis.replay_data import ReplayData
from backend.analysis.replay_meta import metadata_from_replay
from backend.ingest import DeadlockApiClient, NotFound


def match_data(client: DeadlockApiClient, match_id: int, account_id: int,
               engine: Engine) -> tuple[dict, ReplayData | None]:
    """(metadata, replay or None). Raises NotFound or RateLimited if nothing is available."""
    try:
        replay = ReplayData.load_for_account(match_id, account_id, engine)
    except LookupError:
        replay = None
    try:
        return client.match_metadata(match_id), replay
    except NotFound:
        if replay is not None:
            return metadata_from_replay(replay), replay
        return client.match_metadata(match_id, allow_steam=True), None
