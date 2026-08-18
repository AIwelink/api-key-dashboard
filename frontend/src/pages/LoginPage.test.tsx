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
import { LoginPage } from "./LoginPage";

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../auth/feishu", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../auth/feishu")>()),
  launchFeishuAuthorization: vi.fn(() => "popup"),
  openFeishuPopup: vi.fn(() => ({ closed: false })),
  startFeishuSessionPolling: vi.fn(() => vi.fn()),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const bindingSession = {
  status: "binding_required" as const,
  session_id: "session-1",
  authorization_url: "https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
  ticket: "ticket-token-at-least-20-characters",
  expires_at: "2026-08-18T12:05:00+00:00",
};

describe("LoginPage Feishu flow", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root.render(<LoginPage onLogin={vi.fn()} showToast={vi.fn()} />));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  it("makes Feishu QR authorization the primary login action", () => {
    const primary = container.querySelector<HTMLButtonElement>('[data-action="feishu-login"]');

    expect(primary?.textContent).toContain("飞书扫码登录");
    expect(container.textContent).toContain("账号密码登录");
  });

  it("opens a placeholder popup synchronously before creating the session", async () => {
    vi.mocked(api).mockResolvedValueOnce(bindingSession);
    const primary = container.querySelector<HTMLButtonElement>('[data-action="feishu-login"]');

    await act(async () => primary?.click());

    expect(openFeishuPopup).toHaveBeenCalledBefore(vi.mocked(api));
    expect(launchFeishuAuthorization).toHaveBeenCalledWith(
      bindingSession,
      expect.objectContaining({ popup: expect.anything() }),
    );
    expect(startFeishuSessionPolling).toHaveBeenCalled();
  });

  it("enters mandatory binding when password login returns binding_required", async () => {
    vi.mocked(api).mockResolvedValueOnce(bindingSession);
    const details = container.querySelector("details");
    details?.setAttribute("open", "");
    const email = container.querySelector<HTMLInputElement>('input[name="email"]');
    const password = container.querySelector<HTMLInputElement>('input[name="password"]');
    const form = container.querySelector<HTMLFormElement>("form");
    if (email) email.value = "member@example.com";
    if (password) password.value = "password123";

    await act(async () => form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));

    expect(container.querySelector('[data-auth-phase="binding"]')?.textContent).toContain("完成飞书绑定");
    expect(container.querySelector("form")).toBeNull();
    expect(startFeishuSessionPolling).toHaveBeenCalled();
  });
});
