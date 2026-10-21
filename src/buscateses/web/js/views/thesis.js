/** Full thesis record ("ficha completa"). */

import {
  addFavorite,
  downloadBlob,
  exportFile,
  exportText,
  getSimilar,
  getThesis,
  removeFavorite,
} from "../api.js";
import { copyButton } from "../components/copy.js";
import { thesisCard } from "../components/thesis-card.js";
import { showError, showToast } from "../components/toast.js";
import { h, safeUrl } from "../dom.js";
import { formatDate, formatNumber, formatYear, shortDegree, splitList } from "../format.js";
import { icon } from "../icons.js";
import { parseSearchState, searchHash } from "../search-params.js";
import { loadFavorites, setFavorite, state } from "../state.js";

function backButton(ctx) {
  const button = h(
    "button",
    { class: "back-link", type: "button" },
    icon("chevronLeft", { size: 16 }),
    h("span", {}, "Voltar"),
  );
  button.addEventListener("click", () => {
    if (state.previousHash && state.previousHash !== window.location.hash) {
      history.back();
    } else {
      ctx.navigate(state.lastSearchHash || "#/");
    }
  });
  return button;
}

function saveButton(item) {
  const id = String(item.id);
  let saved = state.favorites.has(id);
  const button = h("button", {
    class: "btn btn--ghost",
    type: "button",
    "aria-pressed": String(saved),
  });
  const render = () => {
    button.replaceChildren(
      icon("bookmark", { size: 16 }),
      h("span", {}, saved ? "Salvo na Minha seleção" : "Salvar na Minha seleção"),
    );
    button.setAttribute("aria-pressed", String(saved));
  };
  render();
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
      render();
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function scholarUrl(item) {
  const query = [item.title, item.author].filter(Boolean).join(" ").trim();
  return query
    ? `https://scholar.google.com/scholar?q=${encodeURIComponent(query)}`
    : "";
}

function fullTextLink(item) {
  const url = safeUrl(item.url);
  if (url) {
    return h(
      "a",
      {
        class: "btn btn--primary",
        href: url,
        target: "_blank",
        rel: "noopener noreferrer",
      },
      icon("external", { size: 16 }),
      h("span", {}, "Texto completo"),
      h("span", { class: "sr-only" }, " (link externo, abre em nova aba)"),
    );
  }
  const fallback = scholarUrl(item);
  if (!fallback) return null;
  return h(
    "a",
    {
      class: "btn btn--quiet",
      href: fallback,
      target: "_blank",
      rel: "noopener noreferrer",
    },
    icon("external", { size: 16 }),
    h("span", {}, "Procurar o texto completo"),
    h(
      "span",
      { class: "sr-only" },
      " no Google Acadêmico (link externo, abre em nova aba)",
    ),
  );
}

function metaRow(label, value) {
  if (value === null || value === undefined || value === "" || value === 0) return [];
  return [h("dt", {}, label), h("dd", {}, String(value))];
}

/** Human-readable label for the record's origin (manifest source codes). */
export function sourceLabel(source) {
  if (source === "capes-catalogo") return "Catálogo de Teses da CAPES";
  if (source === "bdtd") return "BDTD (IBICT)";
  return "Dados abertos da CAPES";
}

function fichaPanel(item) {
  const pages =
    item.pages && Number(item.pages) > 0
      ? `${formatNumber(item.pages)} páginas`
      : "";
  const institution = [item.institution_acronym, item.institution]
    .filter(Boolean)
    .join(" — ");
  const rows = [
    ...metaRow("Ano", formatYear(item.year)),
    ...metaRow("Data da defesa", formatDate(item.defense_date)),
    ...metaRow("Nível", item.degree),
    ...metaRow("Instituição", institution),
    ...metaRow("UF", item.uf),
    ...metaRow("Região", item.region),
    ...metaRow("Programa", item.program),
    ...metaRow("Grande área", item.great_area),
    ...metaRow("Área do conhecimento", item.knowledge_area),
    ...metaRow("Área de avaliação", item.evaluation_area),
    ...metaRow("Área de concentração", item.concentration_area),
    ...metaRow("Linha de pesquisa", item.research_line),
    ...metaRow("Idioma", item.language),
    ...metaRow("Páginas", pages),
    ...metaRow("Biblioteca", item.library),
    ...metaRow("Fonte dos dados", sourceLabel(item.source)),
  ];
  return h(
    "section",
    { class: "panel", "aria-labelledby": "ficha-title" },
    h("h2", { id: "ficha-title" }, "Ficha"),
    h("dl", { class: "meta-list" }, rows),
  );
}

function citationPanel(item, citation) {
  const id = String(item.id);
  const citationText =
    citation || "Não foi possível gerar a referência agora. Tente novamente em instantes.";
  const panel = h(
    "section",
    { class: "panel", "aria-labelledby": "citation-title" },
    h("h2", { id: "citation-title" }, "Como citar"),
    h("p", { class: "citation", id: "citation-text" }, citationText),
  );

  const actions = h("div", { class: "panel-actions" });
  if (citation) {
    actions.append(
      copyButton(citation, {
        label: "Copiar referência",
        successMessage: "Referência ABNT copiada.",
      }),
    );
  }
  for (const [format, label, fallback] of [
    ["bibtex", "Baixar BibTeX", "buscateses.bib"],
    ["ris", "Baixar RIS", "buscateses.ris"],
  ]) {
    const button = h(
      "button",
      { class: "btn btn--ghost", type: "button" },
      icon("download", { size: 16 }),
      h("span", {}, label),
    );
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await exportFile({ format, ids: [id] });
        downloadBlob(result.blob, result.filename || fallback);
        showToast("O arquivo foi gerado.");
      } catch (error) {
        showError(error);
      } finally {
        button.disabled = false;
      }
    });
    actions.append(button);
  }
  panel.append(actions);
  return panel;
}

