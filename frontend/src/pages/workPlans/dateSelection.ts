const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY_MS = 86_400_000;
const MAX_SELECTED_DATES = 5;

export type TimeOption = Readonly<{ value: string; label: string }>;

const HALF_HOUR_OPTIONS: readonly TimeOption[] = Array.from({ length: 48 }, (_, index) => {
  const hour = Math.floor(index / 2);
  const minute = index % 2 === 0 ? "00" : "30";
  const value = `${String(hour).padStart(2, "0")}:${minute}`;
  return { value, label: value };
});
const END_OF_DAY_OPTION: TimeOption = { value: "24:00", label: "24:00" };

function parseIsoDate(value: string): number {
  const match = ISO_DATE_PATTERN.exec(value);
  if (!match) {
    throw new Error("日期格式无效");
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const timestamp = Date.UTC(year, month - 1, day);
  const parsed = new Date(timestamp);
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day
  ) {
    throw new Error("日期格式无效");
  }
  return timestamp;
}

function formatIsoDate(timestamp: number): string {
  const value = new Date(timestamp);
  return [
    String(value.getUTCFullYear()).padStart(4, "0"),
    String(value.getUTCMonth() + 1).padStart(2, "0"),
    String(value.getUTCDate()).padStart(2, "0"),
  ].join("-");
}

export function isoDateRange(startDate: string, endDate: string, limit = Number.POSITIVE_INFINITY): string[] {
  if (!startDate || !endDate) return [];
  const start = parseIsoDate(startDate);
  const end = parseIsoDate(endDate);
  if (end < start) {
    return [];
  }
  const normalizedLimit = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : Number.POSITIVE_INFINITY;
  if (normalizedLimit === 0) return [];
  const values: string[] = [];
  for (let cursor = start; cursor <= end; cursor += DAY_MS) {
    values.push(formatIsoDate(cursor));
    if (values.length >= normalizedLimit) break;
  }
  return values;
}

export function resolveWeekdays(
  startDate: string,
  endDate: string,
  weekdays: number[],
  limit = Number.POSITIVE_INFINITY,
): string[] {
  if (!startDate || !endDate) return [];
  const selectedWeekdays = new Set(weekdays.filter((value) => value >= 0 && value <= 6));
  const start = parseIsoDate(startDate);
  const end = parseIsoDate(endDate);
  if (end < start || !selectedWeekdays.size) return [];
  const normalizedLimit = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : Number.POSITIVE_INFINITY;
  if (normalizedLimit === 0) return [];
  const values: string[] = [];
  for (let cursor = start; cursor <= end; cursor += DAY_MS) {
    if (selectedWeekdays.has(new Date(cursor).getUTCDay())) values.push(formatIsoDate(cursor));
    if (values.length >= normalizedLimit) break;
  }
  return values;
}

export function normalizeSelectedDates(values: string[]): string[] {
  return [...new Set(
    values
      .filter((value) => value.trim().length > 0)
      .map((value) => formatIsoDate(parseIsoDate(value))),
  )].sort();
}

export function validateSelectedDates(values: string[]): string | null {
  if (values.length === 0) {
    return "请至少选择 1 个计划日期";
  }
  if (normalizeSelectedDates(values).length > MAX_SELECTED_DATES) {
    return "一次最多添加 5 天计划，请缩小日期范围";
  }
  return null;
}

export function thirtyMinuteOptions(options: { includeEndOfDay?: boolean } = {}): readonly TimeOption[] {
  return options.includeEndOfDay ? [...HALF_HOUR_OPTIONS, END_OF_DAY_OPTION] : HALF_HOUR_OPTIONS;
}
