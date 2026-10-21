import fs from "node:fs/promises";

import { expect, test } from "@playwright/test";
import { RESULTS_HASH, clearFavorites } from "./helpers.js";

test.beforeEach(async ({ request }) => {
  await clearFavorites(request);
});

test.afterEach(async ({ request }) => {
  await clearFavorites(request);
});

test("salvar, ver na seleção, exportar CSV e remover", async ({ page }) => {
  await page.goto(RESULTS_HASH);
  const first = page.locator(".ficha").first();
  await expect(first).toBeVisible();

  const save = first.getByRole("button", { name: "Salvar", exact: true });
  await save.click();
  await expect(first.getByRole("button", { name: "Salvo", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator(".toast")).toContainText("Adicionado à Minha seleção.");

  await page.goto("/#/selecao");
  await expect(page.getByRole("heading", { level: 1, name: "Minha seleção" })).toBeVisible();
  await expect(page.locator(".ficha")).toHaveCount(1);

  await page.locator("details.menu > summary").click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /CSV/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("buscateses.csv");
  const path = await download.path();
  const content = await fs.readFile(path, "utf8");
  expect(content.split("\n")[0]).toContain("Título;Autor");
  expect(content).toContain("Sobrenome");

  await page.getByRole("button", { name: "Remover", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sua seleção está vazia" })).toBeVisible();
});

test("exporta referências ABNT dos resultados", async ({ page }) => {
  await page.goto(RESULTS_HASH);
  await page.locator("details.menu > summary").click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /Referências ABNT/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("buscateses-referencias.txt");
  const path = await download.path();
  const content = await fs.readFile(path, "utf8");
  expect(content.toLowerCase()).toContain("sobrenome");
});
