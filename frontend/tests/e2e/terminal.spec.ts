import { test, expect } from "@playwright/test";
test("landing and all research pages use real demo data", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Volume is not depth." }),
  ).toBeVisible();
  await page.screenshot({ path: `test-results/landing-${test.info().project.name}.png`, fullPage: true });
  await page.getByRole("link", { name: "Explore the radar" }).click();
  await expect(
    page.getByRole("heading", { name: "Liquidity overview." }),
  ).toBeVisible();
  await expect(
    page.getByText("HISTORICAL / DEMO", { exact: false }),
  ).toBeVisible();
  await page.screenshot({ path: `test-results/dashboard-${test.info().project.name}.png`, fullPage: true });
  for (const [url, title] of [
    ["/market-risk", "Read the conditions."],
    ["/history", "Historical liquidity."],
    ["/positions", "Size changes everything."],
    ["/backtest", "Challenge the signal."],
    ["/methodology", "Know what you are measuring."],
  ]) {
    await page.goto(url);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
});
test("stress and historical selection preserve flags and unavailable states", async ({
  page,
}) => {
  await page.goto("/positions");
  await page.getByRole("combobox", { name: "Scenario", exact: true }).selectOption("STRESS_75");
  await page
    .getByRole("combobox", { name: "Position size", exact: true })
    .selectOption("1000000");
  await page.getByRole("button", { name: "Analyze position" }).click();
  await expect(
    page.getByText("Selected scenario retains 25% of observed volume."),
  ).toBeVisible();
  await expect(
    page.getByText("Extrapolation: Yes", { exact: true }),
  ).toBeVisible();
  await page.goto("/dashboard?as_of=2020-01-01T00%3A00%3A00Z");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("404");
});
test("backtest filters and real baseline comparison", async ({ page }) => {
  await page.goto("/backtest");
  await page.getByRole("combobox", { name: "Horizon", exact: true }).selectOption("60");
  await page
    .getByRole("combobox", { name: "Sample", exact: true })
    .selectOption("NON_OVERLAPPING_MATCHED");
  await page.getByRole("button", { name: "Explore results" }).click();
  await expect(page).toHaveURL(/sample=NON_OVERLAPPING_MATCHED/);
  await expect(
    page.getByRole("cell", { name: "Low current volume baseline" }),
  ).toBeVisible();
});
