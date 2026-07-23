import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GrowthCreateModal } from "./GrowthCreateModal";

describe("GrowthCreateModal", () => {
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
