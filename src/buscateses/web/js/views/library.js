/** "Minha biblioteca": upload, document list and local search. */

import {
  deleteLibraryDocument,
  isNotFound,
  listLibraryDocuments,
  searchLibrary,
  updateLibraryDocument,
  uploadLibrary,
} from "../api.js";
import { confirmDialog } from "../components/dialog.js";
import { createDropzone } from "../components/dropzone.js";
import { pollJob, stageLabel } from "../components/progress.js";
import { showError, showToast } from "../components/toast.js";
import { h, highlighted } from "../dom.js";
import { formatNumber, plural } from "../format.js";
import { icon } from "../icons.js";

const INTRO =
  "Adicione seus livros e artigos em PDF, EPUB, Word ou texto. Eles ficam só neste computador e podem ser pesquisados aqui e pelo seu assistente de IA.";

const STATUS_TEXT = {
  queued: ["Na fila", "O arquivo aguarda a vez de ser processado."],
  processing: ["Processando", "Estamos extraindo o texto e preparando a busca."],
  ready: ["Pronto para pesquisar", ""],
  error: ["Não foi possível ler", ""],
  needs_ocr: [
    "Precisa de reconhecimento de imagem",
    "Este PDF parece ser uma imagem digitalizada; sem texto para pesquisar.",
  ],
};

function pageHead() {
  return [
    h("h1", {}, "Minha biblioteca"),
    h("p", { class: "page-head", style: { marginBottom: "24px" } }, INTRO),
  ];
}

function comingSoon(outlet) {
  outlet.replaceChildren(
    ...pageHead(),
    h(
      "div",
      { class: "notice" },
      h("span", { class: "notice__icon" }, icon("book", { size: 22 })),
      h(
        "div",
        {},
        h("h2", {}, "Em breve"),
        h(
          "p",
          {},
          "A biblioteca de documentos pessoais ainda está sendo preparada nesta versão do BuscaTeses. Quando ela chegar, seus arquivos ficarão apenas neste computador.",
        ),
        h("a", { class: "btn btn--ghost", href: "#/" }, "Pesquisar no catálogo enquanto isso"),
      ),
    ),
  );
  return { title: "Minha biblioteca" };
}

function statusNode(status, error) {
  const [label, explanation] = STATUS_TEXT[status] ?? [status || "Status desconhecido", ""];
  return h(
    "p",
    { class: "document__status" },
    h("span", { class: `status status--${status || "queued"}` }, label),
    explanation || error ? h("span", { class: "muted" }, ` ${explanation || error}`) : null,
  );
}

function documentCard(doc, { onChanged, onRemoved }) {
  const id = String(doc.id);
  const titleInput = h("input", {
    class: "document__title",
    type: "text",
    value: doc.title || doc.filename || "Documento sem título",
    "aria-label": "Título do documento",
    title: "Edite o título e pressione Enter ou saia do campo para salvar",
  });

  const saveTitle = async () => {
    const next = titleInput.value.trim();
    if (!next || next === doc.title) {
      titleInput.value = doc.title || doc.filename || next;
      return;
    }
    try {
      await updateLibraryDocument(id, { title: next });
      doc.title = next;
      showToast("Título atualizado.");
      onChanged();
    } catch (error) {
      titleInput.value = doc.title || "";
      showError(error);
    }
  };
  titleInput.addEventListener("change", saveTitle);
  titleInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      titleInput.blur();
    }
  });

  const bits = [
    doc.author,
    doc.year ? String(doc.year) : "",
    doc.pages ? `${formatNumber(doc.pages)} páginas` : "",
    doc.chunks ? `${formatNumber(doc.chunks)} trechos` : "",
    doc.kind ? String(doc.kind).toLocaleUpperCase("pt-BR") : "",
  ].filter(Boolean);

  const removeButton = h(
    "button",
    { class: "btn btn--quiet", type: "button" },
    icon("trash", { size: 16 }),
    h("span", {}, "Excluir"),
  );
  removeButton.addEventListener("click", async () => {
    const confirmed = await confirmDialog({
      title: "Excluir documento?",
      message: `“${doc.title || doc.filename || "Este documento"}” será removido da sua biblioteca. Esta ação não pode ser desfeita.`,
      confirmLabel: "Excluir documento",
      cancelLabel: "Cancelar",
      danger: true,
    });
    if (!confirmed) return;
    try {
      await deleteLibraryDocument(id);
      showToast("Documento excluído.");
      onRemoved();
    } catch (error) {
      showError(error);
    }
  });

  return h(
    "li",
    { class: "document" },
    h(
      "div",
      {},
      titleInput,
      h("p", { class: "document__meta" }, bits.join(" · ")),
      statusNode(doc.status, doc.error),
    ),
    h("div", { class: "document__actions" }, removeButton),
  );
}

