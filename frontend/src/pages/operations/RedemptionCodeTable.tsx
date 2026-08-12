export type RedemptionCodeOrigin = "management_panel" | "api_site";

export type RedemptionCodeRow = {
  id: number;
  site_id: string;
  code_mask: string;
  type?: string;
  value?: number;
  status: string;
  origin: RedemptionCodeOrigin;
  created_by?: string | null;
  created_by_current_user: boolean;
  created_at?: string | null;
  expires_at?: string | null;
  used_by?: number | string | null;
  used_at?: string | null;
  user?: { email?: string | null } | null;
};

export type RedemptionCodeListResponse = {
  items: RedemptionCodeRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  truncated: boolean;
};

type RedemptionCodeTableProps = {
  canDelete?: boolean;
  canWrite: boolean;
  loading: boolean;
  onDelete: (row: RedemptionCodeRow) => void;
  onPageChange: (page: number) => void;
  onReveal: (row: RedemptionCodeRow) => void;
  onSelectionChange: (selectedIds: Set<number>) => void;
  page: number;
  pages: number;
  rows: RedemptionCodeRow[];
  selectedIds: Set<number>;
  total: number;
};

const siteLabels: Record<string, string> = {
  aiwelink: "AIWeLink",
  aigclink: "AIGCLink",
};

const statusLabels: Record<string, string> = {
  unused: "未使用",
  used: "已使用",
  expired: "已过期",
  disabled: "已禁用",
};

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function toggleSelection(selectedIds: Set<number>, codeId: number, checked: boolean) {
  const next = new Set(selectedIds);
  if (checked) next.add(codeId);
  else next.delete(codeId);
  return next;
}

export function RedemptionCodeTable({
  canWrite,
  canDelete = false,
  loading,
  onDelete,
  onPageChange,
  onReveal,
  onSelectionChange,
  page,
  pages,
  rows,
  selectedIds,
  total,
}: RedemptionCodeTableProps) {
  const selectableIds = canDelete ? rows.filter((row) => row.status === "unused").map((row) => row.id) : [];
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id));

  return (
    <>
      <div className="operations-table-scroll operations-redemption-table">
        <table>
          <thead>
            <tr>
              {canDelete && (
                <th className="operations-selection-cell">
                  <input
                    aria-label="选择当前页未使用兑换码"
                    checked={allSelected}
                    disabled={selectableIds.length === 0}
                    onChange={(event) => {
                      const next = new Set(selectedIds);
                      selectableIds.forEach((id) => event.target.checked ? next.add(id) : next.delete(id));
                      onSelectionChange(next);
                    }}
                    type="checkbox"
                  />
                </th>
              )}
              <th>兑换码</th><th>站点</th><th>额度</th><th>状态</th><th>来源</th><th>创建账号</th><th>创建时间</th><th>使用账号</th><th>使用时间</th>{canWrite && <th>操作</th>}
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((row) => {
              const unused = row.status === "unused";
              return (
                <tr key={`${row.site_id}-${row.id}`}>
                  {canDelete && (
                    <td className="operations-selection-cell">
                      <input
                        aria-label={`选择兑换码 ${row.code_mask}`}
                        checked={selectedIds.has(row.id)}
                        disabled={!unused}
                        onChange={(event) => onSelectionChange(toggleSelection(selectedIds, row.id, event.target.checked))}
                        type="checkbox"
                      />
                    </td>
                  )}
                  <td><strong className="operations-code-mask">{row.code_mask}</strong></td>
                  <td>{siteLabels[row.site_id] || row.site_id}</td>
                  <td>{Number(row.value || 0).toLocaleString("zh-CN")}</td>
                  <td><span className={`operations-status-tag ${row.status}`}>{statusLabels[row.status] || row.status}</span></td>
                  <td>{row.origin === "management_panel" ? "管理面板创建" : "API站点创建"}</td>
                  <td>{row.created_by || "-"}{row.created_by_current_user && <small className="operations-current-user-label">当前账号</small>}</td>
                  <td>{formatDateTime(row.created_at)}</td>
                  <td>{row.user?.email || row.used_by || "-"}</td>
                  <td>{formatDateTime(row.used_at)}</td>
                  {canWrite && (
                    <td>
                      <div className="operations-row-actions">
                        <button className="ghost operations-row-button" type="button" onClick={() => onReveal(row)}>查看明文</button>
                        {canDelete && unused && <button aria-label={`删除兑换码 ${row.code_mask}`} className="ghost operations-row-button danger-text" type="button" onClick={() => onDelete(row)}>删除</button>}
                      </div>
                    </td>
                  )}
                </tr>
              );
            }) : (
              <tr><td colSpan={canWrite ? (canDelete ? 11 : 10) : 9}>{loading ? "正在加载兑换码..." : "当前筛选下暂无兑换码"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="operations-pagination">
        <span>共 {total} 条</span>
        <div>
          <button aria-label="上一页" className="ghost icon-button" disabled={page <= 1 || loading} onClick={() => onPageChange(page - 1)} type="button">‹</button>
          <span>第 {page} / {Math.max(1, pages)} 页</span>
          <button aria-label="下一页" className="ghost icon-button" disabled={page >= pages || loading} onClick={() => onPageChange(page + 1)} type="button">›</button>
        </div>
      </div>
    </>
  );
}
