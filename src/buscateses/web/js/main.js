/** Application bootstrap: theme, navigation, status banners and router. */

import { initVLibras } from "./accessibility.js";
import { getCatalogUpdate } from "./api.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { startRouter } from "./router.js";
import { loadFavorites, loadStatus, state } from "./state.js";
import {
  applyTheme,
  resolvedTheme,
  setThemePreference,
  toggleTheme,
  updateThemeToggleLabel,
  watchSystemTheme,
} from "./theme.js";

const SKIP_KEY = "buscateses.onboarding-skipped";
const CATALOG_CHECK_KEY = "buscateses.catalog-checked";
const MODEL_BANNER_KEY = "buscateses.model-banner-dismissed";
const CATALOG_BANNER_KEY = "buscateses.catalog-banner-dismissed";
const APP_UPDATE_BANNER_KEY = "buscateses.app-update-dismissed";

function readSession(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key, value) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* storage can be unavailable; the app still works for this page */
  }
}

/* -------------------------------------------------------------------- theme */

function paintThemeIcon() {
  const button = document.getElementById("theme-toggle");
  if (!button) return;
  button.replaceChildren(icon(resolvedTheme() === "dark" ? "sun" : "moon", { size: 20 }));
  updateThemeToggleLabel();
}

function setupTheme() {
  applyTheme();
  paintThemeIcon();
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    toggleTheme();
    paintThemeIcon();
  });
  watchSystemTheme();
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener("change", paintThemeIcon);
  window.addEventListener("storage", (event) => {
    if (event.key === "buscateses.theme") {
      applyTheme();
      paintThemeIcon();
    }
  });
}

/* ---------------------------------------------------------------- navigation */

function setupMobileNav() {
  const toggle = document.getElementById("nav-toggle");
  const nav = document.getElementById("nav-principal");
  if (!toggle || !nav) return;

  const setOpen = (open, { returnFocus = false } = {}) => {
    toggle.setAttribute("aria-expanded", String(open));
    nav.classList.toggle("is-open", open);
    const label = toggle.querySelector(".sr-only");
    if (label) label.textContent = open ? "Fechar menu" : "Abrir menu";
    if (open) {
      nav.querySelector("a")?.focus({ preventScroll: true });
    } else if (returnFocus) {
      toggle.focus({ preventScroll: true });
    }
  };

  toggle.addEventListener("click", () => {
    setOpen(toggle.getAttribute("aria-expanded") !== "true");
  });
  nav.addEventListener("click", (event) => {
    if (event.target.closest("a")) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      event.preventDefault();
      setOpen(false, { returnFocus: true });
    }
  });
  window.addEventListener("hashchange", () => {
    if (toggle.getAttribute("aria-expanded") === "true") setOpen(false);
  });
}

function setupSkipLink() {
  const link = document.querySelector(".skip-link");
  const main = document.getElementById("conteudo");
  if (!link || !main) return;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    main.focus();
    main.scrollIntoView({ block: "start", behavior: "auto" });
  });
}

/* ------------------------------------------------------------------ banners */

function bannerNode({ text, actionLabel, href, tone = "", storageKey, onClose }) {
  const banner = h(
    "div",
    { class: `banner${tone ? ` banner--${tone}` : ""}` },
    icon(tone === "info" ? "info" : "alert", { size: 18 }),
    h(
      "p",
      { class: "banner__text" },
      text,
      href
        ? h(
            "a",
            {
              href,
              style: { marginLeft: "6px" },
              ...(href.startsWith("http")
                ? { target: "_blank", rel: "noopener noreferrer" }
                : {}),
            },
            actionLabel || "Ver",
          )
        : null,
    ),
  );
  const close = h(
    "button",
    { class: "icon-btn banner__close", type: "button", "aria-label": "Fechar aviso" },
    icon("close", { size: 16 }),
  );
  close.addEventListener("click", () => {
    banner.remove();
    if (storageKey) writeSession(storageKey, "1");
    onClose?.();
  });
  banner.append(close);
  return banner;
}

function addBanner(options) {
  const host = document.getElementById("banners");
  if (!host) return;
  if (options.storageKey && readSession(options.storageKey)) return;
  host.append(bannerNode(options));
  host.hidden = false;
}

function showModelBanner(status) {
  if (!status?.model || status.model.ready || status.model.downloading) return;
  addBanner({
    text: "A busca por significado está desligada porque o modelo de embedding ainda não foi instalado.",
    actionLabel: "Instalar modelo de embedding",
    href: "#/primeiros-passos",
    storageKey: MODEL_BANNER_KEY,
  });
}

function showCatalogBanner(info) {
  if (!info?.available) return;
  const extra = info.records_delta
    ? ` (${Math.abs(Number(info.records_delta)).toLocaleString("pt-BR")} registros a mais)`
    : "";
  addBanner({
    text: `Uma nova versão do catálogo está disponível${extra}.`,
    actionLabel: "Ver atualização",
    href: "#/configuracoes",
    tone: "info",
    storageKey: CATALOG_BANNER_KEY,
  });
}

function checkCatalogOnce(status) {
  if (!status?.catalog?.installed) return;
  if (state.catalogUpdateChecked || readSession(CATALOG_CHECK_KEY)) return;
  state.catalogUpdateChecked = true;
  writeSession(CATALOG_CHECK_KEY, "1");
  getCatalogUpdate()
    .then((info) => {
      state.catalogUpdate = info;
      showCatalogBanner(info);
    })
    .catch((error) => {
      // Update checks are optional and may fail offline: never surface an error.
      console.debug("Catalog update check unavailable.", error?.message ?? error);
    });
}

/* ----------------------------------------------------------- app updates */

function showAppUpdateBanner(info) {
  if (!info?.available || !info.url) return;
  addBanner({
    text: `Nova versão do BuscaTeses disponível${info.latest ? ` (${info.latest})` : ""}.`,
    actionLabel: "Ver versão",
    href: info.url,
    tone: "info",
    storageKey: APP_UPDATE_BANNER_KEY,
  });
}

/* --------------------------------------------------------------------- boot */

async function boot() {
  setupTheme();
  setupMobileNav();
  setupSkipLink();
  initVLibras();

  if (readSession(SKIP_KEY) === "1") state.onboardingSkipped = true;

  let status = null;
  try {
    status = await loadStatus(true);
  } catch {
    /* the router shows a friendly error when a view cannot load */
  }

  if (status) {
    state.status = status;
    const version = document.getElementById("footer-version");
    if (version && status.app?.version) version.textContent = status.app.version;
    showModelBanner(status);
    showAppUpdateBanner(status.app_update);
    checkCatalogOnce(status);

    const atHome = !window.location.hash || window.location.hash === "#/" ||
      window.location.hash === "#";
    if (status.setup_required && atHome && !state.onboardingSkipped) {
      try {
        history.replaceState(null, "", "#/primeiros-passos");
      } catch {
        window.location.hash = "#/primeiros-passos";
      }
    }
  }

  loadFavorites().catch(() => state.favorites);

  let router = null;
  const ctx = {
    navigate(hash) {
      if (window.location.hash === hash) router?.refresh();
      else window.location.hash = hash;
    },
  };
  router = startRouter(document.getElementById("conteudo"), ctx);
}

boot();
