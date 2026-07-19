export function isCurrentSiteRequest(requestSiteId: string, currentSiteId: string): boolean {
  return Boolean(requestSiteId) && requestSiteId === currentSiteId;
}

export function mergeCapacitySummaryForRequest<
  TSummary,
  TGroup extends { id: number; capacity_summary?: TSummary },
>(
  groups: TGroup[],
  requestKey: string,
  currentRequestKey: string,
  groupId: number,
  capacitySummary: TSummary,
): TGroup[] {
  if (!requestKey || requestKey !== currentRequestKey) return groups;
  return groups.map((group) =>
    group.id === groupId ? { ...group, capacity_summary: capacitySummary } : group,
  );
}
