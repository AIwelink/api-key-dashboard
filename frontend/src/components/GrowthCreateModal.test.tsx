import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GrowthCreateModal, submitGrowthCreateModal } from "./GrowthCreateModal";

describe("GrowthCreateModal", () => {
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

  it("renders an accessible dialog with its supplied title and form body", () => {
    const html = renderToStaticMarkup(
      <GrowthCreateModal
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        saving={false}
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

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('aria-labelledby="growth-create-modal-title"');
    expect(html).toContain('id="growth-create-modal-title"');
    expect(html).toContain("新建推广链接");
    expect(html).toContain('name="source_name"');
  });

  it("shows a saving label and disables close, cancel, and submit actions while saving", () => {
    const html = renderToStaticMarkup(
      <GrowthCreateModal
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        saving
        submitDisabled={false}
        submitLabel="创建链接"
        title="新建推广链接"
      >
        <input aria-label="来源名称" />
      </GrowthCreateModal>,
    );

    expect(html).toContain("保存中...");
    expect(html).toMatch(/aria-label="关闭"[^>]*disabled=""/);
    expect(html).toMatch(/>取消<\/button>/);
    expect((html.match(/disabled=""/g) || []).length).toBeGreaterThanOrEqual(3);
  });
});
