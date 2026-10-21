/** Accessible pagination controls. */

import { h } from "../dom.js";
import { formatNumber } from "../format.js";
import { icon } from "../icons.js";

/**
 * Build the page number list with first/last and neighbours.
 *
 * @param {number} current
 * @param {number} total
 * @returns {Array<number|"gap">}
 */
function pageWindow(current, total) {
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const ordered = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const output = [];
  let previous = 0;
  for (const page of ordered) {
    if (previous && page - previous > 1) output.push("gap");
    output.push(page);
    previous = page;
  }
  return output;
}

/**
 * @param {{page: number, pages: number, total: number, pageSize: number}} result
 * @param {(page: number) => void} onPage
 * @returns {HTMLElement|null}
 */
export function createPagination(result, onPage) {
  const page = Number(result.page) || 1;
  const pages = Number(result.pages) || 0;
  if (pages <= 1) return null;

  const nav = h("nav", { class: "pagination", "aria-label": "Paginação dos resultados" });

  const previous = h(
    "button",
    {
      class: "pagination__page",
      type: "button",
      disabled: page <= 1,
      "aria-label": "Página anterior",
      onclick: () => onPage(page - 1),
    },
    icon("chevronLeft", { size: 16 }),
  );
  nav.append(previous);

  for (const entry of pageWindow(page, pages)) {
    if (entry === "gap") {
      nav.append(h("span", { class: "pagination__gap", "aria-hidden": "true" }, "…"));
      continue;
    }
    nav.append(
      h(
        "button",
        {
          class: "pagination__page",
          type: "button",
          "aria-current": entry === page ? "page" : null,
          "aria-label": `Página ${formatNumber(entry)}`,
          onclick: () => {
            if (entry !== page) onPage(entry);
          },
        },
        formatNumber(entry),
      ),
    );
  }

  const next = h(
    "button",
    {
      class: "pagination__page",
      type: "button",
      disabled: page >= pages,
      "aria-label": "Próxima página",
      onclick: () => onPage(page + 1),
    },
    icon("chevronRight", { size: 16 }),
  );
  nav.append(next);

  const first = (page - 1) * (result.page_size || result.pageSize || 20) + 1;
  const last = Math.min(first + (result.results?.length ?? 0) - 1, result.total);
  nav.append(
    h(
      "p",
      { class: "pagination__info" },
      `Página ${formatNumber(page)} de ${formatNumber(pages)} · resultados ${formatNumber(
        first,
      )} a ${formatNumber(last)} de ${result.total_capped ? "mais de " : ""}${formatNumber(
        result.total,
      )}`,
    ),
  );

  return nav;
}
