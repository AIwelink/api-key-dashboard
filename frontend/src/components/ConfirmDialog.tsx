import { useEffect, useId, useRef, useState } from "react";

import { useModalFocus } from "../hooks/useModalFocus";

export type ConfirmDialogTone = "default" | "danger";

export function dismissConfirmDialog(confirming: boolean, onCancel: () => void): void {
  if (!confirming) onCancel();
}

type ConfirmDialogProps = {
  cancelText?: string;
  confirmText?: string;
  details?: Array<[string, string | number | null | undefined]>;
  message?: string;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
  open: boolean;
  title: string;
  tone?: ConfirmDialogTone;
};

export function ConfirmDialog({
  cancelText = "取消",
  confirmText = "确认",
  details = [],
  message,
  onCancel,
  onConfirm,
  open,
  title,
  tone = "default",
}: ConfirmDialogProps) {
  const confirmingRef = useRef(false);
  const [confirming, setConfirming] = useState(false);
  const titleId = useId();
  const dismissIfIdle = () => dismissConfirmDialog(confirmingRef.current, onCancel);
  const dialogRef = useModalFocus<HTMLElement>(open, dismissIfIdle);

  useEffect(() => {
    confirmingRef.current = false;
    setConfirming(false);
  }, [open, title]);

  if (!open) return null;

  const confirmOnce = async () => {
    if (confirmingRef.current) return;
    confirmingRef.current = true;
    setConfirming(true);
    try {
      await onConfirm();
    } finally {
      confirmingRef.current = false;
      setConfirming(false);
    }
  };

  return (
    <div className="confirm-backdrop" role="presentation" onMouseDown={dismissIfIdle}>
      <section
        aria-labelledby={titleId}
        aria-modal="true"
        className={`confirm-dialog ${tone}`}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-header">
          <h3 id={titleId}>{title}</h3>
          <button aria-label="关闭" className="ghost icon-button" disabled={confirming} onClick={dismissIfIdle} type="button">
            ×
          </button>
        </div>
        {message && <p className="confirm-message">{message}</p>}
        {details.length > 0 && (
          <dl className="confirm-details">
            {details.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value ?? "-"}</dd>
              </div>
            ))}
          </dl>
        )}
        <div className="confirm-actions">
          <button className="ghost" disabled={confirming} onClick={dismissIfIdle} type="button">
            {cancelText}
          </button>
          <button className={tone === "danger" ? "danger-button" : ""} disabled={confirming} onClick={confirmOnce} type="button">
            {confirming ? "处理中..." : confirmText}
          </button>
        </div>
      </section>
    </div>
  );
}
