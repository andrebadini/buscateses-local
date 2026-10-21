/** Small shared application state (no framework, no hidden magic). */

import { getFacets, getStatus, listFavorites } from "./api.js";

export const state = {
  /** @type {Record<string, any> | null} */
  status: null,
  /** @type {Record<string, any> | null} */
  facets: null,
  /** @type {Record<string, any> | null} */
  settings: null,
  /** @type {Set<string>} */
  favorites: new Set(),
  favoritesLoaded: false,
  /** Last search hash, used by the thesis page's back button. */
  lastSearchHash: "#/",
  /** Previous internal hash, tracked by the router. */
  previousHash: "",
  /** Result of the catalog update check, memoized for the session. */
  catalogUpdate: null,
  catalogUpdateChecked: false,
  /** Set when the user deliberately skips the first-run wizard. */
  onboardingSkipped: false,
};

let statusPromise = null;
let facetsPromise = null;
let favoritesPromise = null;

/** Load /api/status once; pass force=true to refresh. */
export function loadStatus(force = false) {
  if (!force && state.status) return Promise.resolve(state.status);
  if (!force && statusPromise) return statusPromise;
  statusPromise = getStatus()
    .then((data) => {
      state.status = data;
      return data;
    })
    .finally(() => {
      statusPromise = null;
    });
  return statusPromise;
}

/** Load /api/facets once (global facet counts). */
export function loadFacets(force = false) {
  if (!force && state.facets) return Promise.resolve(state.facets);
  if (!force && facetsPromise) return facetsPromise;
  facetsPromise = getFacets()
    .then((data) => {
      state.facets = data ?? {};
      return state.facets;
    })
    .catch(() => {
      // Filters are optional; the search still works without global facets.
      state.facets = {};
      return state.facets;
    })
    .finally(() => {
      facetsPromise = null;
    });
  return facetsPromise;
}

/** Load favorites once; pass force=true after bulk changes. */
export function loadFavorites(force = false) {
  if (!force && state.favoritesLoaded) return Promise.resolve(state.favorites);
  if (!force && favoritesPromise) return favoritesPromise;
  favoritesPromise = listFavorites()
    .then((items) => {
      state.favorites = new Set((items ?? []).map((item) => item.id));
      state.favoritesLoaded = true;
      return state.favorites;
    })
    .finally(() => {
      favoritesPromise = null;
    });
  return favoritesPromise;
}

/** Update the cached favorite set from a toggle. */
export function setFavorite(id, saved) {
  if (saved) state.favorites.add(id);
  else state.favorites.delete(id);
}
