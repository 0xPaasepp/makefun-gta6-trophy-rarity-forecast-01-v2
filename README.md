# makefun-gta6-trophy-rarity-forecast-01-v2

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/0xPaasepp/makefun-gta6-trophy-rarity-forecast-01-v2/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/0xPaasepp/makefun-gta6-trophy-rarity-forecast-01-v2/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

This program forecasts how many GTA VI players will have earned each trophy by the end of week one. It reads the trophy-page history up to a checkpoint, collapses repeated reads into distinct snapshots, extrapolates the number of players tracked on a log scale against log time, and extrapolates each trophy's earners on the logit scale with an earners-weighted trend shrunk toward its tier mean — or, when the history is flat and no trend can be fitted, with a dilution prior in which the incoming cohort of new players earns each trophy at a progress-discounted rate. Around each median forecast it builds a spread from the observed residual scatter, a random-walk term that grows with the horizon, and a small-count term, and reports the five requested quantiles of each trophy's earn rate at the target time. Running it writes one `.out` file per case into the output directory and prints each case's forecast players tracked and implied total earners to standard error.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=../kit/gta6-trophy-rarity-forecast-01-kit/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=ddn7bre1&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
