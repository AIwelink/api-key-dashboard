type PoolTrafficSummary = {
  latest_tpm?: number;
  latest_rpm?: number;
};

export function poolTrafficMetrics(summary?: PoolTrafficSummary) {
  return {
    tpm: summary?.latest_tpm,
    rpm: summary?.latest_rpm,
  };
}
