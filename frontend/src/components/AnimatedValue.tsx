import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from "react";

export const AutoRefreshAnimationContext = createContext(0);

export type ChangeDirection = "up" | "down" | "changed";

export function changeDirection(previous: string, current: string): ChangeDirection | null {
  if (previous === current) return null;
  const previousNumber = numericValue(previous);
  const currentNumber = numericValue(current);
  if (previousNumber !== null && currentNumber !== null) {
    if (currentNumber > previousNumber) return "up";
    if (currentNumber < previousNumber) return "down";
  }
  return "changed";
}

export function AnimatedValue({ value, className = "" }: { value: ReactNode; className?: string }) {
  const refreshRevision = useContext(AutoRefreshAnimationContext);
  const serializedValue = comparableValue(value);
  const previousValueRef = useRef(serializedValue);
  const previousRevisionRef = useRef(refreshRevision);
  const [animation, setAnimation] = useState<{ direction: ChangeDirection; nonce: number } | null>(null);

  useEffect(() => {
    const direction = changeDirection(previousValueRef.current, serializedValue);
    const refreshed = previousRevisionRef.current !== refreshRevision;
    previousValueRef.current = serializedValue;
    previousRevisionRef.current = refreshRevision;
    if (refreshed && direction) {
      setAnimation({ direction, nonce: refreshRevision });
    }
  }, [refreshRevision, serializedValue]);

  const animationClass = animation ? `auto-refresh-value-change ${animation.direction}` : "";
  return (
    <span className={`auto-refresh-value ${animationClass} ${className}`.trim()} key={animation?.nonce || 0}>
      {value}
    </span>
  );
}

function comparableValue(value: ReactNode): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "bigint") return String(value);
  return "";
}

function numericValue(value: string): number | null {
  const match = value.replaceAll(",", "").match(/[-+]?\d*\.?\d+/);
  if (!match) return null;
  const parsed = Number(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
}
