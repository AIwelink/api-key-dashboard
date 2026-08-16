export type SegmentMotionSnapshot = {
  key: string;
  state: string;
  startAt: string;
  endAt: string;
};

export function memberEntryDelay(index: number): number {
  return index < 3 ? 220 + index * 40 : 260;
}

export function findChangedSegmentKeys(
  previous: readonly SegmentMotionSnapshot[],
  next: readonly SegmentMotionSnapshot[],
): Set<string> {
  const previousByKey = new Map(previous.map((segment) => [segment.key, segment]));
  return new Set(next
    .filter((segment) => {
      const old = previousByKey.get(segment.key);
      return !old
        || old.state !== segment.state
        || old.startAt !== segment.startAt
        || old.endAt !== segment.endAt;
    })
    .map((segment) => segment.key));
}
