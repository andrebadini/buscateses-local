/** Non-blocking status messages announced by screen readers. */

import { h } from "../dom.js";
import { icon } from "../icons.js";

/**
 * Show a toast.
 *
 * @param {string} message
 * @param {{tone?: "info"|"error", duration?: number}} [options]
 */
export function showToast(message, { tone = "info", duration = 6000 } = {}) {
  const container = document.getElementById("toasts");
  if (!container) return;
  const node = h(
    "div",
    {
      class: tone === "error" ? "toast toast--error" : "toast",
      role: tone === "error" ? "alert" : "status",
    },
    icon(tone === "error" ? "alert" : "check", { size: 18 }),
    h("span", {}, message),
  );
  container.append(node);
  const timer = setTimeout(() => node.remove(), duration);
  node.addEventListener("mouseenter", () => clearTimeout(timer), { once: true });
}

/** Shorthand for error toasts. */
export function showError(error) {
  const message =
    typeof error === "string"
      ? error
      : error?.message || "Ocorreu um erro inesperado. Tente de novo.";
  showToast(message, { tone: "error", duration: 8000 });
}
