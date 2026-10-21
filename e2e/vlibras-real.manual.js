/**
 * Manual check against the real VLibras service (Unity WebGL widget).
 *
 * This file intentionally does not match Playwright's default `*.spec.js`
 * pattern, so the normal suite stays offline. Run it with:
 *
 *   npx playwright test --config playwright.vlibras.config.js
 *
 * It reports every CSP violation, blocked request and failed response, then
 * asserts that the widget markup and its WebGL canvas were actually created.
 */
import { expect, test } from "@playwright/test";

test("VLibras real carrega sem violações de CSP", async ({ page }) => {
  test.setTimeout(240_000);
  const csp = [];
  const blocked = [];
  const aborted = [];
  const failed = [];
  const loaded = [];

  page.on("console", (message) => {
    const text = message.text();
    if (/content security policy|refused to/i.test(text)) csp.push(text);
  });
  page.on("pageerror", (error) => csp.push(`pageerror: ${error.message}`));
  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText || "falhou";
    const entry = `${request.url()} :: ${failure}`;
    // Redirect hops surface as ERR_ABORTED even when the final URL loads.
    if (failure === "net::ERR_ABORTED") aborted.push(entry);
    else blocked.push(entry);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) failed.push(`${response.status()} ${response.url()}`);
  });
  page.on("request", (request) => {
    if (/vlibras|jsdelivr/i.test(request.url())) loaded.push(request.url());
  });

  await page.goto("/#/");
  await page.locator("#libras-toggle").click();
  await expect(page.locator("#vlibras-widget")).toHaveCount(1);

  // The plugin script is fetched over the network; Unity assets follow.
  try {
    await page.waitForFunction(() => Boolean(window.VLibras?.Widget), null, {
      timeout: 60_000,
    });
  } catch (error) {
    console.log(
      JSON.stringify(
        { stage: "plugin", csp, blocked, aborted, failed, loaded: [...new Set(loaded)] },
        null,
        2,
      ),
    );
    throw error;
  }
  // The plugin v7 renders its real button inside a shadow root. Clicking it
  // fetches `vlibras-plugin-app.js`, which boots the Unity WebGL translator.
  await page.evaluate(() => {
    const wrapper = document.getElementById("vlibras-access-wrapper");
    const button = wrapper?.shadowRoot?.getElementById("vlibras-button");
    if (button instanceof HTMLElement) button.click();
  });
  await page.waitForTimeout(25_000);

  const snapshot = await page.evaluate(() => {
    const root = document.querySelector("[vw]");
    const appRoot = document.getElementById("vlibras-app-root");
    const appShadow = appRoot?.shadowRoot;
    return {
      widget: Boolean(root),
      rootClass: root?.className || "",
      accessButton: document.querySelectorAll("[vw-access-button]").length,
      shadowButton: Boolean(
        document.getElementById("vlibras-access-wrapper")?.shadowRoot
          ?.getElementById("vlibras-button"),
      ),
      appRoot: Boolean(appRoot),
      appCanvas: appShadow?.querySelectorAll("canvas").length || 0,
      appIframe: appShadow?.querySelectorAll("iframe").length || 0,
      wrapperHtml: (root?.innerHTML || "").slice(0, 1500),
    };
  });

  console.log(
    JSON.stringify(
      { csp, blocked, aborted, failed, loaded: [...new Set(loaded)], snapshot },
      null,
      2,
    ),
  );

  const unityLoaded = loaded.some((url) => url.includes("playerweb.wasm.code.unityweb"));

  expect(snapshot.widget).toBe(true);
  expect(snapshot.appRoot).toBe(true);
  expect(unityLoaded).toBe(true);
  expect(csp).toEqual([]);
  expect(blocked).toEqual([]);
});
