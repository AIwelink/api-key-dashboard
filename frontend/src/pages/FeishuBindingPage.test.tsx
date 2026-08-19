// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import {
  launchFeishuAuthorization,
  openFeishuPopup,
  startFeishuSessionPolling,
} from "../auth/feishu";
import type { User } from "../types";
import { FeishuBindingPage } from "./FeishuBindingPage";

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../auth/feishu", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../auth/feishu")>()),
  launchFeishuAuthorization: vi.fn(() => "popup"),
  openFeishuPopup: vi.fn(() => ({ closed: false })),
  startFeishuSessionPolling: vi.fn(() => vi.fn()),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const session = {
  session_id: "session-bind-1",
  authorization_url: "https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
  ticket: "ticket-token-at-least-20-characters",
  expires_at: "2026-08-18T12:05:00+00:00",
};

const user: User = {
  email: "member@example.com",
  name: "Member",
  role: "maintainer",
  authorization_status: "active",
  feishu_bound: false,
};

describe("FeishuBindingPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root.render(<FeishuBindingPage token="local-jwt" user={user} onBound={vi.fn()} onLogout={vi.fn()} />));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it("starts an authenticated binding session and offers no skip action", async () => {
    vi.mocked(api).mockResolvedValueOnce(session);
    const bind = container.querySelector<HTMLButtonElement>("[data-action=feishu-bind]");

    await act(async () => bind?.click());

    expect(container.textContent).toContain("绑定飞书后才能继续使用");
    expect(container.textContent).not.toContain("稍后");
    expect(api).toHaveBeenCalledWith("/auth/feishu/bind-session", "local-jwt", { method: "POST" });
    expect(openFeishuPopup).toHaveBeenCalled();
    expect(launchFeishuAuthorization).toHaveBeenCalledWith(session, expect.objectContaining({ flow: "binding" }));
    expect(startFeishuSessionPolling).toHaveBeenCalled();
  });
});
