/** Client for the local BuscaTeses HTTP API (SPEC §8). */

const DEFAULT_TIMEOUT = 30_000;
const EXPORT_TIMEOUT = 90_000;
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {number} [status]
   */
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Whether an error is a "not implemented yet" 404 for an optional route. */
export function isNotFound(error) {
  return error instanceof ApiError && error.status === 404;
}

function defaultMessage(status) {
  switch (status) {
    case 400:
      return "Confira os dados da busca e tente novamente.";
    case 403:
      return "A requisição foi bloqueada por segurança. Atualize a página e tente de novo.";
    case 404:
      return "Não encontramos o que você procurava.";
    case 405:
      return "Esta ação ainda não está disponível.";
    case 409:
      return "Já existe uma tarefa em andamento. Aguarde ela terminar.";
    case 422:
      return "Confira os dados e tente novamente.";
    case 503:
      return "Este recurso ainda não está disponível.";
    default:
      return "O BuscaTeses encontrou um erro inesperado. Tente de novo.";
  }
}

function filenameFromDisposition(header) {
  if (!header) return null;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      return encoded[1];
    }
  }
  const simple = /filename="?([^";]+)"?/i.exec(header);
  return simple ? simple[1] : null;
}

/**
 * Perform a request with a timeout and consistent pt-BR errors.
 *
 * @param {string} path
 * @param {{
 *   method?: string,
 *   body?: unknown,
 *   timeout?: number,
 *   responseType?: "json"|"text"|"blob",
 *   signal?: AbortSignal,
 * }} [options]
 */
