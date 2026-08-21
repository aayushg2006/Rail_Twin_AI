import { expect, test } from "@playwright/test";

test("Focus Mode selects an episode without opening What-if", async ({ page }) => {
  await page.goto("/conflict");
  await expect(page.getByText("Twin connected", { exact: true })).toBeVisible();
  const focus = page.getByRole("button", { name: "Focus mode", exact: true });
  await expect(focus).toBeEnabled();
  await focus.click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText(/REAL IST CLOCK.*CONFLICT FOCUS/)).toBeVisible();
});

test("real map and PF6/PF7 schematic share the Vasai topology", async ({ page }) => {
  await page.goto("/conflict");
  await expect(page.getByLabel("Interactive OpenStreetMap railway view")).toBeVisible();
  await expect(page.getByText("OpenStreetMap railway topology", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Schematic", exact: true }).click();
  await expect(page.getByText("operational schematic", { exact: false })).toBeVisible();
  await expect(page.getByText("PF 6", { exact: true })).toBeVisible();
  await expect(page.getByText("PF 7", { exact: true })).toBeVisible();
  await expect(page.getByText("BRD", { exact: true })).toBeVisible();
  await expect(page.getByText("BRU", { exact: true })).toBeVisible();
});

test("LIVE clock disables pause and multipliers but keeps look-ahead", async ({ page }) => {
  await page.goto("/conflict");
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeDisabled();
  await expect(page.getByLabel("Look ahead")).toBeEnabled();
});

test("What-if opens from a live conflict and shows the queue comparison", async ({ page }) => {
  await page.goto("/decision");
  await expect(page.getByText("Twin connected", { exact: true })).toBeVisible();
  const episode = page.getByRole("button", { name: /^Open What-if for/ }).first();
  await expect(episode).toBeVisible();
  await episode.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Recommendation", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Options", { exact: true })).toBeVisible();
  const option = dialog.locator("tbody tr").first();
  await expect(option).toBeVisible();
  await option.click();
  await expect(dialog.getByText("Predicted if no action", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Predicted saving", { exact: true })).toBeVisible();
});
