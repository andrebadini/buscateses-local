/** Accessible confirmation dialog built on <dialog>. */

import { h } from "../dom.js";

/**
 * Ask for confirmation. Returns true when confirmed.
 *
 * @param {{
 *   title: string,
 *   message: string,
 *   confirmLabel?: string,
 *   cancelLabel?: string,
 *   danger?: boolean,
 * }} options
 * @returns {Promise<boolean>}
 */
export function confirmDialog({
  title,
  message,
  confirmLabel = "Confirmar",
  cancelLabel = "Cancelar",
  danger = false,
}) {
  return new Promise((resolve) => {
    const previousFocus = document.activeElement;
    const dialog = h("dialog", { "aria-labelledby": "dialog-title" });
    let settled = false;

    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
      if (dialog.open) dialog.close();
      dialog.remove();
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
    };

    const cancelButton = h(
      "button",
      { class: "btn btn--ghost", type: "button", onclick: () => finish(false) },
      cancelLabel,
    );
    const confirmButton = h(
      "button",
      {
        class: danger ? "btn btn--danger" : "btn btn--primary",
        type: "button",
        onclick: () => finish(true),
      },
      confirmLabel,
    );

    dialog.append(
      h("h2", { id: "dialog-title" }, title),
      h("p", {}, message),
      h("div", { class: "dialog-actions" }, cancelButton, confirmButton),
    );
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(false);
    });
    document.body.append(dialog);
    dialog.showModal();
    (danger ? cancelButton : confirmButton).focus();
  });
}

/**
 * Show arbitrary content in a modal dialog.
 *
 * @param {{title: string, content: Node, closeLabel?: string}} options
 * @returns {Promise<void>}
 */
export function informationDialog({ title, content, closeLabel = "Fechar" }) {
  return new Promise((resolve) => {
    const previousFocus = document.activeElement;
    const dialog = h("dialog", { "aria-labelledby": "info-dialog-title" });
    const finish = () => {
      if (dialog.open) dialog.close();
      dialog.remove();
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
      resolve();
    };
    const closeButton = h(
      "button",
      { class: "btn btn--primary", type: "button", onclick: finish },
      closeLabel,
    );
    dialog.append(
      h("h2", { id: "info-dialog-title" }, title),
      content,
      h("div", { class: "dialog-actions" }, closeButton),
    );
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish();
    });
    dialog.addEventListener("close", finish, { once: true });
    document.body.append(dialog);
    dialog.showModal();
    closeButton.focus();
  });
}
