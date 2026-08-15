import { useEffect, useRef } from "react";

type ModalEntry = {
  element: HTMLElement;
  opener: HTMLElement | null;
};

const modalStack: ModalEntry[] = [];
const focusableSelector = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function nextModalFocusIndex(currentIndex: number, count: number, backward: boolean): number {
  if (count <= 0) return -1;
  if (currentIndex < 0) return backward ? count - 1 : 0;
  return (currentIndex + (backward ? -1 : 1) + count) % count;
}

function focusableElements(element: HTMLElement): HTMLElement[] {
  return Array.from(element.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (candidate) => !candidate.closest("[inert]"),
  );
}

function syncModalStack() {
  const top = modalStack.at(-1)?.element;
  modalStack.forEach(({ element }) => {
    element.inert = element !== top;
  });
}

export function useModalFocus<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const dialogRef = useRef<T | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const element = dialogRef.current;
    if (!open || !element) return;

    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const entry = { element, opener };
    modalStack.push(entry);
    syncModalStack();

    queueMicrotask(() => {
      if (modalStack.at(-1) !== entry) return;
      (focusableElements(element)[0] || element).focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (modalStack.at(-1) !== entry) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = focusableElements(element);
      const currentIndex = focusable.indexOf(document.activeElement as HTMLElement);
      const nextIndex = nextModalFocusIndex(currentIndex, focusable.length, event.shiftKey);
      event.preventDefault();
      (nextIndex >= 0 ? focusable[nextIndex] : element).focus();
    };

    const keepFocusInside = (event: FocusEvent) => {
      if (modalStack.at(-1) !== entry || element.contains(event.target as Node)) return;
      (focusableElements(element)[0] || element).focus();
    };

    document.addEventListener("keydown", handleKeyDown, true);
    document.addEventListener("focusin", keepFocusInside, true);
    return () => {
      document.removeEventListener("keydown", handleKeyDown, true);
      document.removeEventListener("focusin", keepFocusInside, true);
      const index = modalStack.indexOf(entry);
      if (index >= 0) modalStack.splice(index, 1);
      element.inert = false;
      syncModalStack();
      queueMicrotask(() => {
        if (!opener?.isConnected) return;
        const top = modalStack.at(-1)?.element;
        if (!top || top.contains(opener)) opener.focus();
      });
    };
  }, [open]);

  return dialogRef;
}
