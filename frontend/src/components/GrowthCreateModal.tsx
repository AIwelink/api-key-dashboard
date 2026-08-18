import { type FormEvent, type ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

type GrowthCreateModalProps = {
  children: ReactNode;
  onClose: () => void;
  onSubmit: () => void;
  saving: boolean;
  submitDisabled: boolean;
  submitLabel: string;
  title: string;
};

const focusableSelector = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'a[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusableElements(container: HTMLElement | null) {
  if (!container) return [];

  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (element) => !element.hasAttribute("hidden") && element.getAttribute("aria-hidden") !== "true",
  );
}

export function submitGrowthCreateModal(
  event: Pick<FormEvent<HTMLFormElement>, "preventDefault">,
  saving: boolean,
  submitDisabled: boolean,
  onSubmit: () => void,
) {
  event.preventDefault();
  if (!saving && !submitDisabled) onSubmit();
}

export function GrowthCreateModal({
  children,
  onClose,
  onSubmit,
  saving,
  submitDisabled,
  submitLabel,
  title,
}: GrowthCreateModalProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const savingRef = useRef(saving);

  onCloseRef.current = onClose;
  savingRef.current = saving;

  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    const focusDialog = () => {
      const [firstFocusable] = getFocusableElements(dialogRef.current);
      if (firstFocusable) {
        firstFocusable.focus();
      } else {
        dialogRef.current?.focus();
      }
    };

    document.body.style.overflow = "hidden";
    focusDialog();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!savingRef.current) onCloseRef.current();
        return;
      }

      if (event.key !== "Tab") return;

      const focusable = getFocusableElements(dialogRef.current);
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeElement = document.activeElement;

      if (event.shiftKey && (activeElement === first || !dialogRef.current?.contains(activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (activeElement === last || !dialogRef.current?.contains(activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      if (trigger?.isConnected && !trigger.matches(":disabled")) trigger.focus();
    };
  }, []);

  const close = () => {
    if (!saving) onClose();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    submitGrowthCreateModal(event, saving, submitDisabled, onSubmit);
  };

  const modal = (
    <div className="growth-create-modal-backdrop" onMouseDown={close} role="presentation">
      <section
        aria-labelledby="growth-create-modal-title"
        aria-modal="true"
        className="growth-create-modal"
        onMouseDown={(event) => event.stopPropagation()}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <form className="growth-create-modal-form" onSubmit={submit}>
          <header className="growth-create-modal-header">
            <h3 id="growth-create-modal-title">{title}</h3>
            <button aria-label="关闭" className="ghost icon-button" disabled={saving} onClick={close} type="button">
              ×
            </button>
          </header>
          <div className="growth-create-modal-body">{children}</div>
          <footer className="growth-create-modal-footer">
            <button className="ghost" disabled={saving} onClick={close} type="button">
              取消
            </button>
            <button disabled={saving || submitDisabled} type="submit">
              {saving ? "保存中..." : submitLabel}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );

  return typeof document === "undefined" ? modal : createPortal(modal, document.body);
}
