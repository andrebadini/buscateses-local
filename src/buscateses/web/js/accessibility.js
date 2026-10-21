/** Optional VLibras widget: off by default and loaded only when requested. */

const STORAGE_KEY = "buscateses.vlibras";
const WIDGET_ID = "vlibras-widget";
const PLUGIN_URL = "https://vlibras.gov.br/app/vlibras-plugin.js";
const WIDGET_URL = "https://vlibras.gov.br/app";

/** Read the saved preference, tolerating blocked storage. */
export function isVLibrasEnabled() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Persist the preference, tolerating blocked storage. */
function storePreference(enabled) {
  try {
    if (enabled) localStorage.setItem(STORAGE_KEY, "1");
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage can be unavailable; the widget still follows this click */
  }
}

function widgetRoot() {
  return document.getElementById(WIDGET_ID);
}

/** Create the markup the VLibras plugin expects and load its script. */
export function loadVLibras() {
  if (widgetRoot() || document.querySelector("[vw]")) {
    return;
  }
  const root = document.createElement("div");
  root.id = WIDGET_ID;
  root.setAttribute("vw", "");
  root.className = "enabled";
  root.innerHTML =
    '<div vw-access-button class="active"></div>' +
    '<div vw-plugin-wrapper><div class="vw-plugin-top-wrapper"></div></div>';
  document.body.append(root);

  const script = document.createElement("script");
  script.src = PLUGIN_URL;
  script.async = true;
  script.addEventListener("load", () => {
    try {
      if (window.VLibras?.Widget) new window.VLibras.Widget(WIDGET_URL);
    } catch {
      /* the accessibility widget must never block the app */
    }
  });
  script.addEventListener("error", () => {
    script.remove();
    root.remove();
  });
  document.body.append(script);
}

/** Remove the widget from the page (the plugin script stays loaded). */
export function unloadVLibras() {
  widgetRoot()?.remove();
}

/** Update every Libras toggle to reflect ``enabled``. */
export function updateVLibrasControls(enabled = isVLibrasEnabled()) {
  const button = document.getElementById("libras-toggle");
  if (button) {
    button.setAttribute("aria-pressed", String(enabled));
    button.title = enabled ? "Desativar o tradutor de Libras" : "Ativar o tradutor de Libras";
  }
  document.querySelectorAll("[data-libras-toggle]").forEach((input) => {
    if ("checked" in input) input.checked = enabled;
  });
}

/** Turn the widget on or off and remember the choice. */
export function setVLibrasEnabled(enabled) {
  const value = Boolean(enabled);
  storePreference(value);
  if (value) loadVLibras();
  else unloadVLibras();
  updateVLibrasControls(value);
  return value;
}

function onToggleClick() {
  setVLibrasEnabled(!isVLibrasEnabled());
}

/** Wire the header button and load the widget when the user opted in. */
export function initVLibras() {
  updateVLibrasControls();
  document.getElementById("libras-toggle")?.addEventListener("click", onToggleClick);
  if (!isVLibrasEnabled()) return;
  if (document.readyState === "complete") setTimeout(loadVLibras, 500);
  else window.addEventListener("load", () => setTimeout(loadVLibras, 500), { once: true });
}
