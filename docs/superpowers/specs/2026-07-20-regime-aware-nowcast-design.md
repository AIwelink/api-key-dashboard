# Regime-Aware Surge Nowcast Design

## Goal

Detect sudden increases in Plus-pool demand within five minutes and turn that signal into calibrated current-hour and near-term capacity forecasts without deep learning.

## Evidence

The latest 60-day hourly backtest shows `robust_seasonal_analog v1` at 57.03% P50 WAPE and -10.13% Bias. Its P90 coverage is acceptable at 87.79%, but 5:1 risk loss is 1.9233 versus 1.2808 for the daily-seasonal baseline. Production final evaluations are more decisive: current-hour `max(model_remaining, realtime_remaining)` has 96.30% WAPE and +95.17% Bias, while the realtime channel alone has 47.16% WAPE. An exploratory elapsed-time blend reaches 48.35% WAPE, 89.94% coverage, and 0.5509 risk loss, but this is in-sample evidence and is not sufficient for direct release.

## Architecture

### Direct Cost Sampling

The existing PostgreSQL current-hour group counter will also return cumulative account-side `account_cost`. Each one-minute Mongo sample will persist the cumulative value, delta, cost per minute, and annualized cost per hour. TPM, RPM, and concurrency remain corroborating signals. Retention increases from 14 to 60 days so chronological minute-level backtests can be reproduced.

### Surge Detector

A pure detector consumes only samples available at the evaluation time. It calculates direct-cost EMA3/EMA15/EMA60, short and medium ratios, a robust median/MAD score, positive CUSUM, TPM/RPM confirmation, and recent decline evidence. It emits:

```text
stage: stable | warming | surge | cooling
strength: 0..1
confidence: 0..1
signal_count: number of positive signals in the latest three minutes
```

Two of the last three minutes must confirm a surge. A single weaker signal enters `warming`; a confirmed decline enters `cooling`. Direct account cost is preferred, with TPM/RPM ratios as fallback when cost deltas have not accumulated.

### Dynamic Current-Hour Nowcast

The current `max(model, realtime)` rule is replaced by an elapsed-time and regime-aware blend:

```text
realtime_weight = clip(minute / 45 + 0.25 * surge_strength, 0.20, 1.00)
selected_remaining = model_remaining * (1 - realtime_weight)
                   + realtime_remaining * realtime_weight
```

During a confirmed surge, a bounded safety uplift preserves capacity protection. The detector exports enough component values for every five-minute capacity snapshot and final evaluation to compare model, realtime, blend, and selected channels.

### One-to-Three-Hour Transmission

Positive deviation between current direct cost speed and the current-hour seasonal speed is propagated only into the first three forecast points. The uplift decays across the current and next two hours; horizons after three hours retain the seasonal analog shape. This prevents a short spike from contaminating the full 24-hour forecast.

### Failure Behavior

Stale or insufficient minute data leaves the detector in `waiting_data` and uses the existing hourly forecast. Missing direct cost falls back to TPM/RPM-derived speed. Invalid counters never create negative deltas. No fallback may manufacture a dangerous state from absent data.

## Backtesting

A minute repository reads group `account_cost`, requests, and tokens in natural one-minute buckets. Rolling-origin evaluation issues a Nowcast every five minutes and compares:

- current maximum rule;
- model-only remaining;
- realtime-only remaining;
- fixed blends;
- regime-aware dynamic blend.

The split is chronological. Threshold selection uses the earlier period and final reporting uses the latest complete period. Reports include WAPE, Bias, P90 coverage, Pinball Loss, 5:1 risk loss, issue-minute bands, pressure stages, and surge-event precision/recall/detection delay.

## Release Gates

The production selector changes to v2 only if the final holdout satisfies all of these:

- P90 coverage is between 85% and 95%;
- 5:1 risk loss improves by at least 30% versus the current maximum rule;
- WAPE is no worse than realtime-only;
- detected surge median delay is at most five minutes;
- no future information is used by any feature or residual calibration.

If the gates fail, direct-cost samples and backtest tooling may ship, but the online selector remains v1.
