/** Search state <-> URL query string (the URL is the source of truth). */

export const MODES = new Set(["hybrid", "text", "semantic"]);
export const SORTS = new Set(["relevance", "year_desc", "year_asc"]);

export const MODE_LABELS = {
  hybrid: "Inteligente",
  text: "Palavras exatas",
  semantic: "Por significado",
};

export const MODE_HINTS = {
  hybrid:
    "Combina as palavras digitadas com o significado. Costuma trazer os resultados mais úteis.",
  text:
    "Procura exatamente as palavras digitadas. Use aspas para uma frase e “-” para excluir um termo.",
  semantic:
    "Busca pelo sentido, mesmo quando as palavras não aparecem no título ou no resumo. Precisa do modelo de embedding.",
};

const PAGE_SIZE = 20;

function validMode(value) {
  return MODES.has(value) ? value : "hybrid";
}

function validSort(value) {
  return SORTS.has(value) ? value : "relevance";
}

function optionalInt(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number.parseInt(value, 10);
  return Number.isFinite(number) ? number : null;
}

/** Build the canonical search state from URLSearchParams. */
export function parseSearchState(urlParams) {
  return {
    q: (urlParams.get("q") ?? "").trim(),
    mode: validMode(urlParams.get("mode")),
    yearMin: optionalInt(urlParams.get("year_min")),
    yearMax: optionalInt(urlParams.get("year_max")),
    degree: urlParams.getAll("degree").filter(Boolean),
    uf: urlParams.getAll("uf").filter(Boolean),
    institution: urlParams.getAll("institution").filter(Boolean),
    greatArea: urlParams.getAll("great_area").filter(Boolean),
    knowledgeArea: urlParams.getAll("knowledge_area").filter(Boolean),
    sort: validSort(urlParams.get("sort")),
    page: Math.max(1, optionalInt(urlParams.get("page")) ?? 1),
    pageSize: PAGE_SIZE,
  };
}

/** Serialize the search state into the hash query string (without "#/"). */
export function toQueryString(searchState) {
  const params = new URLSearchParams();
  if (searchState.q) params.set("q", searchState.q);
  if (searchState.mode && searchState.mode !== "hybrid") params.set("mode", searchState.mode);
  if (searchState.yearMin !== null && searchState.yearMin !== undefined) {
    params.set("year_min", String(searchState.yearMin));
  }
  if (searchState.yearMax !== null && searchState.yearMax !== undefined) {
    params.set("year_max", String(searchState.yearMax));
  }
  for (const value of searchState.degree ?? []) params.append("degree", value);
  for (const value of searchState.uf ?? []) params.append("uf", value);
  for (const value of searchState.institution ?? []) params.append("institution", value);
  for (const value of searchState.greatArea ?? []) params.append("great_area", value);
  for (const value of searchState.knowledgeArea ?? []) params.append("knowledge_area", value);
  if (searchState.sort && searchState.sort !== "relevance") params.set("sort", searchState.sort);
  if (searchState.page && searchState.page > 1) params.set("page", String(searchState.page));
  return params.toString();
}

/** Full hash for a search state. */
export function searchHash(searchState) {
  const query = toQueryString(searchState);
  return query ? `#/?${query}` : "#/";
}

/** Count the active filters (not the query or sort). */
export function countActiveFilters(searchState) {
  let count = 0;
  if (searchState.yearMin !== null && searchState.yearMin !== undefined) count += 1;
  if (searchState.yearMax !== null && searchState.yearMax !== undefined) count += 1;
  count += searchState.degree?.length ?? 0;
  count += searchState.uf?.length ?? 0;
  count += searchState.institution?.length ?? 0;
  count += searchState.greatArea?.length ?? 0;
  count += searchState.knowledgeArea?.length ?? 0;
  return count;
}

/** Clone the state, clearing every filter but keeping query/mode/sort. */
export function withoutFilters(searchState) {
  return {
    ...searchState,
    yearMin: null,
    yearMax: null,
    degree: [],
    uf: [],
    institution: [],
    greatArea: [],
    knowledgeArea: [],
    page: 1,
  };
}