async function request(path, options = {}) {
  const {
    method = "GET",
    body,
    timeout = DEFAULT_TIMEOUT,
    responseType = "json",
    signal,
  } = options;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const abortFromCaller = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", abortFromCaller, { once: true });
  }

  /** @type {Record<string, string>} */
  const headers = { Accept: "application/json" };
  let payload;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (MUTATING.has(method)) headers["X-BuscaTeses"] = "1";

  try {
    const response = await fetch(path, {
      method,
      headers,
      body: payload,
      signal: controller.signal,
      credentials: "same-origin",
    });

    if (!response.ok) {
      let detail = "";
      try {
        const data = await response.json();
        if (data && typeof data.detail === "string") detail = data.detail;
      } catch {
        /* a non-JSON error still gets a friendly message */
      }
      throw new ApiError(detail || defaultMessage(response.status), response.status);
    }

    if (responseType === "blob") {
      return {
        blob: await response.blob(),
        filename: filenameFromDisposition(response.headers.get("content-disposition")),
      };
    }
    if (responseType === "text") return await response.text();
    const type = response.headers.get("content-type") || "";
    return type.includes("application/json") ? await response.json() : await response.text();
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error?.name === "AbortError") {
      if (signal?.aborted) throw error;
      throw new ApiError(
        "A resposta demorou demais. Verifique se o BuscaTeses continua aberto e tente de novo.",
      );
    }
    throw new ApiError(
      "Não foi possível falar com o BuscaTeses. Verifique se ele continua aberto e tente de novo.",
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

/** Add a query value only when it is meaningful. */
function addValue(params, key, value) {
  if (value === undefined || value === null || value === "") return;
  params.append(key, String(value));
}

/** Build API query parameters from the search state object. */
export function buildSearchParams(state) {
  const params = new URLSearchParams();
  addValue(params, "q", state.q);
  addValue(params, "mode", state.mode);
  addValue(params, "year_min", state.yearMin);
  addValue(params, "year_max", state.yearMax);
  for (const value of state.degree ?? []) addValue(params, "degree", value);
  for (const value of state.uf ?? []) addValue(params, "uf", value);
  for (const value of state.institution ?? []) addValue(params, "institution", value);
  for (const value of state.greatArea ?? []) addValue(params, "great_area", value);
  for (const value of state.knowledgeArea ?? []) addValue(params, "knowledge_area", value);
  addValue(params, "sort", state.sort);
  addValue(params, "page", state.page);
  params.set("page_size", String(state.pageSize ?? 20));
  return params;
}

/* ------------------------------------------------------------------- status */

export const getStatus = () => request("/api/status");

/* ------------------------------------------------------------------- search */

export function search(state, options = {}) {
  return request(`/api/search?${buildSearchParams(state)}`, options);
}

export const getFacets = (options = {}) => request("/api/facets", options);

export const getThesis = (id, options = {}) =>
  request(`/api/theses/${encodeURIComponent(id)}`, options);

export const getSimilar = (id, k = 10, options = {}) =>
  request(`/api/theses/${encodeURIComponent(id)}/similar?k=${k}`, options);

/* ------------------------------------------------------------------ exports */

function exportUrl({ format, state, ids }) {
  const params = new URLSearchParams();
  params.set("format", format);
  if (ids?.length) {
    for (const id of ids) params.append("ids", id);
  } else if (state) {
    const searchParams = buildSearchParams(state);
    for (const [key, value] of searchParams) params.append(key, value);
  }
  return `/api/export?${params}`;
}

export function exportText({ format, ids, state }) {
  return request(exportUrl({ format, ids, state }), {
    responseType: "text",
    timeout: EXPORT_TIMEOUT,
  });
}

export async function exportFile({ format, ids, state }) {
  const result = await request(exportUrl({ format, ids, state }), {
    responseType: "blob",
    timeout: EXPORT_TIMEOUT,
  });
  return result;
}

/** Trigger a browser download for a blob returned by the API. */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || "buscateses";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/* ---------------------------------------------------------------- favorites */

export const listFavorites = (options = {}) => request("/api/favorites", options);

export const addFavorite = (id) =>
  request("/api/favorites", { method: "POST", body: { id } });

export const removeFavorite = (id) =>
  request(`/api/favorites/${encodeURIComponent(id)}`, { method: "DELETE" });

/* ------------------------------------------------------------------- setup */

export const startSetup = (components) =>
  request("/api/setup", { method: "POST", body: { components } });

export const getJob = (id, options = {}) =>
  request(`/api/jobs/${encodeURIComponent(id)}`, options);

export const listJobs = (options = {}) => request("/api/jobs", options);

/* ----------------------------------------------------------------- catalog */

export const getCatalogUpdate = (options = {}) =>
  request("/api/catalog/update", options);

export const startCatalogUpdate = () =>
  request("/api/catalog/update", { method: "POST", body: undefined });

/* ---------------------------------------------------------------- settings */

export const getSettings = (options = {}) => request("/api/settings", options);

export const saveSettings = (payload) =>
  request("/api/settings", { method: "PUT", body: payload });

export const testEmbedding = (text) =>
  request("/api/settings/test-embedding", {
    method: "POST",
    body: text ? { text } : {},
  });

/* ----------------------------------------------------------------- library */

const LIBRARY_DOCS = "/api/library/documents";

export const listLibraryDocuments = (options = {}) => request(LIBRARY_DOCS, options);

export const uploadLibrary = (formData) =>
  request("/api/library/upload", { method: "POST", body: formData, timeout: 120_000 });

export const updateLibraryDocument = (id, payload) =>
  request(`${LIBRARY_DOCS}/${encodeURIComponent(id)}`, { method: "PATCH", body: payload });

export const deleteLibraryDocument = (id) =>
  request(`${LIBRARY_DOCS}/${encodeURIComponent(id)}`, { method: "DELETE" });

export function searchLibrary(query, options = {}) {
  const params = new URLSearchParams({ q: query });
  return request(`/api/library/search?${params}`, options);
}

/* ------------------------------------------------------------ integrations */

export const listIntegrations = (options = {}) => request("/api/integrations", options);

export const connectIntegration = (id) =>
  request(`/api/integrations/${encodeURIComponent(id)}/connect`, {
    method: "POST",
    body: undefined,
  });

export const disconnectIntegration = (id) =>
  request(`/api/integrations/${encodeURIComponent(id)}/disconnect`, {
    method: "POST",
    body: undefined,
  });

/* ------------------------------------------------------------------- remote */

export const getRemote = (options = {}) => request("/api/remote", options);

export const startRemote = (includeLibrary) =>
  request("/api/remote/start", {
    method: "POST",
    body: { include_library: Boolean(includeLibrary) },
  });

export const stopRemote = () =>
  request("/api/remote/stop", { method: "POST", body: undefined });

export const rotateRemote = () =>
  request("/api/remote/rotate", { method: "POST", body: undefined });

/* --------------------------------------------------------------------- misc */

export const quitApp = () => request("/api/app/quit", { method: "POST", body: undefined });

export const openDataDir = () =>
  request("/api/app/open-data-dir", { method: "POST", body: undefined });
