#!/usr/bin/env python3
"""Collect football/esports odds and store time-series in Supabase."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python scripts/odds_tracker/collector.py` from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from scripts.odds_tracker.config import Settings, load_settings, validate_for_run
from scripts.odds_tracker.db import (
    finish_collection_run,
    insert_odds_ticks,
    make_client,
    start_collection_run,
    upsert_match,
)
from scripts.odds_tracker.the_odds_api import TheOddsAPI


def _maybe_load_dotenv() -> None:
    if load_dotenv is None:
        return
    env_path = _REPO_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def discover_esports_keys(api: TheOddsAPI) -> list[str]:
    """Return active sport keys that look like esports (Valorant, CS2, etc.)."""
    sports = api.list_sports(all_sports=True)
    keywords = ("valorant", "esport", "esports", "counter", "league_of", "dota")
    found: list[str] = []
    for sport in sports:
        if not sport.get("active"):
            continue
        blob = " ".join(
            str(sport.get(field, ""))
            for field in ("key", "title", "description", "group")
        ).lower()
        if any(word in blob for word in keywords):
            found.append(sport["key"])
    return found


def run_collect(settings: Settings, *, dry_run: bool, discover_esports: bool) -> int:
    errors = validate_for_run(settings, dry_run=dry_run)
    if errors:
        for message in errors:
            print(f"error: {message}", file=sys.stderr)
        return 2

    api = TheOddsAPI(settings.odds_api_key)
    sport_keys = list(settings.all_sport_keys)
    if discover_esports and not settings.esports_sport_keys:
        try:
            discovered = discover_esports_keys(api)
            if discovered:
                print(f"discovered esports keys: {', '.join(discovered)}")
                for key in discovered:
                    if key not in sport_keys:
                        sport_keys.append(key)
        except Exception as exc:  # noqa: BLE001
            print(f"warn: esports discovery failed: {exc}", file=sys.stderr)

    commence_from, commence_to = settings.commence_window()
    captured_at = datetime.now(timezone.utc)
    client = None
    run_id: str | None = None

    if not dry_run:
        client = make_client(settings.supabase_url, settings.supabase_service_role_key)
        run_id = start_collection_run(
            client,
            source="the_odds_api",
            sport_keys=sport_keys,
            regions=settings.regions,
        )

    total_events = 0
    total_ticks = 0
    try:
        for sport_key in sport_keys:
            print(
                f"fetching {sport_key} "
                f"({','.join(settings.regions)}) "
                f"{commence_from.date()} → {commence_to.date()}"
            )
            events = api.fetch_odds(
                sport_key,
                regions=settings.regions,
                markets=["h2h"],
                commence_from=commence_from,
                commence_to=commence_to,
                odds_format="decimal",
            )
            if not events:
                print(f"  no events for {sport_key}")
                continue

            for event in events:
                total_events += 1
                if dry_run:
                    books = len(event.bookmakers)
                    outcomes = sum(
                        len(m.get("outcomes") or [])
                        for b in event.bookmakers
                        for m in b.get("markets") or []
                    )
                    print(
                        f"  {event.home_team} vs {event.away_team} "
                        f"@ {event.commence_time.isoformat()} "
                        f"({books} books, {outcomes} outcomes)"
                    )
                    continue

                assert client is not None
                match_id = upsert_match(client, event)
                inserted = insert_odds_ticks(
                    client,
                    match_id=match_id,
                    event=event,
                    captured_at=captured_at,
                )
                total_ticks += inserted

            quota = api.last_quota
            print(
                f"  events={len(events)} quota_cost={quota.last_cost} "
                f"remaining={quota.remaining}"
            )

        if dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "sport_keys": sport_keys,
                        "regions": settings.regions,
                        "events_seen": total_events,
                    },
                    indent=2,
                )
            )
            return 0

        assert client is not None and run_id is not None
        finish_collection_run(
            client,
            run_id,
            status="ok",
            events_seen=total_events,
            ticks_inserted=total_ticks,
            requests_used=api.last_quota.used,
            requests_remaining=api.last_quota.remaining,
        )
        print(
            f"done: events={total_events} ticks={total_ticks} "
            f"requests_remaining={api.last_quota.remaining}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        if client is not None and run_id is not None:
            finish_collection_run(
                client,
                run_id,
                status="error",
                events_seen=total_events,
                ticks_inserted=total_ticks,
                requests_used=api.last_quota.used,
                requests_remaining=api.last_quota.remaining,
                error_message=str(exc),
            )
        raise


def main() -> None:
    _maybe_load_dotenv()
    parser = argparse.ArgumentParser(description="Store odds snapshots in Supabase")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch from The Odds API only; do not write to Supabase",
    )
    parser.add_argument(
        "--discover-esports",
        action="store_true",
        help="Auto-append active esports sport keys from /sports",
    )
    parser.add_argument(
        "--list-sports",
        action="store_true",
        help="Print active sports and exit (no quota cost beyond /sports)",
    )
    args = parser.parse_args()
    settings = load_settings()

    if args.list_sports:
        if not settings.odds_api_key:
            print("error: ODDS_API_KEY required", file=sys.stderr)
            sys.exit(2)
        api = TheOddsAPI(settings.odds_api_key)
        sports = api.list_sports(all_sports=True)
        for sport in sorted(sports, key=lambda s: s.get("key", "")):
            active = "yes" if sport.get("active") else "no"
            print(f"{sport['key']:40} active={active:3}  {sport.get('title', '')}")
        sys.exit(0)

    sys.exit(run_collect(settings, dry_run=args.dry_run, discover_esports=args.discover_esports))


if __name__ == "__main__":
    main()
