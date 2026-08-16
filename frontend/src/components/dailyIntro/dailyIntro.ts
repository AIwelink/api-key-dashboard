export interface DailyIntroStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export type DailyIntroMember = {
  id?: string;
  email: string;
};

const DAILY_INTRO_STORAGE_PREFIX = "aiwelink.daily-team-intro.v1";
const shanghaiDateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function shanghaiDateKey(now: Date): string {
  const parts = shanghaiDateFormatter.formatToParts(now);
  const value = (type: "year" | "month" | "day") => (
    parts.find((part) => part.type === type)?.value ?? ""
  );
  return `${value("year")}-${value("month")}-${value("day")}`;
}

export function dailyIntroIdentity(member: DailyIntroMember): string {
  return member.id?.trim() || member.email.trim().toLowerCase();
}

function dailyIntroStorageKey(member: DailyIntroMember): string {
  return `${DAILY_INTRO_STORAGE_PREFIX}:${encodeURIComponent(dailyIntroIdentity(member))}`;
}

export function shouldShowDailyIntro(
  storage: DailyIntroStorage,
  member: DailyIntroMember,
  now = new Date(),
): boolean {
  try {
    return storage.getItem(dailyIntroStorageKey(member)) !== shanghaiDateKey(now);
  } catch {
    return true;
  }
}

export function claimDailyIntro(
  storage: DailyIntroStorage,
  member: DailyIntroMember,
  now = new Date(),
): boolean {
  const date = shanghaiDateKey(now);
  const storageKey = dailyIntroStorageKey(member);

  try {
    if (storage.getItem(storageKey) === date) return false;
    storage.setItem(storageKey, date);
  } catch {
    return true;
  }

  return true;
}
