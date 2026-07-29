# Adaptive P90 Surge Propagation Design

## Goal

Make the conservative P90 runway react within minutes when traffic resumes or surges, without replacing the historical hourly forecast with a short-lived realtime spike.

The current-speed runway remains a direct observation. P50 remains the expected seasonal path. P90 becomes an adaptive risk path that combines the existing seasonal forecast, the current minute-level demand regime, and the group's own historical post-surge behavior.

## Root Cause

The current Nowcast replaces only the forecast point that contains the current natural hour. When the remaining quota outlasts that partial hour, the P90 runway immediately returns to untouched seasonal forecast points. A group can therefore show a current-speed runway of only a few hours while its P90 runway remains almost unchanged.

This is especially visible after a near outage: the completed hourly context contains the low-traffic interruption, while minute samples already show that traffic has recovered.

## Forecast Layers

The adaptive P90 path has three layers:

1. **Seasonal baseline**: the existing hourly analog P90 remains the minimum risk forecast for every hour.
2. **Realtime shock observation**: direct `account_cost` per minute is preferred, with TPM and RPM acting as confirmation channels through the existing demand-regime detector.
3. **Historical persistence**: the previous 56 days are searched for comparable rising-demand events. Their following three complete hours determine how much of a surge usually persists for this group.

No layer replaces the others. The short-term signal can only raise the P90 risk path during a confirmed `warming` or `surge` state. It does not lower the seasonal P90 path.

## Historical Persistence Profiles

During hourly forecast generation, completed hourly history is used to build rising-demand transition profiles:

- An event anchor compares an hour's cost with the median of its preceding three complete hours.
- `warming` events have a ratio from 1.20 to below 1.50.
- `surge` events have a ratio of at least 1.50.
- Consecutive rising hours inside the following three-hour window belong to the same event rather than becoming nested anchors.
- Use the greater of the anchor-hour cost and the first following complete-hour cost as the recovered-rate reference. Record the next one, two, and three hour ratios against that reference so a partially interrupted anchor hour does not amplify an already recovered realtime rate twice.
- Prefer events from the same local-time band and the same weekday/weekend class. Fall back to all events in the same intensity class when the preferred set is too small.
- Use recency-weighted P90 persistence ratios so the result remains conservative while still reflecting this group's observed recovery pattern.

Each profile stores its event count, confidence, and three hourly persistence ratios. A profile requires enough completed events before it can receive full weight. Sparse profiles remain usable at reduced weight rather than inventing certainty.

## Adaptive Propagation

Propagation runs after the current-hour Nowcast:

1. Select the historical profile matching the current `warming` or `surge` state.
2. Convert the current direct burn rate into historical continuation candidates for the next three hours using the profile's persistence ratios.
3. Compute a blend weight from demand-regime confidence, signal persistence, channel confirmation, and historical profile confidence.
4. Blend only the positive difference between the seasonal P90 and the historical continuation candidate.
5. Apply the strongest weight to the next hour and reduce it over hours two and three. The historical persistence ratio remains part of each hour's calculation, so the decay is learned rather than solely fixed.

Conceptually:

```text
continuation[h] = realtime_hourly_cost * historical_p90_persistence[h]
weight[h] = regime_confidence * profile_confidence * horizon_decay[h]
adaptive_p90[h] = seasonal_p90[h]
                  + max(0, continuation[h] - seasonal_p90[h]) * weight[h]
```

The current partial hour keeps the existing minute-level Nowcast. `stable` and `cooling` states do not propagate a positive shock. The P50 path is unchanged in this iteration.

## Safety Rules

- Realtime propagation requires fresh continuous minute samples and a confirmed `warming` or `surge` state.
- A single unconfirmed minute cannot change future P90 points.
- Missing or sparse historical transition data limits the blend weight.
- Every adjusted P90 point remains greater than or equal to its P50 point and original seasonal P90 value.
- Propagation affects at most the next three complete hours.
- Forecast generation and profile calculation use only data available before the forecast issue time.

## Observability

Capacity output records:

- whether adaptive propagation was applied;
- selected profile class and event count;
- profile confidence and three persistence ratios;
- realtime hourly cost used by the bridge;
- number of future points adjusted;
- original and adjusted P90 costs for the adjusted horizon.

These fields allow later capacity samples and forecast evaluations to explain why P90 changed.

## Testing

Unit tests cover:

- a confirmed recovery surge lowers P90 runway by adjusting future hours;
- P90 does not collapse all the way to current-speed runway when historical persistence is moderate;
- a partial-hour recovery ramp is merged into one event and is not applied twice to the realtime rate;
- historically persistent surges produce a stronger adjustment than historically short surges;
- stable, cooling, stale, and unconfirmed signals do not propagate;
- sparse history receives limited influence;
- P50 and the original seasonal P90 floor remain unchanged;
- cached forecasts preserve historical profiles.

Backtest evaluation compares the existing and adaptive P90 on rolling origins using P90 coverage, pinball loss, 5:1 underprediction risk loss, and runway error during detected surge windows. Release requires no future-data leakage and no material regression outside surge windows.
