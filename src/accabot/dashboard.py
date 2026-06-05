from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .api_football import ApiFootballClient
from .config import load_settings
from .espn import EspnSoccerClient, seeded_world_cup_fixtures, world_cup_fixture_summaries
from .football_data import FootballDataClient
from .http import ApiError
from .odds_api import OddsApiClient
from .scoring import build_accumulator, extract_selections


def run_dashboard(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard running at http://{host}:{port}")
    server.serve_forever()


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(DASHBOARD_HTML)
            return
        if parsed.path == "/api/snapshot":
            payload = build_snapshot(_query_params(parsed.query))
            self._send_json(payload)
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_snapshot(params: dict[str, str]) -> dict[str, Any]:
    settings = load_settings()
    sport = params.get("sport", "soccer_epl")
    legs = _int_param(params, "legs", 4)
    competition = params.get("competition", "PL")
    days = _int_param(params, "days", 7)
    today = date.today()

    odds_key = params.get("odds_key") or settings.odds_api_key
    football_data_token = params.get("football_data_token") or settings.football_data_token
    api_football_key = params.get("api_football_key") or settings.api_football_key

    snapshot: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "sport": sport,
            "legs": legs,
            "competition": competition,
            "days": days,
        },
        "odds": _empty_panel(),
        "acca": _empty_panel(),
        "fixtures": _empty_panel(),
        "world_cup": _empty_panel(),
        "injuries": _empty_panel(),
        "lineups": _empty_panel(),
    }

    events: list[dict[str, Any]] = []
    if odds_key:
        try:
            events = OddsApiClient(odds_key).odds(
                sport,
                regions=params.get("regions", settings.odds_regions),
                markets=params.get("markets", settings.odds_markets),
                odds_format=params.get("odds_format", settings.odds_format),
            )
            selections = extract_selections(events, sport_key=sport)
            snapshot["odds"] = {
                "ok": True,
                "events": [_event_summary(event) for event in events],
                "selections": [_selection_summary(selection) for selection in selections[:60]],
            }
            acca = build_accumulator(selections, legs=legs)
            snapshot["acca"] = {
                "ok": True,
                "legs": [_selection_summary(selection) for selection in acca.selections],
                "decimal_odds": acca.decimal_odds,
                "model_probability": acca.model_probability,
                "expected_value": acca.expected_value,
            }
        except (ApiError, ValueError, KeyError) as exc:
            snapshot["odds"] = _error_panel(str(exc))
            snapshot["acca"] = _error_panel("Accumulator unavailable because odds could not be loaded.")
    else:
        snapshot["odds"] = _missing_panel("ODDS_API_KEY")
        snapshot["acca"] = _missing_panel("ODDS_API_KEY")

    if football_data_token:
        try:
            payload = FootballDataClient(football_data_token).matches(
                competition=competition,
                date_from=today,
                date_to=today + timedelta(days=days),
                status="SCHEDULED",
            )
            snapshot["fixtures"] = {
                "ok": True,
                "matches": [_football_data_match_summary(match) for match in payload.get("matches", [])],
            }
        except (ApiError, KeyError) as exc:
            snapshot["fixtures"] = _error_panel(str(exc))
    else:
        snapshot["fixtures"] = _missing_panel("FOOTBALL_DATA_TOKEN")

    world_cup_fixture_ids: list[int] = []
    if api_football_key:
        client = ApiFootballClient(api_football_key)
        snapshot["world_cup"] = _load_api_football_world_cup(client, params)
        world_cup_fixture_ids = [
            int(item["fixture_id"])
            for item in snapshot["world_cup"].get("fixtures", [])
            if str(item.get("fixture_id", "")).isdigit()
        ]
    else:
        snapshot["world_cup"] = _load_espn_world_cup(params)

    if api_football_key:
        client = ApiFootballClient(api_football_key)
        snapshot["injuries"] = _load_injuries(client, params, default_fixture_ids=world_cup_fixture_ids)
        snapshot["lineups"] = _load_lineups(client, params)
    else:
        snapshot["injuries"] = _missing_panel("API_FOOTBALL_KEY")
        snapshot["lineups"] = _missing_panel("API_FOOTBALL_KEY")

    return snapshot


