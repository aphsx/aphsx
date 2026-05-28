"""The Odds API v4 client — https://the-odds-api.com/liveapi/guides/v4/"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

BASE_URL = "https://api.the-odds-api.com/v4"
TIMEOUT = 25


@dataclass
class QuotaInfo:
    remaining: int | None
    used: int | None
    last_cost: int | None


@dataclass
class OddsEvent:
    id: str
    sport_key: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[dict[str, Any]]
    raw: dict[str, Any]


class TheOddsAPI:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "aphsx-odds-tracker/1.0"})
        self.last_quota = QuotaInfo(None, None, None)

    def _parse_quota(self, response: requests.Response) -> None:
        def _int_header(name: str) -> int | None:
            value = response.headers.get(name)
            if value is None:
                return None
            try:
                return int(value)
            except ValueError:
                return None

        self.last_quota = QuotaInfo(
            remaining=_int_header("x-requests-remaining"),
            used=_int_header("x-requests-used"),
            last_cost=_int_header("x-requests-last"),
        )

    def list_sports(self, *, all_sports: bool = False) -> list[dict[str, Any]]:
        params: dict[str, str] = {"apiKey": self.api_key}
        if all_sports:
            params["all"] = "true"
        response = self.session.get(
            f"{BASE_URL}/sports/",
            params=params,
            timeout=TIMEOUT,
        )
        self._parse_quota(response)
        response.raise_for_status()
        return response.json()

    def fetch_odds(
        self,
        sport_key: str,
        *,
        regions: list[str],
        markets: list[str] | None = None,
        commence_from: datetime | None = None,
        commence_to: datetime | None = None,
        odds_format: str = "decimal",
    ) -> list[OddsEvent]:
        params: dict[str, str] = {
            "apiKey": self.api_key,
            "regions": ",".join(regions),
            "oddsFormat": odds_format,
        }
        if markets:
            params["markets"] = ",".join(markets)
        if commence_from is not None:
            params["commenceTimeFrom"] = commence_from.strftime("%Y-%m-%dT%H:%M:%SZ")
        if commence_to is not None:
            params["commenceTimeTo"] = commence_to.strftime("%Y-%m-%dT%H:%M:%SZ")

        response = self.session.get(
            f"{BASE_URL}/sports/{sport_key}/odds",
            params=params,
            timeout=TIMEOUT,
        )
        self._parse_quota(response)
        response.raise_for_status()
        payload: list[dict[str, Any]] = response.json()
        events: list[OddsEvent] = []
        for row in payload:
            commence_raw = row["commence_time"]
            if commence_raw.endswith("Z"):
                commence = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
            else:
                commence = datetime.fromisoformat(commence_raw)
            events.append(
                OddsEvent(
                    id=row["id"],
                    sport_key=row.get("sport_key", sport_key),
                    commence_time=commence,
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    bookmakers=row.get("bookmakers") or [],
                    raw=row,
                )
            )
        return events
