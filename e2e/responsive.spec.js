import { expect, test } from "@playwright/test";
import { ROUTES, waitForPage } from "./helpers.js";

test("320px e 360px não têm rolagem horizontal", async ({ page }) => {
  for (const width of [320, 360]) {
    await page.setViewportSize({ width, height: 800 });
    for (const route of ROUTES) {
      await page.goto(route.path);
      await waitForPage(page);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `${route.name} em ${width}px`).toBeLessThanOrEqual(1);
    }
  }
});

test("menu móvel abre, navega e fecha com Escape", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/#/");
  await waitForPage(page);

  const toggle = page.locator("#nav-toggle");
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const nav = page.locator("#nav-principal");
  await expect(nav).toBeVisible();
  await expect(nav.getByRole("link", { name: "Minha biblioteca" })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();

  await toggle.click();
  await nav.getByRole("link", { name: "Configurações", exact: true }).click();
  await expect(page).toHaveURL(/#\/configuracoes/);
  await expect(page.getByRole("heading", { level: 1, name: "Configurações" })).toBeVisible();
});
