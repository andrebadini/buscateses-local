/** "Minha seleção": saved theses with bulk export. */

import { getThesis, listFavorites, removeFavorite } from "../api.js";
import { createExportMenu } from "../components/export-menu.js";
import { selectionCard } from "../components/thesis-card.js";
import { showError, showToast } from "../components/toast.js";
import { h } from "../dom.js";
import { formatNumber, plural } from "../format.js";
import { icon } from "../icons.js";
import { loadFavorites, setFavorite, state } from "../state.js";

/**
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 */
export async function renderSelection(outlet, ctx) {
  await loadFavorites(true).catch(() => state.favorites);
  const favorites = await listFavorites({ signal: ctx.signal });
  const ids = favorites.map((favorite) => favorite.id);

  const details = await Promise.all(
    ids.map((id) =>
      getThesis(id, { signal: ctx.signal })
        .then((item) => ({ id, item }))
        .catch(() => ({ id, item: null })),
    ),
  );
  let items = details.filter((entry) => entry.item).map((entry) => entry.item);
  const missing = details.filter((entry) => !entry.item).length;

  const heading = h("h1", {}, "Minha seleção");
  const intro = h(
    "p",
    { class: "page-head", style: { marginBottom: "24px" } },
    items.length
      ? `${formatNumber(items.length)} ${plural(
          items.length,
          "trabalho salvo",
          "trabalhos salvos",
        )} neste computador.`
      : "Os trabalhos que você salvar aparecem aqui.",
  );

  const listHost = h("div", {});
  const actionsHost = h("div", { class: "button-row" });

  if (missing) {
    actionsHost.append(
      h(
        "p",
        { class: "muted small", style: { flexBasis: "100%" } },
        `${formatNumber(missing)} ${plural(
          missing,
          "trabalho salvo não está",
          "trabalhos salvos não estão",
        )} mais no catálogo.`,
      ),
    );
  }

  function renderList() {
    if (!items.length) {
      listHost.replaceChildren(
        h(
          "div",
          { class: "empty-state" },
          icon("bookmark", { size: 28, class: "empty-state__icon" }),
          h("h2", {}, "Sua seleção está vazia"),
          h(
            "p",
            {},
            "Durante a busca, use o botão “Salvar” para guardar trabalhos aqui. Depois você pode exportar a lista em CSV, BibTeX, RIS ou ABNT.",
          ),
          h("a", { class: "btn btn--primary", href: "#/" }, "Fazer uma busca"),
        ),
      );
      exportSlot.replaceChildren();
      return;
    }

    const list = h(
      "ol",
      { class: "fichas", "aria-label": "Trabalhos salvos" },
      items.map((item) =>
        h(
          "li",
          {},
          selectionCard(item, {
            onRemove: async (id) => {
              try {
                await removeFavorite(id);
                setFavorite(id, false);
                items = items.filter((entry) => String(entry.id) !== String(id));
                showToast("Trabalho removido da Minha seleção.");
                intro.textContent = items.length
                  ? `${formatNumber(items.length)} ${plural(
                      items.length,
                      "trabalho salvo",
                      "trabalhos salvos",
                    )} neste computador.`
                  : "Os trabalhos que você salvar aparecem aqui.";
                renderList();
              } catch (error) {
                showError(error);
              }
            },
          }),
        ),
      ),
    );
    listHost.replaceChildren(list);
    exportSlot.replaceChildren(
      createExportMenu({
        ids: () => items.map((item) => String(item.id)),
        label: "Exportar seleção",
      }),
    );
  }

  const exportSlot = h("div", {});
  actionsHost.append(exportSlot);

  outlet.replaceChildren(heading, intro, actionsHost, listHost);
  renderList();

  return { title: "Minha seleção" };
}
