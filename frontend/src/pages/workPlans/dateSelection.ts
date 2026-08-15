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

export function isoDateRange(startDate: string, endDate: string): string[] {
  const start = parseIsoDate(startDate);
  const end = parseIsoDate(endDate);
  if (end < start) {
    return [];
  }
  const values: string[] = [];
  for (let cursor = start; cursor <= end; cursor += DAY_MS) {
    values.push(formatIsoDate(cursor));
  }
  return values;
}

export function resolveWeekdays(startDate: string, endDate: string, weekdays: number[]): string[] {
  const selectedWeekdays = new Set(weekdays.filter((value) => value >= 0 && value <= 6));
  return isoDateRange(startDate, endDate).filter((value) =>
    selectedWeekdays.has(new Date(parseIsoDate(value)).getUTCDay()),
  );
}

export function normalizeSelectedDates(values: string[]): string[] {
  return [...new Set(values.map((value) => formatIsoDate(parseIsoDate(value))))].sort();
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

export function thirtyMinuteOptions(): readonly TimeOption[] {
  return HALF_HOUR_OPTIONS;
}
