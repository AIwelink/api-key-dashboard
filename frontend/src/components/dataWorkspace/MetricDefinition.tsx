import { useId } from "react";

import "./DataWorkspace.css";

export type MetricDefinitionDetails = {
  definition: string;
  formula: string;
  included?: string;
  excluded?: string;
  source: string;
  freshness: string;
};

type Props = {
  label: string;
  details: MetricDefinitionDetails;
  align?: "start" | "end";
  showLabel?: boolean;
};

export function MetricDefinition({ label, details, align = "start", showLabel = true }: Props) {
  const tooltipId = useId();

  return (
    <span className={`metric-definition align-${align}`}>
      {showLabel ? <span className="metric-definition-label">{label}</span> : null}
      <button
        type="button"
        className="metric-definition-trigger"
        aria-label={`查看${label}口径`}
        aria-describedby={tooltipId}
      >
        i
      </button>
      <span className="metric-definition-tooltip" id={tooltipId} role="tooltip">
        <strong>{label}</strong>
        <span className="metric-definition-summary">{details.definition}</span>
        <code>{details.formula}</code>
        <span className="metric-definition-grid">
          {details.included ? <><b>纳入</b><em>{details.included}</em></> : null}
          {details.excluded ? <><b>排除</b><em>{details.excluded}</em></> : null}
          <b>来源</b><em>{details.source}</em>
          <b>更新</b><em>{details.freshness}</em>
        </span>
      </span>
    </span>
  );
}
