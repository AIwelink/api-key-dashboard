type PoolTrafficSummary = {
  traffic_site_id?: string;
  latest_tpm?: number;
  latest_rpm?: number;
};

export function poolTrafficMetrics(summary?: PoolTrafficSummary) {
  const siteId = String(summary?.traffic_site_id || "").trim();
  return {
    siteLabel: siteId === "5001" || siteId.endsWith("-5001") ? "5001" : siteId || "5001",
    tpm: summary?.latest_tpm,
    rpm: summary?.latest_rpm,
  };
}
