"""Builds a static, self-contained HTML dashboard summarizing every Premier League
season stored in data/acca-bot.sqlite3 (2010/11 - 2025/26).

Usage:
    python scripts/build_history_dashboard.py

Regenerate this any time backfill.py pulls in more seasons/fixtures -- it always
reads straight from the sqlite database, so the report stays in sync with the data.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "acca-bot.sqlite3"
TEMPLATE_PATH = ROOT / "scripts" / "dashboard_template.html"
OUTPUT_PATH = ROOT / "reports" / "pl_history_dashboard.html"
TEAM_TEMPLATE_PATH = ROOT / "scripts" / "team_explorer_template.html"
TEAM_OUTPUT_PATH = ROOT / "reports" / "pl_team_explorer.html"

LEAGUE_ID = 39  # Premier League in the api-football id space this DB uses


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    data = build_data(con)
    team_data = build_team_explorer(con)
    con.close()

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":"))
    html = template.replace("__DASHBOARD_DATA__", payload)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(html):,} bytes) from {DB_PATH}")

    team_template = TEAM_TEMPLATE_PATH.read_text(encoding="utf-8")
    team_payload = json.dumps(team_data, separators=(",", ":"))
    team_html = team_template.replace("__TEAM_DATA__", team_payload)
    TEAM_OUTPUT_PATH.write_text(team_html, encoding="utf-8")
    print(f"Wrote {TEAM_OUTPUT_PATH} ({len(team_html):,} bytes) from {DB_PATH}")


def build_data(con: sqlite3.Connection) -> dict:
    teams = {row["id"]: row["name"] for row in con.execute("SELECT id, name FROM teams")}

    fixtures = con.execute(
        """
        SELECT id, season, home_team_id, away_team_id, home_goals, away_goals,
               home_goals_ht, away_goals_ht, kickoff_utc
        FROM fixtures
        WHERE league_id = ?
        ORDER BY kickoff_utc
        """,
        (LEAGUE_ID,),
    ).fetchall()

    stats_rows = con.execute(
        """
        SELECT fs.fixture_id, fs.team_id, fs.shots_total, fs.shots_on_target,
               fs.possession_pct, fs.corners, fs.fouls, fs.yellow_cards,
               fs.red_cards, fs.offsides, fs.passes_total, fs.passes_accurate,
               fs.saves, fs.expected_goals, f.season
        FROM fixture_stats fs
        JOIN fixtures f ON f.id = fs.fixture_id
        WHERE f.league_id = ?
        """,
        (LEAGUE_ID,),
    ).fetchall()

    standings = con.execute(
        """
        SELECT season, matchday, team_id, played, won, drawn, lost, goals_for,
               goals_against, goal_diff, points, position
        FROM standings_snapshots
        WHERE league_id = ?
        """,
        (LEAGUE_ID,),
    ).fetchall()

    importance = con.execute(
        """
        SELECT fi.fixture_id, fi.importance, fi.is_derby, fi.title_component,
               fi.europe_component, fi.relegation_component, fi.season_weight,
               fi.home_position_before, fi.away_position_before,
               f.season, f.home_team_id, f.away_team_id, f.home_goals, f.away_goals,
               f.kickoff_utc
        FROM fixture_importance fi
        JOIN fixtures f ON f.id = fi.fixture_id
        WHERE f.league_id = ?
        ORDER BY fi.importance DESC
        """,
        (LEAGUE_ID,),
    ).fetchall()

    seasons = sorted({f["season"] for f in fixtures})

    season_trends = _build_season_trends(fixtures, stats_rows, standings, seasons, teams)
    team_table = _build_team_table(fixtures, standings, teams)
    team_style = _build_team_style(stats_rows, teams)
    xg_performance = _build_xg_performance(fixtures, stats_rows, teams)
    discipline = _build_discipline(stats_rows, teams)
    importance = _build_importance(importance, teams)
    promising = _build_promising(standings, teams, seasons)
    shot_efficiency = _build_shot_efficiency(fixtures, stats_rows, teams)

    data = {
        "generated_at": _now_iso(),
        "meta": _build_meta(fixtures, teams, seasons),
        "season_trends": season_trends,
        "team_table": team_table,
        "team_style": team_style,
        "xg_performance": xg_performance,
        "discipline": discipline,
        "importance": importance,
        "promising": promising,
        "shot_efficiency": shot_efficiency,
        "spotlights": _build_spotlights(season_trends, team_table, xg_performance, discipline, promising, shot_efficiency),
    }
    return data


def _build_spotlights(season_trends, team_table, xg_performance, discipline, promising, shot_efficiency) -> list[dict]:
    spotlights = []

    if len(season_trends) >= 2:
        first, last = season_trends[0], season_trends[-1]
        delta = round((last["home_win_pct"] - first["home_win_pct"]) * 100, 1)
        direction = "fallen" if delta < 0 else "risen"
        spotlights.append(
            {
                "title": "Home advantage is shifting",
                "detail": f"Home win rate has {direction} from {first['home_win_pct']*100:.1f}% in {first['season']} to {last['home_win_pct']*100:.1f}% in {last['season']} ({delta:+.1f} pts).",
            }
        )

    if team_table:
        most_titles = max(team_table, key=lambda r: r["titles"])
        if most_titles["titles"] > 0:
            title_word = "title" if most_titles["titles"] == 1 else "titles"
            spotlights.append(
                {
                    "title": f"{most_titles['team']} lead the era",
                    "detail": f"{most_titles['titles']} {title_word}, {most_titles['points']} points and a +{most_titles['gd']} goal difference across {most_titles['seasons']} seasons in the data.",
                }
            )
        best_ppg = max(team_table, key=lambda r: r["ppg"])
        spotlights.append(
            {
                "title": f"{best_ppg['team']}: best points-per-game",
                "detail": f"{best_ppg['ppg']} points per game across {best_ppg['played']} matches -- the highest sustained rate in the dataset.",
            }
        )

    if xg_performance:
        clinical = xg_performance[0]
        wasteful = xg_performance[-1]
        spotlights.append(
            {
                "title": f"{clinical['team']}: most clinical finishing",
                "detail": f"Scored {clinical['diff']:+.1f} goals above their expected-goals total ({clinical['goals_for']} goals from {clinical['xg_for']:.1f} xG) since 2022/23.",
            }
        )
        if wasteful["diff"] < 0:
            spotlights.append(
                {
                    "title": f"{wasteful['team']}: underperforming their chances",
                    "detail": f"{wasteful['diff']:+.1f} goals below expected-goals ({wasteful['goals_for']} from {wasteful['xg_for']:.1f} xG) -- due a positive correction if their process holds.",
                }
            )

    if promising.get("points_trend"):
        best_riser = promising["points_trend"][0]
        if best_riser["delta"] > 0:
            spotlights.append(
                {
                    "title": f"{best_riser['team']}: team on the rise",
                    "detail": f"+{best_riser['delta']} points from {best_riser['earlier_season']} to {best_riser['latest_season']} ({best_riser['points_earlier']} -> {best_riser['points_latest']}) -- the biggest improvement in the league.",
                }
            )

    if discipline:
        cleanest = min(discipline, key=lambda r: r["trouble_score"])
        spotlights.append(
            {
                "title": f"{cleanest['team']}: cleanest disciplinary record",
                "detail": f"Just {cleanest['cards_per_game']:.2f} cards per game across {cleanest['matches']} sampled matches.",
            }
        )

    if shot_efficiency:
        sharp = shot_efficiency[0]
        spotlights.append(
            {
                "title": f"{sharp['team']}: sharpest shooters",
                "detail": f"Converts {sharp['conversion_pct']:.1f}% of shots into goals ({sharp['goals_per_game']:.2f} goals/game from {sharp['shots_per_game']:.1f} shots/game).",
            }
        )

    return spotlights


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def season_label(season: int) -> str:
    return f"{season}/{str(season + 1)[-2:]}"


def _build_meta(fixtures, teams, seasons) -> dict:
    total_goals = sum((f["home_goals"] or 0) + (f["away_goals"] or 0) for f in fixtures)
    return {
        "league": "Premier League",
        "seasons": [season_label(s) for s in seasons],
        "season_count": len(seasons),
        "first_season": season_label(seasons[0]),
        "last_season": season_label(seasons[-1]),
        "fixture_count": len(fixtures),
        "team_count": len({f["home_team_id"] for f in fixtures} | {f["away_team_id"] for f in fixtures}),
        "total_goals": total_goals,
        "avg_goals_per_game": round(total_goals / len(fixtures), 3) if fixtures else 0,
    }


def _build_season_trends(fixtures, stats_rows, standings, seasons, teams) -> list[dict]:
    by_season_fixtures: dict[int, list] = defaultdict(list)
    for f in fixtures:
        by_season_fixtures[f["season"]].append(f)

    by_season_stats: dict[int, list] = defaultdict(list)
    for s in stats_rows:
        by_season_stats[s["season"]].append(s)

    # champion / bottom3 from matchday 38 (or the max matchday available) standings
    by_season_standing: dict[int, list] = defaultdict(list)
    for row in standings:
        by_season_standing[row["season"]].append(row)

    trends = []
    for season in seasons:
        fx = by_season_fixtures[season]
        n = len(fx)
        goals = sum((f["home_goals"] or 0) + (f["away_goals"] or 0) for f in fx)
        home_wins = sum(1 for f in fx if (f["home_goals"] or 0) > (f["away_goals"] or 0))
        draws = sum(1 for f in fx if (f["home_goals"] or 0) == (f["away_goals"] or 0))
        away_wins = n - home_wins - draws

        st = by_season_stats[season]
        avg_cards = None
        avg_possession_spread = None
        avg_shots = None
        avg_xg = None
        if st:
            cards = [row["yellow_cards"] + (row["red_cards"] or 0) for row in st if row["yellow_cards"] is not None]
            if cards:
                avg_cards = round(sum(cards) / len(cards), 2)
            shots = [row["shots_total"] for row in st if row["shots_total"] is not None]
            if shots:
                avg_shots = round(sum(shots) / len(shots), 2)
            xg = [row["expected_goals"] for row in st if row["expected_goals"] is not None]
            if xg:
                avg_xg = round(sum(xg) / len(xg), 3)

        rows = by_season_standing[season]
        champion = None
        bottom3 = []
        if rows:
            max_md = max(r["matchday"] for r in rows)
            final_rows = [r for r in rows if r["matchday"] == max_md]
            final_rows.sort(key=lambda r: r["position"])
            if final_rows:
                champion = teams.get(final_rows[0]["team_id"], "?")
                bottom3 = [teams.get(r["team_id"], "?") for r in final_rows if r["position"] >= max(final_rows, key=lambda r: r["position"])["position"] - 2]

        trends.append(
            {
                "season": season_label(season),
                "matches": n,
                "avg_goals_per_game": round(goals / n, 3) if n else 0,
                "home_win_pct": round(home_wins / n, 4) if n else 0,
                "draw_pct": round(draws / n, 4) if n else 0,
                "away_win_pct": round(away_wins / n, 4) if n else 0,
                "avg_cards_per_team": avg_cards,
                "avg_shots_per_team": avg_shots,
                "avg_xg_per_team": avg_xg,
                "champion": champion,
                "relegated": bottom3,
            }
        )
    return trends


def _build_team_table(fixtures, standings, teams) -> list[dict]:
    agg: dict[int, dict] = defaultdict(
        lambda: {
            "played": 0,
            "won": 0,
            "drawn": 0,
            "lost": 0,
            "gf": 0,
            "ga": 0,
            "seasons": set(),
        }
    )
    for f in fixtures:
        hg, ag = f["home_goals"], f["away_goals"]
        if hg is None or ag is None:
            continue
        h, a = f["home_team_id"], f["away_team_id"]
        agg[h]["played"] += 1
        agg[a]["played"] += 1
        agg[h]["gf"] += hg
        agg[h]["ga"] += ag
        agg[a]["gf"] += ag
        agg[a]["ga"] += hg
        agg[h]["seasons"].add(f["season"])
        agg[a]["seasons"].add(f["season"])
        if hg > ag:
            agg[h]["won"] += 1
            agg[a]["lost"] += 1
        elif hg < ag:
            agg[a]["won"] += 1
            agg[h]["lost"] += 1
        else:
            agg[h]["drawn"] += 1
            agg[a]["drawn"] += 1

    # season-end honors from standings snapshots
    by_season_standing: dict[int, list] = defaultdict(list)
    for row in standings:
        by_season_standing[row["season"]].append(row)

    titles: dict[int, int] = defaultdict(int)
    top4: dict[int, int] = defaultdict(int)
    relegations: dict[int, int] = defaultdict(int)
    best_position: dict[int, int] = {}
    for season, rows in by_season_standing.items():
        max_md = max(r["matchday"] for r in rows)
        final_rows = [r for r in rows if r["matchday"] == max_md]
        max_pos = max(r["position"] for r in final_rows) if final_rows else 0
        for r in final_rows:
            tid = r["team_id"]
            pos = r["position"]
            if pos == 1:
                titles[tid] += 1
            if pos <= 4:
                top4[tid] += 1
            if pos >= max_pos - 2:
                relegations[tid] += 1
            if tid not in best_position or pos < best_position[tid]:
                best_position[tid] = pos

    table = []
    for tid, row in agg.items():
        played = row["played"]
        if played == 0:
            continue
        points = row["won"] * 3 + row["drawn"]
        table.append(
            {
                "team": teams.get(tid, f"#{tid}"),
                "seasons": len(row["seasons"]),
                "played": played,
                "won": row["won"],
                "drawn": row["drawn"],
                "lost": row["lost"],
                "gf": row["gf"],
                "ga": row["ga"],
                "gd": row["gf"] - row["ga"],
                "points": points,
                "ppg": round(points / played, 3),
                "titles": titles.get(tid, 0),
                "top4": top4.get(tid, 0),
                "relegations": relegations.get(tid, 0),
                "best_position": best_position.get(tid),
            }
        )
    table.sort(key=lambda r: (-r["points"], -r["ppg"]))
    return table


def _build_team_style(stats_rows, teams) -> list[dict]:
    agg: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    counts: dict[int, int] = defaultdict(int)
    for row in stats_rows:
        tid = row["team_id"]
        counts[tid] += 1
        for key in ("shots_total", "shots_on_target", "possession_pct", "corners", "fouls", "passes_total", "passes_accurate", "saves"):
            val = row[key]
            if val is not None:
                agg[tid][key] += val
                agg[tid][key + "_n"] += 1

    style = []
    for tid, n in counts.items():
        if n < 20:
            continue  # not enough sampled matches for a stable average
        row = agg[tid]

        def avg(key: str):
            cnt = row.get(key + "_n", 0)
            return round(row[key] / cnt, 2) if cnt else None

        pass_acc = None
        if row.get("passes_total_n") and row["passes_total"] > 0:
            pass_acc = round(100 * row["passes_accurate"] / row["passes_total"], 1)

        style.append(
            {
                "team": teams.get(tid, f"#{tid}"),
                "matches_sampled": n,
                "avg_possession": avg("possession_pct"),
                "avg_shots": avg("shots_total"),
                "avg_shots_on_target": avg("shots_on_target"),
                "avg_corners": avg("corners"),
                "avg_fouls": avg("fouls"),
                "pass_accuracy": pass_acc,
            }
        )
    style.sort(key=lambda r: -(r["avg_possession"] or 0))
    return style


def _build_xg_performance(fixtures, stats_rows, teams) -> list[dict]:
    fixture_season = {f["id"]: f["season"] for f in fixtures}
    agg: dict[int, dict] = defaultdict(lambda: {"xg_for": 0.0, "goals_for": 0, "matches": 0})
    goals_by_fixture_team: dict[tuple, int] = {}
    for f in fixtures:
        goals_by_fixture_team[(f["id"], f["home_team_id"])] = f["home_goals"]
        goals_by_fixture_team[(f["id"], f["away_team_id"])] = f["away_goals"]

    for row in stats_rows:
        if row["expected_goals"] is None:
            continue
        tid = row["team_id"]
        fid = row["fixture_id"]
        goals = goals_by_fixture_team.get((fid, tid))
        if goals is None:
            continue
        agg[tid]["xg_for"] += row["expected_goals"]
        agg[tid]["goals_for"] += goals
        agg[tid]["matches"] += 1

    out = []
    for tid, row in agg.items():
        if row["matches"] < 15:
            continue
        diff = row["goals_for"] - row["xg_for"]
        out.append(
            {
                "team": teams.get(tid, f"#{tid}"),
                "matches": row["matches"],
                "xg_for": round(row["xg_for"], 2),
                "goals_for": row["goals_for"],
                "diff": round(diff, 2),
                "diff_per_game": round(diff / row["matches"], 3),
            }
        )
    out.sort(key=lambda r: -r["diff_per_game"])
    return out


def _build_discipline(stats_rows, teams) -> list[dict]:
    agg: dict[int, dict] = defaultdict(lambda: {"yellow": 0, "red": 0, "fouls": 0, "matches": 0})
    for row in stats_rows:
        tid = row["team_id"]
        if row["yellow_cards"] is None:
            continue
        agg[tid]["yellow"] += row["yellow_cards"]
        agg[tid]["red"] += row["red_cards"] or 0
        agg[tid]["fouls"] += row["fouls"] or 0
        agg[tid]["matches"] += 1

    out = []
    for tid, row in agg.items():
        if row["matches"] < 20:
            continue
        cards = row["yellow"] + row["red"] * 2  # red counted as heavier for the "trouble" score
        out.append(
            {
                "team": teams.get(tid, f"#{tid}"),
                "matches": row["matches"],
                "yellow": row["yellow"],
                "red": row["red"],
                "fouls_per_game": round(row["fouls"] / row["matches"], 2),
                "cards_per_game": round((row["yellow"] + row["red"]) / row["matches"], 3),
                "trouble_score": round(cards / row["matches"], 3),
            }
        )
    out.sort(key=lambda r: -r["trouble_score"])
    return out


def _build_importance(importance_rows, teams) -> dict:
    top = []
    for row in importance_rows[:15]:
        top.append(
            {
                "season": season_label(row["season"]),
                "date": (row["kickoff_utc"] or "")[:10],
                "home": teams.get(row["home_team_id"], "?"),
                "away": teams.get(row["away_team_id"], "?"),
                "score": f"{row['home_goals']}-{row['away_goals']}" if row["home_goals"] is not None else "",
                "importance": round(row["importance"], 3),
                "is_derby": bool(row["is_derby"]),
                "title_component": round(row["title_component"], 2),
                "europe_component": round(row["europe_component"], 2),
                "relegation_component": round(row["relegation_component"], 2),
            }
        )

    derby_count = sum(1 for r in importance_rows if r["is_derby"])
    derby_pairs: dict[tuple, int] = defaultdict(int)
    for r in importance_rows:
        if r["is_derby"]:
            pair = tuple(sorted((teams.get(r["home_team_id"], "?"), teams.get(r["away_team_id"], "?"))))
            derby_pairs[pair] += 1
    top_derbies = sorted(derby_pairs.items(), key=lambda kv: -kv[1])[:10]

    avg_importance = round(sum(r["importance"] for r in importance_rows) / len(importance_rows), 4) if importance_rows else 0

    return {
        "top_matches": top,
        "derby_count": derby_count,
        "total_matches": len(importance_rows),
        "avg_importance": avg_importance,
        "top_derby_fixtures": [{"pair": f"{p[0]} vs {p[1]}", "meetings": c} for p, c in top_derbies],
    }


def _build_promising(standings, teams, seasons) -> dict:
    if len(seasons) < 2:
        return {"points_trend": [], "xg_process": []}

    latest = seasons[-1]
    earlier = seasons[-3] if len(seasons) >= 3 else seasons[0]

    def final_points(season: int) -> dict[int, int]:
        rows = [r for r in standings if r["season"] == season]
        if not rows:
            return {}
        max_md = max(r["matchday"] for r in rows)
        return {r["team_id"]: r["points"] for r in rows if r["matchday"] == max_md}

    latest_points = final_points(latest)
    earlier_points = final_points(earlier)

    trend = []
    for tid, pts in latest_points.items():
        if tid in earlier_points:
            trend.append(
                {
                    "team": teams.get(tid, f"#{tid}"),
                    "points_latest": pts,
                    "points_earlier": earlier_points[tid],
                    "delta": pts - earlier_points[tid],
                    "latest_season": season_label(latest),
                    "earlier_season": season_label(earlier),
                }
            )
    trend.sort(key=lambda r: -r["delta"])

    return {"points_trend": trend, "latest_season": season_label(latest), "earlier_season": season_label(earlier)}


def _build_shot_efficiency(fixtures, stats_rows, teams) -> list[dict]:
    goals_by_fixture_team: dict[tuple, int] = {}
    for f in fixtures:
        goals_by_fixture_team[(f["id"], f["home_team_id"])] = f["home_goals"]
        goals_by_fixture_team[(f["id"], f["away_team_id"])] = f["away_goals"]

    agg: dict[int, dict] = defaultdict(lambda: {"shots": 0, "sot": 0, "goals": 0, "matches": 0})
    for row in stats_rows:
        if row["shots_total"] is None:
            continue
        tid = row["team_id"]
        goals = goals_by_fixture_team.get((row["fixture_id"], tid))
        if goals is None:
            continue
        agg[tid]["shots"] += row["shots_total"]
        agg[tid]["sot"] += row["shots_on_target"] or 0
        agg[tid]["goals"] += goals
        agg[tid]["matches"] += 1

    out = []
    for tid, row in agg.items():
        if row["matches"] < 20 or row["shots"] == 0:
            continue
        out.append(
            {
                "team": teams.get(tid, f"#{tid}"),
                "matches": row["matches"],
                "shots_per_game": round(row["shots"] / row["matches"], 2),
                "conversion_pct": round(100 * row["goals"] / row["shots"], 2),
                "sot_pct": round(100 * row["sot"] / row["shots"], 2) if row["shots"] else None,
                "goals_per_game": round(row["goals"] / row["matches"], 2),
            }
        )
    out.sort(key=lambda r: -r["conversion_pct"])
    return out


# --------------------------------------------------------------------------
# Team Explorer page: latest-season snapshot + per-team performance over time
# --------------------------------------------------------------------------

def build_team_explorer(con: sqlite3.Connection) -> dict:
    teams = {row["id"]: row["name"] for row in con.execute("SELECT id, name FROM teams")}

    fixtures = con.execute(
        """
        SELECT id, season, home_team_id, away_team_id, home_goals, away_goals, kickoff_utc
        FROM fixtures WHERE league_id = ? ORDER BY kickoff_utc
        """,
        (LEAGUE_ID,),
    ).fetchall()

    stats_rows = con.execute(
        """
        SELECT fs.fixture_id, fs.team_id, fs.shots_total, fs.shots_on_target,
               fs.possession_pct, fs.corners, fs.fouls, fs.yellow_cards, fs.red_cards,
               fs.passes_total, fs.passes_accurate, fs.expected_goals,
               f.season, f.kickoff_utc
        FROM fixture_stats fs JOIN fixtures f ON f.id = fs.fixture_id
        WHERE f.league_id = ?
        """,
        (LEAGUE_ID,),
    ).fetchall()

    seasons = sorted({f["season"] for f in fixtures})

    # Compute each season's final table straight from match results (the stored
    # standings snapshots carry an off-by-one 'played' field), ranking by the
    # real Premier League tiebreakers: points, then goal difference, then goals for.
    rec: dict[tuple, dict] = defaultdict(
        lambda: {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0, "points": 0}
    )
    for f in fixtures:
        hg, ag = f["home_goals"], f["away_goals"]
        if hg is None or ag is None:
            continue
        for tid, gf, ga in ((f["home_team_id"], hg, ag), (f["away_team_id"], ag, hg)):
            r = rec[(f["season"], tid)]
            r["played"] += 1
            r["gf"] += gf
            r["ga"] += ga
            if gf > ga:
                r["won"] += 1
                r["points"] += 3
            elif gf == ga:
                r["drawn"] += 1
                r["points"] += 1
            else:
                r["lost"] += 1

    final_rows: dict[tuple, dict] = {}
    season_size: dict[int, int] = {}
    for season in seasons:
        entries = [(tid, r) for (s, tid), r in rec.items() if s == season]
        entries.sort(key=lambda kv: (-kv[1]["points"], -(kv[1]["gf"] - kv[1]["ga"]), -kv[1]["gf"]))
        season_size[season] = len(entries)
        for position, (tid, r) in enumerate(entries, start=1):
            final_rows[(season, tid)] = {
                "team_id": tid,
                "position": position,
                "played": r["played"],
                "won": r["won"],
                "drawn": r["drawn"],
                "lost": r["lost"],
                "goals_for": r["gf"],
                "goals_against": r["ga"],
                "goal_diff": r["gf"] - r["ga"],
                "points": r["points"],
            }

    # goals lookup for xG comparisons
    goals_by_fixture_team: dict[tuple, int] = {}
    for f in fixtures:
        goals_by_fixture_team[(f["id"], f["home_team_id"])] = f["home_goals"]
        goals_by_fixture_team[(f["id"], f["away_team_id"])] = f["away_goals"]
    # opponent lookup so we can attribute xG conceded
    opponent: dict[tuple, int] = {}
    for f in fixtures:
        opponent[(f["id"], f["home_team_id"])] = f["away_team_id"]
        opponent[(f["id"], f["away_team_id"])] = f["home_team_id"]
    xg_by_fixture_team: dict[tuple, float] = {}
    for row in stats_rows:
        if row["expected_goals"] is not None:
            xg_by_fixture_team[(row["fixture_id"], row["team_id"])] = row["expected_goals"]

    # per (season, team) aggregate of detailed stats
    stat_agg: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
    stat_dates: dict[tuple, str] = {}
    for row in stats_rows:
        key = (row["season"], row["team_id"])
        a = stat_agg[key]
        a["n"] += 1
        for field in ("shots_total", "shots_on_target", "possession_pct", "corners",
                      "fouls", "yellow_cards", "red_cards", "passes_total", "passes_accurate"):
            if row[field] is not None:
                a[field] += row[field]
                a[field + "_n"] += 1
        if row["expected_goals"] is not None:
            a["xg_for"] += row["expected_goals"]
            a["xg_n"] += 1
            opp = opponent.get((row["fixture_id"], row["team_id"]))
            xg_ag = xg_by_fixture_team.get((row["fixture_id"], opp)) if opp else None
            if xg_ag is not None:
                a["xg_against"] += xg_ag
                a["xg_against_n"] += 1
        dt = (row["kickoff_utc"] or "")[:10]
        if key not in stat_dates or dt > stat_dates[key]:
            stat_dates[key] = dt

    def avg(a, field):
        n = a.get(field + "_n", 0)
        return round(a[field] / n, 2) if n else None

    # -- per-team season-by-season records --
    team_ids = {f["home_team_id"] for f in fixtures} | {f["away_team_id"] for f in fixtures}
    team_seasons: dict[str, list] = {}
    for tid in team_ids:
        name = teams.get(tid, f"#{tid}")
        records = []
        for season in seasons:
            fr = final_rows.get((season, tid))
            if fr is None:
                continue
            a = stat_agg.get((season, tid))
            xg_for = round(a["xg_for"], 1) if a and a.get("xg_n") else None
            xg_against = round(a["xg_against"], 1) if a and a.get("xg_against_n") else None
            records.append(
                {
                    "season": season_label(season),
                    "season_start": season,
                    "position": fr["position"],
                    "played": fr["played"],
                    "won": fr["won"],
                    "drawn": fr["drawn"],
                    "lost": fr["lost"],
                    "gf": fr["goals_for"],
                    "ga": fr["goals_against"],
                    "gd": fr["goal_diff"],
                    "points": fr["points"],
                    "ppg": round(fr["points"] / fr["played"], 2) if fr["played"] else None,
                    "table_size": season_size.get(season, 20),
                    "stat_matches": int(a["n"]) if a else 0,
                    "avg_possession": avg(a, "possession_pct") if a else None,
                    "avg_shots": avg(a, "shots_total") if a else None,
                    "xg_for": xg_for,
                    "xg_against": xg_against,
                }
            )
        if records:
            team_seasons[name] = records

    # -- latest-season snapshot --
    latest = seasons[-1]
    prev = seasons[-2] if len(seasons) >= 2 else None
    latest_table = []
    for tid in team_ids:
        fr = final_rows.get((latest, tid))
        if fr is None:
            continue
        a = stat_agg.get((latest, tid))
        prev_fr = final_rows.get((prev, tid)) if prev else None
        latest_table.append(
            {
                "position": fr["position"],
                "team": teams.get(tid, f"#{tid}"),
                "played": fr["played"],
                "won": fr["won"],
                "drawn": fr["drawn"],
                "lost": fr["lost"],
                "gf": fr["goals_for"],
                "ga": fr["goals_against"],
                "gd": fr["goal_diff"],
                "points": fr["points"],
                "xg_for": round(a["xg_for"], 1) if a and a.get("xg_n") else None,
                "xg_against": round(a["xg_against"], 1) if a and a.get("xg_against_n") else None,
                "possession": avg(a, "possession_pct") if a else None,
                "prev_points": prev_fr["points"] if prev_fr else None,
                "points_delta": (fr["points"] - prev_fr["points"]) if prev_fr else None,
                "prev_position": prev_fr["position"] if prev_fr else None,
            }
        )
    latest_table.sort(key=lambda r: r["position"])

    latest_highlights = _latest_highlights(latest_table, season_label(latest))

    # -- data coverage / recency (honest 'freshness' at the match-sample level) --
    coverage = []
    for tid in team_ids:
        name = teams.get(tid, f"#{tid}")
        total_stat = sum(int(v["n"]) for k, v in stat_agg.items() if k[1] == tid)
        recent = seasons[-3:]
        recent_stat = sum(int(stat_agg[(s, tid)]["n"]) for s in recent if (s, tid) in stat_agg)
        last_dt = max((stat_dates[(s, tid)] for s in seasons if (s, tid) in stat_dates), default="")
        in_latest = (latest, tid) in final_rows
        if total_stat == 0 and not in_latest:
            continue
        coverage.append(
            {
                "team": name,
                "stat_matches": total_stat,
                "recent_stat_matches": recent_stat,
                "last_stat_date": last_dt,
                "in_latest_season": in_latest,
            }
        )
    coverage.sort(key=lambda r: (-int(r["in_latest_season"]), -r["recent_stat_matches"]))

    return {
        "generated_at": _now_iso(),
        "meta": {
            "league": "Premier League",
            "seasons": [season_label(s) for s in seasons],
            "latest_season": season_label(latest),
            "first_season": season_label(seasons[0]),
            "stat_seasons_from": season_label(2014),
            "xg_seasons_from": season_label(2022),
        },
        "team_seasons": team_seasons,
        "team_list": sorted(team_seasons.keys()),
        "latest": {
            "season": season_label(latest),
            "table": latest_table,
            "highlights": latest_highlights,
        },
        "coverage": coverage,
    }


def _latest_highlights(table: list[dict], season_lbl: str) -> list[dict]:
    if not table:
        return []
    highlights = []
    champ = table[0]
    highlights.append({"label": "Champions", "team": champ["team"], "detail": f"{champ['points']} pts, {champ['gf']}-{champ['ga']} goals ({season_lbl})"})
    top_scorers = max(table, key=lambda r: r["gf"])
    highlights.append({"label": "Most goals", "team": top_scorers["team"], "detail": f"{top_scorers['gf']} scored"})
    best_defense = min(table, key=lambda r: r["ga"])
    highlights.append({"label": "Best defence", "team": best_defense["team"], "detail": f"{best_defense['ga']} conceded"})
    with_xg = [r for r in table if r["xg_for"] is not None]
    if with_xg:
        best_xg = max(with_xg, key=lambda r: r["xg_for"])
        highlights.append({"label": "Most xG created", "team": best_xg["team"], "detail": f"{best_xg['xg_for']:.1f} xG"})
    with_delta = [r for r in table if r["points_delta"] is not None]
    if with_delta:
        riser = max(with_delta, key=lambda r: r["points_delta"])
        if riser["points_delta"] > 0:
            highlights.append({"label": "Most improved", "team": riser["team"], "detail": f"+{riser['points_delta']} pts vs previous season"})
        faller = min(with_delta, key=lambda r: r["points_delta"])
        if faller["points_delta"] < 0:
            highlights.append({"label": "Biggest drop", "team": faller["team"], "detail": f"{faller['points_delta']} pts vs previous season"})
    return highlights


if __name__ == "__main__":
    main()
