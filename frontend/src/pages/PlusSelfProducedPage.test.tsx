import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  PlusSelfProducedView,
  buildSettingsPayload,
  type PlusGroupOption,
  type PlusGroupSelection,
  type PlusSelfProducedResult,
  type PlusSelfProducedStatus,
} from "./PlusSelfProducedPage";


const status: PlusSelfProducedStatus = {
  site_id: "US06-5002",
  source_group_id: 14,
  plus_group_id: 16,
  banned_group_id: 17,
  plus_error_group_id: 19,
  model: "gpt-5.6-sol",
  running: false,
  settings: {
    enabled: true,
    interval_minutes: 15,
    source_group_id: 14,
    plus_group_id: 16,
    banned_group_id: 17,
    plus_error_group_id: 19,
  },
  last_run: {
    status: "completed_with_errors",
    candidates: 8,
    tested: 8,
    eligible: 4,
    promoted: 3,
    banned: 2,
    downgraded: 1,
    plus_errors: 1,
    failed: 3,
    finished_at: "2026-07-21T10:00:00+00:00",
  },
};

const results: PlusSelfProducedResult[] = [
  {
    id: "US06-5002:10",
    remote_account_id: 10,
    account_name: "plus user@example.com",
    classification: "passed",
    action_status: "promoted",
    tested_at: "2026-07-21T10:00:00+00:00",
  },
  {
    id: "US06-5002:11",
    remote_account_id: 11,
    account_name: "plusready@example.com",
    classification: "rate_limited_but_eligible",
    action_status: "promoted",
    error: "API returned 429",
    tested_at: "2026-07-21T10:00:01+00:00",
  },
  {
    id: "US06-5002:12",
    remote_account_id: 12,
    account_name: "blocked@example.com",
    classification: "unauthorized_banned",
    action_status: "banned",
    error: "API returned 401",
    tested_at: "2026-07-21T10:00:02+00:00",
  },
  {
    id: "US06-5002:13",
    remote_account_id: 13,
    account_name: "free@example.com",
    classification: "model_not_supported",
    action_status: "not_moved",
    error: "model is not supported when using Codex with a ChatGPT account",
    tested_at: "2026-07-21T10:00:03+00:00",
  },
  {
    id: "US06-5002:14",
    remote_account_id: 14,
    account_name: "failed@example.com",
    classification: "failed",
    action_status: "not_moved",
    error: "API returned 403",
    tested_at: "2026-07-21T10:00:04+00:00",
  },
  {
    id: "US06-5002:15",
    remote_account_id: 15,
    account_name: "plus free@example.com",
    classification: "model_not_supported",
    action_status: "reverted_to_free",
    tested_at: "2026-07-21T10:00:05+00:00",
  },
  {
    id: "US06-5002:16",
    remote_account_id: 16,
    account_name: "plus blocked@example.com",
    classification: "unauthorized_banned",
    action_status: "plus_error",
    tested_at: "2026-07-21T10:00:06+00:00",
  },
  {
    id: "US06-5002:17",
    remote_account_id: 17,
    account_name: "reset failed@example.com",
    classification: "failed",
    action_status: "model_reset_failed",
    error: "model reset failed",
    tested_at: "2026-07-21T10:00:07+00:00",
  },
];

const groups: PlusGroupOption[] = [
  { id: 14, name: "plus自产", status: "active" },
  { id: 16, name: "plus 正常号池", status: "active" },
  { id: 17, name: "封禁账号池", status: "active" },
  { id: 19, name: "plus 错误池", status: "active" },
];

const groupSelection: PlusGroupSelection = {
  source_group_id: 14,
  plus_group_id: 16,
  banned_group_id: 17,
  plus_error_group_id: 19,
};

const callbacks = {
  onEnabledChange: () => undefined,
  onIntervalChange: () => undefined,
  onGroupChange: () => undefined,
  onSave: () => undefined,
  onRun: () => undefined,
  onPageChange: () => undefined,
};

describe("plus self-produced page", () => {
  it("renders workflow facts, metrics, and every probe classification", () => {
    const html = renderToStaticMarkup(
      <PlusSelfProducedView
        status={status}
        results={results}
        enabled
        intervalMinutes={15}
        groups={groups}
        groupSelection={groupSelection}
        loading={false}
        saving={false}
        running={false}
        resultsTotal={205}
        resultsPage={1}
        resultsPageSize={100}
        {...callbacks}
      />,
    );

    expect(html).toContain("plus自产");
    expect(html).toContain("US06-5002");
    expect(html).toContain("14 → 16");
    expect(html).toContain("14 → 17");
    expect(html).toContain("16 → 14");
    expect(html).toContain("16 → 19");
    expect(html).toContain("自产来源池");
    expect(html).toContain("Plus 正常池");
    expect(html).toContain("封禁池");
    expect(html).toContain("Plus 错误池");
    expect(html).toContain("14 · plus自产");
    expect(html).toContain("16 · plus 正常号池");
    expect(html).toContain("gpt-5.6-sol");
    expect(html).toContain("15 分钟");
    expect(html).toContain("测试通过");
    expect(html).toContain("429 可用");
    expect(html).toContain("401 封禁");
    expect(html).toContain("模型不支持");
    expect(html).toContain("失败");
    expect(html).toContain("已晋级");
    expect(html).toContain("已转封禁");
    expect(html).toContain("已还原 Free");
    expect(html).toContain("Plus 错误池");
    expect(html).toContain("模型重置失败");
    expect(html).toContain("205 条");
    expect(html).toContain("第 1 / 3 页");
    expect(html).toContain("下一页");
  });

  it("disables commands while a probe is running", () => {
    const html = renderToStaticMarkup(
      <PlusSelfProducedView
        status={{ ...status, running: true }}
        results={[]}
        enabled
        intervalMinutes={15}
        groups={groups}
        groupSelection={groupSelection}
        loading={false}
        saving={false}
        running
        resultsTotal={0}
        resultsPage={1}
        resultsPageSize={100}
        {...callbacks}
      />,
    );

    expect(html).toContain("探测中...");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>探测中\.\.\.<\/button>/);
  });

  it("uses gpt-5.6-sol while status is loading", () => {
    const html = renderToStaticMarkup(
      <PlusSelfProducedView
        status={null}
        results={[]}
        enabled
        intervalMinutes={15}
        groups={groups}
        groupSelection={groupSelection}
        loading
        saving={false}
        running={false}
        resultsTotal={0}
        resultsPage={1}
        resultsPageSize={100}
        {...callbacks}
      />,
    );

    expect(html).toContain("gpt-5.6-sol");
    expect(html).toContain("16 → 19");
  });

  it("blocks save when group roles are not one-to-one", () => {
    const html = renderToStaticMarkup(
      <PlusSelfProducedView
        status={status}
        results={[]}
        enabled
        intervalMinutes={15}
        groups={groups}
        groupSelection={{ ...groupSelection, plus_group_id: 14 }}
        loading={false}
        saving={false}
        running={false}
        resultsTotal={0}
        resultsPage={1}
        resultsPageSize={100}
        {...callbacks}
      />,
    );

    expect(html).toContain("四个分组必须一对一，不能重复");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>保存设置<\/button>/);
  });

  it("builds a complete settings payload", () => {
    expect(buildSettingsPayload(true, 15, groupSelection)).toEqual({
      enabled: true,
      interval_minutes: 15,
      source_group_id: 14,
      plus_group_id: 16,
      banned_group_id: 17,
      plus_error_group_id: 19,
    });
  });
});
