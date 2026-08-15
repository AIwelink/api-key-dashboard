import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ConfirmDialog, dismissConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
  it("does not dismiss while confirmation is in flight", () => {
    let cancelCount = 0;

    dismissConfirmDialog(true, () => { cancelCount += 1; });

    expect(cancelCount).toBe(0);
  });

  it("exposes its title as the dialog accessible name", () => {
    const html = renderToStaticMarkup(
      <ConfirmDialog
        onCancel={() => undefined}
        onConfirm={() => undefined}
        open
        title="确认取消这条计划？"
      />,
    );

    const titleId = html.match(/aria-labelledby="([^"]+)"/)?.[1];
    expect(titleId).toBeTruthy();
    expect(html).toContain(`<h3 id="${titleId}">确认取消这条计划？</h3>`);
  });
});
