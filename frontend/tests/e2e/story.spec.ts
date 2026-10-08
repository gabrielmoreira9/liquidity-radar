import { test, expect } from "@playwright/test";

test("product story follows scroll progress and keeps real previews", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  const scene = page.locator("#market");
  await expect(scene).toBeAttached();
  await expect(
    page.locator("#history-story").getByText("HISTORICAL / DEMO"),
  ).toBeAttached();
  await expect(
    page.getByRole("img", { name: "Liquidity Radar — ON-CHAIN" }).first(),
  ).toBeVisible();
  const composition = scene.locator("[data-scroll-composition]");
  if (testInfo.project.name === "chromium") {
    await page.evaluate(() => {
      const scene = document.querySelector("#market")!;
      window.scrollTo(
        0,
        scene.getBoundingClientRect().top +
          window.scrollY -
          window.innerHeight * 0.8,
      );
    });
    await expect
      .poll(() => composition.evaluate((e) => getComputedStyle(e).transform))
      .not.toBe("none");
    const before = await composition.evaluate(
      (e) => getComputedStyle(e).transform,
    );
    await page.evaluate(() => window.scrollBy(0, 350));
    await expect
      .poll(() => composition.evaluate((e) => getComputedStyle(e).transform))
      .not.toBe(before);
  } else {
    await expect(composition).toHaveCSS("transform", "none");
    await expect(scene.locator(".scene-pin")).toHaveCSS("position", "relative");
  }
  for (const id of ["market", "forward", "position-story", "history-story"]) {
    await page.evaluate((id) => {
      const s = document.getElementById(id)!;
      window.scrollTo(
        0,
        s.offsetTop +
          (window.innerWidth > 900
            ? Math.max(0, (s.offsetHeight - window.innerHeight) / 2)
            : 0),
      );
    }, id);
    await page.screenshot({
      path: `test-results/story-${id}-${testInfo.project.name}.png`,
    });
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

test("reduced motion exposes the complete story without pinned transforms", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.locator("#position-story")).toBeAttached();
  for (const id of [
    "beneath",
    "market",
    "forward",
    "position-story",
    "history-story",
  ]) {
    await expect(page.locator(`#${id} .scene-pin`)).toHaveCSS(
      "position",
      "relative",
    );
    await expect(page.locator(`#${id} [data-scroll-composition]`)).toHaveCSS(
      "transform",
      "none",
    );
    await expect(page.locator(`#${id} [data-scroll-composition]`)).toHaveCSS(
      "opacity",
      "1",
    );
  }
});
