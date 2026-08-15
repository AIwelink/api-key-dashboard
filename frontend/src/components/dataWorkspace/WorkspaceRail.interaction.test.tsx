// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { WorkspaceRail } from "./WorkspaceRail";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("WorkspaceRail interaction", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    window.history.replaceState(null, "", "/");
    root = null;
    container = null;
  });

  it("moves the current-location state when another section is selected", async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => root?.render(
      <WorkspaceRail
        label="页面索引"
        items={[
          { id: "summary", label: "总览" },
          { id: "cohort", label: "留存" },
        ]}
      />,
    ));

    const links = [...container.querySelectorAll("a")];
    expect(links[0].getAttribute("aria-current")).toBe("location");
    expect(links[1].getAttribute("aria-current")).toBeNull();

    await act(async () => links[1].click());

    expect(links[0].getAttribute("aria-current")).toBeNull();
    expect(links[1].getAttribute("aria-current")).toBe("location");
  });

  it("follows an initial deep link and later hash navigation", async () => {
    window.history.replaceState(null, "", "/#cohort");
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => root?.render(
      <WorkspaceRail
        label="页面索引"
        items={[
          { id: "summary", label: "总览" },
          { id: "cohort", label: "留存" },
        ]}
      />,
    ));

    const links = [...container.querySelectorAll("a")];
    expect(links[1].getAttribute("aria-current")).toBe("location");

    await act(async () => {
      window.history.replaceState(null, "", "/#summary");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    expect(links[0].getAttribute("aria-current")).toBe("location");
    expect(links[1].getAttribute("aria-current")).toBeNull();
  });
});
