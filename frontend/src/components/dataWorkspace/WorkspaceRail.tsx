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
  return (
    <nav className={`workspace-rail${className ? ` ${className}` : ""}`} aria-label={label}>
      <span className="workspace-rail-title">页面索引</span>
      <div className="workspace-rail-links">
        {items.map((item, index) => (
          <a className={index === 0 ? "active" : ""} href={`#${item.id}`} key={item.id}>
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
