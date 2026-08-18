// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GrowthCreateModal, submitGrowthCreateModal } from "./GrowthCreateModal";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("GrowthCreateModal", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    root = null;
    container = null;
  });

  async function renderModal(saving = false) {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => {
      root?.render(
        <GrowthCreateModal
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          saving={saving}
          submitDisabled={false}
          submitLabel="创建链接"
          title="新建推广链接"
        >
          <label>
            来源名称
            <input name="source_name" />
          </label>
        </GrowthCreateModal>,
      );
    });
    return document.body.querySelector<HTMLElement>('[role="dialog"]');
  }

  it("prevents native form submission before invoking the business submit callback", () => {
    const event = { preventDefault: vi.fn() };
    const onSubmit = vi.fn();

    submitGrowthCreateModal(event, false, false, onSubmit);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it.each([
    ["saving", true, false],
    ["submission is disabled", false, true],
  ])("does not invoke the business submit callback while %s", (_reason, saving, submitDisabled) => {
    const event = { preventDefault: vi.fn() };
    const onSubmit = vi.fn();

    submitGrowthCreateModal(event, saving, submitDisabled, onSubmit);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("renders an accessible dialog with its supplied title and form body", async () => {
    const dialog = await renderModal();

    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    expect(dialog?.getAttribute("aria-labelledby")).toBe("growth-create-modal-title");
    expect(dialog?.querySelector("#growth-create-modal-title")?.textContent).toBe("新建推广链接");
    expect(dialog?.querySelector('input[name="source_name"]')).not.toBeNull();
  });

  it("shows a saving label and disables close, cancel, and submit actions while saving", async () => {
    const dialog = await renderModal(true);
    const buttons = [...(dialog?.querySelectorAll<HTMLButtonElement>("button") || [])];

    expect(dialog?.textContent).toContain("保存中...");
    expect(buttons.find((button) => button.getAttribute("aria-label") === "关闭")?.disabled).toBe(true);
    expect(buttons.find((button) => button.textContent === "取消")?.disabled).toBe(true);
    expect(buttons.every((button) => button.disabled)).toBe(true);
  });

  it("mounts the backdrop at the document body so transformed page containers cannot offset it", async () => {
    await renderModal();

    const backdrop = document.body.querySelector<HTMLElement>(".growth-create-modal-backdrop");
    expect(backdrop?.parentElement).toBe(document.body);
  });
});