def _load_api_football_world_cup(client: ApiFootballClient, params: dict[str, str]) -> dict[str, Any]:
    league = _int_param(params, "api_league", 1)
    season = _int_param(params, "api_season", 2026)
    next_count = _int_param(params, "world_cup_next", 20)
    try:
        payload = client.fixtures(league=league, season=season, next_count=next_count)
        return {
            "ok": True,
            "source": "API-Football",
            "fixtures": [_api_football_fixture_summary(item) for item in payload.get("response", [])],
        }
    except (ApiError, KeyError) as exc:
        fallback = _load_espn_world_cup(params)
        fallback["message"] = f"API-Football World Cup fixtures failed, showing ESPN fallback: {exc}"
        return fallback


def _load_espn_world_cup(params: dict[str, str]) -> dict[str, Any]:
    days = _int_param(params, "world_cup_days", 60)
    try:
        payload = EspnSoccerClient().scoreboard(
            league=params.get("espn_league", "fifa.world"),
            date_from=date.today(),
            date_to=date.today() + timedelta(days=days),
        )
        return {
            "ok": True,
            "source": "ESPN",
            "fixtures": world_cup_fixture_summaries(payload),
        }
    except (ApiError, KeyError) as exc:
        return {
            "ok": True,
            "source": "Bundled FIFA schedule seed",
            "fixtures": seeded_world_cup_fixtures(limit=_int_param(params, "world_cup_next", 24)),
            "message": f"Live ESPN World Cup fixtures unavailable, showing bundled schedule seed: {exc}",
        }


def _load_injuries(
    client: ApiFootballClient,
    params: dict[str, str],
    *,
    default_fixture_ids: list[int] | None = None,
) -> dict[str, Any]:
    fixture = _optional_int(params, "api_fixture")
    league = _optional_int(params, "api_league")
    season = _optional_int(params, "api_season")
    match_date = _optional_date(params, "injury_date")
    team_ids = _csv_ints(params.get("team_ids", ""))
    auto_fixture_ids = (default_fixture_ids or [])[: _int_param(params, "injury_fixture_limit", 8)]

    if fixture:
        try:
            payload = client.injuries(fixture=fixture)
            return {"ok": True, "injuries": [_injury_summary(item) for item in payload.get("response", [])]}
        except (ApiError, KeyError) as exc:
            return _error_panel(str(exc))

    if team_ids:
        injuries: list[dict[str, Any]] = []
        errors: list[str] = []
        for team_id in team_ids:
            try:
                payload = client.injuries(team=team_id, league=league, season=season, match_date=match_date)
                injuries.extend(_injury_summary(item) for item in payload.get("response", []))
            except (ApiError, KeyError) as exc:
                errors.append(f"team {team_id}: {exc}")
        return {"ok": not errors, "injuries": injuries, "errors": errors}

    if auto_fixture_ids:
        injuries = []
        errors = []
        for fixture_id in auto_fixture_ids:
            try:
                payload = client.injuries(fixture=fixture_id)
                injuries.extend(_injury_summary(item) for item in payload.get("response", []))
            except (ApiError, KeyError) as exc:
                errors.append(f"fixture {fixture_id}: {exc}")
        return {
            "ok": not errors,
            "injuries": injuries,
            "errors": errors,
            "message": "Auto-loaded injuries from upcoming World Cup API-Football fixtures.",
        }

    if league and season:
        try:
            payload = client.injuries(league=league, season=season, match_date=match_date)
            return {"ok": True, "injuries": [_injury_summary(item) for item in payload.get("response", [])]}
        except (ApiError, KeyError) as exc:
            return _error_panel(str(exc))

    return {
        "ok": False,
        "message": "Pass an API-Football fixture id, team ids, or league + season to load injuries.",
    }


