import { expect, test } from "@playwright/test";
import { RESULTS_HASH } from "./helpers.js";

test("a home mostra o intervalo de anos com gramática correta", async ({ page }) => {
  await page.goto("/#/");
  const lead = page.locator(".search-hero__lead").first();
  await expect(lead).toContainText(/trabalhos defendidos entre 1990 e 2024/);
  await expect(lead).toContainText(/\d+ trabalhos/);
});

test("busca por teclado, filtros, ordenação e paginação", async ({ page }) => {
  await page.goto("/#/");
  await expect(
    page.getByRole("heading", { level: 1, name: /Pesquise teses e dissertações/ }),
  ).toBeVisible();

  const input = page.locator("#search-q");
  await input.fill("educação inclusiva");
  await input.press("Enter");

  await expect(page).toHaveURL(/q=educa/);
  await expect(page.locator(".ficha").first()).toBeVisible();
  await expect(page.locator(".result-summary")).toContainText(/resultados|cerca de/);

  // Sorting keeps the search state in the URL.
  await page.getByLabel("Ordenação dos resultados").selectOption("year_desc");
  await expect(page).toHaveURL(/sort=year_desc/);
  await expect(page.locator(".ficha").first()).toBeVisible();

  // Pagination advances to the second page.
  const secondPage = page.getByRole("button", { name: "Página 2" });
  if (await secondPage.isVisible()) {
    await secondPage.click();
    await expect(page).toHaveURL(/page=2/);
    await expect(page.locator(".ficha").first()).toBeVisible();
  }

  // A facet checkbox filters with the keyboard and adds a removable chip.
  const aside = page.locator("aside.filters-panel");
  await expect(aside).toBeVisible();
  await aside.locator('input[name="degree"][value="Doutorado"]').check();
  await expect(page).toHaveURL(/degree=Doutorado/);
  await expect(page.locator(".chip").filter({ hasText: "Nível: Doutorado" })).toBeVisible();
  await expect(page.locator(".ficha").first()).toBeVisible();
  const degrees = await page.locator(".ficha .ficha__degree").allTextContents();
  expect(degrees.some((text) => text.includes("Doutorado"))).toBeTruthy();

  await page.getByRole("button", { name: "Limpar filtros" }).first().click();
  await expect(page).not.toHaveURL(/degree=/);
});

test("modo por significado e sugestões preenchem a busca", async ({ page }) => {
  await page.goto("/#/");
  await page.getByRole("radio", { name: "Por significado" }).check();
  await expect(page.locator("#mode-hint")).toContainText(/sentido/i);
  await page.locator(".suggestion").first().click();
  await expect(page.locator(".ficha").first()).toBeVisible();
  await expect(page).toHaveURL(/mode=semantic/);
});

test("a primeira busca mostra esqueleto de fichas e a dica após dois segundos", async ({
  page,
}) => {
  await page.route("**/api/search?*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    await route.continue();
  });

  await page.goto("/#/?q=educa%C3%A7%C3%A3o%20inclusiva");
  await expect(page.locator(".skeleton-card")).toHaveCount(3);
  await expect(page.locator(".search-skeleton__hint")).toBeVisible({ timeout: 5000 });
  await expect(page.locator(".search-skeleton__hint")).toContainText(
    "Preparando a busca por significado — a primeira busca leva alguns segundos.",
  );
  await expect(page.locator(".ficha").first()).toBeVisible({ timeout: 10_000 });
});

test("o esqueleto respeita prefers-reduced-motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/api/search?*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });

  await page.goto("/#/?q=educa%C3%A7%C3%A3o%20inclusiva");
  const block = page.locator(".skeleton-block").first();
  await expect(block).toBeVisible();
  expect(await block.evaluate((node) => getComputedStyle(node).animationName)).toBe("none");
});

test("filtros em diálogo no celular", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto(RESULTS_HASH);
  await expect(page.locator(".ficha").first()).toBeVisible();

  await page.getByRole("button", { name: "Filtros" }).click();
  const dialog = page.locator("dialog.filters-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Filtros" })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
});