function passageResult(group) {
  return h(
    "div",
    { class: "library-group" },
    h("h3", {}, group.title),
    h(
      "ul",
      { class: "passages" },
      group.passages.map((passage) => {
        const start = passage.page_start ?? passage.page;
        const end = passage.page_end ?? start;
        const pageLabel =
          start && end && Number(end) > Number(start)
            ? `p. ${start}–${end}`
            : start
              ? `p. ${start}`
              : "";
        return h(
          "li",
          { class: "passage" },
          h("p", {}, highlighted(passage.snippet || passage.text || "")),
          h(
            "div",
            {},
            pageLabel ? h("span", { class: "passage__page" }, pageLabel) : null,
            start
              ? h(
                  "a",
                  {
                    class: "btn btn--quiet btn--small",
                    href: `/api/library/documents/${encodeURIComponent(
                      group.docId,
                    )}/file#page=${encodeURIComponent(start)}`,
                    target: "_blank",
                    rel: "noopener noreferrer",
                  },
                  "Abrir na página",
                  h("span", { class: "sr-only" }, " (abre em nova aba)"),
                )
              : null,
          ),
        );
      }),
    ),
  );
}

/** Group search hits by document, at most three passages per document. */
function groupPassages(items) {
  /** @type {Map<string, {docId: string, title: string, passages: Array<any>}>} */
  const groups = new Map();
  for (const item of items) {
    const docId = String(item.doc_id ?? item.document_id ?? "");
    const key = docId || String(item.document_title ?? item.title ?? "desconhecido");
    if (!groups.has(key)) {
      groups.set(key, {
        docId,
        title:
          item.document_title || item.title || item.filename || "Documento da biblioteca",
        passages: [],
        total: 0,
      });
    }
    const group = groups.get(key);
    group.total += 1;
    if (group.passages.length < 3) group.passages.push(item);
  }
  return [...groups.values()];
}

