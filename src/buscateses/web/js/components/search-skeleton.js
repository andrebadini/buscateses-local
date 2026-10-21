/** Placeholder cards shown while the first search loads the semantic model. */

import { h } from "../dom.js";

export const FIRST_SEARCH_HINT =
  "Preparando a busca por significado — a primeira busca leva alguns segundos.";

/** One gray card with the same footprint as a result card. */
function skeletonCard() {
  return h(
    "li",
    { class: "skeleton-card" },
    h(
      "div",
      { class: "skeleton-card__rail" },
      h("span", { class: "skeleton-block skeleton-block--year" }),
      h("span", { class: "skeleton-block skeleton-block--degree" }),
    ),
    h(
      "div",
      { class: "skeleton-card__body" },
      h("span", { class: "skeleton-block skeleton-block--title" }),
      h("span", { class: "skeleton-block skeleton-block--title skeleton-block--short" }),
      h("span", { class: "skeleton-block skeleton-block--line" }),
      h("span", { class: "skeleton-block skeleton-block--line skeleton-block--short" }),
      h("span", { class: "skeleton-block skeleton-block--meta" }),
    ),
  );
}

/**
 * Build the animated search placeholder.
 *
 * The hint appears after two seconds, when a cold semantic model starts to
 * make the wait noticeable. Reduced-motion users get static gray cards.
 *
 * @returns {{element: HTMLElement, dispose: () => void}}
 */
export function createSearchSkeleton() {
  const hint = h(
    "p",
    { class: "search-skeleton__hint", hidden: true },
    FIRST_SEARCH_HINT,
  );
  const timer = setTimeout(() => {
    if (hint.isConnected) hint.hidden = false;
  }, 2000);

  const element = h(
    "div",
    {
      class: "search-skeleton",
      role: "status",
      "aria-live": "polite",
      "aria-label": "Preparando os resultados da busca",
    },
    h(
      "ol",
      { class: "fichas search-skeleton__list", "aria-hidden": "true" },
      skeletonCard(),
      skeletonCard(),
      skeletonCard(),
    ),
    hint,
  );

  return { element, dispose: () => clearTimeout(timer) };
}
