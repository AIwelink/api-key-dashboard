import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RedemptionCodeTable, type RedemptionCodeRow } from "./RedemptionCodeTable";


const rows: RedemptionCodeRow[] = [
  {
    id: 101,
    site_id: "aiwelink",
    code_mask: "rede...cret",
    value: 100,
    status: "unused",
    origin: "management_panel",
    created_by: "Owner",
    created_by_current_user: true,
    created_at: "2026-08-12T04:00:00Z",
  },
  {
    id: 102,
    site_id: "aiwelink",
    code_mask: "used...cret",
    value: 50,
    status: "used",
    origin: "api_site",
    created_by_current_user: false,
    created_at: "2026-08-12T03:00:00Z",
    used_by: 49,
    used_at: "2026-08-12T03:30:00Z",
    user: { email: "customer@example.com" },
  },
];

describe("redemption code table", () => {
  it("renders masked rows, attribution labels, and stable columns", () => {
    const html = renderToStaticMarkup(
      <RedemptionCodeTable
        canWrite
        loading={false}
        onDelete={() => undefined}
        onPageChange={() => undefined}
        onReveal={() => undefined}
        onSelectionChange={() => undefined}
        page={1}
        pages={2}
        rows={rows}
        selectedIds={new Set()}
        total={2}
      />,
    );

    expect(html).toContain("兑换码");
    expect(html).toContain("来源");
    expect(html).toContain("创建账号");
    expect(html).toContain("使用账号");
    expect(html).toContain("管理面板创建");
    expect(html).toContain("API站点创建");
    expect(html).toContain("rede...cret");
    expect(html).not.toContain("redeem-secret");
    expect(html).toContain("当前账号");
  });

  it("keeps deletion disabled unless the capability is explicitly enabled", () => {
    const html = renderToStaticMarkup(
      <RedemptionCodeTable
        canWrite
        loading={false}
        onDelete={() => undefined}
        onPageChange={() => undefined}
        onReveal={() => undefined}
        onSelectionChange={() => undefined}
        page={1}
        pages={1}
        rows={rows}
        selectedIds={new Set()}
        total={2}
      />,
    );

    expect(html).not.toContain('type="checkbox"');
    expect(html).not.toContain("删除兑换码");
    expect(html).toContain("查看明文");
  });

  it("only allows unused rows to be selected and deleted when deletion is enabled", () => {
    const html = renderToStaticMarkup(
      <RedemptionCodeTable
        canDelete
        canWrite
        loading={false}
        onDelete={() => undefined}
        onPageChange={() => undefined}
        onReveal={() => undefined}
        onSelectionChange={() => undefined}
        page={1}
        pages={1}
        rows={rows}
        selectedIds={new Set([101])}
        total={2}
      />,
    );

    expect((html.match(/type="checkbox"/g) || []).length).toBe(3);
    expect(html).toContain('aria-label="删除兑换码 rede...cret"');
    expect(html).not.toContain('aria-label="删除兑换码 used...cret"');
    expect(html).toContain('aria-label="选择兑换码 used...cret" disabled=""');
  });

  it("keeps read-only roles masked without sensitive actions", () => {
    const html = renderToStaticMarkup(
      <RedemptionCodeTable
        canWrite={false}
        loading={false}
        onDelete={() => undefined}
        onPageChange={() => undefined}
        onReveal={() => undefined}
        onSelectionChange={() => undefined}
        page={1}
        pages={2}
        rows={rows}
        selectedIds={new Set()}
        total={2}
      />,
    );

    expect(html).not.toContain('type="checkbox"');
    expect(html).not.toContain("查看明文");
    expect(html).not.toContain("删除兑换码");
  });

  it("renders accessible pagination controls", () => {
    const html = renderToStaticMarkup(
      <RedemptionCodeTable
        canWrite
        loading={false}
        onDelete={() => undefined}
        onPageChange={() => undefined}
        onReveal={() => undefined}
        onSelectionChange={() => undefined}
        page={2}
        pages={3}
        rows={rows}
        selectedIds={new Set()}
        total={30}
      />,
    );

    expect(html).toContain('aria-label="上一页"');
    expect(html).toContain('aria-label="下一页"');
    expect(html).toContain("第 2 / 3 页");
  });
});
