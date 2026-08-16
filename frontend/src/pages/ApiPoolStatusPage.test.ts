import { describe, expect, it } from "vitest";

import { stableVisibleItems } from "./ApiPoolStatusPage";

describe("API pool request view state", () => {
  it("reuses the same empty collection while request data does not match the current view", () => {
    const accounts = [{ id: 1 }];

    const first = stableVisibleItems(false, accounts);
    const second = stableVisibleItems(false, accounts);

    expect(first).toBe(second);
    expect(first).toEqual([]);
    expect(stableVisibleItems(true, accounts)).toBe(accounts);
  });
});
