"""Supabase persistence for matches and odds ticks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from .the_odds_api import OddsEvent


def make_client(url: str, service_role_key: str) -> Client:
    return create_client(url, service_role_key)


def _decimal_price(price: float) -> float:
    """Use decimal odds; convert only when values look American."""
    if price < 0:
        return 1.0 + 100.0 / abs(price)
    if price >= 100:
        return 1.0 + price / 100.0
    return float(price)


def _implied_prob(decimal_odds: float) -> float | None:
    if decimal_odds <= 0:
        return None
    return 1.0 / decimal_odds


def upsert_match(client: Client, event: OddsEvent, sport_title: str | None = None) -> str:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "external_id": event.id,
        "sport_key": event.sport_key,
        "sport_title": sport_title,
        "home_team": event.home_team,
        "away_team": event.away_team,
        "commence_time": event.commence_time.isoformat(),
        "updated_at": now,
    }
    result = (
        client.table("matches")
        .upsert(row, on_conflict="external_id")
        .execute()
    )
    data = result.data
    if data and len(data) > 0:
        return data[0]["id"]
    fetched = (
        client.table("matches")
        .select("id")
        .eq("external_id", event.id)
        .limit(1)
        .execute()
    )
    if fetched.data:
        return fetched.data[0]["id"]
    raise RuntimeError(f"Failed to upsert match {event.id}")


def insert_odds_ticks(
    client: Client,
    *,
    match_id: str,
    event: OddsEvent,
    captured_at: datetime,
    source: str = "the_odds_api",
) -> int:
    rows: list[dict[str, Any]] = []
    captured_iso = captured_at.isoformat()
    for book in event.bookmakers:
        book_key = book.get("key", "unknown")
        book_title = book.get("title")
        last_update = book.get("last_update")
        for market in book.get("markets") or []:
            market_key = market.get("key", "h2h")
            for outcome in market.get("outcomes") or []:
                raw_price = float(outcome["price"])
                decimal = _decimal_price(raw_price)
                rows.append(
                    {
                        "match_id": match_id,
                        "captured_at": captured_iso,
                        "source": source,
                        "bookmaker_key": book_key,
                        "bookmaker_title": book_title,
                        "market_key": market_key,
                        "outcome_name": outcome["name"],
                        "price": decimal,
                        "point": outcome.get("point"),
                        "implied_prob": _implied_prob(decimal),
                        "last_update": last_update,
                        "raw": {
                            "event_id": event.id,
                            "bookmaker": book,
                            "outcome": outcome,
                        },
                    }
                )
    if not rows:
        return 0
    client.table("odds_ticks").insert(rows).execute()
    return len(rows)


def start_collection_run(
    client: Client,
    *,
    source: str,
    sport_keys: list[str],
    regions: list[str],
) -> str:
    row = {
        "source": source,
        "sport_keys": sport_keys,
        "regions": regions,
        "status": "running",
    }
    result = client.table("collection_runs").insert(row).execute()
    return result.data[0]["id"]


def finish_collection_run(
    client: Client,
    run_id: str,
    *,
    status: str,
    events_seen: int,
    ticks_inserted: int,
    requests_used: int | None,
    requests_remaining: int | None,
    error_message: str | None = None,
) -> None:
    client.table("collection_runs").update(
        {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "events_seen": events_seen,
            "ticks_inserted": ticks_inserted,
            "requests_used": requests_used,
            "requests_remaining": requests_remaining,
            "error_message": error_message,
        }
    ).eq("id", run_id).execute()
