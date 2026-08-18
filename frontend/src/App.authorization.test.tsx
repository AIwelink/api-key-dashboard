// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api/client";
import type { User } from "./types";
import App from "./App";

vi.mock("./api/client", () => ({ api: vi.fn() }));
vi.mock("./hooks/useForegroundPresence", () => ({ useForegroundPresence: vi.fn() }));
vi.mock("./components/dailyIntro/DailyTeamIntro", () => ({ DailyTeamIntroGate: () => null }));
vi.mock("./pages/WorkPlansPage", () => ({ WorkPlansPage: () => <div data-testid="work-plans">工作计划</div> }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const pendingUser: User = {
  id: "pending@example.com",
  email: "pending@example.com",
  name: "待授权成员",
  role: "viewer",
  authorization_status: "pending",
  feishu_bound: true,
  feishu_name: "飞书成员",
  permissions: { allowed_views: [], default_view: null },
};

const activeUser: User = {
  ...pendingUser,
  authorization_status: "active",
  role: "maintainer",
  permissions: { allowed_views: ["work-plans"], default_view: "work-plans" },
};

describe("App pending authorization boundary", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("token", "local-jwt");
    localStorage.setItem("user", JSON.stringify(pendingUser));
    window.history.replaceState({}, "", "/");
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("renders only the pending page until an administrator activates the user", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(pendingUser)
      .mockResolvedValueOnce(activeUser);

    await act(async () => root.render(<App />));
    await act(async () => undefined);

    expect(container.textContent).toContain("尚未分配系统权限，请联系管理员");
    expect(container.querySelector(".pending-auth-page")).not.toBeNull();
    expect(container.querySelector(".sidebar")).toBeNull();
    expect(container.querySelector(".app-shell")).toBeNull();

    const refresh = [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("刷新权限"));
    await act(async () => refresh?.click());

    expect(container.querySelector(".pending-auth-page")).toBeNull();
    expect(container.querySelector(".sidebar")).not.toBeNull();
    expect(container.textContent).toContain("工作计划");
  });
});
