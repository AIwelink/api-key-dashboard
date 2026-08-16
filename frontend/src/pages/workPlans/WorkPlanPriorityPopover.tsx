import { ListOrdered, X } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

import type { WorkPlanMember } from "./types";

type WorkPlanPriorityPopoverProps = {
  member: WorkPlanMember;
  busy?: boolean;
  onChange?: (memberId: string, priority: number | null) => Promise<void> | void;
};

const POPOVER_GAP = 5;
const VIEWPORT_PADDING = 8;

type PopoverPosition = {
  left: number;
  top: number;
  ready: boolean;
};

function popoverPosition(
  trigger: DOMRect,
  panel: DOMRect,
  viewportWidth: number,
  viewportHeight: number,
): PopoverPosition {
  const maxLeft = Math.max(VIEWPORT_PADDING, viewportWidth - panel.width - VIEWPORT_PADDING);
  const left = Math.min(Math.max(trigger.right - panel.width, VIEWPORT_PADDING), maxLeft);
  const below = trigger.bottom + POPOVER_GAP;
  const above = trigger.top - panel.height - POPOVER_GAP;
  const maxTop = Math.max(VIEWPORT_PADDING, viewportHeight - panel.height - VIEWPORT_PADDING);
  const top = below + panel.height <= viewportHeight - VIEWPORT_PADDING || above < VIEWPORT_PADDING
    ? Math.min(Math.max(below, VIEWPORT_PADDING), maxTop)
    : above;
  return { left, top, ready: true };
}

export function WorkPlanPriorityPopover({
  member,
  busy = false,
  onChange,
}: WorkPlanPriorityPopoverProps) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(member.work_plan_priority?.toString() ?? "");
  const [error, setError] = useState("");
  const [position, setPosition] = useState<PopoverPosition>({ left: VIEWPORT_PADDING, top: VIEWPORT_PADDING, ready: false });
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    setValue(member.work_plan_priority?.toString() ?? "");
  }, [member.work_plan_priority]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !panelRef.current?.contains(target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return undefined;
    const updatePosition = () => {
      if (!triggerRef.current || !panelRef.current) return;
      setPosition(popoverPosition(
        triggerRef.current.getBoundingClientRect(),
        panelRef.current.getBoundingClientRect(),
        window.innerWidth,
        window.innerHeight,
      ));
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open]);

  const save = async (priority: number | null) => {
    if (!onChange || busy) return;
    setError("");
    try {
      await onChange(member.member_id, priority);
      setOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "优先级保存失败");
    }
  };

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const priority = Number(value);
    if (!Number.isSafeInteger(priority) || priority < 1) {
      setError("请输入大于 0 的整数");
      return;
    }
    void save(priority);
  };

  return (
    <div className="work-plan-priority" ref={rootRef}>
      <button
        aria-controls={panelId}
        aria-expanded={open}
        aria-label={`设置${member.member_name}的排班优先级`}
        className="work-plan-priority-trigger"
        disabled={busy}
        onClick={() => { setError(""); setOpen((current) => !current); }}
        ref={triggerRef}
        title="设置排班优先级"
        type="button"
      >
        <ListOrdered aria-hidden="true" size={15} />
      </button>
      {open ? createPortal(
        <form
          className="work-plan-priority-popover"
          id={panelId}
          onSubmit={submit}
          ref={panelRef}
          style={{
            left: position.left,
            top: position.top,
            visibility: position.ready ? "visible" : "hidden",
          } as CSSProperties}
        >
          <header>
            <div><strong>排班优先级</strong><span>数字越小越靠前</span></div>
            <button aria-label="关闭优先级设置" className="work-plan-icon-button" onClick={() => setOpen(false)} type="button"><X size={15} /></button>
          </header>
          <label>
            <span>优先级</span>
            <input
              autoFocus
              inputMode="numeric"
              min="1"
              onChange={(event) => setValue(event.target.value)}
              placeholder="例如 1"
              step="1"
              type="number"
              value={value}
            />
          </label>
          {error ? <p role="alert">{error}</p> : null}
          <footer>
            {member.work_plan_priority != null ? <button className="ghost" disabled={busy} onClick={() => void save(null)} type="button">清除</button> : <span />}
            <button disabled={busy || !onChange} type="submit">{busy ? "保存中" : "保存"}</button>
          </footer>
        </form>,
        document.body,
      ) : null}
    </div>
  );
}
