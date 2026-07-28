import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  OperationsManagementPage,
  buildOperationsQuery,
  buildRedemptionPayload,
  canManageOperations,
  conversionRateEffectiveHint,
  emptyRedemptionForm,
  refreshFailureMessage,
} from "./OperationsManagementPage";


const props = {
  token: "token",
  role: "admin",
  showToast: () => undefined,
};

describe("operations management workspace", () => {
  it("builds the default cached analytics query", () => {
    expect(buildOperationsQuery({ siteId: "", segment: "all", range: "7d" })).toBe(
      "?segment=all&range=7d",
    );
    expect(buildOperationsQuery({ siteId: "aiwelink", segment: "internal", range: "30d" })).toBe(
      "?site_id=aiwelink&segment=internal&range=30d",
    );
  });

  it("limits write controls to owner and admin", () => {
    expect(canManageOperations("owner")).toBe(true);
    expect(canManageOperations("admin")).toBe(true);
    expect(canManageOperations("operator")).toBe(false);
  });

  it("builds a trimmed redemption request with an idempotency key", () => {
    const payload = buildRedemptionPayload({
      ...emptyRedemptionForm,
      site_id: " aiwelink ",
      purpose: "internal",
      code_count: "2",
      balance_units_per_code: "100",
      note: " 团队额度 ",
    }, "batch-1");

    expect(payload).toEqual({
      site_id: "aiwelink",
      purpose: "internal",
      code_count: 2,
      balance_units_per_code: 100,
      cash_amount_cny: 0,
      note: "团队额度",
      idempotency_key: "batch-1",
    });
  });

  it("surfaces failed source refreshes with the affected site", () => {
    expect(refreshFailureMessage([
      { site_id: "aiwelink", status: "failed", error: "no conversion rate" },
      { site_id: "aigclink", status: "succeeded" },
    ])).toBe("AIWeLink：no conversion rate");
    expect(refreshFailureMessage([
      { site_id: "aiwelink", status: "succeeded" },
    ])).toBe("");
  });

  it("explains the first-rate historical coverage rule", () => {
    expect(conversionRateEffectiveHint).toContain("首次配置留空将覆盖全部历史数据");
    expect(conversionRateEffectiveHint).toContain("以后调整留空将从当前时间生效");
  });

  it("renders the overview as a full-width query-first workspace", () => {
    const html = renderToStaticMarkup(<OperationsManagementPage {...props} />);

    expect(html).toContain("运营概览");
    expect(html).toContain("内部人员");
    expect(html).toContain("额度与兑换码");
    expect(html).toContain("待分类");
    expect(html).toContain('class="operations-query-bar"');
    expect(html).toContain("最近 7 天");
    expect(html).toContain("全部用户");
    expect(html).toContain('class="operations-freshness-banner');
    expect(html).toContain('class="operations-metric-grid"');
    expect(html).toContain('class="operations-trend-grid"');
    expect(html).not.toContain("\u8c03\u7528\u6210\u672c");
    expect(html).toContain("注册用户");
    expect(html).toContain("净收入");
  });

  it("renders internal-user management as a table-based page", () => {
    const html = renderToStaticMarkup(
      <OperationsManagementPage {...props} initialTab="internal-users" />,
    );

    expect(html).toContain("内部人员名单");
    expect(html).toContain("添加内部人员");
    expect(html).toContain('class="operations-table-scroll"');
    expect(html).toContain("业务用户 ID");
    expect(html).toContain("生效时间");
  });

  it("renders credit actions and conversion rates for admin", () => {
    const html = renderToStaticMarkup(
      <OperationsManagementPage {...props} initialTab="credits" />,
    );

    expect(html).toContain("生成兑换码");
    expect(html).toContain("调整余额");
    expect(html).toContain("新增换算比例");
    expect(html).toContain("余额换算比例");
    expect(html).toContain("每 1 CNY 对应余额");
  });

  it("keeps operator credit and internal-user tabs read-only", () => {
    const creditHtml = renderToStaticMarkup(
      <OperationsManagementPage {...props} role="operator" initialTab="credits" />,
    );
    const internalHtml = renderToStaticMarkup(
      <OperationsManagementPage {...props} role="operator" initialTab="internal-users" />,
    );

    expect(creditHtml).not.toContain("生成兑换码");
    expect(creditHtml).not.toContain("调整余额");
    expect(creditHtml).not.toContain("新增换算比例");
    expect(internalHtml).not.toContain("添加内部人员");
  });

  it("renders pending classification as an independent table page", () => {
    const html = renderToStaticMarkup(
      <OperationsManagementPage {...props} initialTab="classification" />,
    );

    expect(html).toContain("待分类额度记录");
    expect(html).toContain("来源类型");
    expect(html).toContain("额度");
    expect(html).toContain("发生时间");
  });
});
