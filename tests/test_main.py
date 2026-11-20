#!/usr/bin/env python3
"""Tests for main.py, run as a subprocess (never imported).

Run: python3 tests/test_main.py
Exits non-zero on any failure; prints PASS/FAIL per check.
"""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(REPO, "main.py")
KIT_INPUT = os.path.join(
    os.path.dirname(REPO), "kit",
    "gta6-trophy-rarity-forecast-01-kit", "input")
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]

FAILURES = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL") + "  " + name
          + (("  -- " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def run_program(input_dir, output_dir, extra_env=None, cwd=None,
                extra_args=None):
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = input_dir
    env["MAKEFUN_OUTPUT_DIR"] = output_dir
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable, "-P", "-s", MAIN]
    if extra_args:
        cmd = [sys.executable] + extra_args + [MAIN]
    started = time.time()
    proc = subprocess.run(cmd, env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=60)
    return proc, time.time() - started


def parse_diagnostics(stderr):
    found = {}
    for line in stderr.splitlines():
        match = re.match(
            r"(\S+): forecast_players=([\d.]+) "
            r"implied_total_earners=([\d.]+)", line)
        if match:
            found[match.group(1)] = (float(match.group(2)),
                                      float(match.group(3)))
    return found


def synthetic_case(case_id, at, target, horizon, trophies, history):
    return {
        "schema": "gta6-trophy-rarity-forecast-01/v1",
        "id": case_id,
        "at": at,
        "horizon_minutes": horizon,
        "target": target,
        "levels": LEVELS,
        "game": {"name": "Grand Theft Auto VI", "platform": "PS5",
                 "release_date": "2026-11-19", "source": "synthetic"},
        "trophies": trophies,
        "history": history,
        "notes": "synthetic test case",
    }


def trophy(tid, tier, points):
    return {"id": tid, "name": "Trophy " + tid,
            "description": "Synthetic trophy.", "tier": tier,
            "points": points, "secret": False}


def read(slot, at, players, rates):
    return {"slot": slot, "at": at, "players_tracked": players,
            "trophies_earned": int(sum(rates) * players / 100.0)
            if all(r is not None for r in rates) else 0,
            "rates": rates}


def main():
    # ---- practice inputs: shape --------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = os.path.join(tmp, "out")
        proc, elapsed = run_program(KIT_INPUT, out_dir)
        check("practice run exits 0", proc.returncode == 0, proc.stderr[-500:])
        check("practice run fast (<8s)", elapsed < 8, "%.2fs" % elapsed)
        diag = parse_diagnostics(proc.stderr)
        check("diagnostics present for both cases",
              len(diag) >= 2, proc.stderr[-500:])
        for stem in ("p00", "p01"):
            path = os.path.join(out_dir, stem + ".out")
            check("output %s exists" % stem, os.path.isfile(path))
            if not os.path.isfile(path):
                continue
            raw = open(path, "rb").read()
            check("output %s under byte cap" % stem,
                  len(raw) <= 65536, len(raw))
            check("output %s is a regular file" % stem,
                  os.path.isfile(path) and not os.path.islink(path))
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                check("output %s parses as JSON" % stem, False, exc)
                continue
            check("output %s has exactly one key" % stem,
                  list(data.keys()) == ["trophies"], list(data.keys()))
            rows = data["trophies"]
            check("output %s has 55 rows" % stem, len(rows) == 55, len(rows))
            ids = [row.get("id") for row in rows]
            check("output %s ids unique" % stem,
                  len(set(ids)) == 55, len(set(ids)))
            shape_ok = True
            for row in rows:
                if set(row.keys()) != {"id", "q"}:
                    shape_ok = False
                    break
                qs = row["q"]
                if len(qs) != 5:
                    shape_ok = False
                    break
                if any((not isinstance(q, (int, float)))
                       or (isinstance(q, bool))
                       or (not math.isfinite(q))
                       or q < 0 or q > 100 for q in qs):
                    shape_ok = False
                    break
                if any(qs[j] < qs[j - 1] for j in range(1, 5)):
                    shape_ok = False
                    break
            check("output %s rows well-formed" % stem, shape_ok)

        # ---- aggregates ------------------------------------------------
        if "p00" in diag:
            players, earners = diag["p00"]
            check("p00 forecast players ~3868 (+-50%)",
                  abs(players - 3868) / 3868 <= 0.5, players)
            check("p00 implied earners ~4276 (+-25%)",
                  abs(earners - 4276) / 4276 <= 0.25, earners)
        else:
            check("p00 diagnostics present", False, sorted(diag))
        if "p01" in diag:
            players, earners = diag["p01"]
            check("p01 implied earners ~4276 (+-25%)",
                  abs(earners - 4276) / 4276 <= 0.25, earners)
        else:
            check("p01 diagnostics present", False, sorted(diag))

    # ---- synthetic trend case ------------------------------------------
    trophies = [trophy("t1", "gold", 90), trophy("t2", "silver", 30),
                trophy("t3", "bronze", 15)]
    times = ["2026-11-19T00:30:00Z", "2026-11-19T01:00:00Z",
             "2026-11-19T01:30:00Z", "2026-11-19T02:00:00Z",
             "2026-11-19T02:30:00Z"]
    players = [1000, 1200, 1500, 1900, 2400]
    rates_t1 = [8.0, 8.8, 9.5, 10.2, 11.0]
    rates_t2 = [20.0, 21.0, 22.0, 23.0, 24.0]
    rates_t3 = [4.0, 4.5, 5.0, 5.5, 6.0]
    history = [read(i, times[i], players[i],
                    [rates_t1[i], rates_t2[i], rates_t3[i]])
               for i in range(5)]

    def run_synthetic(case, tmp):
        in_dir = os.path.join(tmp, "in")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(in_dir, exist_ok=True)
        with open(os.path.join(in_dir, case["id"] + ".in"), "w") as handle:
            json.dump(case, handle)
        proc, _ = run_program(in_dir, out_dir)
        if proc.returncode != 0:
            return None, proc
        with open(os.path.join(out_dir, case["id"] + ".out")) as handle:
            return json.load(handle)["trophies"], proc

    with tempfile.TemporaryDirectory() as tmp:
        short = synthetic_case(
            "syn1", "2026-11-19T02:30:00Z", "2026-11-19T03:30:00Z", 60,
            trophies, history)
        long_case = synthetic_case(
            "syn2", "2026-11-19T02:30:00Z", "2026-11-19T08:30:00Z", 360,
            trophies, history)
        rows_short, proc = run_synthetic(short, tmp)
        check("synthetic short-horizon run exits 0",
              proc is not None and proc.returncode == 0,
              proc.stderr[-300:] if proc else "")
        rows_long, proc = run_synthetic(long_case, tmp)
        check("synthetic long-horizon run exits 0",
              proc is not None and proc.returncode == 0,
              proc.stderr[-300:] if proc else "")
        if rows_short and rows_long:
            med_short = {r["id"]: r["q"][2]
                         for r in rows_short}
            med_long = {r["id"]: r["q"][2]
                        for r in rows_long}
            check("trend medians rise above last observed rate (t1)",
                  med_long["t1"] > 11.0, med_long["t1"])
            check("trend medians rise above last observed rate (t3)",
                  med_long["t3"] > 6.0, med_long["t3"])
            check("fastest-rising median below 2x last rate (t3)",
                  med_long["t3"] < 2 * 6.0, med_long["t3"])
            check("fastest-rising median below 2x last rate (t1)",
                  med_long["t1"] < 2 * 11.0, med_long["t1"])
            spread_short = {r["id"]:
                            r["q"][4] - r["q"][0]
                            for r in rows_short}
            spread_long = {r["id"]:
                           r["q"][4] - r["q"][0]
                           for r in rows_long}
            check("spread widens with horizon (t1)",
                  spread_long["t1"] > spread_short["t1"],
                  (spread_short["t1"], spread_long["t1"]))
            check("spread widens with horizon (t3)",
                  spread_long["t3"] > spread_short["t3"],
                  (spread_short["t3"], spread_long["t3"]))

        # ---- nulls and single read ------------------------------------
        null_history = [dict(history[0]),
                        dict(history[1], rates=[None, 21.0, 1.5]),
                        dict(history[2]),
                        dict(history[3]),
                        dict(history[4])]
        null_case = synthetic_case(
            "syn3", "2026-11-19T02:30:00Z", "2026-11-19T03:30:00Z", 60,
            trophies, null_history)
        rows, proc = run_synthetic(null_case, tmp)
        ok = rows is not None and len(rows) == 3 and all(
            len(r["q"]) == 5
            and all(0 <= q <= 100 for q in r["q"])
            for r in rows)
        check("null-rate case produces valid output", ok,
              proc.stderr[-300:] if proc else "")
        single_case = synthetic_case(
            "syn4", "2026-11-19T00:30:00Z", "2026-11-19T02:00:00Z", 90,
            trophies, [history[0]])
        rows, proc = run_synthetic(single_case, tmp)
        ok = rows is not None and len(rows) == 3 and all(
            len(r["q"]) == 5
            and all(0 <= q <= 100 for q in r["q"])
            for r in rows)
        check("single-read case produces valid output", ok,
              proc.stderr[-300:] if proc else "")

    # ---- determinism + grader reproduction ------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        out1 = os.path.join(tmp, "out1")
        out2 = os.path.join(tmp, "out2")
        other_cwd = os.path.join(tmp, "elsewhere")
        os.makedirs(other_cwd)
        proc1, elapsed1 = run_program(KIT_INPUT, out1, cwd=other_cwd)
        proc2, elapsed2 = run_program(KIT_INPUT, out2, cwd=other_cwd)
        check("grader run 1 exits 0", proc1.returncode == 0,
              proc1.stderr[-300:])
        check("grader run 2 exits 0", proc2.returncode == 0,
              proc2.stderr[-300:])
        check("grader runs fast (<8s each)",
              elapsed1 < 8 and elapsed2 < 8, (elapsed1, elapsed2))
        identical = True
        for stem in ("p00", "p01"):
            a = open(os.path.join(out1, stem + ".out"), "rb").read()
            b = open(os.path.join(out2, stem + ".out"), "rb").read()
            if a != b:
                identical = False
        check("grader runs byte-identical", identical)

    print()
    if FAILURES:
        print("FAILED: %d check(s): %s" % (len(FAILURES), FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
