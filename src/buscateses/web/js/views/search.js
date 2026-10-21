/** Search screen: initial hero, results, filters, export and pagination. */

import { search } from "../api.js";
import { createExportMenu } from "../components/export-menu.js";
import { createFilterDrawer, createFilters } from "../components/filters.js";
import { createPagination } from "../components/pagination.js";
import { thesisCard } from "../components/thesis-card.js";
import { showError } from "../components/toast.js";
import { h } from "../dom.js";
import { formatNumber, formatTook, formatYears, plural } from "../format.js";
import { icon } from "../icons.js";
import {
  MODE_HINTS,
  MODE_LABELS,
  MODES,
  countActiveFilters,
  parseSearchState,
  searchHash,
  withoutFilters,
} from "../search-params.js";
import { loadFacets, loadFavorites, loadStatus, state } from "../state.js";

const SUGGESTIONS = [
  "letramento racial na educação infantil",
  "recuperação judicial de empresas",
  "agricultura familiar no cerrado",
  "inteligência artificial no Judiciário",
];

function leadText(status) {
  const catalog = status?.catalog;
  if (!catalog?.installed) {
    return "Instale o catálogo para pesquisar entre as teses e dissertações brasileiras.";
  }
  const records = formatNumber(catalog.records);
  const years = formatYears(catalog.years);
  return years
    ? `${records} trabalhos defendidos entre ${years}, disponíveis neste computador.`
    : `${records} registros disponíveis neste computador.`;
}

function subheading(text) {
  return h("p", { class: "search-hero__lead" }, text);
}

/**
 * Initial screen with the big search field.
 *
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 * @param {Record<string, any>} initial
 */
async function renderHero(outlet, ctx, initial) {
  const status = await loadStatus().catch(() => state.status);
  const hint = h("p", { class: "mode-hint", id: "mode-hint" }, MODE_HINTS[initial.mode]);

  const input = h("input", {
    class: "search-form__input",
    id: "search-q",
    name: "q",
    type: "search",
    placeholder: "Ex.: letramento racial na educação infantil",
    autocomplete: "off",
    "aria-describedby": "mode-hint search-tip",
  });

  const modeInputs = Object.entries(MODE_LABELS).map(([mode, label]) => {
    const radio = h("input", {
      type: "radio",
      name: "mode",
      value: mode,
      checked: initial.mode === mode,
    });
    radio.addEventListener("change", () => {
      hint.textContent = MODE_HINTS[mode];
    });
    return h("label", {}, radio, h("span", {}, label));
  });

  const form = h(
    "form",
    {
      class: "search-form",
      role: "search",
      "aria-labelledby": "hero-title",
      onsubmit: (event) => {
        event.preventDefault();
        const query = input.value.trim();
        if (!query) {
          showError("Digite uma palavra ou frase para buscar.");
          input.focus();
          return;
        }
        const mode = form.querySelector('input[name="mode"]:checked')?.value ?? "hybrid";
        window.location.hash = searchHash({ ...initial, q: query, mode, page: 1 });
      },
    },
    h(
      "div",
      { class: "search-form__row" },
      icon("search", { size: 22, class: "search-form__icon" }),
      input,
      h("button", { class: "btn btn--primary", type: "submit" }, "Buscar"),
    ),
    h(
      "fieldset",
      { class: "mode-choice" },
      h("legend", {}, "Como buscar"),
      ...modeInputs,
    ),
    hint,
    h(
      "p",
      { class: "search-tip", id: "search-tip" },
      "Dica: use aspas para procurar uma frase exata e ",
      h("code", {}, "-"),
      " antes de uma palavra para excluí-la.",
    ),
  );

  const suggestions = h(
    "div",
    { class: "suggestions" },
    h("span", { class: "suggestions__label" }, "Experimente:"),
    SUGGESTIONS.map((suggestion) =>
      h(
        "button",
        {
          class: "suggestion",
          type: "button",
          onclick: () => {
            input.value = suggestion;
            form.requestSubmit();
          },
        },
        suggestion,
      ),
    ),
  );

  const children = [
    h("h1", { id: "hero-title" }, "Pesquise teses e dissertações brasileiras"),
    subheading(leadText(status)),
    form,
    suggestions,
  ];

  if (status && !status.catalog?.installed) {
    children.splice(
      2,
      0,
      h(
        "div",
        { class: "notice", style: { marginBottom: "24px" } },
        h("span", { class: "notice__icon" }, icon("alert", { size: 22 })),
        h(
          "div",
          {},
          h("h2", {}, "O catálogo ainda não está instalado"),
          h(
            "p",
            {},
            "Enquanto isso, você pode conhecer o BuscaTeses e conectar seus assistentes de IA.",
          ),
          h("a", { class: "btn btn--primary", href: "#/primeiros-passos" }, "Instalar agora"),
        ),
      ),
    );
  }

  outlet.replaceChildren(h("section", { class: "search-hero" }, children));
  input.focus({ preventScroll: true });
  return { title: "Buscar" };
}

