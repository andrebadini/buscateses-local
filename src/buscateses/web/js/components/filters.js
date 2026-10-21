/** Filter sidebar / mobile drawer. */

import { h } from "../dom.js";
import { formatNumber, plural } from "../format.js";
import { icon } from "../icons.js";
import { showError } from "./toast.js";

const MAX_FACET_ITEMS = 40;

/**
 * @param {Array<{value: string, label?: string, count?: number}>} items
 * @param {string[]} selected
 * @param {string} kind
 * @param {(kind: string, values: string[]) => void} onSelection
 */
function checkboxList(items, selected, kind, onSelection) {
  const list = h("div", { class: "check-list" });
  for (const item of items.slice(0, MAX_FACET_ITEMS)) {
    const input = h("input", {
      type: "checkbox",
      name: kind,
      value: item.value,
      checked: selected.includes(item.value),
    });
    input.addEventListener("change", () => {
      const values = [...list.querySelectorAll(`input[name="${kind}"]:checked`)].map(
        (node) => node.value,
      );
      onSelection(kind, values);
    });
    list.append(
      h(
        "label",
        { class: "check" },
        input,
        h("span", {}, item.label || item.value),
        typeof item.count === "number"
          ? h("span", { class: "check__count" }, formatNumber(item.count))
          : null,
      ),
    );
  }
  return list;
}

function facetGroup(
  title,
  items,
  selected,
  kind,
  onSelection,
  { searchable = false, prefix = "filters" } = {},
) {
  if (!items?.length) return null;
  const headingId = `${prefix}-${kind}-title`;
  const search = searchable
    ? h("input", {
        class: "input filter-search",
        type: "search",
        placeholder: "Filtrar a lista",
        "aria-label": `Filtrar ${title.toLowerCase()}`,
      })
    : null;
  const list = checkboxList(items, selected, kind, onSelection);
  if (search) {
    search.addEventListener("input", () => {
      const term = search.value.trim().toLocaleLowerCase("pt-BR");
      for (const label of list.querySelectorAll(".check")) {
        const text = label.textContent.toLocaleLowerCase("pt-BR");
        label.hidden = Boolean(term) && !text.includes(term);
      }
    });
  }
  return h(
    "div",
    { class: "filter-group", role: "group", "aria-labelledby": headingId },
    h("span", { class: "filter-group__title", id: headingId }, title),
    search,
    list,
  );
}

/**
 * Build the filter form.
 *
 * @param {{
 *   searchState: Record<string, any>,
 *   resultFacets: Record<string, any>,
 *   globalFacets: Record<string, any>,
 *   prefix: string,
 *   onChange: () => void,
 * }} options
 */
