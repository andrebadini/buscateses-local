/** Shared routes and small helpers for the Playwright suite. */

export const RESULTS_HASH = "/#/?q=educa%C3%A7%C3%A3o%20inclusiva";

export const ROUTES = [
  { name: "busca", path: "/#/" },
  { name: "resultados", path: RESULTS_HASH },
  { name: "ficha", path: "/#/tese/capes%3A0001" },
  { name: "biblioteca", path: "/#/biblioteca" },
  { name: "selecao", path: "/#/selecao" },
  { name: "conectar", path: "/#/conectar" },
  { name: "configuracoes", path: "/#/configuracoes" },
  { name: "sobre", path: "/#/sobre" },
  { name: "primeiros-passos", path: "/#/primeiros-passos" },
];

export const AUTH_HEADERS = { "X-BuscaTeses": "1" };

/** Wait until the router has rendered a page heading. */
export async function waitForPage(page) {
  await page.locator("main h1").first().waitFor({ state: "visible" });
}

/** Remove every saved favorite so tests start from a clean selection. */
export async function clearFavorites(request) {
  const response = await request.get("/api/favorites");
  if (!response.ok()) return;
  for (const favorite of await response.json()) {
    await request.delete(`/api/favorites/${encodeURIComponent(favorite.id)}`, {
      headers: AUTH_HEADERS,
    });
  }
}

/** Run axe and return serious or critical violations. */
export async function seriousViolations(page, AxeBuilder) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa"])
    .analyze();
  return results.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );
}
