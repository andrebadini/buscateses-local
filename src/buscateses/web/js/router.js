/** Hash router: one render function per screen. */

import { createSearchSkeleton } from "./components/search-skeleton.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { state } from "./state.js";
import { renderAbout } from "./views/about.js";
import { renderConnect } from "./views/connect.js";
import { renderLibrary } from "./views/library.js";
import { renderOnboarding } from "./views/onboarding.js";
import { renderSearch } from "./views/search.js";
import { renderSelection } from "./views/selection.js";
import { renderSettings } from "./views/settings.js";
import { renderThesis } from "./views/thesis.js";

/** @typedef {{outlet: HTMLElement, ctx: Record<string, any>}} RouterContext */

const ROUTES = [
  {
    id: "search",
    pattern: /^#\/?$/,
    nav: "search",
    title: "Buscar",
    render: (outlet, ctx, route) => renderSearch(outlet, ctx, route.params),
  },
  {
    id: "onboarding",
    pattern: /^#\/primeiros-passos$/,
    nav: "",
    title: "Primeiros passos",
    render: (outlet, ctx) => renderOnboarding(outlet, ctx),
  },
  {
    id: "thesis",
    pattern: /^#\/tese\/(.+)$/,
    nav: "search",
    title: "Ficha da tese",
    render: (outlet, ctx, route) => renderThesis(outlet, ctx, route.id),
  },
  {
    id: "library",
    pattern: /^#\/biblioteca$/,
    nav: "library",
    title: "Minha biblioteca",
    render: (outlet, ctx) => renderLibrary(outlet, ctx),
  },
  {
    id: "selection",
    pattern: /^#\/selecao$/,
    nav: "selection",
    title: "Minha seleção",
    render: (outlet, ctx) => renderSelection(outlet, ctx),
  },
  {
    id: "connect",
    pattern: /^#\/conectar$/,
    nav: "connect",
    title: "Conectar assistentes de IA",
    render: (outlet, ctx) => renderConnect(outlet, ctx),
  },
  {
    id: "settings",
    pattern: /^#\/configuracoes$/,
    nav: "settings",
    title: "Configurações",
    render: (outlet, ctx) => renderSettings(outlet, ctx),
  },
  {
    id: "about",
    pattern: /^#\/sobre$/,
    nav: "",
    title: "Sobre",
    render: (outlet, ctx) => renderAbout(outlet, ctx),
  },
];

function currentRoute() {
  const raw = window.location.hash || "#/";
  const question = raw.indexOf("?");
  const path = question === -1 ? raw : raw.slice(0, question);
  const query = question === -1 ? "" : raw.slice(question + 1);
  const params = new URLSearchParams(query);
  for (const route of ROUTES) {
    const match = route.pattern.exec(path);
    if (match) return { route, path, params, match };
  }
  return { route: null, path, params, match: null };
}

/** Update aria-current on the main navigation. */
function updateNavigation(navId) {
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === navId) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

/** Render a friendly error state instead of an empty screen. */
function renderError(outlet, message, onRetry) {
  outlet.replaceChildren(
    h(
      "div",
      { class: "notice notice--error", role: "alert" },
      h("span", { class: "notice__icon" }, icon("alert", { size: 22 })),
      h(
        "div",
        {},
        h("h1", { style: { fontSize: "var(--fs-25)" } }, "Não foi possível abrir esta página"),
        h("p", {}, message),
        onRetry
          ? h(
              "button",
              { class: "btn btn--ghost", type: "button", onclick: onRetry },
              "Tentar de novo",
            )
          : null,
      ),
    ),
  );
  const heading = outlet.querySelector("h1");
  heading?.setAttribute("tabindex", "-1");
  heading?.focus({ preventScroll: true });
}

/** Render the not-found screen. */
function renderNotFound(outlet) {
  outlet.replaceChildren(
    h(
      "div",
      { class: "empty-state" },
      h("h1", { tabindex: "-1" }, "Página não encontrada"),
      h("p", {}, "O endereço que você abriu não existe no BuscaTeses."),
      h("a", { class: "btn btn--primary", href: "#/" }, "Ir para a busca"),
    ),
  );
  outlet.querySelector("h1")?.focus({ preventScroll: true });
}

/**
 * Start the hash router.
 *
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx Shared helpers passed to every view.
 */
export function startRouter(outlet, ctx) {
  let controller = new AbortController();
  let currentHash = window.location.hash || "#/";

  async function handle() {
    const rawHash = window.location.hash || "#/";
    if (rawHash === currentHash && outlet.childElementCount) {
      // Same hash: still allow a programmatic refresh.
    }
    state.previousHash = currentHash;
    currentHash = rawHash;

    controller.abort();
    controller = new AbortController();
    ctx.signal = controller.signal;

    const { route, params, match } = currentRoute();
    if (!route) {
      updateNavigation("");
      document.title = "Página não encontrada — BuscaTeses";
      renderNotFound(outlet);
      return;
    }

    updateNavigation(route.nav);
    document.title = `${route.title} — BuscaTeses`;
    outlet.setAttribute("aria-busy", "true");

    // A search request may need to load the semantic model on first use.
    // Skeleton cards make that wait informative instead of a generic spinner.
    const showSkeleton = route.id === "search" && Boolean(params.get("q"));
    const skeleton = showSkeleton ? createSearchSkeleton() : null;
    outlet.replaceChildren(
      skeleton?.element ??
        h(
          "div",
          { class: "boot-loading", role: "status" },
          h("span", { class: "boot-loading__dot", "aria-hidden": "true" }),
          "Carregando…",
        ),
    );

    try {
      const result = await route.render(outlet, ctx, {
        params,
        match,
        id: match?.[1] ? decodeURIComponent(match[1]) : null,
      });
      if (controller.signal.aborted) return;
      if (result?.title) document.title = `${result.title} — BuscaTeses`;
      outlet.removeAttribute("aria-busy");
      const heading = outlet.querySelector("h1");
      if (heading) {
        if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
        heading.focus({ preventScroll: true });
      }
      window.scrollTo({ top: 0, behavior: "auto" });
    } catch (error) {
      if (controller.signal.aborted || error?.name === "AbortError") return;
      outlet.removeAttribute("aria-busy");
      renderError(
        outlet,
        error?.message || "Ocorreu um erro inesperado. Tente de novo.",
        () => handle(),
      );
    } finally {
      skeleton?.dispose();
    }
  }

  window.addEventListener("hashchange", handle);
  if (!window.location.hash) {
    try {
      history.replaceState(null, "", "#/");
    } catch {
      /* the router still works without rewrite */
    }
  }
  handle();

  return {
    refresh: handle,
  };
}
