type ScalePoint = readonly [value: number, percent: number];

export type CapacityScaleTone = "excellent" | "info" | "success" | "warning" | "danger" | "muted";

export function runwayScalePercent(value: unknown, target: unknown): number | null {
  const number = optionalNumber(value);
  const targetNumber = optionalNumber(target);
  if (number === null || targetNumber === null || targetNumber <= 0) return null;
  const normalizedTarget = Math.max(1, targetNumber);
  const tier = 100 / 6;
  return tieredPercent(number, [
    [0, 0],
    [1, tier],
    [normalizedTarget, tier * 2],
    [normalizedTarget * 2, tier * 3],
    [normalizedTarget * 4, tier * 4],
    [normalizedTarget * 8, tier * 5],
    [48, 100],
  ]);
}

export function concurrencyCoverageScalePercent(value: unknown, target: unknown): number | null {
  const number = optionalNumber(value);
  const targetNumber = optionalNumber(target);
  if (number === null || targetNumber === null || targetNumber <= 1) return null;
  const tier = 100 / 6;
  return tieredPercent(number, [
    [0, 0],
    [0.8, tier],
    [1, tier * 2],
    [targetNumber, tier * 3],
    [targetNumber * 1.25, tier * 4],
    [targetNumber * (5 / 3), tier * 5],
    [5, 100],
  ]);
}

export function runwayTone(value: unknown, ready: unknown): CapacityScaleTone {
  if (ready !== true) return "muted";
  const number = optionalNumber(value);
  if (number === null) return "muted";
  if (number < 1) return "danger";
  if (number < 3) return "warning";
  if (number >= 48) return "excellent";
  if (number >= 24) return "info";
  return "success";
}

export function concurrencyCoverageTone(value: unknown, ready: unknown): CapacityScaleTone {
  if (ready !== true) return "muted";
  const number = optionalNumber(value);
  if (number === null) return "muted";
  if (number < 1) return "danger";
  if (number < 1.2) return "warning";
  if (number >= 5) return "excellent";
  if (number >= 2) return "info";
  return "success";
}

function tieredPercent(value: number, points: ScalePoint[]): number {
  const normalizedValue = Math.max(0, value);
  if (normalizedValue <= points[0][0]) return points[0][1];
  for (let index = 1; index < points.length; index += 1) {
    const [upperValue, upperPercent] = points[index];
    const [lowerValue, lowerPercent] = points[index - 1];
    if (normalizedValue <= upperValue) {
      const ratio = (normalizedValue - lowerValue) / Math.max(Number.EPSILON, upperValue - lowerValue);
      return lowerPercent + ratio * (upperPercent - lowerPercent);
    }
  }
  return 100;
}

function optionalNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
