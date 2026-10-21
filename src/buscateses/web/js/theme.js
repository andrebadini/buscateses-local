/** Theme preference: light, dark or system. Persisted in localStorage. */

const STORAGE_KEY = "buscateses.theme";
const VALID = new Set(["light", "dark", "system"]);

/** Read the saved preference, tolerating blocked storage. */
export function getThemePreference() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return VALID.has(value) ? value : "system";
  } catch {
    return "system";
  }
}

/** Persist a preference, tolerating blocked storage. */
export function setThemePreference(preference) {
  const value = VALID.has(preference) ? preference : "system";
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    /* private mode or storage disabled: the theme still applies for this page */
  }
  applyTheme(value);
  return value;
}

/** Apply a preference to <html>. "system" follows prefers-color-scheme via CSS. */
export function applyTheme(preference = getThemePreference()) {
  const root = document.documentElement;
  if (preference === "light" || preference === "dark") {
    root.dataset.theme = preference;
  } else {
    delete root.dataset.theme;
  }
  updateThemeToggleLabel();
}

/** The color theme currently visible to the user. */
export function resolvedTheme() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Flip between light and dark and store the explicit choice. */
export function toggleTheme() {
  return setThemePreference(resolvedTheme() === "dark" ? "light" : "dark");
}

/** Keep the header button label aligned with the visible theme. */
export function updateThemeToggleLabel() {
  const button = document.getElementById("theme-toggle");
  if (!button) return;
  const dark = resolvedTheme() === "dark";
  button.setAttribute("aria-label", dark ? "Mudar para o tema claro" : "Mudar para o tema escuro");
  button.title = dark ? "Mudar para o tema claro" : "Mudar para o tema escuro";
  button.dataset.themeState = dark ? "dark" : "light";
}

/** React to operating-system theme changes while preference is "system". */
export function watchSystemTheme() {
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!media) return;
  media.addEventListener("change", () => {
    if (getThemePreference() === "system") updateThemeToggleLabel();
  });
}
