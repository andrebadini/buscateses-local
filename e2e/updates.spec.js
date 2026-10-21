import { expect, test } from "@playwright/test";

test("falha ao consultar atualizações não mostra banner nem erro", async ({ page }) => {
  const debug = [];
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "debug") debug.push(message.text());
    if (message.type() === "error") errors.push(message.text());
  });
  await page.route("**/api/catalog/update", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Sem conexão." }),
    }),
  );

  const firstCheck = page.waitForRequest("**/api/catalog/update");
  await page.goto("/#/");
  await firstCheck;
  await expect(page.locator("#banners .banner")).toHaveCount(0);

  await page.goto("/#/configuracoes");
  await expect
    .poll(
      () =>
        debug.filter((line) => line.includes("Catalog update check unavailable")).length,
    )
    .toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Não foi possível verificar o catálogo agora")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  // Browsers log failed network requests themselves; the app must not log errors.
  expect(errors.filter((line) => !line.startsWith("Failed to load resource"))).toEqual([]);
});

test("mostra aviso discreto de nova versão e permite desligar a verificação", async ({
  page,
}) => {
  await page.route("**/api/status", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.app_update = {
      available: true,
      latest: "9.9.9",
      url: "https://github.com/andrebadini/buscateses-local/releases/latest",
    };
    await route.fulfill({ response, json: payload });
  });

  let saved = null;
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() !== "PUT") {
      await route.continue();
      return;
    }
    saved = route.request().postDataJSON();
    await route.fulfill({
      json: { updates: { check_app: false, check_catalog: true } },
    });
  });

  await page.goto("/#/");
  const banner = page.locator("#banners .banner", {
    hasText: "Nova versão do BuscaTeses disponível",
  });
  await expect(banner).toBeVisible();
  await expect(banner.locator("a")).toHaveAttribute(
    "href",
    "https://github.com/andrebadini/buscateses-local/releases/latest",
  );

  await page.goto("/#/configuracoes");
  const toggle = page.locator("#check-app-updates");
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await expect(toggle).not.toBeChecked();
  await expect(page.getByText("A verificação de novas versões está desligada.")).toBeVisible();
  expect(saved).toEqual({ updates: { check_app: false } });
});
