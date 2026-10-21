import { expect, test } from "@playwright/test";
import { RESULTS_HASH } from "./helpers.js";

test("ficha completa sem URL oferece busca no Google Acadêmico", async ({ page }) => {
  await page.goto("/#/tese/capes%3A0002");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const fallback = page.getByRole("link", { name: /Procurar o texto completo/ });
  await expect(fallback).toBeVisible();
  await expect(fallback).toHaveAttribute("target", "_blank");
  await expect(fallback).toHaveAttribute(
    "href",
    /^https:\/\/scholar\.google\.com\/scholar\?q=/,
  );
  await expect(fallback).toContainText("link externo, abre em nova aba");

  // The region is displayed with name_case, not as stored (uppercase).
  const region = page
    .locator("dt", { hasText: "Região" })
    .locator("xpath=following-sibling::dd[1]");
  await expect(region).toHaveText("Sudeste");
});

test("cartões de trabalhos semelhantes mostram programa e área", async ({ page }) => {
  await page.goto("/#/tese/capes%3A0001");
  const similar = page.locator(".fichas .ficha").first();
  await expect(similar).toBeVisible();
  const meta = similar.locator(".ficha__meta").first();
  await expect(meta).toContainText(/Programa de Pós-Graduação/i);
  const text = await meta.textContent();
  // Institution, program and knowledge area are all shown.
  expect((text.match(/·/g) || []).length).toBeGreaterThanOrEqual(2);
});

test("no celular há espaço entre título e autoria", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/#/tese/capes%3A0001");
  const author = page.locator(".ficha__author").first();
  await expect(author).toBeVisible();
  const marginTop = await author.evaluate(
    (node) => getComputedStyle(node).marginTop,
  );
  expect(marginTop).toBe("6px");
});

test("ficha completa, resumo bilíngue, citação e semelhantes", async ({ page }) => {
  await page.goto(RESULTS_HASH);
  const firstTitle = page.locator(".ficha__title a").first();
  const title = (await firstTitle.textContent()).trim();
  await firstTitle.click();

  await expect(page).toHaveURL(/#\/tese\//);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(title);
  await expect(page.getByRole("heading", { name: "Ficha", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Como citar" })).toBeVisible();
  await expect(page.locator("#citation-text")).toContainText(/Sobrenome/i);
  await expect(page.getByRole("heading", { name: "Trabalhos semelhantes" })).toBeVisible();
  await expect(page.locator(".fichas .ficha").first()).toBeVisible();

  const abstract = page.locator(".abstract-text");
  const portuguese = await abstract.textContent();
  await page.getByRole("button", { name: "Abstract" }).click();
  await expect(page.getByRole("button", { name: "Abstract" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(abstract).not.toHaveText(portuguese);

  const firstKeyword = page.locator(".keywords button").first();
  const keyword = (await firstKeyword.textContent()).trim();
  await firstKeyword.click();
  await expect(page).toHaveURL(/q=/);
  await expect(page.locator(".ficha").first()).toBeVisible();
  await expect(page.locator(".search-toolbar input")).toHaveValue(keyword);
});
