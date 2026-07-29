import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  GrowthDatabaseConfigForm,
  type GrowthDatabaseSettings,
  type GrowthSchemaStatus,
} from "./TrafficAnalysisConfigPage";


const unconfigured: GrowthDatabaseSettings = {
  database_type: "postgresql",
  sql_dsn_configured: false,
  database_endpoint: "",
  last_database_test_at: null,
  last_database_test_ok: null,
  last_database_test_error: "",
  last_database_latency_ms: null,
  last_database_version: "",
};

const configured: GrowthDatabaseSettings = {
  ...unconfigured,
  sql_dsn_configured: true,
  database_endpoint: "growth.internal:5432/aiwelink_growth",
  last_database_test_at: "2026-07-21T00:00:00Z",
  last_database_test_ok: true,
  last_database_latency_ms: 18.4,
  last_database_version: "PostgreSQL 17.5",
};

const callbacks = {
  onSqlDsnChange: () => undefined,
  onSave: () => undefined,
  onTest: () => undefined,
  onInitialize: () => undefined,
};

const uninitializedSchema: GrowthSchemaStatus = {
  initialized: false,
  current_version: null,
  latest_version: "0001_initial",
  pending_versions: ["0001_initial"],
  domain_table_count: 0,
};

const initializedSchema: GrowthSchemaStatus = {
  initialized: true,
  current_version: "0001_initial",
  latest_version: "0001_initial",
  pending_versions: [],
  domain_table_count: 12,
};

describe("growth database config form", () => {
  it("renders the fixed PostgreSQL form and disables testing before configuration", () => {
    const html = renderToStaticMarkup(
      <GrowthDatabaseConfigForm
        settings={unconfigured}
        sqlDsn=""
        loading={false}
        saving={false}
        testing={false}
        initializing={false}
        schemaStatus={null}
        {...callbacks}
      />,
    );

    expect(html).toContain("访问流量分析配置");
    expect(html).toContain("PostgreSQL");
    expect(html).toContain("postgresql://");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>测试数据库连接<\/button>/);
  });

  it("shows only the configured endpoint and public test result", () => {
    const html = renderToStaticMarkup(
      <GrowthDatabaseConfigForm
        settings={configured}
        sqlDsn=""
        loading={false}
        saving={false}
        testing={false}
        initializing={false}
        schemaStatus={initializedSchema}
        {...callbacks}
      />,
    );

    expect(html).toContain("growth.internal:5432/aiwelink_growth");
    expect(html).toContain("PostgreSQL 17.5");
    expect(html).not.toContain("topsecret");
  });

  it("offers initialization when the configured database has no growth schema", () => {
    const html = renderToStaticMarkup(
      <GrowthDatabaseConfigForm
        settings={configured}
        schemaStatus={uninitializedSchema}
        sqlDsn=""
        loading={false}
        saving={false}
        testing={false}
        initializing={false}
        {...callbacks}
      />,
    );

    expect(html).toContain("数据库结构");
    expect(html).toContain("未初始化");
    expect(html).toContain("初始化数据库");
    expect(html).toContain("0001_initial");
  });

  it("shows the current schema version and table count after initialization", () => {
    const html = renderToStaticMarkup(
      <GrowthDatabaseConfigForm
        settings={configured}
        schemaStatus={initializedSchema}
        sqlDsn=""
        loading={false}
        saving={false}
        testing={false}
        initializing={false}
        {...callbacks}
      />,
    );

    expect(html).toContain("结构已就绪");
    expect(html).toContain("0001_initial");
    expect(html).toContain("12 张业务表");
    expect(html).not.toContain(">初始化数据库<");
  });
});
