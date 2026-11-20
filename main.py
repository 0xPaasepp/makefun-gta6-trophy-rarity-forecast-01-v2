#!/usr/bin/env python3
"""Forecast GTA VI trophy earn rates one week out.

Reads every ``*.in`` case file from the input directory and writes one
``*.out`` JSON file per case to the output directory.

Model, in brief: work in earners and players separately, then divide.
Player counts are extrapolated on a log scale against log time; each
trophy's earners are extrapolated on the logit scale with an
earners-weighted fit shrunk toward its tier mean, or, when the history
is flat, with a dilution prior for the incoming cohort of new players.
Quantiles come from a normal spread on the logit scale around the
median forecast.

Priors taken from the totals the competition brief itself publishes,
not from any per-trophy answer:
  * a flat history is expected to refresh with players tracked growing
    about 6.06x (the brief's first-refresh jump);
  * the new-cohort progress constant 13.5 is calibrated on the brief's
    published first-refresh totals (3868 players tracked, 4276
    trophies earned): with it the implied new-cohort earners total
    3329 against the published 3333, and a trophy at 100% stays at
    essentially 100%.

Pure standard library, deterministic, no network, no randomness.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import math
import statistics
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

# --- constants -----------------------------------------------------------

RELEASE_EPOCH = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)
EXPECTED_LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z_VALUES = [-1.645, -0.674, 0.0, 0.674, 1.645]
MAX_OUTPUT_BYTES = 65536

# Prior from the brief's published totals: first refresh multiplies the
# players tracked by about 6.06x.
FLAT_REFRESH_MULTIPLIER = 6.06
# Prior from the brief's published totals: new-cohort progress constant.
PROGRESS_CONSTANT = 13.5

# Smallest / largest rate the logit machinery is allowed to see.
LOGIT_LOW = 0.01
LOGIT_HIGH = 99.99
MEDIAN_LOW = 0.05
MEDIAN_HIGH = 99.99
QUANTILE_LOW = 0.01
QUANTILE_HIGH = 99.99


def _parse_time(text):
    """Parse an ISO-8601 timestamp (trailing Z allowed) to UTC datetime."""
    if isinstance(text, str) and text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hours_since_release(moment):
    return (moment - RELEASE_EPOCH).total_seconds() / 3600.0


def _time_coord(moment):
    """Log-time coordinate: ln(1 + hours since the release day began)."""
    hours = _hours_since_release(moment)
    return math.log(max(1.0 + hours, 0.01))


def _logit(percent):
    clamped = min(max(percent, LOGIT_LOW), LOGIT_HIGH)
    frac = clamped / 100.0
    return math.log(frac / (1.0 - frac))


def _inv_logit_percent(logit_value):
    if logit_value >= 0:
        frac = 1.0 / (1.0 + math.exp(-logit_value))
    else:
        exp_value = math.exp(logit_value)
        frac = exp_value / (1.0 + exp_value)
    return 100.0 * frac


def _weighted_line(xs, ys, weights):
    """Weighted least squares line; returns (slope, intercept)."""
    total_w = sum(weights)
    if total_w <= 0:
        return 0.0, (sum(ys) / len(ys) if ys else 0.0)
    x_bar = sum(w * x for w, x in zip(weights, xs)) / total_w
    y_bar = sum(w * y for w, y in zip(weights, ys)) / total_w
    num = sum(w * (x - x_bar) * (y - y_bar)
              for w, x, y in zip(weights, xs, ys))
    den = sum(w * (x - x_bar) ** 2 for w, x in zip(weights, xs))
    if den <= 0:
        return 0.0, y_bar
    slope = num / den
    return slope, y_bar - slope * x_bar


def _ols_line(xs, ys):
    """Ordinary least squares line; returns (slope, intercept)."""
    n = len(xs)
    x_bar = sum(xs) / n
    y_bar = sum(ys) / n
    num = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys))
    den = sum((x - x_bar) ** 2 for x in xs)
    if den <= 0:
        return 0.0, y_bar
    slope = num / den
    return slope, y_bar - slope * x_bar


def _round_half_up_4(value):
    """Round to 4 decimals, half up; whole numbers come back as int."""
    rounded = Decimal(str(value)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP)
    as_float = float(rounded)
    if as_float == int(as_float):
        return int(as_float)
    return as_float


def _input_dir():
    return os.environ.get("MAKEFUN_INPUT_DIR") or "/input"


def _output_dir():
    return os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output"


def _collapse_snapshots(history):
    """Keep a read only when players or any rate differs from last kept."""
    kept = []
    for read in history:
        if not kept:
            kept.append(read)
            continue
        last = kept[-1]
        if read.get("players_tracked") != last.get("players_tracked"):
            kept.append(read)
        elif read.get("rates") != last.get("rates"):
            kept.append(read)
    return kept


def forecast_case(case):
    """Return (rows, forecast_players, implied_total_earners)."""
    if case.get("levels") != EXPECTED_LEVELS:
        raise ValueError("unexpected levels: %r" % (case.get("levels"),))
    history = case.get("history")
    if not history:
        raise ValueError("case %s has no history" % (case.get("id"),))
    trophies = case["trophies"]
    horizon_minutes = float(case["horizon_minutes"])
    horizon_days = horizon_minutes / 1440.0
    target = _parse_time(case["target"])

    snapshots = _collapse_snapshots(history)
    snap_times = [_parse_time(read["at"]) for read in snapshots]
    snap_coords = [_time_coord(moment) for moment in snap_times]
    snap_players = [float(read["players_tracked"]) for read in snapshots]
    last_players = snap_players[-1]

    # --- refresh cadence / expected lag ----------------------------------
    if len(snapshots) >= 2:
        gaps = []
        for prev, cur in zip(snap_times, snap_times[1:]):
            gaps.append((cur - prev).total_seconds() / 60.0)
        median_gap = statistics.median(gaps)
        lag_minutes = min(0.5 * median_gap, 120.0)
    else:
        median_gap = None
        lag_minutes = 0.0
    lag_days = lag_minutes / 1440.0
    target_coord = _time_coord(
        datetime.fromtimestamp(
            target.timestamp() - lag_minutes * 60.0, tz=timezone.utc))

    # --- forecast players tracked ----------------------------------------
    distinct_player_counts = len(set(snap_players))
    if distinct_player_counts >= 3:
        log_players = [math.log(p) for p in snap_players]
        slope, intercept = _ols_line(snap_coords, log_players)
        slope = min(max(slope, 0.0), 2.0)
        # Re-anchor the intercept to the data centre after clamping the
        # slope, so the clamped line still passes through (mean coord,
        # mean log-players).
        intercept = (sum(log_players) / len(log_players)
                     - slope * sum(snap_coords) / len(snap_coords))
        forecast_players = math.exp(intercept + slope * target_coord)
        if forecast_players < last_players:
            forecast_players = last_players
        refreshes_observed = True
    else:
        refreshes_observed = False
        if horizon_minutes >= 60:
            forecast_players = last_players * FLAT_REFRESH_MULTIPLIER
        else:
            forecast_players = last_players

    # --- per-trophy series ------------------------------------------------
    n_trophies = len(trophies)
    series = []  # per trophy: dict with coords, logits, weights, earners
    for idx in range(n_trophies):
        rates = []
        carried = None
        for read in snapshots:
            rate = read["rates"][idx]
            if rate is None:
                rate = carried
            else:
                carried = rate
            if rate is None:
                rate = 0.01
            rates.append(float(rate))
        earners = [r * p / 100.0 for r, p in zip(rates, snap_players)]
        weights = [max(e, 0.5) ** 0.75 for e in earners]
        logits = [_logit(r) for r in rates]
        series.append({
            "rates": rates,
            "earners": earners,
            "weights": weights,
            "logits": logits,
            "distinct_rates": len(set(rates)),
        })

    # --- fitted trends, shrunk toward tier means --------------------------
    fitted = {}  # trophy index -> (own slope, own WLS intercept)
    for idx, item in enumerate(series):
        if item["distinct_rates"] < 3:
            continue
        slope, intercept = _weighted_line(
            snap_coords, item["logits"], item["weights"])
        fitted[idx] = {
            "slope": slope,
            "intercept": intercept,
        }

    tier_slopes = {}
    for idx, fit in fitted.items():
        tier_slopes.setdefault(trophies[idx]["tier"], []).append(fit["slope"])
    all_slopes = [fit["slope"] for fit in fitted.values()]
    tier_mean = {}
    for tier, slopes in tier_slopes.items():
        tier_mean[tier] = sum(slopes) / len(slopes)
    global_mean = sum(all_slopes) / len(all_slopes) if all_slopes else 0.0

    # --- residual spread pooled over fitted trophies ----------------------
    residual_sq = []
    for idx, fit in fitted.items():
        for coord, logit_value in zip(snap_coords, series[idx]["logits"]):
            diff = logit_value - (fit["intercept"] + fit["slope"] * coord)
            residual_sq.append(diff * diff)
    residual_spread = (
        math.sqrt(sum(residual_sq) / len(residual_sq)) if residual_sq else 0.0)

    n_snaps = len(snapshots)
    random_walk = 0.5 * math.sqrt(horizon_days)

    rows = []
    implied_total = 0.0
    for idx, trophy in enumerate(trophies):
        item = series[idx]
        earners_last = item["earners"][-1]
        small_count = min(1.0 / math.sqrt(earners_last + 1.0), 0.5)
        last_rate = item["rates"][-1]

        if idx in fitted:
            fit = fitted[idx]
            tier = trophy["tier"]
            mean_slope = tier_mean.get(tier, global_mean)
            own_weight = n_snaps / (n_snaps + 3.0)
            shrunk_slope = (own_weight * fit["slope"]
                            + (1.0 - own_weight) * mean_slope)
            # Pivot the shrunk line on the UNWEIGHTED centre of the
            # trophy's own points (plain mean time coordinate, plain
            # mean logit rate), not the earners-weighted centre: the
            # weighted pivot anchors the line at the very recent,
            # huge-count snapshots and destabilises the extrapolation
            # when snapshots are few.
            t_mean = sum(snap_coords) / len(snap_coords)
            y_mean = sum(item["logits"]) / len(item["logits"])
            shrunk_intercept = y_mean - shrunk_slope * t_mean
            median_logit = shrunk_intercept + shrunk_slope * target_coord
            median = _inv_logit_percent(median_logit)
            median = min(max(median, MEDIAN_LOW), MEDIAN_HIGH)
            total_spread = math.sqrt(
                residual_spread ** 2 + random_walk ** 2 + small_count ** 2)
            total_spread = min(max(total_spread, 0.25), 1.0)
        else:
            # Dilution prior: old earners stay, new players earn at a
            # progress-discounted rate (see module docstring).
            old_earners = last_rate * last_players / 100.0
            new_players = max(forecast_players - last_players, 0.0)
            progress = 1.0 - math.exp(-last_rate / PROGRESS_CONSTANT)
            new_cohort_rate = last_rate * progress
            new_earners = new_players * new_cohort_rate / 100.0
            median = 100.0 * (old_earners + new_earners) / forecast_players
            median = min(max(median, MEDIAN_LOW), MEDIAN_HIGH)
            if len(snapshots) == 1 and horizon_minutes < 60:
                refresh_part = 0.0
            elif refreshes_observed:
                refresh_part = 0.5 * math.sqrt(lag_days)
            else:
                refresh_part = 0.9
            total_spread = math.sqrt(
                random_walk ** 2 + refresh_part ** 2 + small_count ** 2)

        implied_total += median * forecast_players / 100.0
        median_logit = _logit(median)
        quantiles = []
        for z_value in Z_VALUES:
            value = _inv_logit_percent(median_logit + z_value * total_spread)
            value = _round_half_up_4(value)
            value = min(max(value, QUANTILE_LOW), QUANTILE_HIGH)
            quantiles.append(value)
        # Enforce non-decreasing order with a running maximum.
        for j in range(1, len(quantiles)):
            if quantiles[j] < quantiles[j - 1]:
                quantiles[j] = quantiles[j - 1]
        rows.append({"id": trophy["id"], "q": quantiles})

    return rows, forecast_players, implied_total


def main():
    input_dir = _input_dir()
    output_dir = _output_dir()
    os.makedirs(output_dir, exist_ok=True)
    case_names = sorted(
        name for name in os.listdir(input_dir) if name.endswith(".in"))
    for name in case_names:
        case_path = os.path.join(input_dir, name)
        with open(case_path, "r", encoding="utf-8") as handle:
            try:
                case = json.load(handle)
            except json.JSONDecodeError as exc:
                raise ValueError("unreadable JSON in %s: %s" % (name, exc))
        if not isinstance(case, dict):
            raise ValueError("case %s is not a JSON object" % name)
        rows, forecast_players, implied_total = forecast_case(case)
        payload = {"trophies": rows}
        text = json.dumps(payload, separators=(",", ":")) + "\n"
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise ValueError("output for %s exceeds %d bytes"
                             % (case.get("id"), MAX_OUTPUT_BYTES))
        out_name = os.path.splitext(name)[0] + ".out"
        out_path = os.path.join(output_dir, out_name)
        # Write as a regular file (never a link): remove any existing
        # link first, then write fresh.
        if os.path.islink(out_path) or os.path.exists(out_path):
            os.unlink(out_path)
        with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        sys.stderr.write(
            "%s: forecast_players=%.1f implied_total_earners=%.1f\n"
            % (case.get("id"), forecast_players, implied_total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