function warningNodes(warnings, relaxed) {
  const nodes = [];
  const list = Array.isArray(warnings) ? warnings : [];
  if (list.includes("semantic_unavailable")) {
    nodes.push(
      h(
        "div",
        { class: "warning", role: "status" },
        icon("alert", { size: 18 }),
        h(
          "p",
          {},
          "A busca por significado está desligada porque o modelo de embedding não está instalado. ",
          h("a", { href: "#/primeiros-passos" }, "Instalar agora"),
          ".",
        ),
      ),
    );
  }
  if (list.includes("catalog_missing")) {
    nodes.push(
      h(
        "div",
        { class: "warning", role: "status" },
        icon("alert", { size: 18 }),
        h(
          "p",
          {},
          "O catálogo ainda não foi instalado. ",
          h("a", { href: "#/primeiros-passos" }, "Instalar agora"),
          ".",
        ),
      ),
    );
  }
  if (relaxed) {
    nodes.push(
      h(
        "div",
        { class: "warning", role: "status" },
        icon("info", { size: 18 }),
        h(
          "p",
          {},
          "Não encontramos todos os termos juntos. Estes resultados trazem pelo menos parte deles.",
        ),
      ),
    );
  }
  const known = new Set(["semantic_unavailable", "catalog_missing"]);
  const unknown = list.filter((warning) => !known.has(warning));
  if (unknown.length) {
    nodes.push(
      h(
        "div",
        { class: "warning", role: "status" },
        icon("info", { size: 18 }),
        h("p", {}, "A busca funcionou com algumas limitações."),
      ),
    );
  }
  return nodes;
}

/** Summary line, e.g. "cerca de 400 mais relevantes · em 42 ms". */
function fillSummary(node, result) {
  const total = Number(result.total) || 0;
  const children = [];
  if (result.total_capped) {
    children.push(
      "mais de ",
      h("strong", {}, formatNumber(total)),
      " ",
      plural(total, "resultado", "resultados"),
    );
  } else if (result.total_is_estimate) {
    children.push("cerca de ", h("strong", {}, formatNumber(total)), " mais relevantes");
  } else {
    children.push(
      h("strong", {}, formatNumber(total)),
      " ",
      plural(total, "resultado", "resultados"),
    );
  }
  if (result.took_ms !== undefined) {
    children.push(h("span", { class: "muted" }, ` · em ${formatTook(result.took_ms)}`));
  }
  node.replaceChildren(...children);
}

/**
 * Build the filter chips and the "clear filters" action.
 */
function createChipBar(searchState, onChange) {
  const bar = h("div", { class: "active-filters" });
  const current = searchState;
  const notify = onChange;

  const chip = (label, remove) => {
    const button = h(
      "button",
      { type: "button", "aria-label": `Remover filtro: ${label}` },
      icon("close", { size: 14 }),
    );
    button.addEventListener("click", () => {
      remove();
      notify();
    });
    return h("span", { class: "chip" }, h("span", {}, label), button);
  };

  const render = () => {
    const items = [];
    if (current.yearMin !== null && current.yearMax !== null) {
      items.push(
        chip(`Ano: ${current.yearMin} a ${current.yearMax}`, () => {
          current.yearMin = null;
          current.yearMax = null;
        }),
      );
    } else if (current.yearMin !== null) {
      items.push(
        chip(`A partir de ${current.yearMin}`, () => {
          current.yearMin = null;
        }),
      );
    } else if (current.yearMax !== null) {
      items.push(
        chip(`Até ${current.yearMax}`, () => {
          current.yearMax = null;
        }),
      );
    }
    const groups = [
      ["degree", "Nível", current.degree],
      ["uf", "UF", current.uf],
      ["institution", "Instituição", current.institution],
      ["great_area", "Grande área", current.greatArea],
      ["knowledge_area", "Área", current.knowledgeArea],
    ];
    for (const [, label, values] of groups) {
      for (const value of values) {
        items.push(
          chip(`${label}: ${value}`, () => {
            const key =
              label === "Nível"
                ? "degree"
                : label === "UF"
                  ? "uf"
                  : label === "Instituição"
                    ? "institution"
                    : label === "Grande área"
                      ? "greatArea"
                      : "knowledgeArea";
            current[key] = current[key].filter((item) => item !== value);
          }),
        );
      }
    }
    if (items.length) {
      items.push(
        h(
          "span",
          { class: "chip chip--clear" },
          h(
            "button",
            {
              type: "button",
              onclick: () => {
                const clean = withoutFilters(current);
                Object.assign(current, clean);
                notify();
              },
            },
            "Limpar filtros",
          ),
        ),
      );
    }
    bar.replaceChildren(...items);
    bar.hidden = items.length === 0;
    return items.length;
  };

  return { element: bar, render };
}

