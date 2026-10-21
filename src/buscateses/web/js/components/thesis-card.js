/** The signature result card: a library catalogue card ("ficha"). */

import {
  addFavorite,
  exportText,
  removeFavorite,
} from "../api.js";
import { h, highlighted, safeUrl } from "../dom.js";
import { formatYear, shortDegree, splitList } from "../format.js";
import { icon } from "../icons.js";
import { setFavorite, state } from "../state.js";
import { copyWithToast } from "./copy.js";
import { showError, showToast } from "./toast.js";

const MATCH_LABELS = {
  texto: { label: "Contém suas palavras", hint: "Os termos buscados aparecem no título, no resumo ou nas palavras-chave." },
  significado: { label: "Assunto semelhante", hint: "O tema é próximo do que você buscou, mesmo com outras palavras." },
};
const MAX_KEYWORDS = 4;

function externalLink(url, { className = "btn btn--quiet" } = {}) {
  const safe = safeUrl(url);
  if (!safe) return null;
  return h(
    "a",
    {
      class: className,
      href: safe,
      target: "_blank",
      rel: "noopener noreferrer",
    },
    icon("external", { size: 16 }),
    h("span", {}, "Texto completo"),
    h("span", { class: "sr-only" }, " (link externo, abre em nova aba)"),
  );
}

function favoriteButton(item) {
  const id = String(item.id);
  let saved = state.favorites.has(id);

  const render = (button) => {
    button.setAttribute("aria-pressed", String(saved));
    button.replaceChildren(
      icon("bookmark", { size: 16 }),
      h("span", {}, saved ? "Salvo" : "Salvar"),
    );
    button.title = saved ? "Remover da Minha seleção" : "Adicionar à Minha seleção";
  };

  const button = h("button", {
    class: "btn btn--quiet",
    type: "button",
    "aria-pressed": String(saved),
  });
  render(button);

  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (saved) {
        await removeFavorite(id);
        setFavorite(id, false);
        saved = false;
        showToast("Removido da Minha seleção.");
      } else {
        await addFavorite(id);
        setFavorite(id, true);
        saved = true;
        showToast("Adicionado à Minha seleção.");
      }
      render(button);
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });

  return button;
}

function copyReferenceButton(item) {
  return h(
    "button",
    {
      class: "btn btn--quiet",
      type: "button",
      onclick: (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        copyWithToast(
          () => exportText({ format: "abnt", ids: [String(item.id)] }),
          "Referência ABNT copiada.",
        ).finally(() => {
          button.disabled = false;
        });
      },
    },
    icon("copy", { size: 16 }),
    h("span", {}, "Copiar referência"),
  );
}

function metaLine(item) {
  const parts = [];
  if (item.institution_acronym && item.institution) {
    parts.push(`${item.institution_acronym} — ${item.institution}`);
  } else if (item.institution) {
    parts.push(item.institution);
  } else if (item.institution_acronym) {
    parts.push(item.institution_acronym);
  }
  if (item.program) parts.push(item.program);
  if (!parts.length) return null;
  return h(
    "p",
    { class: "ficha__meta" },
    parts.map((part) => h("span", {}, part)),
  );
}

/**
 * Build a thesis card.
 *
 * @param {Record<string, any>} item
 * @param {{compact?: boolean, actions?: boolean, headingLevel?: 2|3}} [options]
 * @returns {HTMLElement}
 */
export function thesisCard(item, { compact = false, actions = true, headingLevel = 2 } = {}) {
  const id = String(item.id ?? "");
  const title = item.title || "Título não informado";
  const year = formatYear(item.year) || "s.d.";
  const advisors = splitList(item.advisors).join("; ");
  const tags = Array.isArray(item.matched_by) ? item.matched_by : [];

  const rail = h(
    "div",
    { class: "ficha__rail" },
    h("span", { class: "ficha__year" }, year),
    h("span", { class: "ficha__degree" }, shortDegree(item.degree)),
    h("span", { class: "ficha__hole", "aria-hidden": "true" }),
  );

  const author = item.author
    ? h("p", { class: "ficha__author" }, item.author)
    : null;

  if (compact) {
    const meta = [];
    for (const value of [
      item.institution_acronym || item.institution,
      item.program,
      item.knowledge_area,
    ]) {
      if (value && !meta.includes(value)) meta.push(value);
    }
    return h(
      "article",
      { class: "ficha ficha--compact" },
      rail,
      h(
        "div",
        { class: "ficha__body" },
        h(
          `h${headingLevel}`,
          { class: "ficha__title" },
          h("a", { href: `#/tese/${encodeURIComponent(id)}` }, title),
        ),
        author,
        meta.length ? h("p", { class: "ficha__meta" }, meta.join(" · ")) : null,
      ),
    );
  }

  const snippet = item.snippet
    ? h("p", { class: "ficha__snippet" }, highlighted(item.snippet))
    : null;

  const tagNodes = tags.map((tag) => {
    const match = MATCH_LABELS[tag];
    return h(
      "span",
      { class: "tag tag--match", title: match?.hint ?? "" },
      h("span", { class: "tag__check", "aria-hidden": "true" }, "✓"),
      match?.label ?? tag,
    );
  });
  const keywords = splitList(item.keywords).slice(0, MAX_KEYWORDS);
  if (keywords.length) {
    tagNodes.push(h("span", { class: "sr-only" }, "Palavras-chave: "));
    for (const keyword of keywords) {
      tagNodes.push(h("span", { class: "tag tag--keyword" }, keyword));
    }
  }

  const actionNodes = actions
    ? h(
        "div",
        { class: "ficha__actions" },
        h(
          "a",
          { class: "btn btn--quiet", href: `#/tese/${encodeURIComponent(id)}` },
          "Ver ficha completa",
        ),
        favoriteButton(item),
        copyReferenceButton(item),
        h("span", { class: "spacer" }),
        externalLink(item.url),
      )
    : null;

  return h(
    "article",
    { class: "ficha" },
    rail,
    h(
      "div",
      { class: "ficha__body" },
      h(
        `h${headingLevel}`,
        { class: "ficha__title" },
        h("a", { href: `#/tese/${encodeURIComponent(id)}` }, title),
      ),
      author,
      advisors ? h("p", { class: "ficha__meta" }, `Orientação: ${advisors}`) : null,
      metaLine(item),
      snippet,
      tagNodes.length ? h("div", { class: "ficha__tags" }, tagNodes) : null,
      actionNodes,
    ),
  );
}

/**
 * A card for "Minha seleção" with a remove action.
 *
 * @param {Record<string, any>} item
 * @param {{onRemove: (id: string) => Promise<void> | void}} options
 */
export function selectionCard(item, { onRemove }) {
  const card = thesisCard(item);
  const actions = card.querySelector(".ficha__actions");
  if (!actions) return card;

  const removeButton = h(
    "button",
    { class: "btn btn--quiet", type: "button" },
    icon("trash", { size: 16 }),
    h("span", {}, "Remover"),
  );
  removeButton.addEventListener("click", async () => {
    removeButton.disabled = true;
    try {
      await onRemove(String(item.id));
    } finally {
      removeButton.disabled = false;
    }
  });

  const favorite = actions.querySelector('button[aria-pressed]');
  favorite?.remove();
  actions.append(removeButton);
  return card;
}
