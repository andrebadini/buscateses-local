import { expect, test } from "@playwright/test";

test("alterna e persiste o tema escuro", async ({ page }) => {
  await page.goto("/#/");
  const html = page.locator("html");
  const toggle = page.locator("#theme-toggle");

  await expect(toggle).toHaveAttribute("aria-label", "Mudar para o tema escuro");
  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toHaveAttribute("aria-label", "Mudar para o tema claro");

  const darkBackground = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor,
  );
  expect(darkBackground).not.toBe("rgb(246, 247, 245)");

  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toHaveAttribute("aria-label", "Mudar para o tema claro");

  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "light");
});
