# acca-bot

Starter toolkit for soccer accumulator bet analysis. It begins with:

- The Odds API for bookmaker prices and market data.
- football-data.org for fixtures, teams, competitions, and basic player/team context.
- ESPN's public soccer scoreboard feed as a no-key World Cup fixture fallback.
- A placeholder accumulator ranking model that you can replace with real team/player features.

This is analysis software, not betting advice. Accumulators are high-variance products and the first model deliberately stays simple until richer data is wired in.

## Data Sources

Recommended first stack:

| Need | Starter source | Notes |
| --- | --- | --- |
| Odds | [The Odds API](https://the-odds-api.com/liveapi/guides/v4/) | Supports soccer sport keys such as `soccer_epl`; use `/v4/sports` to discover available competitions. |
| Fixtures / teams / competitions | [football-data.org](https://www.football-data.org/documentation/api) | Useful free/low-friction football API with Premier League, Champions League, World Cup coverage depending on plan. |
| Injuries / lineups / deeper player context | [API-Football](https://www.api-football.com/documentation) | Used for injuries, lineups, and World Cup fixture IDs when configured. |
| World Cup fixtures fallback | ESPN soccer scoreboard feed | Used by the dashboard when API-Football is not configured. |

## Setup

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Set API keys in your shell:

```powershell
$env:ODDS_API_KEY="your-the-odds-api-key"
$env:FOOTBALL_DATA_TOKEN="your-football-data-token"
$env:API_FOOTBALL_KEY="your-api-football-key"
```

Optional configuration:

```powershell
$env:ODDS_REGIONS="uk"
$env:ODDS_MARKETS="h2h"
$env:ODDS_FORMAT="decimal"
```

## Commands

List available sports from The Odds API:

```powershell
acca-bot sports
```

Show upcoming Premier League head-to-head prices:

```powershell
acca-bot odds --sport soccer_epl
```

Build a basic four-leg accumulator candidate:

```powershell
acca-bot acca --sport soccer_epl --legs 4
```

Show upcoming Premier League fixtures from football-data.org:

```powershell
acca-bot fixtures --competition PL --days 7
```

Show injury context from API-Football:

```powershell
acca-bot injuries --fixture 123456
```

Show available lineups from API-Football:

```powershell
acca-bot lineups --fixture 123456
```

Run the local dashboard:

```powershell
acca-bot dashboard
```

Backfill a season of Premier League fixtures (and per-fixture statistics) from API-Football into local SQLite storage, then score every fixture for match importance:

```powershell
acca-bot backfill --season 2023
```

`--season 2023` means the 2023/24 season. `--league` defaults to `39` (Premier League; league ids are stable across seasons in API-Football). Pass `--no-stats` to only pull fixture results and skip the per-fixture statistics calls, which is the cheapest way to spend a limited request budget. The database file defaults to `data/acca-bot.sqlite3` (`--db` to change it).

**API-Football's free plan is capped at 100 requests/day and 10/minute, with very limited historical season access.** The `backfill` command persists its daily request count to `.acca-bot-ratelimit.json` next to wherever you run it, so if a backfill run hits the daily cap partway through, it stops cleanly and picks up where it left off the next UTC day - just re-run the same command. A full season backfill with statistics costs roughly 1 (fixtures) + 1-per-finished-fixture (statistics) requests, so plan multi-day runs on the free tier, or upgrade once you've validated the pipeline.

Then open `http://127.0.0.1:8000`. You can pass API keys through environment variables before starting the dashboard, or enter them into the dashboard inputs. The browser stores those inputs in local storage for your machine only.

Useful football-data.org competition codes include `PL` for Premier League, `CL` for Champions League, and `WC` for World Cup.

The dashboard now loads World Cup fixtures automatically:

- With `API_FOOTBALL_KEY`, it uses API-Football league `1` and season `2026` by default, then scans the first upcoming fixture IDs for injuries.
- Without `API_FOOTBALL_KEY`, it shows World Cup fixtures from ESPN's public scoreboard feed, but exact injuries and lineups are unavailable.
- If live fixture feeds are unavailable, it falls back to a bundled 2026 World Cup fixture seed so the dashboard still has matches to display locally.

For exact match lineups, enter the API-Football fixture id in the dashboard. Without fixture/team identity mapping, The Odds API odds events cannot yet be automatically joined to API-Football injury records.

## How The First Model Works

The current model ranks selections by:

1. Convert bookmaker decimal odds into implied probability.
2. Add a tiny placeholder probability uplift.
3. Rank by the difference between model probability and implied probability.
4. Build an accumulator while allowing only one selection per event.

That is intentionally naive. The next version should replace the placeholder with features such as:

- Team strength ratings and recent form.
- Home/away split.
- Rest days and travel.
- Injuries, suspensions, and expected lineups.
- Market movement and bookmaker consensus.
- Competition importance and rotation risk.

## Project Structure

```text
src/accabot/
  cli.py               Command-line interface
  dashboard.py         Local web dashboard server and frontend
  api_football.py      API-Football client for fixtures, statistics, injuries, and lineups
  espn.py              ESPN soccer scoreboard fallback client
  config.py            Environment-based settings
  football_data.py     football-data.org client
  odds_api.py          The Odds API client
  scoring.py           Selection extraction and accumulator ranking
  models.py            Core dataclasses
  storage.py           SQLite schema and upsert/fetch helpers for historical data
  ratelimit.py         Persisted rate limiter for API-Football's per-minute/per-day caps
  importance.py        Match importance scoring engine (title/Europe/relegation stakes, derbies)
  backfill.py          Orchestrates client + storage + importance into one resumable season backfill
tests/
  test_scoring.py
  test_importance.py
  test_storage.py
  test_ratelimit.py
```

## Match Importance

Every backfilled fixture gets an `importance` score in `[0, 1]` in the `fixture_importance` table, computed entirely from locally stored results (no extra API calls) after a backfill. It reflects Premier-League-specific stakes, not the cup/group/final concept from knockout competitions:

- **Title race** - how close a team sits to the league leader's points total.
- **European qualification race** - how close a team sits to the auto-qualification cutoff line (rank 4/5 by default).
- **Relegation battle** - how close a team sits to the drop-zone cutoff line.
- Each component fades toward 0 once a team is mathematically too far away, given games remaining (`points gap > games_remaining * 3`).
- A match's stakes score is the max of its two teams' averaged components across those three zones.
- A **season weight** (0.4 early season, ramping to 1.0 by the run-in) scales stakes, since a mathematically-live title race in matchday 3 doesn't carry the same weight as one in matchday 36.
- A small fixed bonus is added for known **derbies** (see `DERBY_PAIRS` in `importance.py` - a manually curated, extensible list).

Standings snapshots are computed matchday-by-matchday using only results from strictly earlier matchdays, so importance reflects what was actually at stake going into a match, not hindsight from the final table. This is a first-pass heuristic, not ground truth - tune the boundary ranks, weighting curve, and derby list as the model matures.

## Next Build Steps

1. Add team/player identity mapping between The Odds API, football-data.org, and API-Football (needed to join live odds events to backfilled fixtures).
2. Backfill several past Premier League seasons via `acca-bot backfill`, then add a real probability model trained/backtested against that historical data (including `fixture_importance` as a feature).
3. Add bankroll controls, max correlation rules, and bookmaker/market filters.
4. Build a small UI once the data model is stable.
