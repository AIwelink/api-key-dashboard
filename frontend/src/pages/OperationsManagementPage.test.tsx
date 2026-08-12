import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  OperationsManagementPage,
  InternalUserActionButtons,
  adjustmentSubmitDisabled,
  averageConsumption,
  buildOperationsQuery,
  buildRedemptionPayload,
  canManageOperations,
  conversionRateEffectiveHint,
  emptyRedemptionForm,
  formatLifecycleRate,
  formatRetentionRate,
  operationsIncomeLabel,
  orderOperationsSites,
  paymentRate,
  internalUserDeleteDetails,
  preferredOperationsSiteId,
  recognitionStatusLabel,
  refreshFailureMessage,
  redemptionSubmitDisabled,
} from "./OperationsManagementPage";
import type { OperationsSiteId } from "../types";


const props = {
  token: "token",
  role: "admin",
  allowedSiteIds: ["aiwelink", "aigclink"] as OperationsSiteId[],
  showToast: () => undefined,
};

describe("operations management workspace", () => {
  it("prioritizes AIWeLink and falls back to the first authorized site", () => {
    const sites = [
      { value: "aigclink" as OperationsSiteId, label: "AIGCLink" },
      { value: "aiwelink" as OperationsSiteId, label: "AIWeLink" },
    ];

    expect(orderOperationsSites(sites).map((site) => site.value)).toEqual(["aiwelink", "aigclink"]);
    expect(preferredOperationsSiteId(sites)).toBe("aiwelink");
    expect(preferredOperationsSiteId([sites[0]])).toBe("aigclink");
    expect(preferredOperationsSiteId([])).toBe("");
  });

  it("defaults multi-site selectors to AIWeLink while keeping all-sites explicit", () => {
    const html = renderToStaticMarkup(<OperationsManagementPage {...props} />);
    const firstSiteSelect = html.match(/<label><span>站点<\/span><select[^>]*>[\s\S]*?<\/select><\/label>/)?.[0] || "";

    expect(firstSiteSelect).toContain('value="aiwelink" selected=""');
    expect(firstSiteSelect.indexOf(">AIWeLink<")).toBeLessThan(firstSiteSelect.indexOf(">全部站点<"));

    const aigclinkOnly = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={["aigclink"]} />,
    );
    expect(aigclinkOnly).not.toContain("AIWeLink");
    expect(aigclinkOnly).toContain('value="aigclink" selected=""');
  });

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

  it("allows sale credit commands to be submitted without recorded cash", () => {
    expect(redemptionSubmitDisabled({
      ...emptyRedemptionForm,
      purpose: "sale",
      balance_units_per_code: "100",
      cash_amount_cny: "0",
    })).toBe(false);
    expect(adjustmentSubmitDisabled({
      site_id: "aiwelink",
      external_user_id: "user-1",
      purpose: "sale",
      balance_units: "100",
      cash_amount_cny: "0",
      note: "",
    })).toBe(false);
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
    expect(html).toContain('<option value="ordinary" selected="">普通用户</option>');
    expect(html).toContain('<option value="all">全部用户</option>');
    expect(html).toContain('class="operations-freshness-banner');
    expect(html).toContain('class="operations-metric-grid"');
    expect(html).toContain('class="operations-overview-table-stack"');
    expect(html).not.toContain("\u8c03\u7528\u6210\u672c");
    expect(html).toContain("注册用户");
    expect(html).toContain("运营趋势");
    expect(html).toContain("站点运营对比");
    expect(html).toContain("消耗额度");
    expect(html).toContain("人均消耗");
    expect(html).toContain("付费率");
    expect(html).not.toContain("账号运营明细");
    expect(html).not.toContain("收入趋势");
    expect(html).not.toContain("用户活动趋势");
    expect(html).not.toContain("流水收入");
    expect(html).not.toContain("净收入");
  });

  it("calculates site comparison metrics with zero-denominator guards", () => {
    expect(averageConsumption({ consumed_balance_units: 20, active_user_count: 4 })).toBe(5);
    expect(paymentRate({ payer_count: 1, active_user_count: 4 })).toBe(25);
    expect(averageConsumption({ consumed_balance_units: 20, active_user_count: 0 })).toBe(0);
    expect(paymentRate({ payer_count: 1, active_user_count: 0 })).toBe(0);
  });

  it("formats lifecycle ratios without treating immature data as zero", () => {
    expect(formatLifecycleRate(0.25)).toBe("25.0%");
    expect(formatLifecycleRate(null)).toBe("--");
    expect(formatRetentionRate({ numerator: 3, denominator: 4, rate: 0.75 })).toBe("75.0%");
    expect(formatRetentionRate({ numerator: null, denominator: null, rate: null })).toBe("--");
  });

  it("uses site-specific income wording", () => {
    expect(operationsIncomeLabel("aiwelink")).toBe("现金收入");
    expect(operationsIncomeLabel("aigclink")).toBe("调用计费收入");
    expect(operationsIncomeLabel("")).toBe("收入");
  });

  it("uses site-specific income wording in the overview summary", () => {
    const aiwelinkHtml = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={["aiwelink"]} />,
    );
    const aigclinkHtml = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={["aigclink"]} />,
    );

    expect(aiwelinkHtml).toContain('<div class="operations-metric"><span>现金收入</span>');
    expect(aigclinkHtml).toContain('<div class="operations-metric"><span>调用计费收入</span>');
  });

  it("hides AIGCLink value rankings when AIWeLink is the selected site", () => {
    const aiwelinkHtml = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={["aiwelink", "aigclink"]} />,
    );
    const aigclinkHtml = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={["aigclink"]} />,
    );

    expect(aiwelinkHtml).not.toContain("模型计费排行");
    expect(aiwelinkHtml).not.toContain("企业客户排行");
    expect(aigclinkHtml).toContain("模型计费排行");
    expect(aigclinkHtml).toContain("企业客户排行");
  });

  it("renders lifecycle, cohort, billing, and value ranking sections", () => {
    const html = renderToStaticMarkup(<OperationsManagementPage {...props} />);
    const aigclinkHtml = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={["aigclink"]} />,
    );

    expect(html).toContain("生命周期指标");
    expect(html).toContain("24 小时激活率");
    expect(html).toContain("7 日激活率");
    expect(html).toContain("D7 留存");
    expect(html).toContain("活跃用户付费率");
    expect(html).toContain("本期付款率");
    expect(html).toContain("流失预警");
    expect(html).toContain("使用流失");
    expect(html).toContain("回流用户");
    expect(html).toContain("付费与计费分层");
    expect(html).toContain("现金收入");
    expect(html).toContain("调用计费收入");
    expect(html).toContain("订阅摊销收入");
    expect(html).toContain("付费状态未知");
    expect(html).toContain("留存 Cohort");
    expect(html).toContain("D30");
    expect(aigclinkHtml).toContain("模型计费排行");
    expect(aigclinkHtml).toContain("企业客户排行");
    expect(html).toContain('class="operations-lifecycle-grid"');
    expect(aigclinkHtml).toContain('class="operations-value-grid"');
  });

  it("renders internal-user management as a table-based page", () => {
    const html = renderToStaticMarkup(
      <OperationsManagementPage {...props} initialTab="internal-users" />,
    );

    expect(html).toContain("内部人员名单");
    expect(html).toContain("添加内部人员");
    expect(html).toContain('class="operations-table-scroll"');
    expect(html).toContain("邮箱");
    expect(html).toContain("识别状态");
    expect(html).toContain("识别时间");
    expect(html).toContain("业务用户 ID");
    expect(html).toContain("生效时间");
    expect(recognitionStatusLabel("recognized")).toBe("识别成功");
    expect(recognitionStatusLabel("pending")).toBe("待识别");
  });

  it("renders edit and delete actions for writable internal users", () => {
    const item = {
      internal_user_id: "internal-1",
      site_id: "aiwelink",
      email: "staff@example.com",
      external_user_id: "49",
      recognition_status: "recognized" as const,
      active_from: "2026-08-10T08:00:00Z",
    };
    const html = renderToStaticMarkup(
      <InternalUserActionButtons item={item} onDelete={() => undefined} onEdit={() => undefined} />,
    );

    expect(html).toContain("编辑");
    expect(html).toContain("删除");
    expect(html).toContain("删除内部人员 staff@example.com");
    expect(internalUserDeleteDetails(item)).toEqual([
      ["站点", "AIWeLink"],
      ["邮箱", "staff@example.com"],
      ["业务用户 ID", "49"],
    ]);
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
describe("operations site access", () => {
  it("renders a stable no-access state without the analytics workspace", () => {
    const html = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={[]} />,
    );

    expect(html).toContain("\u5c1a\u672a\u5206\u914d\u8fd0\u8425\u7ad9\u70b9\u6743\u9650");
    expect(html).not.toContain('class="operations-metric-grid"');
    expect(html).not.toContain("\u5237\u65b0\u6e90\u6570\u636e");
  });

  it("shows only the assigned site for a single-site user", () => {
    const html = renderToStaticMarkup(
      <OperationsManagementPage {...props} allowedSiteIds={["aiwelink"]} />,
    );

    expect(html).toContain("AIWeLink");
    expect(html).not.toContain("AIGCLink");
    expect(html).not.toContain("\u5168\u90e8\u7ad9\u70b9");
  });

  it("retains the all-sites option for a two-site user", () => {
    const html = renderToStaticMarkup(<OperationsManagementPage {...props} />);

    expect(html).toContain("\u5168\u90e8\u7ad9\u70b9");
    expect(html).toContain("AIWeLink");
    expect(html).toContain("AIGCLink");
  });
});
