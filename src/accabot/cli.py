from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from .api_football import ApiFootballClient
from .config import load_settings
from .dashboard import run_dashboard
from .football_data import FootballDataClient
from .odds_api import OddsApiClient
from .scoring import build_accumulator, extract_selections


def main() -> None:
    parser = argparse.ArgumentParser(prog="acca-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sports = subparsers.add_parser("sports", help="List active sports from The Odds API.")
    sports.add_argument("--all", action="store_true", help="Include inactive/out-of-season sports.")

    odds = subparsers.add_parser("odds", help="Show upcoming soccer odds.")
    odds.add_argument("--sport", default="soccer_epl", help="The Odds API sport key.")

    acca = subparsers.add_parser("acca", help="Build a simple accumulator candidate.")
    acca.add_argument("--sport", default="soccer_epl", help="The Odds API sport key.")
    acca.add_argument("--legs", type=int, default=4, help="Number of accumulator legs.")

    fixtures = subparsers.add_parser("fixtures", help="Show upcoming football fixtures.")
    fixtures.add_argument("--competition", default="PL", help="football-data.org competition code.")
    fixtures.add_argument("--days", type=int, default=7, help="Days ahead to search.")

    injuries = subparsers.add_parser("injuries", help="Show API-Football injury context.")
    injuries.add_argument("--fixture", type=int, help="API-Football fixture id.")
    injuries.add_argument("--team", type=int, help="API-Football team id.")
    injuries.add_argument("--league", type=int, help="API-Football league id.")
    injuries.add_argument("--season", type=int, help="Season year, required with league/team filters.")

    lineups = subparsers.add_parser("lineups", help="Show API-Football fixture lineups.")
    lineups.add_argument("--fixture", type=int, required=True, help="API-Football fixture id.")
    lineups.add_argument("--team", type=int, help="API-Football team id.")

    dashboard = subparsers.add_parser("dashboard", help="Run the local web dashboard.")
    dashboard.add_argument("--host", default="127.0.0.1", help="Dashboard host.")
    dashboard.add_argument("--port", type=int, default=8000, help="Dashboard port.")

    args = parser.parse_args()
    settings = load_settings()

    if args.command == "sports":
        _require(settings.odds_api_key, "ODDS_API_KEY")
        client = OddsApiClient(settings.odds_api_key)
        print(json.dumps(client.sports(all_sports=args.all), indent=2))
        return

    if args.command == "odds":
        _require(settings.odds_api_key, "ODDS_API_KEY")
        client = OddsApiClient(settings.odds_api_key)
        events = client.odds(
            args.sport,
            regions=settings.odds_regions,
            markets=settings.odds_markets,
            odds_format=settings.odds_format,
        )
        for selection in extract_selections(events, sport_key=args.sport)[:30]:
            print(
                f"{selection.label} | {selection.bookmaker} | "
                f"implied={selection.implied_probability:.2%} edge={selection.edge:.2%}"
            )
        return

    if args.command == "acca":
        _require(settings.odds_api_key, "ODDS_API_KEY")
        client = OddsApiClient(settings.odds_api_key)
        events = client.odds(
            args.sport,
            regions=settings.odds_regions,
            markets=settings.odds_markets,
            odds_format=settings.odds_format,
        )
        acca = build_accumulator(extract_selections(events, sport_key=args.sport), legs=args.legs)
        for index, selection in enumerate(acca.selections, start=1):
            print(f"{index}. {selection.label} | {selection.bookmaker} | edge={selection.edge:.2%}")
        print(f"Combined odds: {acca.decimal_odds:.2f}")
        print(f"Model probability: {acca.model_probability:.2%}")
        print(f"Expected value: {acca.expected_value:.2%}")
        return

    if args.command == "fixtures":
        _require(settings.football_data_token, "FOOTBALL_DATA_TOKEN")
        client = FootballDataClient(settings.football_data_token)
        today = date.today()
        payload = client.matches(
            competition=args.competition,
            date_from=today,
            date_to=today + timedelta(days=args.days),
            status="SCHEDULED",
        )
        for match in payload.get("matches", []):
            print(
                f"{match['utcDate']} | {match['homeTeam']['name']} vs "
                f"{match['awayTeam']['name']} | matchday={match.get('matchday')}"
            )
        return

    if args.command == "injuries":
        _require(settings.api_football_key, "API_FOOTBALL_KEY")
        if not any([args.fixture, args.team, args.league]):
            raise SystemExit("Pass at least one of --fixture, --team, or --league.")
        client = ApiFootballClient(settings.api_football_key)
        payload = client.injuries(
            fixture=args.fixture,
            team=args.team,
            league=args.league,
            season=args.season,
        )
        for item in payload.get("response", []):
            player = item.get("player", {})
            team = item.get("team", {})
            fixture = item.get("fixture", {})
            print(
                f"{fixture.get('date', 'unknown date')} | {team.get('name', 'unknown team')} | "
                f"{player.get('name', 'unknown player')} | {player.get('reason', 'unknown reason')} | "
                f"{player.get('type', 'unknown type')}"
            )
        return

    if args.command == "lineups":
        _require(settings.api_football_key, "API_FOOTBALL_KEY")
        client = ApiFootballClient(settings.api_football_key)
        payload = client.lineups(fixture=args.fixture, team=args.team)
        for lineup in payload.get("response", []):
            team = lineup.get("team", {})
            coach = lineup.get("coach", {})
            print(f"{team.get('name', 'unknown team')} | coach={coach.get('name')} | {lineup.get('formation')}")
            for player in lineup.get("startXI", []):
                details = player.get("player", {})
                print(f"  XI {details.get('number')}: {details.get('name')} ({details.get('pos')})")
        return

    if args.command == "dashboard":
        run_dashboard(host=args.host, port=args.port)


def _require(value: str | None, name: str) -> None:
    if not value:
        raise SystemExit(f"Set {name} in your environment first.")


if __name__ == "__main__":
    main()
