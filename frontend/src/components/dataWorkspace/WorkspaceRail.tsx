import { useEffect, useState } from "react";

import "./DataWorkspace.css";

export type WorkspaceRailItem = {
  id: string;
  label: string;
  count?: string;
};

type Props = {
  label: string;
  items: WorkspaceRailItem[];
  status?: {
    title: string;
    detail: string;
    tone?: "healthy" | "warning" | "muted";
  };
  className?: string;
};

export function WorkspaceRail({ label, items, status, className = "" }: Props) {
  const firstItemId = items[0]?.id || "";
  const itemIds = items.map((item) => item.id);
  const itemIdsKey = itemIds.join("\u0000");
  const [activeItemId, setActiveItemId] = useState(firstItemId);

  useEffect(() => {
    const availableItemIds = new Set(itemIds);
    const updateFromHash = () => {
      const hashItemId = window.location.hash.slice(1);
      setActiveItemId(availableItemIds.has(hashItemId) ? hashItemId : firstItemId);
    };

    updateFromHash();
    window.addEventListener("hashchange", updateFromHash);
    return () => window.removeEventListener("hashchange", updateFromHash);
  }, [firstItemId, itemIdsKey]);

  return (
    <nav className={`workspace-rail${className ? ` ${className}` : ""}`} aria-label={label}>
      <span className="workspace-rail-title">页面索引</span>
      <div className="workspace-rail-links">
        {items.map((item) => (
          <a
            aria-current={activeItemId === item.id ? "location" : undefined}
            className={activeItemId === item.id ? "active" : ""}
            href={`#${item.id}`}
            key={item.id}
            onClick={() => setActiveItemId(item.id)}
          >
            <span>{item.label}</span>
            {item.count ? <small>{item.count}</small> : null}
          </a>
        ))}
      </div>
      {status ? (
        <div className={`workspace-rail-status ${status.tone || "muted"}`}>
          <strong>{status.title}</strong>
          <span>{status.detail}</span>
        </div>
      ) : null}
    </nav>
  );
}
