import { expect, test } from "@playwright/test";

const PLUGIN = "https://vlibras.gov.br/app/vlibras-plugin.js";
const STUB =
  "window.VLibras = { Widget: function (url) { window.__vlibrasUrl = url; } };";

test("VLibras fica desligado por padrão e só carrega quando ativado", async ({ page }) => {
  const requests = [];
  const cspErrors = [];
  page.on("request", (request) => {
    if (request.url().startsWith("https://vlibras.gov.br")) requests.push(request.url());
  });
  page.on("console", (message) => {
    if (/Content Security Policy/i.test(message.text())) cspErrors.push(message.text());
  });
  await page.route(PLUGIN, (route) =>
    route.fulfill({ contentType: "application/javascript", body: STUB }),
  );

  await page.goto("/#/");
  const button = page.locator("#libras-toggle");
  await expect(button).toBeVisible();
  await expect(button).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("#vlibras-widget")).toHaveCount(0);
  expect(requests).toEqual([]);

  await button.click();
  await expect(button).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#vlibras-widget")).toHaveCount(1);
  await expect
    .poll(() => page.evaluate(() => window.__vlibrasUrl))
    .toBe("https://vlibras.gov.br/app");
  expect(await page.evaluate(() => localStorage.getItem("buscateses.vlibras"))).toBe("1");
  expect(cspErrors).toEqual([]);

  await page.reload();
  await expect(page.locator("#vlibras-widget")).toHaveCount(1);

  await page.goto("/#/configuracoes");
  const settingsToggle = page.locator("#libras-enabled");
  await expect(settingsToggle).toBeChecked();
  await settingsToggle.uncheck();
  await expect(settingsToggle).not.toBeChecked();
  await expect(page.locator("#libras-widget")).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("buscateses.vlibras")))
    .toBeNull();
  await expect(page.locator("#libras-toggle")).toHaveAttribute("aria-pressed", "false");
  expect(cspErrors).toEqual([]);
});

test("falha de rede do VLibras não quebra a interface", async ({ page }) => {
  await page.route(PLUGIN, (route) => route.abort());
  await page.goto("/#/");
  await page.evaluate(() => localStorage.setItem("buscateses.vlibras", "1"));
  await page.reload();
  await expect(page.locator("#vlibras-widget")).toHaveCount(0);
  await expect(page.locator("main h1").first()).toBeVisible();
});
