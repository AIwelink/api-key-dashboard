export type ClientMetricStatus = {
  site_id: string;
  client_type: "newapi" | "sub2api" | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_bucket_at: string | null;
  last_quality: "complete" | "missing" | "delayed" | "counter_reset" | null;
  last_rpm: number | null;
  last_tpm: number | null;
  consecutive_failures: number;
  last_error: string | null;
  source_updated_at?: string | null;
  updated_at: string | null;
};

const qualityLabels: Record<NonNullable<ClientMetricStatus["last_quality"]>, string> = {
  complete: "完整",
  missing: "缺失",
  delayed: "上游延迟",
  counter_reset: "计数器重置",
};

const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export function clientMetricDisplay(status: ClientMetricStatus | null) {
  const complete = status?.last_quality === "complete";
  const hasFailures = (status?.consecutive_failures || 0) > 0;
  return {
    rpm: complete && status?.last_rpm !== null && status?.last_rpm !== undefined
      ? numberFormat.format(status.last_rpm)
      : "无数据",
    tpm: complete && status?.last_tpm !== null && status?.last_tpm !== undefined
      ? numberFormat.format(status.last_tpm)
      : "无数据",
    quality: status?.last_quality ? qualityLabels[status.last_quality] : "尚未采样",
    tone: !status?.last_quality ? "muted" : hasFailures || !complete ? "error" : "success",
    failures: status ? `${status.consecutive_failures || 0}` : "-",
  } as const;
}
