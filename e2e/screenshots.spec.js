import fs from "node:fs/promises";

import { expect, test } from "@playwright/test";
import { AUTH_HEADERS, RESULTS_HASH, waitForPage } from "./helpers.js";

const OUTPUT = "/tmp/buscateses-shots";

const SCREENS = [
  { slug: "busca", path: "/#/" },
  { slug: "resultados", path: RESULTS_HASH },
  { slug: "ficha", path: "/#/tese/capes%3A0001" },
  { slug: "biblioteca", path: "/#/biblioteca" },
  { slug: "selecao", path: "/#/selecao" },
  { slug: "conectar", path: "/#/conectar" },
  { slug: "configuracoes", path: "/#/configuracoes" },
  { slug: "sobre", path: "/#/sobre" },
  { slug: "primeiros-passos", path: "/#/primeiros-passos" },
];

const SIZES = [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
];

test("captura todas as telas nos dois temas", async ({ page, request }) => {
  test.setTimeout(240_000);
  await fs.mkdir(OUTPUT, { recursive: true });

  await request.post("/api/favorites", {
    headers: AUTH_HEADERS,
    data: { id: "capes:0001" },
  });

  try {
    for (const size of SIZES) {
      await page.setViewportSize(size);
      for (const theme of ["light", "dark"]) {
        await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
        const themeName = theme === "dark" ? "escuro" : "claro";
        for (const screen of SCREENS) {
          await page.goto(screen.path);
          await waitForPage(page);
          if (screen.slug === "resultados") {
            await expect(page.locator(".ficha").first()).toBeVisible();
          }
          await page.waitForTimeout(350);
          const file = `${OUTPUT}/${screen.slug}-${themeName}-${size.width}.png`;
          await page.screenshot({ path: file });
        }
      }
    }
  } finally {
    await request.delete("/api/favorites/capes%3A0001", { headers: AUTH_HEADERS });
  }
});