function abstractSection(item) {
  const pt = item.abstract?.trim();
  const en = item.abstract_en?.trim();
  if (!pt && !en) {
    return h(
      "section",
      { class: "section" },
      h("h2", {}, "Resumo"),
      h("p", { class: "muted" }, "Este trabalho não tem resumo disponível no catálogo."),
    );
  }

  const text = h("p", { class: "abstract-text" }, pt || en);
  let current = "pt";

  const switchButtons = [];
  const makeButton = (language, label, available) => {
    if (!available) return null;
    const button = h(
      "button",
      { type: "button", "aria-pressed": String(current === language) },
      label,
    );
    button.addEventListener("click", () => {
      current = language;
      text.textContent = language === "pt" ? pt || en : en || pt;
      for (const node of switchButtons) {
        node.setAttribute("aria-pressed", String(node.dataset.language === language));
      }
    });
    button.dataset.language = language;
    switchButtons.push(button);
    return button;
  };

  return h(
    "section",
    { class: "section", "aria-labelledby": "abstract-title" },
    h("h2", { id: "abstract-title" }, "Resumo"),
    pt && en
      ? h(
          "div",
          { class: "abstract-switch", role: "group", "aria-label": "Idioma do resumo" },
          makeButton("pt", "Português", true),
          makeButton("en", "Abstract", true),
        )
      : null,
    text,
  );
}

function keywordsSection(item, ctx) {
  const keywords = splitList(item.keywords);
  if (!keywords.length) return null;
  return h(
    "section",
    { class: "section", "aria-labelledby": "keywords-title" },
    h("h2", { id: "keywords-title" }, "Palavras-chave"),
    h(
      "ul",
      { class: "keywords", "aria-label": "Palavras-chave, cada uma inicia uma busca" },
      keywords.map((keyword) =>
        h(
          "li",
          {},
          h(
            "button",
            {
              type: "button",
              onclick: () => {
                const fresh = parseSearchState(new URLSearchParams());
                fresh.q = keyword;
                ctx.navigate(searchHash(fresh));
              },
            },
            keyword,
          ),
        ),
      ),
    ),
  );
}

async function similarSection(item, ctx) {
  const section = h(
    "section",
    { class: "section", "aria-labelledby": "similar-title" },
    h("h2", { id: "similar-title" }, "Trabalhos semelhantes"),
  );
  try {
    const items = await getSimilar(String(item.id), 6, { signal: ctx.signal });
    if (!items?.length) {
      section.append(
        h(
          "p",
          { class: "muted" },
          "Não encontramos trabalhos semelhantes a este no catálogo.",
        ),
      );
      return section;
    }
    section.append(
      h(
        "ol",
        { class: "fichas", "aria-label": "Trabalhos semelhantes" },
        items
          .filter((similar) => similar.id !== item.id)
          .map((similar) =>
            h("li", {}, thesisCard(similar, { compact: true, headingLevel: 3 })),
          ),
      ),
    );
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    section.append(
      h(
        "div",
        { class: "notice" },
        h("span", { class: "notice__icon" }, icon("info", { size: 22 })),
        h(
          "div",
          {},
          h("h3", {}, "Os trabalhos semelhantes não estão disponíveis"),
          h(
            "p",
            {},
            "Eles dependem do modelo de embedding. ",
            h("a", { href: "#/primeiros-passos" }, "Instalar o modelo de embedding agora"),
            ".",
          ),
        ),
      ),
    );
  }
  return section;
}

/**
 * Render the full thesis page.
 *
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 * @param {string} id
 */
export async function renderThesis(outlet, ctx, id) {
  const item = await getThesis(id, { signal: ctx.signal });
  await loadFavorites().catch(() => state.favorites);

  const citationPromise = exportText({ format: "abnt", ids: [id] }).catch(() => "");

  const advisors = splitList(item.advisors);
  const authors = item.author || "Autoria não informada";
  const metaBits = [
    formatYear(item.year),
    shortDegree(item.degree),
    item.institution_acronym || item.institution || "",
  ].filter(Boolean);

  const actions = h(
    "div",
    { class: "thesis-actions" },
    saveButton(item),
    fullTextLink(item),
  );

  const header = h(
    "header",
    { class: "thesis-header" },
    h("h1", {}, item.title || "Título não informado"),
    h("p", { class: "thesis-byline" }, authors),
    advisors.length
      ? h("p", { class: "thesis-byline" }, `Orientação: ${advisors.join("; ")}`)
      : null,
    h("p", { class: "thesis-meta" }, metaBits.join(" · ")),
    actions,
  );

  const citation = (await citationPromise).trim();

  outlet.replaceChildren(
    backButton(ctx),
    header,
    abstractSection(item),
    keywordsSection(item, ctx),
    h(
      "div",
      { class: "detail-grid" },
      fichaPanel(item),
      citationPanel(item, citation),
    ),
    await similarSection(item, ctx),
  );

  return { title: item.title || "Ficha da tese" };
}
