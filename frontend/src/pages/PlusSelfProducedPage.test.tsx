import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  PlusSelfProducedView,
  type PlusSelfProducedResult,
  type PlusSelfProducedStatus,
} from "./PlusSelfProducedPage";


const status: PlusSelfProducedStatus = {
  site_id: "US06-5002",
  source_group_id: 4,
  plus_group_id: 6,
  banned_group_id: 7,
  model: "gpt-5.4",
  running: false,
  settings: {
    enabled: true,
    interval_minutes: 15,
  },
  last_run: {
    status: "completed_with_errors",
    candidates: 8,
    tested: 8,
    eligible: 4,
    promoted: 3,
    banned: 2,
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
];

const callbacks = {
  onEnabledChange: () => undefined,
  onIntervalChange: () => undefined,
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
    expect(html).toContain("4 → 6");
    expect(html).toContain("4 → 7");
    expect(html).toContain("gpt-5.4");
    expect(html).toContain("15 分钟");
    expect(html).toContain("测试通过");
    expect(html).toContain("429 可用");
    expect(html).toContain("401 封禁");
    expect(html).toContain("模型不支持");
    expect(html).toContain("失败");
    expect(html).toContain("已晋级");
    expect(html).toContain("已转封禁");
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
});
