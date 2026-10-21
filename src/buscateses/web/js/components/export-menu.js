/** Export dropdown for CSV, BibTeX, RIS and ABNT references. */

import { downloadBlob, exportFile } from "../api.js";
import { h } from "../dom.js";
import { icon } from "../icons.js";
import { showError, showToast } from "./toast.js";

const FORMATS = [
  { id: "csv", label: "CSV", extension: "csv", filename: "buscateses.csv" },
  { id: "bibtex", label: "BibTeX", extension: "bib", filename: "buscateses.bib" },
  { id: "ris", label: "RIS", extension: "ris", filename: "buscateses.ris" },
  {
    id: "abnt",
    label: "Referências ABNT",
    extension: "txt",
    filename: "buscateses-referencias.txt",
  },
];

/**
 * @param {{
 *   getState?: () => Record<string, any> | null,
 *   ids?: string[] | (() => string[]),
 *   label?: string,
 *   disabled?: boolean,
 * }} options
 */
export function createExportMenu({ getState = null, ids = [], label = "Exportar", disabled = false } = {}) {
  const details = h("details", { class: "menu" });
  const summary = h(
    "summary",
    {
      class: "btn btn--ghost",
      "aria-disabled": disabled ? "true" : null,
    },
    icon("download", { size: 16 }),
    h("span", {}, label),
    icon("chevronDown", { size: 16, class: "chevron" }),
  );
  if (disabled) {
    summary.addEventListener("click", (event) => event.preventDefault());
  }

  const list = h("div", { class: "menu__list", role: "group", "aria-label": "Formatos de exportação" });

  for (const format of FORMATS) {
    const button = h(
      "button",
      { type: "button" },
      h("span", {}, format.label),
      h(
        "span",
        { class: "sr-only" },
        ` — baixar arquivo ${format.extension.toUpperCase()}`,
      ),
    );
    button.addEventListener("click", async () => {
      details.open = false;
      button.disabled = true;
      try {
        const resolvedIds = typeof ids === "function" ? ids() : ids;
        const result = await exportFile({
          format: format.id,
          ids: resolvedIds,
          state: resolvedIds.length ? null : getState?.(),
        });
        downloadBlob(result.blob, result.filename || format.filename);
        showToast("O arquivo da exportação foi gerado.");
      } catch (error) {
        showError(error);
      } finally {
        button.disabled = false;
      }
    });
    list.append(button);
  }

  details.append(summary, list);
  return details;
}