def _load_lineups(client: ApiFootballClient, params: dict[str, str]) -> dict[str, Any]:
    fixture = _optional_int(params, "api_fixture")
    if not fixture:
        return {"ok": False, "message": "Pass an API-Football fixture id to load lineups."}
    try:
        payload = client.lineups(fixture=fixture)
        return {"ok": True, "lineups": [_lineup_summary(item) for item in payload.get("response", [])]}
    except (ApiError, KeyError) as exc:
        return _error_panel(str(exc))


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "commence_time": event.get("commence_time"),
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "bookmakers": len(event.get("bookmakers", [])),
    }


def _selection_summary(selection: Any) -> dict[str, Any]:
    data = asdict(selection)
    data["label"] = selection.label
    return data


def _football_data_match_summary(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "utc_date": match.get("utcDate"),
        "home_team": match.get("homeTeam", {}).get("name"),
        "away_team": match.get("awayTeam", {}).get("name"),
        "competition": match.get("competition", {}).get("name"),
        "matchday": match.get("matchday"),
    }


def _api_football_fixture_summary(item: dict[str, Any]) -> dict[str, Any]:
    fixture = item.get("fixture", {})
    teams = item.get("teams", {})
    venue = fixture.get("venue", {})
    league = item.get("league", {})
    return {
        "source": "API-Football",
        "fixture_id": fixture.get("id"),
        "kickoff": fixture.get("date"),
        "home_team": teams.get("home", {}).get("name"),
        "home_team_id": teams.get("home", {}).get("id"),
        "away_team": teams.get("away", {}).get("name"),
        "away_team_id": teams.get("away", {}).get("id"),
        "venue": venue.get("name"),
        "city": venue.get("city"),
        "league": league.get("name"),
        "round": league.get("round"),
        "status": fixture.get("status", {}).get("long"),
    }


def _injury_summary(item: dict[str, Any]) -> dict[str, Any]:
    player = item.get("player", {})
    team = item.get("team", {})
    fixture = item.get("fixture", {})
    league = item.get("league", {})
    return {
        "player": player.get("name"),
        "player_id": player.get("id"),
        "team": team.get("name"),
        "team_id": team.get("id"),
        "reason": player.get("reason"),
        "type": player.get("type"),
        "fixture_date": fixture.get("date"),
        "fixture_id": fixture.get("id"),
        "league": league.get("name"),
    }


def _lineup_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "team": item.get("team", {}).get("name"),
        "formation": item.get("formation"),
        "coach": item.get("coach", {}).get("name"),
        "start_xi": [player.get("player", {}) for player in item.get("startXI", [])],
        "substitutes": [player.get("player", {}) for player in item.get("substitutes", [])],
    }


def _query_params(query: str) -> dict[str, str]:
    parsed = parse_qs(query, keep_blank_values=False)
    return {key: values[-1] for key, values in parsed.items() if values}


def _int_param(params: dict[str, str], key: str, default: int) -> int:
    try:
        return int(params.get(key, default))
    except ValueError:
        return default


