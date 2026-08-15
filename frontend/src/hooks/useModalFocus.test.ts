// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { nextModalFocusIndex, useModalFocus } from "./useModalFocus";

type HarnessProps = {
  open: boolean;
  onClose: () => void;
};

function Harness({ open, onClose }: HarnessProps) {
  const dialogRef = useModalFocus<HTMLDivElement>(open, onClose);
  return createElement(
    "div",
    null,
    createElement("button", { id: "modal-opener", type: "button" }, "打开"),
    open
      ? createElement(
          "div",
          { ref: dialogRef, role: "dialog", tabIndex: -1 },
          createElement("button", { id: "modal-first", type: "button" }, "第一个"),
          createElement("button", { id: "modal-last", type: "button" }, "最后一个"),
        )
      : null,
  );
}

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  document.body.replaceChildren();
});

describe("modal focus cycling", () => {
  it("wraps forward and backward within the active dialog", () => {
    expect(nextModalFocusIndex(2, 3, false)).toBe(0);
    expect(nextModalFocusIndex(0, 3, true)).toBe(2);
  });

  it("starts at the correct edge when focus is outside the dialog", () => {
    expect(nextModalFocusIndex(-1, 3, false)).toBe(0);
    expect(nextModalFocusIndex(-1, 3, true)).toBe(2);
  });

  it("enters, traps, closes, and restores focus for the active dialog", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    const onClose = vi.fn();

    await act(async () => root?.render(createElement(Harness, { open: false, onClose })));
    const opener = document.querySelector<HTMLButtonElement>("#modal-opener");
    opener?.focus();

    await act(async () => root?.render(createElement(Harness, { open: true, onClose })));
    await act(async () => undefined);
    const first = document.querySelector<HTMLButtonElement>("#modal-first");
    const last = document.querySelector<HTMLButtonElement>("#modal-last");
    expect(document.activeElement).toBe(first);

    last?.focus();
    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Tab" }));
    expect(document.activeElement).toBe(first);

    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Escape" }));
    expect(onClose).toHaveBeenCalledOnce();

    await act(async () => root?.render(createElement(Harness, { open: false, onClose })));
    await act(async () => undefined);
    expect(document.activeElement).toBe(opener);
  });
});
