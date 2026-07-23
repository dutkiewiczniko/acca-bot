from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

from .data import Match

_ATTACK_BOUND = (-3.0, 3.0)
_DEFENSE_BOUND = (-3.0, 3.0)
_HOME_ADV_BOUND = (-2.0, 2.0)
_RHO_BOUND = (-0.3, 0.3)


def _tau(home_goals: np.ndarray, away_goals: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score correlation correction, applied to {0-0,1-0,0-1,1-1}."""
    tau = np.ones_like(lam)
    m00 = (home_goals == 0) & (away_goals == 0)
    m01 = (home_goals == 0) & (away_goals == 1)
    m10 = (home_goals == 1) & (away_goals == 0)
    m11 = (home_goals == 1) & (away_goals == 1)
    tau = np.where(m00, 1 - lam * mu * rho, tau)
    tau = np.where(m01, 1 + lam * rho, tau)
    tau = np.where(m10, 1 + mu * rho, tau)
    tau = np.where(m11, 1 - rho, tau)
    return tau


def _unpack(params: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, float, float]:
    attack = np.empty(n)
    attack[0] = 0.0
    attack[1:] = params[: n - 1]
    defense = params[n - 1 : 2 * n - 1]
    home_adv = params[2 * n - 1]
    rho = params[2 * n]
    return attack, defense, home_adv, rho


def _pack_bounds(n: int) -> list[tuple[float, float]]:
    return [_ATTACK_BOUND] * (n - 1) + [_DEFENSE_BOUND] * n + [_HOME_ADV_BOUND, _RHO_BOUND]


def _dtau(home_goals: np.ndarray, away_goals: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """d(tau)/d(lam), d(tau)/d(mu), d(tau)/d(rho), zero outside the four low-score cells."""
    dtau_dlam = np.zeros_like(lam)
    dtau_dmu = np.zeros_like(mu)
    dtau_drho = np.zeros_like(lam)
    m00 = (home_goals == 0) & (away_goals == 0)
    m01 = (home_goals == 0) & (away_goals == 1)
    m10 = (home_goals == 1) & (away_goals == 0)
    m11 = (home_goals == 1) & (away_goals == 1)
    dtau_dlam[m00] = -mu[m00] * rho
    dtau_dmu[m00] = -lam[m00] * rho
    dtau_drho[m00] = -lam[m00] * mu[m00]
    dtau_dlam[m01] = rho
    dtau_drho[m01] = lam[m01]
    dtau_dmu[m10] = rho
    dtau_drho[m10] = mu[m10]
    dtau_drho[m11] = -1.0
    return dtau_dlam, dtau_dmu, dtau_drho


def _negative_log_likelihood_and_grad(
    params: np.ndarray,
    n: int,
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    weights: np.ndarray,
) -> tuple[float, np.ndarray]:
    attack, defense, home_adv, rho = _unpack(params, n)
    lam = np.exp(attack[home_idx] - defense[away_idx] + home_adv)
    mu = np.exp(attack[away_idx] - defense[home_idx])
    lam = np.clip(lam, 1e-6, 1e6)
    mu = np.clip(mu, 1e-6, 1e6)

    log_p_home = home_goals * np.log(lam) - lam - gammaln(home_goals + 1)
    log_p_away = away_goals * np.log(mu) - mu - gammaln(away_goals + 1)
    tau_raw = _tau(home_goals, away_goals, lam, mu, rho)
    tau = np.clip(tau_raw, 1e-10, None)

    log_lik = weights * (np.log(tau) + log_p_home + log_p_away)
    nll = -float(np.sum(log_lik))

    dtau_dlam, dtau_dmu, dtau_drho = _dtau(home_goals, away_goals, lam, mu, rho)
    dlogtau_dlam = dtau_dlam / tau
    dlogtau_dmu = dtau_dmu / tau
    dlogtau_drho = dtau_drho / tau

    # d(loglik)/d(lam)*lam and d(loglik)/d(mu)*mu, i.e. contributions already
    # scaled by dlam/d(atk_home)=lam and dmu/d(atk_away)=mu via the chain rule.
    dll_dlam_scaled = weights * ((home_goals - lam) + dlogtau_dlam * lam)
    dll_dmu_scaled = weights * ((away_goals - mu) + dlogtau_dmu * mu)
    dll_drho = weights * dlogtau_drho

    attack_grad = np.zeros(n)
    defense_grad = np.zeros(n)
    np.add.at(attack_grad, home_idx, dll_dlam_scaled)
    np.add.at(attack_grad, away_idx, dll_dmu_scaled)
    np.add.at(defense_grad, away_idx, -dll_dlam_scaled)
    np.add.at(defense_grad, home_idx, -dll_dmu_scaled)
    home_adv_grad = float(np.sum(dll_dlam_scaled))
    rho_grad = float(np.sum(dll_drho))

    grad = np.concatenate([attack_grad[1:], defense_grad, [home_adv_grad, rho_grad]])
    return nll, -grad


@dataclass
class DixonColesModel:
    xi: float = 0.0018
    max_goals: int = 10
    team_ids: list[int] = field(default_factory=list)
    team_index: dict[int, int] = field(default_factory=dict)
    attack: np.ndarray | None = None
    defense: np.ndarray | None = None
    home_adv: float = 0.0
    rho: float = 0.0
    as_of: datetime | None = None
    n_matches_fit: int = 0

    def fit(
        self,
        matches: list[Match],
        *,
        team_ids: list[int],
        as_of: datetime,
        x0: np.ndarray | None = None,
    ) -> "DixonColesModel":
        """Fit attack/defense/home-advantage/rho by maximum likelihood.

        ``team_ids`` fixes the full team universe (and parameter-vector layout) so
        that repeated incremental fits during a walk-forward backtest can warm-start
        from the previous solution. ``as_of`` anchors the exponential time-decay
        weighting; matches must all be strictly before it.
        """
        n = len(team_ids)
        team_index = {team_id: i for i, team_id in enumerate(team_ids)}

        home_idx = np.array([team_index[m.home_id] for m in matches], dtype=int)
        away_idx = np.array([team_index[m.away_id] for m in matches], dtype=int)
        home_goals = np.array([m.home_goals for m in matches], dtype=float)
        away_goals = np.array([m.away_goals for m in matches], dtype=float)
        days_since = np.array([(as_of - m.kickoff).days for m in matches], dtype=float)
        weights = np.exp(-self.xi * np.clip(days_since, 0, None))

        n_params = 2 * n + 1
        if x0 is None or len(x0) != n_params:
            guess = np.zeros(n_params)
            guess[2 * n - 1] = 0.2  # home_adv prior
        else:
            guess = x0

        result = minimize(
            _negative_log_likelihood_and_grad,
            guess,
            args=(n, home_idx, away_idx, home_goals, away_goals, weights),
            method="L-BFGS-B",
            jac=True,
            bounds=_pack_bounds(n),
            options={"maxiter": 300},
        )

        attack, defense, home_adv, rho = _unpack(result.x, n)
        self.team_ids = list(team_ids)
        self.team_index = team_index
        self.attack = attack
        self.defense = defense
        self.home_adv = float(home_adv)
        self.rho = float(rho)
        self.as_of = as_of
        self.n_matches_fit = len(matches)
        self._last_x = result.x
        return self

    def goal_expectations(self, home_id: int, away_id: int) -> tuple[float, float]:
        if self.attack is None:
            raise RuntimeError("Model has not been fit yet.")
        hi = self.team_index.get(home_id)
        ai = self.team_index.get(away_id)
        atk_h = self.attack[hi] if hi is not None else 0.0
        def_h = self.defense[hi] if hi is not None else 0.0
        atk_a = self.attack[ai] if ai is not None else 0.0
        def_a = self.defense[ai] if ai is not None else 0.0
        lam = float(np.exp(atk_h - def_a + self.home_adv))
        mu = float(np.exp(atk_a - def_h))
        return lam, mu

    def predict(self, home_id: int, away_id: int) -> dict:
        lam, mu = self.goal_expectations(home_id, away_id)
        g = self.max_goals
        home_goals_grid = np.arange(g)
        away_goals_grid = np.arange(g)

        log_home_pmf = home_goals_grid * np.log(lam) - lam - gammaln(home_goals_grid + 1)
        log_away_pmf = away_goals_grid * np.log(mu) - mu - gammaln(away_goals_grid + 1)
        matrix = np.exp(log_home_pmf[:, None] + log_away_pmf[None, :])

        hh, aa = np.meshgrid(home_goals_grid, away_goals_grid, indexing="ij")
        matrix = matrix * _tau(hh.astype(float), aa.astype(float), np.full_like(matrix, lam), np.full_like(matrix, mu), self.rho)
        matrix = np.clip(matrix, 0, None)
        matrix = matrix / matrix.sum()

        home_win = float(np.sum(matrix[hh > aa]))
        draw = float(np.sum(matrix[hh == aa]))
        away_win = float(np.sum(matrix[hh < aa]))
        over_2_5 = float(np.sum(matrix[(hh + aa) >= 3]))
        btts_yes = float(np.sum(matrix[(hh >= 1) & (aa >= 1)]))

        top_scores = sorted(
            ((f"{i}-{j}", float(matrix[i, j])) for i in range(g) for j in range(g)),
            key=lambda item: item[1],
            reverse=True,
        )[:5]

        return {
            "home": home_win,
            "draw": draw,
            "away": away_win,
            "over_2_5": over_2_5,
            "under_2_5": 1 - over_2_5,
            "btts_yes": btts_yes,
            "btts_no": 1 - btts_yes,
            "lambda_home": lam,
            "mu_away": mu,
            "correct_score": dict(top_scores),
        }
