import { expect, test } from "@playwright/test";
import { waitForPage } from "./helpers.js";

const OFF = {
  state: "off",
  url: null,
  include_library: false,
  message: "Endereço público desligado.",
  cloudflared_installed: true,
};

test("o cartão Sobre oferece apenas Abrir Sobre, sem exportar o catálogo", async ({
  page,
}) => {
  await page.goto("/#/configuracoes");
  await waitForPage(page);

  const card = page
    .locator("section.settings-card")
    .filter({ has: page.getByRole("heading", { name: "Sobre o BuscaTeses" }) });

  await expect(card.getByRole("link", { name: "Abrir Sobre" })).toBeVisible();
  await expect(card.getByRole("button", { name: /Exportar catálogo/ })).toHaveCount(0);
});

test("o guia do assistente mantém o espaçamento entre as seções", async ({ page }) => {
  await page.route("**/api/integrations", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/remote", (route) => route.fulfill({ json: OFF }));

  await page.goto("/#/conectar");
  await waitForPage(page);

  const guide = page.locator("section.web-guide");
  await expect(
    guide.getByRole("heading", { name: "Como configurar o assistente" }),
  ).toBeVisible();
  await expect(guide).toHaveCSS("margin-top", "40px");
});
