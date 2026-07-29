# P90 Primary Runway Display Design

## Goal

Make the API pool status page lead with the conservative P90 runway while keeping the current-speed and P50 expected runways visible and clearly explained for operations staff.

## Display Contract

The existing `实时可用时间` metric keeps its position in the capacity summary. Its hierarchy becomes:

1. Primary value: `P90 保守可用时间`, sourced from `forecast_p90_runway_hours` when the hourly forecast is active.
2. First secondary value: `当前速度`, sourced from `actual_runway_hours`.
3. Second secondary value: `P50 期望`, sourced from `dynamic_runway_hours`.
4. Fallback: when P90 is unavailable, the primary value falls back to `actual_runway_hours` and is labeled as a current-speed estimate.

The progress meter follows the same primary value. P90 uses the dynamic runway target; fallback current-speed mode uses the actual runway target.

## Explanations

The visible secondary line names all three values without describing implementation details. Hover help explains:

- P90 is a high-consumption risk boundary intended for conservative capacity decisions, not the most likely duration.
- Current speed divides currently usable quota by the direct `account_cost` burn rate and does not model future day/night changes.
- P50 is the median hourly demand path and represents the expected seasonal duration.

## Scope

This change is frontend-only. It does not modify forecast generation, capacity calculations, health thresholds, notifications, or refill recommendations.

## Verification

- Source-level UI contract tests require the P90 primary label and the secondary order `当前速度` then `P50 期望`.
- Existing frontend tests must pass.
- TypeScript and the production Vite build must pass.
