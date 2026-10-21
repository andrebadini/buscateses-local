import { expect, test } from "@playwright/test";
import { AUTH_HEADERS } from "./helpers.js";

// Only the missing catalog is downloaded, so the hint may show just its size.
const DOWNLOAD_HINT = /cerca de [\d.,]+ [kMG]?B/;

test("status de instalação leva aos primeiros passos", async ({ page, request }) => {
  const toggle = await request.post("/__e2e__/setup-mode?on=1", { headers: AUTH_HEADERS });
  expect(toggle.ok()).toBeTruthy();

  try {
    await page.goto("/#/");
    await expect(page).toHaveURL(/#\/primeiros-passos/);
    await expect(page.getByRole("heading", { level: 1, name: "Primeiros passos" })).toBeVisible();
    await expect(page.getByText(/Nada do que você pesquisar sai deste computador/)).toBeVisible();
    await expect(page.locator(".onboarding__actions .muted.small")).toContainText(
      DOWNLOAD_HINT,
    );

    await page.getByRole("button", { name: "Baixar e instalar", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Baixar modelo de embedding e catálogo" }),
    ).toBeVisible();
    await expect(
      page.getByText("O modelo de embedding já está pronto neste computador."),
    ).toBeVisible();
    await expect(
      page.getByText("Catálogo de teses e dissertações", { exact: true }),
    ).toBeVisible();
  } finally {
    const restore = await request.post("/__e2e__/setup-mode?on=0", { headers: AUTH_HEADERS });
    expect(restore.ok()).toBeTruthy();
  }
});

test("pular a instalação leva à busca", async ({ page, request }) => {
  await request.post("/__e2e__/setup-mode?on=1", { headers: AUTH_HEADERS });
  try {
    await page.goto("/#/primeiros-passos");
    await page.getByRole("button", { name: "Pular por agora" }).click();
    await expect(page).toHaveURL(/#\/$/);
    await expect(
      page.getByRole("heading", { level: 1, name: /Pesquise teses e dissertações/ }),
    ).toBeVisible();
  } finally {
    await request.post("/__e2e__/setup-mode?on=0", { headers: AUTH_HEADERS });
  }
});

test("etapas mostram o estado real e o botão principal vai à busca", async ({ page }) => {
  await page.goto("/#/primeiros-passos");

  const steps = page.locator(".step");
  await expect(steps.nth(0).locator(".step__state")).toHaveText("Pronto");
  await expect(steps.nth(1).locator(".step__state")).toContainText(
    /Pronto · 60 registros, versão/,
  );
  // Completed steps use the check icon instead of a number.
  await expect(steps.nth(0).locator(".step__num svg")).toBeVisible();
  await expect(steps.nth(1).locator(".step__num svg")).toBeVisible();

  const primary = page.getByRole("button", { name: "Ir para a busca", exact: true });
  await expect(primary).toBeVisible();
  await primary.click();
  await expect(page).toHaveURL(/#\/$/);
});

test("a etapa de assistentes fica pronta quando algum cliente está conectado", async ({
  page,
}) => {
  await page.route("**/api/integrations", (route) =>
    route.fulfill({
      json: [
        { id: "cursor", name: "Cursor", kind: "auto", detected: true, connected: true },
      ],
    }),
  );
  await page.goto("/#/primeiros-passos");

  const step = page.locator(".step").nth(2);
  await expect(step.locator(".step__state")).toHaveText("Pronto");
  await expect(step.locator(".step__num svg")).toBeVisible();
});

test("tamanho do catálogo cai no fallback discreto quando a consulta falha", async ({
  page,
  request,
}) => {
  const toggle = await request.post("/__e2e__/setup-mode?on=1", { headers: AUTH_HEADERS });
  expect(toggle.ok()).toBeTruthy();
  await page.route("**/api/catalog/update", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Sem conexão." }),
    }),
  );
  const debug = [];
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "debug") debug.push(message.text());
    if (message.type() === "error") errors.push(message.text());
  });

  try {
    await page.goto("/#/primeiros-passos");
    await expect(page.locator(".onboarding__actions .muted.small")).toContainText(
      DOWNLOAD_HINT,
    );

    await page.getByRole("button", { name: "Baixar e instalar", exact: true }).click();
    const catalogRow = page.locator(".download-item").nth(1);
    await expect(catalogRow.getByText("cerca de 3 GB")).toBeVisible();

    await expect(page.locator("#banners .banner")).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect
      .poll(() => debug.some((line) => line.includes("Catalog update check unavailable")))
      .toBeTruthy();
    // Browsers log failed network requests themselves; the app must not log errors.
    expect(errors.filter((line) => !line.startsWith("Failed to load resource"))).toEqual([]);
  } finally {
    await request.post("/__e2e__/setup-mode?on=0", { headers: AUTH_HEADERS });
  }
});

test("instalação pede apenas os componentes que faltam", async ({ page, request }) => {
  const toggle = await request.post("/__e2e__/setup-mode?on=1", { headers: AUTH_HEADERS });
  expect(toggle.ok()).toBeTruthy();
  let payload = null;
  await page.route("**/api/setup", async (route) => {
    payload = route.request().postDataJSON();
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Já existe uma instalação em andamento." }),
    });
  });

  try {
    await page.goto("/#/primeiros-passos");
    await page.getByRole("button", { name: "Baixar e instalar", exact: true }).click();
    await page.getByRole("button", { name: "Baixar e instalar", exact: true }).click();

    await expect.poll(() => payload?.components).toEqual(["catalog"]);
  } finally {
    await request.post("/__e2e__/setup-mode?on=0", { headers: AUTH_HEADERS });
  }
});