def _optional_int(params: dict[str, str], key: str) -> int | None:
    value = params.get(key)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _optional_date(params: dict[str, str], key: str) -> date | None:
    value = params.get(key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _csv_ints(value: str) -> list[int]:
    values: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            continue
    return values


def _empty_panel() -> dict[str, Any]:
    return {"ok": False, "message": "Not loaded."}


def _missing_panel(key_name: str) -> dict[str, Any]:
    return {"ok": False, "message": f"Missing {key_name}. Enter it in the dashboard or set it in your shell."}


def _error_panel(message: str) -> dict[str, Any]:
    return {"ok": False, "message": message}


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>acca-bot</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #1f2933;
      --muted: #64748b;
      --line: #d8dee8;
      --panel: #ffffff;
      --page: #f5f7fa;
      --accent: #166534;
      --accent-2: #0f766e;
      --warn: #9a3412;
      --bad: #b91c1c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 24px; font-weight: 750; }
    h2 { font-size: 17px; }
    h3 { font-size: 14px; }
    button {
      min-height: 36px;
      border: 1px solid #115e59;
      background: var(--accent-2);
      color: white;
      padding: 0 14px;
      border-radius: 6px;
      font-weight: 650;
      cursor: pointer;
    }
    button:disabled { opacity: .55; cursor: wait; }
    main {
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      min-height: calc(100vh - 69px);
    }
    aside {
      padding: 18px;
      border-right: 1px solid var(--line);
      background: #ffffff;
    }
    section {
      padding: 18px;
      border-bottom: 1px solid var(--line);
    }
    .content {
      min-width: 0;
    }
    .form-grid {
      display: grid;
      gap: 12px;
      margin-top: 14px;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    input, select {
      width: 100%;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      color: var(--ink);
      background: #ffffff;
      font: inherit;
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
      margin-top: 10px;
    }
    .status {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      padding: 18px;
    }
    .wide { grid-column: 1 / -1; }
    .panel {
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    .panel-body { padding: 12px 14px; }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #f8fafc;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .metric strong { font-size: 20px; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 9px 8px;
      border-bottom: 1px solid #edf1f5;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      background: #fbfcfe;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      border-radius: 999px;
      background: #e8f5ee;
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
    }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .empty {
      color: var(--muted);
      padding: 12px 0;
    }
    @media (max-width: 900px) {
      header { align-items: flex-start; flex-direction: column; }
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .grid { grid-template-columns: 1fr; }
      .metric-row { grid-template-columns: 1fr; }
      .status { white-space: normal; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>acca-bot</h1>
      <p class="hint">Local soccer odds, fixtures, injuries, lineups, and accumulator candidates.</p>
    </div>
    <div class="toolbar">
      <span id="status" class="status">Idle</span>
      <button id="refresh" type="button">Refresh</button>
    </div>
  </header>
  <main>
    <aside>
      <h2>Inputs</h2>
      <form id="controls" class="form-grid">
        <label>The Odds API key <input name="odds_key" type="password" autocomplete="off"></label>
        <label>football-data.org token <input name="football_data_token" type="password" autocomplete="off"></label>
        <label>API-Football key <input name="api_football_key" type="password" autocomplete="off"></label>
        <label>Odds sport key <input name="sport" value="soccer_epl"></label>
        <label>Odds region <input name="regions" value="uk"></label>
        <label>Accumulator legs <input name="legs" type="number" min="1" max="12" value="4"></label>
        <label>football-data competition <input name="competition" value="PL"></label>
        <label>Fixture days ahead <input name="days" type="number" min="1" max="30" value="7"></label>
        <label>API-Football fixture id <input name="api_fixture" inputmode="numeric"></label>
        <label>API-Football league id <input name="api_league" inputmode="numeric" value="1"></label>
        <label>API-Football season <input name="api_season" inputmode="numeric" value="2026"></label>
        <label>World Cup fixtures to load <input name="world_cup_next" type="number" min="1" max="50" value="20"></label>
        <label>Injury fixture scan limit <input name="injury_fixture_limit" type="number" min="1" max="20" value="8"></label>
        <label>API-Football team ids <input name="team_ids" placeholder="33, 40, 42"></label>
        <label>Injury date <input name="injury_date" type="date"></label>
      </form>
      <p class="hint">Keys are kept in this browser tab and sent only to this local server. World Cup fixtures load automatically; exact injuries use API-Football fixture IDs when an API-Football key is available.</p>
    </aside>
    <div class="content">
      <div class="grid">
        <div class="panel wide">
          <div class="panel-head"><h2>Accumulator Candidate</h2><span id="generated" class="status"></span></div>
          <div id="acca" class="panel-body"></div>
        </div>
        <div class="panel wide">
          <div class="panel-head"><h2>World Cup Fixtures</h2><span id="world-cup-count" class="status"></span></div>
          <div id="world-cup" class="panel-body"></div>
        </div>
        <div class="panel wide">
          <div class="panel-head"><h2>Upcoming Odds</h2><span id="odds-count" class="status"></span></div>
          <div id="odds" class="panel-body"></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Fixtures</h2><span id="fixtures-count" class="status"></span></div>
          <div id="fixtures" class="panel-body"></div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Injuries</h2><span id="injuries-count" class="status"></span></div>
          <div id="injuries" class="panel-body"></div>
        </div>
        <div class="panel wide">
          <div class="panel-head"><h2>Lineups</h2><span id="lineups-count" class="status"></span></div>
          <div id="lineups" class="panel-body"></div>
        </div>
      </div>
    </div>
  </main>
  <script>
    const form = document.getElementById('controls');
    const statusEl = document.getElementById('status');
    const refreshButton = document.getElementById('refresh');
    const stored = JSON.parse(localStorage.getItem('accaBotDashboard') || '{}');
    for (const [key, value] of Object.entries(stored)) {
      if (form.elements[key]) form.elements[key].value = value;
    }
    form.addEventListener('input', saveInputs);
    refreshButton.addEventListener('click', loadSnapshot);
    loadSnapshot();

    function saveInputs() {
      const values = Object.fromEntries(new FormData(form).entries());
      localStorage.setItem('accaBotDashboard', JSON.stringify(values));
    }

    async function loadSnapshot() {
      saveInputs();
      refreshButton.disabled = true;
      statusEl.textContent = 'Loading';
      const params = new URLSearchParams(new FormData(form));
      try {
        const response = await fetch('/api/snapshot?' + params.toString());
        const data = await response.json();
        render(data);
        statusEl.textContent = 'Loaded';
      } catch (error) {
        statusEl.textContent = 'Failed';
        document.getElementById('acca').innerHTML = `<p class="empty bad">${escapeHtml(error.message)}</p>`;
      } finally {
        refreshButton.disabled = false;
      }
    }

    function render(data) {
      document.getElementById('generated').textContent = data.generated_at || '';
      renderAcca(data.acca);
      renderWorldCup(data.world_cup);
      renderOdds(data.odds);
      renderFixtures(data.fixtures);
      renderInjuries(data.injuries);
      renderLineups(data.lineups);
    }

    function renderAcca(panel) {
      const target = document.getElementById('acca');
      if (!panel.ok) return renderMessage(target, panel.message);
      const rows = panel.legs.map((leg, index) => `<tr><td>${index + 1}</td><td>${escapeHtml(leg.label)}</td><td>${escapeHtml(leg.bookmaker)}</td><td>${pct(leg.implied_probability)}</td><td>${pct(leg.edge)}</td></tr>`).join('');
      target.innerHTML = `
        <div class="metric-row">
          <div class="metric"><span>Combined odds</span><strong>${num(panel.decimal_odds)}</strong></div>
          <div class="metric"><span>Model probability</span><strong>${pct(panel.model_probability)}</strong></div>
          <div class="metric"><span>Expected value</span><strong class="${panel.expected_value >= 0 ? '' : 'bad'}">${pct(panel.expected_value)}</strong></div>
        </div>
        <table><thead><tr><th>Leg</th><th>Selection</th><th>Bookmaker</th><th>Implied</th><th>Edge</th></tr></thead><tbody>${rows}</tbody></table>
      `;
    }

    function renderOdds(panel) {
      const target = document.getElementById('odds');
      document.getElementById('odds-count').textContent = panel.ok ? `${panel.events.length} events` : '';
      if (!panel.ok) return renderMessage(target, panel.message);
      const rows = panel.selections.map(item => `<tr><td>${dateTime(item.commence_time)}</td><td>${escapeHtml(item.home_team)} vs ${escapeHtml(item.away_team)}</td><td>${escapeHtml(item.outcome)}</td><td>${escapeHtml(item.bookmaker)}</td><td>${num(item.odds)}</td><td>${pct(item.implied_probability)}</td></tr>`).join('');
      target.innerHTML = `<table><thead><tr><th>Kickoff</th><th>Match</th><th>Pick</th><th>Bookmaker</th><th>Odds</th><th>Implied</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function renderWorldCup(panel) {
      const target = document.getElementById('world-cup');
      const fixtures = panel.fixtures || [];
      document.getElementById('world-cup-count').textContent = panel.ok ? `${fixtures.length} fixtures · ${escapeHtml(panel.source || '')}` : '';
      if (!panel.ok) return renderMessage(target, panel.message);
      const note = panel.message ? `<p class="empty warn">${escapeHtml(panel.message)}</p>` : '';
      const rows = fixtures.map(item => `<tr><td>${dateTime(item.kickoff)}</td><td>${escapeHtml(item.home_team)} vs ${escapeHtml(item.away_team)}</td><td>${escapeHtml(item.venue)} ${escapeHtml(item.city)}</td><td>${escapeHtml(item.round || item.status)}</td><td>${escapeHtml(item.fixture_id)}</td></tr>`).join('');
      target.innerHTML = `${note}${rows ? `<table><thead><tr><th>Kickoff</th><th>Match</th><th>Venue</th><th>Stage</th><th>Fixture ID</th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="empty">No World Cup fixtures returned.</p>`}`;
    }

    function renderFixtures(panel) {
      const target = document.getElementById('fixtures');
      document.getElementById('fixtures-count').textContent = panel.ok ? `${panel.matches.length} matches` : '';
      if (!panel.ok) return renderMessage(target, panel.message);
      const rows = panel.matches.map(item => `<tr><td>${dateTime(item.utc_date)}</td><td>${escapeHtml(item.home_team)} vs ${escapeHtml(item.away_team)}</td><td>${escapeHtml(item.matchday)}</td></tr>`).join('');
      target.innerHTML = rows ? `<table><thead><tr><th>Kickoff</th><th>Match</th><th>MD</th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="empty">No scheduled fixtures returned.</p>`;
    }

    function renderInjuries(panel) {
      const target = document.getElementById('injuries');
      document.getElementById('injuries-count').textContent = panel.injuries ? `${panel.injuries.length} players` : '';
      if (!panel.ok && !panel.injuries) return renderMessage(target, panel.message);
      const rows = (panel.injuries || []).map(item => `<tr><td>${escapeHtml(item.team)}</td><td>${escapeHtml(item.player)}</td><td>${escapeHtml(item.reason)}</td><td><span class="pill">${escapeHtml(item.type || 'unknown')}</span></td></tr>`).join('');
      const errors = (panel.errors || []).map(error => `<p class="empty bad">${escapeHtml(error)}</p>`).join('');
      target.innerHTML = `${errors}${rows ? `<table><thead><tr><th>Team</th><th>Player</th><th>Reason</th><th>Type</th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="empty">${escapeHtml(panel.message || 'No injuries returned.')}</p>`}`;
    }

    function renderLineups(panel) {
      const target = document.getElementById('lineups');
      document.getElementById('lineups-count').textContent = panel.lineups ? `${panel.lineups.length} teams` : '';
      if (!panel.ok) return renderMessage(target, panel.message);
      target.innerHTML = panel.lineups.map(lineup => {
        const players = lineup.start_xi.map(player => `<tr><td>${escapeHtml(player.number)}</td><td>${escapeHtml(player.name)}</td><td>${escapeHtml(player.pos)}</td></tr>`).join('');
        return `<h3>${escapeHtml(lineup.team)} <span class="status">${escapeHtml(lineup.formation || '')} ${escapeHtml(lineup.coach || '')}</span></h3><table><thead><tr><th>No</th><th>Player</th><th>Pos</th></tr></thead><tbody>${players}</tbody></table>`;
      }).join('');
    }

    function renderMessage(target, message) {
      target.innerHTML = `<p class="empty warn">${escapeHtml(message || 'No data loaded.')}</p>`;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
    }
    function num(value) { return Number(value || 0).toFixed(2); }
    function pct(value) { return `${(Number(value || 0) * 100).toFixed(2)}%`; }
    function dateTime(value) {
      if (!value) return '';
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? escapeHtml(value) : date.toLocaleString();
    }
  </script>
</body>
</html>
"""