/**
 * Search results screen.
 *
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 * @param {URLSearchParams} urlParams
 */
export async function renderSearch(outlet, ctx, urlParams) {
  const searchState = parseSearchState(urlParams);
  if (!searchState.q) {
    state.lastSearchHash = searchHash(searchState);
    return renderHero(outlet, ctx, searchState);
  }

  state.lastSearchHash = searchHash(searchState);

  const [firstResult, globalFacets] = await Promise.all([
    search(searchState, { signal: ctx.signal }),
    loadFacets(),
    loadFavorites().catch(() => state.favorites),
  ]);

  const heading = h("h1", { class: "sr-only", id: "results-title" }, "Resultados da busca");

  /* Compact search bar -------------------------------------------------- */
  const compactInput = h("input", {
    class: "search-form__input",
    id: "search-q",
    name: "q",
    type: "search",
    value: searchState.q,
    autocomplete: "off",
    "aria-label": "Buscar no catálogo",
  });
  const modeSelect = h(
    "select",
    { class: "select", "aria-label": "Modo de busca" },
    Object.entries(MODE_LABELS).map(([mode, label]) =>
      h("option", { value: mode, selected: searchState.mode === mode }, label),
    ),
  );

  /* Results regions ----------------------------------------------------- */
  const summary = h("p", {
    class: "result-summary",
    "aria-live": "polite",
    "aria-atomic": "true",
  });
  const chips = createChipBar(searchState, () => {
    updateUrl();
    runSearch();
  });
  const warnings = h("div", { class: "warnings", "aria-live": "polite" });
  const resultsRegion = h("div", { class: "results-region" });
  const paginationHost = h("div", {});
  const sortSelect = h(
    "select",
    { class: "select", id: "sort", "aria-label": "Ordenação dos resultados" },
    h("option", { value: "relevance", selected: searchState.sort === "relevance" }, "Relevância"),
    h("option", { value: "year_desc", selected: searchState.sort === "year_desc" }, "Mais recentes"),
    h("option", { value: "year_asc", selected: searchState.sort === "year_asc" }, "Mais antigos"),
  );
  sortSelect.addEventListener("change", () => {
    searchState.sort = sortSelect.value;
    searchState.page = 1;
    updateUrl();
    runSearch();
  });

  let filterForm = null;
  let drawer = null;
  let firstEntry = true;
  let requestToken = 0;

  const exportMenu = createExportMenu({ getState: () => searchState });

  const controls = h(
    "div",
    { class: "result-controls" },
    h(
      "span",
      { class: "sort-control" },
      h("label", { for: "sort" }, "Ordenar"),
      sortSelect,
    ),
    exportMenu,
  );

  const toolbarForm = h(
    "form",
    {
      class: "search-toolbar",
      role: "search",
      "aria-label": "Buscar no catálogo",
      onsubmit: (event) => {
        event.preventDefault();
        const query = compactInput.value.trim();
        if (!query) {
          showError("Digite uma palavra ou frase para buscar.");
          compactInput.focus();
          return;
        }
        searchState.q = query;
        searchState.mode = modeSelect.value;
        searchState.page = 1;
        updateUrl();
        runSearch();
      },
    },
    h(
      "div",
      { class: "search-form__row" },
      icon("search", { size: 20, class: "search-form__icon" }),
      compactInput,
      h("button", { class: "btn btn--primary", type: "submit" }, "Buscar"),
    ),
    modeSelect,
  );

  const resultsBar = h(
    "div",
    { class: "results-bar" },
    summary,
    controls,
  );

  const resultsSection = h(
    "section",
    { class: "results-panel", "aria-labelledby": "results-title" },
    resultsBar,
    chips.element,
    warnings,
    resultsRegion,
    paginationHost,
  );

  const aside = h(
    "aside",
    { class: "filters-panel", "aria-label": "Filtros da busca" },
    h("h2", {}, "Filtros"),
  );

  outlet.replaceChildren(
    heading,
    toolbarForm,
    h("div", { class: "search-layout" }, aside, resultsSection),
  );

  /* URL and fetching ---------------------------------------------------- */
  function updateUrl() {
    state.lastSearchHash = searchHash(searchState);
    try {
      history.replaceState(null, "", state.lastSearchHash);
    } catch {
      /* ignore browsers that block history rewrites */
    }
  }

  function renderWarnings(result) {
    warnings.replaceChildren(...warningNodes(result.warnings, result.relaxed));
  }

  function renderResults(result, { animate = false } = {}) {
    fillSummary(summary, result);
    renderWarnings(result);
    chips.render();

    if (!result.results?.length) {
      paginationHost.replaceChildren();
      const active = countActiveFilters(searchState);
      resultsRegion.replaceChildren(
        h(
          "div",
          { class: "empty-state" },
          icon("search", { size: 28, class: "empty-state__icon" }),
          h("h2", {}, `Não encontramos trabalhos para “${searchState.q}”`),
          h(
            "p",
            {},
            "Tente usar menos palavras, remover filtros ou escrever os termos de outra forma.",
          ),
          h(
            "div",
            { class: "button-row", style: { justifyContent: "center" } },
            active
              ? h(
                  "button",
                  {
                    class: "btn btn--ghost",
                    type: "button",
                    onclick: () => {
                      Object.assign(searchState, withoutFilters(searchState));
                      updateUrl();
                      runSearch();
                    },
                  },
                  "Limpar filtros",
                )
              : null,
            h("a", { class: "btn btn--primary", href: "#/" }, "Fazer outra busca"),
          ),
        ),
      );
      return;
    }

    const list = h(
      "ol",
      { class: "fichas", "aria-label": "Resultados da busca" },
      result.results.map((item) =>
        h("li", {}, thesisCard(item, { headingLevel: 2 })),
      ),
    );
    if (animate && firstEntry) {
      list.classList.add("fichas--entering");
      firstEntry = false;
      setTimeout(() => list.classList.remove("fichas--entering"), 700);
    }
    resultsRegion.replaceChildren(list);

    const pagination = createPagination(result, (page) => {
      searchState.page = page;
      updateUrl();
      runSearch();
      resultsSection.scrollIntoView({ block: "start", behavior: "auto" });
    });
    paginationHost.replaceChildren(pagination ?? "");
  }

  function renderSearchFailure(error) {
    resultsRegion.replaceChildren(
      h(
        "div",
        { class: "notice notice--error", role: "alert" },
        h("span", { class: "notice__icon" }, icon("alert", { size: 22 })),
        h(
          "div",
          {},
          h("h2", {}, "Não foi possível concluir a busca"),
          h("p", {}, error?.message || "Tente de novo em instantes."),
          h("button", { class: "btn btn--ghost", type: "button", onclick: () => runSearch() }, "Tentar de novo"),
        ),
      ),
    );
  }

  async function runSearch({ animate = false } = {}) {
    requestToken += 1;
    const token = requestToken;
    resultsRegion.setAttribute("aria-busy", "true");
    try {
      const result = await search(searchState, { signal: ctx.signal });
      if (token !== requestToken || ctx.signal?.aborted) return;
      renderResults(result, { animate });
      document.title = `Busca por “${searchState.q}” — BuscaTeses`;
    } catch (error) {
      if (error?.name === "AbortError" || ctx.signal?.aborted) return;
      if (token !== requestToken) return;
      renderSearchFailure(error);
    } finally {
      if (token === requestToken) resultsRegion.setAttribute("aria-busy", "false");
    }
  }

  /* Filters ------------------------------------------------------------- */
  const filterOptions = {
    searchState,
    resultFacets: firstResult.facets ?? {},
    globalFacets: globalFacets ?? {},
    onChange: () => {
      updateUrl();
      runSearch();
    },
  };
  filterForm = createFilters({ ...filterOptions, prefix: "side" });
  aside.append(filterForm);

  drawer = createFilterDrawer({
    ...filterOptions,
    prefix: "drawer",
    activeCount: countActiveFilters(searchState),
  });
  controls.append(drawer.openButton);
  outlet.append(drawer.dialog);

  renderResults(firstResult, { animate: true });
  resultsRegion.setAttribute("aria-busy", "false");

  if (firstResult.facets) {
    chips.render();
  }

  return { title: `Busca por “${searchState.q}”` };
}
