import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MetricDefinition } from "./MetricDefinition";
import { WorkspaceRail } from "./WorkspaceRail";

describe("data workspace primitives", () => {
  it("renders a keyboard-accessible metric definition with its full calculation context", () => {
    const html = renderToStaticMarkup(
      <MetricDefinition
        label="活跃用户"
        details={{
          definition: "至少成功调用一次的用户。",
          formula: "COUNT(DISTINCT user_id)",
          included: "普通用户",
          excluded: "内部用户",
          source: "usage_records",
          freshness: "15 分钟",
        }}
      />,
    );

    expect(html).toContain('type="button"');
    expect(html).toContain('aria-describedby="');
    expect(html).toContain('role="tooltip"');
    expect(html).toContain("COUNT(DISTINCT user_id)");
    expect(html).toContain("纳入");
    expect(html).toContain("排除");
    expect(html).toContain("来源");
    expect(html).toContain("更新");
  });

  it("renders a named page index with stable section anchors and status context", () => {
    const html = renderToStaticMarkup(
      <WorkspaceRail
        label="运营概览页面索引"
        items={[
          { id: "summary", label: "经营总览", count: "01" },
          { id: "lifecycle", label: "生命周期", count: "02" },
        ]}
        status={{ title: "数据同步正常", detail: "截至 09:15" }}
      />,
    );

    expect(html).toContain('<nav class="workspace-rail" aria-label="运营概览页面索引">');
    expect(html).toContain('href="#summary"');
    expect(html).toContain('href="#lifecycle"');
    expect(html).toContain('aria-current="location"');
    expect(html).toContain("数据同步正常");
    expect(html).toContain("截至 09:15");
  });
});
