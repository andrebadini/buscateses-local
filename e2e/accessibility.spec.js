import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { ROUTES, seriousViolations, waitForPage } from "./helpers.js";

for (const theme of ["light", "dark"]) {
  test(`axe sem violações graves no tema ${theme}`, async ({ page }) => {
    test.setTimeout(180_000);
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    const problems = [];

    for (const route of ROUTES) {
      await page.goto(route.path);
      await waitForPage(page);
      const violations = await seriousViolations(page, AxeBuilder);
      for (const violation of violations) {
        problems.push(
          `${route.name} (${theme}): ${violation.id} — ${violation.help}\n` +
            violation.nodes
              .slice(0, 3)
              .map((node) => `  ${node.target.join(" ")}: ${node.failureSummary}`)
              .join("\n"),
        );
      }
    }

    expect(problems.join("\n\n")).toBe("");
  });
}
