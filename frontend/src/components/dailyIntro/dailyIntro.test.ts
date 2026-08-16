import { describe, expect, it } from "vitest";

import {
  claimDailyIntro,
  dailyIntroIdentity,
  shanghaiDateKey,
  type DailyIntroStorage,
} from "./dailyIntro";

function memoryStorage(): DailyIntroStorage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
  };
}

describe("daily team intro claim", () => {
  it("changes day exactly at Shanghai midnight", () => {
    expect(shanghaiDateKey(new Date("2026-08-16T15:59:59Z"))).toBe("2026-08-16");
    expect(shanghaiDateKey(new Date("2026-08-16T16:00:00Z"))).toBe("2026-08-17");
  });

  it("prefers a member id and normalizes the email fallback", () => {
    expect(dailyIntroIdentity({ id: " member-1 ", email: "OWNER@EXAMPLE.COM" })).toBe("member-1");
    expect(dailyIntroIdentity({ email: " OWNER@EXAMPLE.COM " })).toBe("owner@example.com");
  });

  it("claims once per member and allows a new claim on the next Shanghai day", () => {
    const storage = memoryStorage();
    const member = { id: "member-1", email: "member@example.com" };
    const otherMember = { id: "member-2", email: "other@example.com" };

    expect(claimDailyIntro(storage, member, new Date("2026-08-16T16:00:00Z"))).toBe(true);
    expect(claimDailyIntro(storage, member, new Date("2026-08-17T15:59:59Z"))).toBe(false);
    expect(claimDailyIntro(storage, otherMember, new Date("2026-08-17T15:59:59Z"))).toBe(true);
    expect(claimDailyIntro(storage, member, new Date("2026-08-17T16:00:00Z"))).toBe(true);
  });

  it("shows the intro without breaking the app when storage is unavailable", () => {
    const unavailableStorage: DailyIntroStorage = {
      getItem: () => { throw new Error("storage blocked"); },
      setItem: () => { throw new Error("storage blocked"); },
    };

    expect(claimDailyIntro(
      unavailableStorage,
      { email: "member@example.com" },
      new Date("2026-08-17T00:00:00Z"),
    )).toBe(true);
  });
});
