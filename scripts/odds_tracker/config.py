"""Environment-driven configuration for odds collection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from datetime import datetime as dt


def _env(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip()


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    odds_api_key: str
    soccer_sport_keys: list[str]
    esports_sport_keys: list[str]
    regions: list[str]
    lookahead_days: int
    odds_papi_key: str | None

    @property
    def all_sport_keys(self) -> list[str]:
        keys: list[str] = []
        for key in self.soccer_sport_keys + self.esports_sport_keys:
            if key not in keys:
                keys.append(key)
        return keys

    def commence_window(self) -> tuple[dt, dt]:
        now = dt.now(timezone.utc)
        end = now + timedelta(days=self.lookahead_days)
        return now, end


def load_settings() -> Settings:
    url = _env("SUPABASE_URL")
    key = _env("SUPABASE_SERVICE_ROLE_KEY")
    odds_key = _env("ODDS_API_KEY")

    soccer = _split_csv(
        _env(
            "ODDS_SOCCER_SPORT_KEYS",
            "soccer_epl,soccer_uefa_champs_league,soccer_spain_la_liga",
        )
    )
    esports = _split_csv(_env("ODDS_ESPORTS_SPORT_KEYS"))
    regions = _split_csv(_env("ODDS_REGIONS", "eu,uk"))
    lookahead = int(_env("ODDS_LOOKAHEAD_DAYS", "7"))
    papi = _env("ODDS_PAPI_KEY") or None

    return Settings(
        supabase_url=url,
        supabase_service_role_key=key,
        odds_api_key=odds_key,
        soccer_sport_keys=soccer,
        esports_sport_keys=esports,
        regions=regions,
        lookahead_days=max(1, lookahead),
        odds_papi_key=papi,
    )


def validate_for_run(settings: Settings, *, dry_run: bool) -> list[str]:
    errors: list[str] = []
    if not settings.odds_api_key:
        errors.append("ODDS_API_KEY is required")
    if not settings.all_sport_keys:
        errors.append("At least one sport key (ODDS_SOCCER_SPORT_KEYS or ODDS_ESPORTS_SPORT_KEYS)")
    if not settings.regions:
        errors.append("ODDS_REGIONS must list at least one region (e.g. eu,uk)")
    if dry_run:
        return errors
    if not settings.supabase_url:
        errors.append("SUPABASE_URL is required (unless --dry-run)")
    if not settings.supabase_service_role_key:
        errors.append("SUPABASE_SERVICE_ROLE_KEY is required (unless --dry-run)")
    return errors
