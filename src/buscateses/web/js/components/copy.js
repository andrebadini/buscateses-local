/** Clipboard helpers with a fallback for browsers without the async API. */

import { h } from "../dom.js";
import { icon } from "../icons.js";
import { showError, showToast } from "./toast.js";

/**
 * Copy text to the clipboard. Throws when both strategies fail.
 *
 * @param {string} text
 */
export async function copyText(text) {
  const value = String(text ?? "");
  if (!value) throw new Error("Não há texto para copiar.");
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      /* fall through to the legacy path */
    }
  }
  const area = h("textarea", {
    class: "sr-only",
    readonly: true,
    value,
  });
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) throw new Error("Não foi possível copiar. Selecione o texto e copie manualmente.");
}

/**
 * Copy a value and announce the result.
 *
 * @param {string | (() => Promise<string>)} source
 * @param {string} successMessage
 */
export async function copyWithToast(source, successMessage = "Copiado.") {
  try {
    const value = typeof source === "function" ? await source() : source;
    await copyText(value);
    showToast(successMessage);
    return true;
  } catch (error) {
    showError(error);
    return false;
  }
}

/**
 * Build a "Copiar" button that resolves its own text when clicked.
 *
 * @param {string | (() => Promise<string>)} source
 * @param {{label?: string, successMessage?: string, class?: string, small?: boolean}} [options]
 */
export function copyButton(
  source,
  {
    label = "Copiar",
    successMessage = "Copiado.",
    class: className = "btn btn--ghost",
    small = false,
  } = {},
) {
  const button = h(
    "button",
    { class: `${className}${small ? " btn--small" : ""}`, type: "button" },
    icon("copy", { size: 16 }),
    h("span", {}, label),
  );
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await copyWithToast(source, successMessage);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}
