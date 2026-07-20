import { describe, expect, it } from "vitest";
import { getVisibleNavigationGroups, viewFromPath } from "./App";

describe("app navigation", () => {
  it("hides account workflow pages without leaving empty navigation groups", () => {
    const groups = getVisibleNavigationGroups(true);
    const visibleKeys = groups.flat().map(([key]) => key);

    expect(groups.every((group) => group.length > 0)).toBe(true);
    expect(visibleKeys).not.toContain("upload");
    expect(visibleKeys).not.toContain("todos");
    expect(visibleKeys).not.toContain("push-error-todos");
    expect(visibleKeys).not.toContain("accounts");
    expect(visibleKeys).not.toContain("available-pool");
    expect(visibleKeys).not.toContain("reserve-pool");
  });

  it("keeps hidden account workflow pages directly addressable", () => {
    expect(viewFromPath("/upload-accounts")).toBe("upload");
    expect(viewFromPath("/todo-and-error-accounts")).toBe("todos");
    expect(viewFromPath("/question-account-assignment")).toBe("push-error-todos");
    expect(viewFromPath("/accounts")).toBe("accounts");
    expect(viewFromPath("/available-pool")).toBe("available-pool");
    expect(viewFromPath("/reserve-pool")).toBe("reserve-pool");
  });

  it("uses the API pools page for root and unknown paths", () => {
    expect(viewFromPath("/")).toBe("api-pools");
    expect(viewFromPath("/unknown-page")).toBe("api-pools");
  });
});