export function createFilters({
  searchState,
  resultFacets,
  globalFacets,
  prefix,
  onChange,
}) {
  const form = h("form", {
    class: "filters-form",
    "aria-label": "Filtros da busca",
    onsubmit: (event) => event.preventDefault(),
  });

  const apply = () => {
    searchState.page = 1;
    onChange();
  };

  const setList = (kind, values) => {
    if (kind === "great_area") searchState.greatArea = values;
    else if (kind === "knowledge_area") searchState.knowledgeArea = values;
    else searchState[kind] = values;
    apply();
  };

  /* Year range ---------------------------------------------------------- */
  const minInput = h("input", {
    class: "input",
    type: "number",
    inputmode: "numeric",
    min: "1980",
    max: "2100",
    step: "1",
    placeholder: "1987",
    value: searchState.yearMin ?? "",
  });
  const maxInput = h("input", {
    class: "input",
    type: "number",
    inputmode: "numeric",
    min: "1980",
    max: "2100",
    step: "1",
    placeholder: "2024",
    value: searchState.yearMax ?? "",
  });

  const applyYears = () => {
    const min = minInput.value ? Number.parseInt(minInput.value, 10) : null;
    const max = maxInput.value ? Number.parseInt(maxInput.value, 10) : null;
    if (min !== null && max !== null && min > max) {
      showError("O ano inicial não pode ser maior que o ano final.");
      return;
    }
    searchState.yearMin = min;
    searchState.yearMax = max;
    apply();
  };

  const yearGroup = h(
    "div",
    { class: "filter-group", role: "group", "aria-labelledby": `${prefix}-year-title` },
    h("span", { class: "filter-group__title", id: `${prefix}-year-title` }, "Ano"),
    h(
      "div",
      { class: "year-range" },
      h("div", { class: "field" }, h("label", { for: `${prefix}-year-min` }, "De"), minInput),
      h("div", { class: "field" }, h("label", { for: `${prefix}-year-max` }, "Até"), maxInput),
    ),
  );
  minInput.id = `${prefix}-year-min`;
  maxInput.id = `${prefix}-year-max`;

  const decades = resultFacets?.decade ?? [];
  if (decades.length) {
    const maximum = Math.max(...decades.map((item) => Number(item.count) || 0), 1);
    const list = h("div", { class: "decades", role: "group", "aria-label": "Décadas" });
    /** @type {Array<{start: number, button: HTMLElement}>} */
    const buttons = [];

    const refreshDecades = () => {
      for (const { start, button } of buttons) {
        button.setAttribute(
          "aria-pressed",
          String(searchState.yearMin === start && searchState.yearMax === start + 9),
        );
      }
    };

    for (const decade of decades) {
      const start = Number.parseInt(decade.value, 10);
      const button = h(
        "button",
        {
          class: "decade",
          type: "button",
          "aria-pressed": "false",
          onclick: () => {
            const active =
              searchState.yearMin === start && searchState.yearMax === start + 9;
            if (active) {
              searchState.yearMin = null;
              searchState.yearMax = null;
            } else {
              searchState.yearMin = start;
              searchState.yearMax = start + 9;
            }
            minInput.value = searchState.yearMin ?? "";
            maxInput.value = searchState.yearMax ?? "";
            refreshDecades();
            apply();
          },
        },
        h("span", {}, decade.label || `${start}–${start + 9}`),
        h("span", { class: "decade__count" }, formatNumber(decade.count)),
        h("span", { class: "sr-only" }, ` ${plural(decade.count, "trabalho", "trabalhos")}`),
        h(
          "span",
          { class: "decade__track", "aria-hidden": "true" },
          h("span", {
            class: "decade__fill",
            style: { width: `${Math.max(4, (Number(decade.count) / maximum) * 100)}%` },
          }),
        ),
      );
      buttons.push({ start, button });
      list.append(button);
    }
    refreshDecades();
    const originalApplyYears = applyYears;
    minInput.addEventListener("change", () => {
      originalApplyYears();
      refreshDecades();
    });
    maxInput.addEventListener("change", () => {
      originalApplyYears();
      refreshDecades();
    });
    yearGroup.append(list);
  } else {
    minInput.addEventListener("change", applyYears);
    maxInput.addEventListener("change", applyYears);
  }
  form.append(yearGroup);

  /* Facet groups -------------------------------------------------------- */
  const degreeItems = resultFacets?.degree?.length ? resultFacets.degree : globalFacets?.degree;
  const ufItems = resultFacets?.uf?.length ? resultFacets.uf : globalFacets?.uf;
  const areaItems = resultFacets?.great_area?.length
    ? resultFacets.great_area
    : globalFacets?.great_area;

  const groups = [
    facetGroup("Nível", degreeItems, searchState.degree, "degree", setList, { prefix }),
    facetGroup("UF", ufItems, searchState.uf, "uf", setList, { prefix }),
    facetGroup("Grande área", areaItems, searchState.greatArea, "great_area", setList, {
      prefix,
    }),
    facetGroup(
      "Área do conhecimento",
      globalFacets?.knowledge_area,
      searchState.knowledgeArea,
      "knowledge_area",
      setList,
      { searchable: true, prefix },
    ),
  ].filter(Boolean);
  form.append(...groups);

  /* Institution --------------------------------------------------------- */
  const institutions = globalFacets?.institution_acronym ?? [];
  const institutionInput = h("input", {
    class: "input",
    type: "text",
    list: `${prefix}-institutions`,
    placeholder: "Digite a sigla",
    "aria-label": "Instituição (sigla)",
    autocomplete: "off",
  });
  const addInstitution = () => {
    const value = institutionInput.value.trim();
    if (!value) return;
    const match =
      institutions.find(
        (item) => item.value.toLocaleLowerCase("pt-BR") === value.toLocaleLowerCase("pt-BR"),
      ) ?? null;
    const clean = match ? match.value : value;
    if (!searchState.institution.includes(clean)) {
      searchState.institution = [...searchState.institution, clean];
      institutionInput.value = "";
      apply();
    }
  };
  institutionInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addInstitution();
    }
  });
  const datalist = h(
    "datalist",
    { id: `${prefix}-institutions` },
    institutions.slice(0, 200).map((item) =>
      h("option", { value: item.value }, item.label || item.value),
    ),
  );
  const institutionGroup = h(
    "div",
    { class: "filter-group", role: "group", "aria-labelledby": `${prefix}-institution-title` },
    h(
      "span",
      { class: "filter-group__title", id: `${prefix}-institution-title` },
      "Instituição",
    ),
    h(
      "div",
      { class: "institution-add" },
      institutionInput,
      h(
        "button",
        { class: "btn btn--ghost btn--small", type: "button", onclick: addInstitution },
        "Adicionar",
      ),
    ),
    datalist,
  );
  form.append(institutionGroup);

  return form;
}

/**
 * Filter drawer for small screens. Returns the dialog element and an opener.
 *
 * @param {{
 *   searchState: Record<string, any>,
 *   resultFacets: Record<string, any>,
 *   globalFacets: Record<string, any>,
 *   onChange: () => void,
 *   activeCount: number,
 * }} options
 */
export function createFilterDrawer({
  searchState,
  resultFacets,
  globalFacets,
  onChange,
  activeCount,
}) {
  const dialog = h("dialog", {
    class: "filters-dialog",
    "aria-labelledby": "filters-drawer-title",
  });

  const closeButton = h(
    "button",
    { class: "icon-btn", type: "button", "aria-label": "Fechar filtros" },
    icon("close", { size: 20 }),
  );
  const close = () => dialog.close();
  closeButton.addEventListener("click", close);
  dialog.addEventListener("cancel", () => {
    // Native Esc already closes it; this keeps focus handling predictable.
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });

  const form = createFilters({
    searchState,
    resultFacets,
    globalFacets,
    prefix: "drawer",
    onChange,
  });

  dialog.append(
    h(
      "div",
      { class: "filters-dialog__head" },
      h("h2", { id: "filters-drawer-title" }, "Filtros"),
      closeButton,
    ),
    form,
    h(
      "div",
      { class: "filters-dialog__footer" },
      h("button", { class: "btn btn--primary", type: "button", onclick: close }, "Ver resultados"),
    ),
  );

  const openButton = h(
    "button",
    {
      class: "btn btn--ghost filters-open",
      type: "button",
      "aria-haspopup": "dialog",
      onclick: () => dialog.showModal(),
    },
    icon("filter", { size: 16 }),
    h("span", {}, "Filtros"),
    activeCount > 0 ? h("span", { class: "badge" }, String(activeCount)) : null,
  );

  return { dialog, openButton };
}
