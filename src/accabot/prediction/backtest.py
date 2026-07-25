from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path

import numpy as np

from .data import Match, load_matches
from .market import load_market_probs
from .poisson import DixonColesModel

_EPS = 1e-12


@dataclass
class MarketRecord:
    """Predicted-vs-realized record for one match, on one market."""

    probs: dict[str, float]  # outcome label -> predicted probability
    outcome: str  # realized outcome label
    fixture_id: int | None = None


@dataclass
class BacktestReport:
    model_name: str
    n_predictions: int = 0
    log_loss: float = 0.0
    brier: float = 0.0
    calibration: list[tuple[float, float, int]] = field(default_factory=list)  # (mean_pred, realized_freq, n)


def _score(records: list[MarketRecord]) -> tuple[float, float]:
    log_losses = []
    briers = []
    for record in records:
        p_true = max(record.probs[record.outcome], _EPS)
        log_losses.append(-np.log(p_true))
        briers.append(sum((p - (1.0 if label == record.outcome else 0.0)) ** 2 for label, p in record.probs.items()))
    return float(np.mean(log_losses)), float(np.mean(briers))


def _calibration(records: list[MarketRecord], *, n_bins: int = 10) -> list[tuple[float, float, int]]:
    """Pool (predicted probability, realized) pairs across all outcome labels and bin by predicted prob."""
    pairs = []
    for record in records:
        for label, p in record.probs.items():
            pairs.append((p, 1.0 if label == record.outcome else 0.0))
    if not pairs:
        return []
    preds = np.array([p for p, _ in pairs])
    actual = np.array([a for _, a in pairs])
    edges = np.linspace(0, 1, n_bins + 1)
    bins = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (preds >= lo) & (preds < hi if hi < 1 else preds <= hi)
        if mask.sum() == 0:
            continue
        bins.append((float(preds[mask].mean()), float(actual[mask].mean()), int(mask.sum())))
    return bins


def _one_x_two_outcome(match: Match) -> str:
    if match.home_goals > match.away_goals:
        return "home"
    if match.home_goals < match.away_goals:
        return "away"
    return "draw"


def run_walk_forward(
    conn: sqlite3.Connection,
    *,
    league_id: int = 39,
    start_season: int = 2010,
    end_season: int = 2024,
    burn_in_seasons: int = 3,
    xi: float = 0.0018,
) -> dict[str, BacktestReport]:
    """Walk-forward validation: at each matchday, fit only on strictly earlier
    matches (never shuffled), predict that matchday, then fold it into the
    training set before moving to the next one.

    Baselines evaluated alongside the Dixon-Coles model:
      - home_advantage_baseline: constant walk-forward H/D/A base rates.
      - market: de-margined bookmaker probabilities from historical_odds (see
        historical_odds.py / market.py). This is the real bar to beat. It is
        scored only where odds exist; the dixon_coles_vs_market report scores
        the model on that identical fixture subset so the comparison is fair.
    """
    all_matches = load_matches(conn, league_id=league_id, seasons=(start_season, end_season))
    if not all_matches:
        raise ValueError("No matches found for the requested seasons.")

    team_ids = sorted({t for m in all_matches for t in (m.home_id, m.away_id)})

    by_matchday: dict[tuple[int, int], list[Match]] = defaultdict(list)
    for m in all_matches:
        by_matchday[(m.season, m.matchday)].append(m)
    ordered_keys = sorted(by_matchday.keys())

    burn_in_end_season = start_season + burn_in_seasons - 1
    training: list[Match] = [m for m in all_matches if m.season <= burn_in_end_season]
    validation_keys = [k for k in ordered_keys if k[0] > burn_in_end_season]

    model_records: list[MarketRecord] = []
    baseline_records: list[MarketRecord] = []

    x_prev = None
    home_wins = sum(1 for m in training if _one_x_two_outcome(m) == "home")
    draws = sum(1 for m in training if _one_x_two_outcome(m) == "draw")
    away_wins = sum(1 for m in training if _one_x_two_outcome(m) == "away")

    for key in validation_keys:
        group = by_matchday[key]
        as_of = min(m.kickoff for m in group)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)

        model = DixonColesModel(xi=xi)
        model.fit(training, team_ids=team_ids, as_of=as_of, x0=x_prev)
        x_prev = model._last_x

        total = home_wins + draws + away_wins
        baseline_probs = {
            "home": home_wins / total,
            "draw": draws / total,
            "away": away_wins / total,
        }

        for match in group:
            outcome = _one_x_two_outcome(match)
            pred = model.predict(match.home_id, match.away_id)
            model_records.append(
                MarketRecord({"home": pred["home"], "draw": pred["draw"], "away": pred["away"]}, outcome, match.fixture_id)
            )
            baseline_records.append(MarketRecord(dict(baseline_probs), outcome, match.fixture_id))

            home_wins += outcome == "home"
            draws += outcome == "draw"
            away_wins += outcome == "away"

        training = training + group

    market_probs = load_market_probs(conn, league_id=league_id)
    market_records: list[MarketRecord] = []
    model_on_market_subset: list[MarketRecord] = []
    for record in model_records:
        probs = market_probs.get(record.fixture_id)
        if probs is None:
            continue
        market_records.append(MarketRecord(dict(probs), record.outcome, record.fixture_id))
        model_on_market_subset.append(record)

    reports: dict[str, BacktestReport] = {}
    scored = (
        ("dixon_coles", model_records),
        ("home_advantage_baseline", baseline_records),
        ("market", market_records),
        ("dixon_coles_vs_market", model_on_market_subset),
    )
    for name, records in scored:
        if not records:
            continue
        log_loss, brier = _score(records)
        reports[name] = BacktestReport(
            model_name=name,
            n_predictions=len(records),
            log_loss=log_loss,
            brier=brier,
            calibration=_calibration(records),
        )
    return reports


def print_report(reports: dict[str, BacktestReport]) -> None:
    for name, report in reports.items():
        print(f"\n=== {name} ===")
        print(f"n predictions (match x outcome rows count separately in calibration): {report.n_predictions}")
        print(f"log-loss: {report.log_loss:.4f}")
        print(f"brier:    {report.brier:.4f}")
        print("calibration (predicted -> realized, n):")
        for pred, actual, n in report.calibration:
            print(f"  {pred:.2f} -> {actual:.2f}  (n={n})")

    if "dixon_coles_vs_market" in reports and "market" in reports:
        model_ll = reports["dixon_coles_vs_market"].log_loss
        market_ll = reports["market"].log_loss
        gap = model_ll - market_ll
        verdict = "model beats market" if gap < 0 else "market beats model"
        print(
            f"\nMODEL vs MARKET (same {reports['market'].n_predictions} fixtures): "
            f"model log-loss {model_ll:.4f} vs market {market_ll:.4f} "
            f"(gap {gap:+.4f}, {verdict}). Market probabilities are de-margined "
            "via the proportional method from football-data.co.uk odds "
            "(Pinnacle where available, else market average)."
        )


def main() -> None:
    db_path = Path("data/acca-bot.sqlite3")
    conn = sqlite3.connect(db_path)
    try:
        reports = run_walk_forward(conn)
    finally:
        conn.close()
    print_report(reports)


if __name__ == "__main__":
    main()