async function renderFullLibrary(outlet, ctx) {
  let documents = await listLibraryDocuments({ signal: ctx.signal });
  if (!Array.isArray(documents)) documents = documents?.documents ?? [];

  const heading = h("h1", {}, "Minha biblioteca");
  const intro = h("p", { class: "page-head", style: { marginBottom: "24px" } }, INTRO);
  const queueHost = h("ul", { class: "upload-queue", "aria-live": "polite" });
  const notices = h("div", { class: "warnings" });
  const documentsHost = h("div", {});
  const documentsCount = h("p", { class: "muted small" });
  const searchResults = h("div", { class: "library-results", "aria-live": "polite" });

  async function refreshDocuments() {
    try {
      const loaded = await listLibraryDocuments({ signal: ctx.signal });
      documents = Array.isArray(loaded) ? loaded : loaded?.documents ?? [];
      renderDocuments();
    } catch (error) {
      showError(error);
    }
  }

  function renderDocuments() {
    const hasReady = documents.some((doc) => doc.status === "ready");
    searchSection.hidden = !hasReady;
    documentsCount.textContent = documents.length
      ? `${formatNumber(documents.length)} ${plural(
          documents.length,
          "documento",
          "documentos",
        )} na biblioteca.`
      : "";
    if (!documents.length) {
      documentsHost.replaceChildren(
        h(
          "p",
          { class: "muted" },
          "Quando você adicionar arquivos, eles aparecem aqui com o status do processamento.",
        ),
      );
      return;
    }
    documentsHost.replaceChildren(
      h(
        "ul",
        { class: "documents", "aria-label": "Documentos da biblioteca" },
        documents.map((doc) =>
          documentCard(doc, {
            onChanged: () => refreshDocuments(),
            onRemoved: () => refreshDocuments(),
          }),
        ),
      ),
    );
  }

  function createUploadItem(filename, message = "Na fila") {
    const status = h("span", { class: "muted small" }, message);
    const bar = h("div", {
      class: "progress",
      role: "progressbar",
      "aria-label": `Progresso de ${filename}`,
      "aria-valuemin": "0",
      "aria-valuemax": "100",
      "aria-valuenow": "0",
    });
    const fill = h("span", { class: "progress__fill", style: { width: "0%" } });
    bar.append(fill);
    const item = h(
      "li",
      { class: "upload-item" },
      h(
        "div",
        { class: "upload-item__head" },
        h("span", { class: "upload-item__name" }, filename),
        status,
      ),
      bar,
    );
    queueHost.append(item);
    return { item, status, bar, fill };
  }

  function failUpload(pending, message) {
    pending.item.classList.add("upload-item--error");
    pending.status.textContent = message;
    pending.bar.remove();
  }

  function trackJob(job, file, pending = null) {
    const jobId = typeof job === "string" ? job : job.id ?? job.job_id;
    const filename =
      (typeof job === "object" && (job.filename || job.name)) || file?.name || "Arquivo";
    if (!jobId) return;
    const entry = pending ?? createUploadItem(filename);
    const { item, status, bar, fill } = entry;

    pollJob(jobId, {
      signal: ctx.signal,
      onUpdate: (snapshot) => {
        const percent =
          typeof snapshot.progress === "number" ? Math.round(snapshot.progress * 100) : null;
        if (percent === null) bar.removeAttribute("aria-valuenow");
        else {
          bar.setAttribute("aria-valuenow", String(percent));
          fill.style.width = `${percent}%`;
        }
        status.textContent = snapshot.message || stageLabel(snapshot.stage);
      },
    })
      .then((snapshot) => {
        if (snapshot.status === "done") {
          status.textContent = "Concluído";
          fill.style.width = "100%";
          bar.setAttribute("aria-valuenow", "100");
          refreshDocuments();
        } else {
          status.textContent =
            snapshot.error || snapshot.message || "Não foi possível processar o arquivo.";
          item.classList.add("upload-item--error");
        }
      })
      .catch((error) => {
        if (error?.name === "AbortError") return;
        status.textContent = error?.message || "Não foi possível acompanhar o arquivo.";
      });
  }

  async function handleFiles(files) {
    notices.replaceChildren();
    const skipped = [];
    let accepted = 0;
    // One file per request, sequentially: this keeps each request below the
    // server's body limit and lets every file show its own progress.
    for (const file of files) {
      if (ctx.signal?.aborted) return;
      const pending = createUploadItem(file.name, "Enviando…");
      const form = new FormData();
      form.append("files", file);
      try {
        const response = await uploadLibrary(form);
        const jobs = response?.jobs ?? [];
        const rejected = response?.skipped ?? [];
        if (jobs.length) {
          trackJob(jobs[0], file, pending);
          accepted += 1;
        } else {
          skipped.push(...rejected);
          failUpload(
            pending,
            rejected[0]?.reason || "O arquivo não foi aceito.",
          );
        }
      } catch (error) {
        if (error?.name === "AbortError") return;
        failUpload(pending, error?.message || "Não foi possível enviar o arquivo.");
      }
    }
    if (skipped.length) {
      notices.append(
        h(
          "div",
          { class: "warning", role: "status" },
          icon("alert", { size: 18 }),
          h(
            "p",
            {},
            "Alguns arquivos não foram aceitos: ",
            skipped
              .map((entry) => `${entry.filename || "arquivo"} (${entry.reason || "formato não aceito"})`)
              .join("; "),
            ".",
          ),
        ),
      );
    }
    if (!accepted && !skipped.length) {
      showToast("Nenhum arquivo para adicionar.");
    } else if (accepted) {
      showToast(
        `${formatNumber(accepted)} ${plural(
          accepted,
          "arquivo entrou",
          "arquivos entraram",
        )} na fila de processamento.`,
      );
    }
  }

  const dropzone = createDropzone({ onFiles: handleFiles });

  const searchInput = h("input", {
    class: "input",
    type: "search",
    id: "library-q",
    placeholder: "Ex.: método participativo",
    "aria-label": "Pesquisar nos documentos da biblioteca",
  });
  const searchForm = h(
    "form",
    {
      class: "inline-form library-search",
      role: "search",
      onsubmit: async (event) => {
        event.preventDefault();
        const query = searchInput.value.trim();
        if (!query) {
          showError("Digite o que você quer encontrar nos seus documentos.");
          searchInput.focus();
          return;
        }
        searchResults.setAttribute("aria-busy", "true");
        try {
          const response = await searchLibrary(query, { signal: ctx.signal });
          const items = Array.isArray(response) ? response : response?.results ?? [];
          if (!items.length) {
            searchResults.replaceChildren(
              h("p", { class: "muted" }, "Nenhum trecho encontrado nos seus documentos."),
            );
            return;
          }
          searchResults.replaceChildren(
            ...groupPassages(items).map((group) => passageResult(group)),
          );
        } catch (error) {
          if (isNotFound(error)) {
            searchResults.replaceChildren(
              h(
                "div",
                { class: "notice" },
                h("span", { class: "notice__icon" }, icon("info", { size: 22 })),
                h(
                  "div",
                  {},
                  h("h3", {}, "Em breve"),
                  h("p", {}, "A busca dentro da biblioteca ainda não está disponível."),
                ),
              ),
            );
          } else if (error?.name !== "AbortError") {
            showError(error);
          }
        } finally {
          searchResults.removeAttribute("aria-busy");
        }
      },
    },
    h(
      "div",
      { class: "field" },
      h("label", { for: "library-q" }, "Pesquisar nos documentos"),
      searchInput,
    ),
    h("button", { class: "btn btn--primary", type: "submit" }, "Pesquisar"),
  );

  const searchSection = h(
    "section",
    { class: "section", "aria-labelledby": "library-search-title" },
    h("h2", { id: "library-search-title" }, "Pesquisar nos documentos"),
    h(
      "p",
      { class: "muted small" },
      "A busca encontra trechos nos arquivos que você adicionou e mostra a página de origem.",
    ),
    searchForm,
    searchResults,
  );

  renderDocuments();

  outlet.replaceChildren(
    heading,
    intro,
    dropzone,
    notices,
    queueHost,
    h("section", { class: "section", "aria-labelledby": "documents-title" },
      h("h2", { id: "documents-title" }, "Seus documentos"),
      documentsCount,
      documentsHost,
    ),
    searchSection,
  );

  return { title: "Minha biblioteca" };
}

/**
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 */
export async function renderLibrary(outlet, ctx) {
  try {
    return await renderFullLibrary(outlet, ctx);
  } catch (error) {
    if (isNotFound(error)) return comingSoon(outlet);
    throw error;
  }
}
