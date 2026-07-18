export type PresenceSegmentTone = "future" | "offline" | "low" | "medium" | "high";

export function presenceSegmentTone(value: number | null): PresenceSegmentTone {
  if (value === null) return "future";
  if (value <= 0) return "offline";
  if (value < 40) return "low";
  if (value < 80) return "medium";
  return "high";
}

export function formatOnlineMinutes(value: number) {
  const totalMinutes = Math.max(0, Math.round(value || 0));
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}天${hours}小时${minutes}分钟`;
  if (hours > 0) return `${hours}小时${minutes}分钟`;
  return `${minutes}分钟`;
}

export function halfHourLabel(slotIndex: number) {
  if (slotIndex >= 48) return "24:00";
  return `${String(Math.floor(slotIndex / 2)).padStart(2, "0")}:${slotIndex % 2 ? "30" : "00"}`;
}
