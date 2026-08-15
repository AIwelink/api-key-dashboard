import { ListOrdered, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import type { WorkPlanMember } from "./types";

type WorkPlanPriorityPopoverProps = {
  member: WorkPlanMember;
  busy?: boolean;
  onChange?: (memberId: string, priority: number | null) => Promise<void> | void;
};

export function WorkPlanPriorityPopover({
  member,
  busy = false,
  onChange,
}: WorkPlanPriorityPopoverProps) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(member.work_plan_priority?.toString() ?? "");
  const [error, setError] = useState("");
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setValue(member.work_plan_priority?.toString() ?? "");
  }, [member.work_plan_priority]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
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
        title="设置排班优先级"
        type="button"
      >
        <ListOrdered aria-hidden="true" size={15} />
      </button>
      {open ? (
        <form className="work-plan-priority-popover" id={panelId} onSubmit={submit}>
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
        </form>
      ) : null}
    </div>
  );
}
