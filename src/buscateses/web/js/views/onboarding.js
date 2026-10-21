/** First-run wizard: welcome, installation, optional AI clients, done. */

import {
  connectIntegration,
  getCatalogUpdate,
  isNotFound,
  listIntegrations,
  listJobs,
  saveSettings,
  startSetup,
} from "../api.js";
import { createProgress, pollJob } from "../components/progress.js";
import { h } from "../dom.js";
import { formatBytes, formatNumber, formatYears } from "../format.js";
import { icon } from "../icons.js";
import { loadStatus, state } from "../state.js";

const STEPS = [
  {
    title: "Modelo de embedding local",
    text: "Transforma textos em vetores para a busca por significado. Cerca de 300 MB.",
  },
  {
    title: "Catálogo de teses",
    text: "Metadados oficiais da CAPES para pesquisar.",
  },
  {
    title: "Assistentes de IA",
    text: "Opcional: conecte o assistente que você usa.",
  },
];

/**
 * State of each numbered step, derived from the real installation status.
 *
 * @param {number} view Current wizard view (1–4).
 * @param {{model: boolean, catalog: boolean, integrations: boolean}} readiness
 * @returns {"done" | "active" | "pending"} State of each numbered step.
 */
function stepStates(view, readiness) {
  const done = [readiness.model, readiness.catalog, readiness.integrations];
  return done.map((isDone, index) => {
    if (isDone) return "done";
    // The integrations step only becomes active while the user is on it.
    if (index === 2) return view === 3 ? "active" : "pending";
    return view === 2 ? "active" : "pending";
  });
}

/** Detail line for the catalog step, including record count and version. */
function catalogDetail(status) {
  const catalog = status?.catalog;
  if (!catalog?.installed) return "Pronto";
  const records = Number(catalog.records);
  const parts = ["Pronto"];
  if (records) parts.push(`${formatNumber(records)} registros`);
  const detail = parts.join(" · ");
  return catalog.version ? `${detail}, versão ${catalog.version}` : detail;
}

function stepStateLabel(index, state, status) {
  if (state === "done") return index === 1 ? catalogDetail(status) : "Pronto";
  if (state === "active") return "Em andamento";
  return index === 2 ? "Opcional" : "Aguardando";
}

function overview(view, status, readiness) {
  const states = stepStates(view, readiness);
  return h(
    "ol",
    { class: "steps", "aria-label": "Etapas da instalação" },
    STEPS.map((item, index) => {
      const state = states[index];
      const done = state === "done";
      const active = state === "active";
      return h(
        "li",
        {
          class: `step${done ? " step--done" : ""}${active ? " step--active" : ""}`,
          "aria-current": active && index === states.indexOf("active") ? "step" : null,
        },
        h(
          "span",
          { class: "step__num", "aria-hidden": "true" },
          done ? icon("check", { size: 16 }) : String(index + 1),
        ),
        h(
          "div",
          {},
          h("h2", {}, item.title),
          h("p", {}, item.text),
          h(
            "p",
            { class: `step__state ${done ? "step__state--done" : "step__state--pending"}` },
            stepStateLabel(index, state, status),
          ),
        ),
      );
    }),
  );
}

/** Accept both the current list response and the legacy `{integrations}` shape. */
function normalizeIntegrations(response) {
  if (Array.isArray(response)) return response;
  return response?.integrations ?? [];
}

/**
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 */
export async function renderOnboarding(outlet, ctx) {
  const [loadedStatus, loadedIntegrations] = await Promise.all([
    loadStatus(true).catch(() => state.status),
    listIntegrations({ signal: ctx.signal }).catch(() => []),
  ]);
  let status = loadedStatus;
  let integrations = normalizeIntegrations(loadedIntegrations);
  let catalogUpdate = null;
  let step = 1;
  let busy = false;
  let resumeAttempted = false;

  // With the semantic engine disabled there is nothing to download.
  const modelReady = () =>
    status?.model?.engine === "none" || Boolean(status?.model?.ready);
  const catalogReady = () => Boolean(status?.catalog?.installed);
  const integrationsReady = () => integrations.some((item) => item.connected);
  const setupReady = () => modelReady() && catalogReady();
  const readiness = () => ({
    model: modelReady(),
    catalog: catalogReady(),
    integrations: integrationsReady(),
  });

  /** Catalog download size from the update endpoint, with an offline fallback. */
  function catalogSizeLabel() {
    const bytes = Number(catalogUpdate?.download_bytes);
    return Number.isFinite(bytes) && bytes > 0
      ? `cerca de ${formatBytes(bytes)}`
      : "cerca de 3 GB";
  }

  /** Size of whatever still needs to be downloaded. */
  function pendingDownloadHint() {
    const parts = [];
    if (!modelReady()) parts.push("cerca de 300 MB");
    if (!catalogReady()) parts.push(catalogSizeLabel());
    return parts.length ? parts.join(" + ") : "Tudo instalado neste computador.";
  }

  const panel = h("div", { class: "onboarding__panel" });
  const stepsHost = h("div", {});
  const heading = h("h1", {}, "Primeiros passos");
  const intro = h(
    "div",
    { class: "onboarding__intro" },
    h(
      "p",
      {},
      "Vamos preparar o catálogo e o modelo de embedding no seu computador; você pode pular etapas e voltar depois.",
    ),
  );

  outlet.replaceChildren(
    h("div", { class: "onboarding" }, heading, intro, panel, stepsHost),
  );

  function renderSteps() {
    stepsHost.replaceChildren(overview(step, status, readiness()));
  }

  function focusPanel() {
    const title = panel.querySelector("h2");
    if (!title) return;
    title.setAttribute("tabindex", "-1");
    title.focus({ preventScroll: true });
  }

  function go(next) {
    step = next;
    renderSteps();
    renderPanel();
    focusPanel();
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function skip() {
    state.onboardingSkipped = true;
    try {
      sessionStorage.setItem("buscateses.onboarding-skipped", "1");
    } catch {
      /* the skip still applies until this page is closed */
    }
    ctx.navigate("#/");
  }

  async function refreshStatus() {
    const [nextStatus, nextIntegrations] = await Promise.all([
      loadStatus(true).catch(() => status),
      listIntegrations({ signal: ctx.signal }).catch(() => integrations),
    ]);
    status = nextStatus;
    integrations = normalizeIntegrations(nextIntegrations);
    return status;
  }

  /**
   * Fetch the catalog download size without delaying the page. Offline or a
   * 503 keeps the "cerca de 3 GB" fallback and logs only at debug level.
   */
  async function loadCatalogUpdate() {
    let info = null;
    try {
      info = await getCatalogUpdate({ signal: ctx.signal });
    } catch (error) {
      console.debug("Catalog update check unavailable.", error?.message ?? error);
      return;
    }
    if (!info) return;
    catalogUpdate = info;
    renderSteps();
    if (step <= 2 && !busy) renderPanel();
  }

  /* ------------------------------------------------------------ welcome */

  function welcomePanel() {
    const ready = setupReady();
    panel.replaceChildren(
      h("section", { "aria-label": "Boas-vindas" },
        h(
          "div",
          { class: "privacy-note" },
          icon("lock", { size: 20 }),
          h(
            "p",
            {},
            "Nada do que você pesquisar sai deste computador. A conexão serve apenas para baixar o catálogo e o modelo de embedding.",
          ),
        ),
        h(
          "div",
          { class: "onboarding__actions onboarding__actions--start" },
          h(
            "button",
            {
              class: "btn btn--primary",
              type: "button",
              onclick: ready ? () => ctx.navigate("#/") : () => go(2),
            },
            ready ? "Ir para a busca" : "Baixar e instalar",
          ),
          h("span", { class: "muted small" }, pendingDownloadHint()),
          ready
            ? null
            : h(
                "button",
                { class: "btn btn--quiet", type: "button", onclick: skip },
                "Pular por agora",
              ),
        ),
      ),
    );
  }

  /* -------------------------------------------------------------- setup */

  function setupPanel() {
    const allReady = setupReady();

    const modelCheck = h("input", {
      type: "checkbox",
      name: "component",
      value: "model",
      checked: !modelReady(),
      disabled: modelReady() || busy,
    });
    const catalogCheck = h("input", {
      type: "checkbox",
      name: "component",
      value: "catalog",
      checked: !catalogReady(),
      disabled: catalogReady() || busy,
    });

    const progressHost = h("div", {});
    const stateLine = h("p", { class: "muted small", role: "status" });
    const actions = h("div", { class: "onboarding__actions" });

    const searchButton = h(
      "button",
      { class: "btn btn--primary", type: "button", onclick: () => ctx.navigate("#/") },
      "Ir para a busca",
    );
    const connectButton = h(
      "button",
      { class: "btn btn--quiet", type: "button", onclick: () => go(3) },
      "Conectar assistentes de IA",
    );
    const backButton = h(
      "button",
      { class: "btn btn--quiet", type: "button", onclick: () => go(1) },
      "Voltar",
    );

    const startButton = h(
      "button",
      { class: "btn btn--primary", type: "button" },
      allReady ? "Ir para a busca" : "Baixar e instalar",
    );

    async function finish() {
      await refreshStatus();
      renderSteps();
      stateLine.textContent = "Instalação concluída. Você já pode pesquisar.";
      actions.replaceChildren(searchButton, connectButton);
    }

    async function runJob(jobId) {
      busy = true;
      startButton.textContent = "Baixar e instalar";
      startButton.disabled = true;
      backButton.disabled = true;
      const progress = createProgress({ label: "Progresso da instalação" });
      progressHost.replaceChildren(progress.element);
      try {
        const job = await pollJob(jobId, { onUpdate: (snapshot) => progress.update(snapshot) });
        if (job.status === "done") {
          await finish();
        } else {
          progress.showError(
            job.error || job.message || "A instalação não terminou. Tente de novo.",
          );
          startButton.textContent = "Tentar de novo";
        }
      } catch (error) {
        if (error?.name !== "AbortError") {
          progress.showError(error?.message || "Não foi possível acompanhar a instalação.");
          startButton.textContent = "Tentar de novo";
        }
      } finally {
        busy = false;
        startButton.disabled = false;
        backButton.disabled = false;
      }
    }

    startButton.addEventListener("click", async () => {
      if (setupReady()) {
        ctx.navigate("#/");
        return;
      }
      const pending = [];
      if (!modelReady()) pending.push("model");
      if (!catalogReady()) pending.push("catalog");
      const checked = new Set(
        [...panel.querySelectorAll('input[name="component"]:checked')].map(
          (node) => node.value,
        ),
      );
      // Never ask the server to download something that is already installed.
      const components = pending.filter((component) => checked.has(component));
      if (!components.length) {
        stateLine.textContent = "Marque ao menos um item para baixar.";
        return;
      }
      stateLine.textContent = "Preparando o download…";
      try {
        const { job_id: jobId } = await startSetup(components);
        await runJob(jobId);
      } catch (error) {
        stateLine.textContent =
          error?.status === 409
            ? "Já existe uma instalação em andamento. Aguarde ela terminar nesta janela."
            : error?.message || "Não foi possível iniciar a instalação.";
      }
    });

    const rows = [
      h(
        "li",
        { class: "download-item" },
        h(
          "label",
          { class: "check" },
          modelCheck,
          h("span", {}, "Modelo de embedding local"),
          h(
            "span",
            { class: "check__count" },
            modelReady() ? "Pronto" : "cerca de 300 MB",
          ),
        ),
        h(
          "p",
          { class: "muted small" },
          modelReady()
            ? "O modelo de embedding já está pronto neste computador."
            : "Transforma textos em vetores e permite encontrar trabalhos parecidos mesmo com outras palavras.",
        ),
      ),
      h(
        "li",
        { class: "download-item" },
        h(
          "label",
          { class: "check" },
          catalogCheck,
          h("span", {}, "Catálogo de teses e dissertações"),
          h(
            "span",
            { class: "check__count" },
            catalogReady() ? "Pronto" : catalogSizeLabel(),
          ),
        ),
        h(
          "p",
          { class: "muted small" },
          catalogReady() && status?.catalog?.records
            ? `${formatNumber(status.catalog.records)} registros entre ${formatYears(
                status.catalog.years,
              )}.`
            : "Metadados oficiais da CAPES, com busca por texto e filtros.",
        ),
      ),
    ];

    if (allReady) {
      stateLine.textContent = "O modelo de embedding e o catálogo já estão instalados.";
      actions.append(searchButton, connectButton);
    } else {
      actions.append(startButton, backButton);
    }

    panel.replaceChildren(
      h("section", { "aria-labelledby": "setup-title" },
        h("h2", { id: "setup-title" }, "Baixar modelo de embedding e catálogo"),
        h(
          "p",
          {},
          "Você pode baixar os dois agora ou apenas o que falta. O progresso mostra o tempo estimado e continua mesmo se você trocar de tela.",
        ),
        h("ul", { class: "download-list" }, rows),
        h(
          "p",
          { class: "settings-note" },
          "O download precisa de conexão. Se ele falhar, tente de novo; o que já foi baixado é aproveitado.",
        ),
        actions,
        stateLine,
        progressHost,
      ),
    );

    if (!allReady && !busy && !resumeAttempted) {
      resumeAttempted = true;
      resumeActiveJob(
        progressHost,
        actions,
        searchButton,
        connectButton,
        backButton,
        stateLine,
      );
    }
  }

  /* ------------------------------------------------------------ resume job */

  async function resumeActiveJob(
    progressHost,
    actions,
    searchButton,
    connectButton,
    backButton,
    stateLine,
  ) {
    let jobs = [];
    try {
      jobs = await listJobs({ signal: ctx.signal });
    } catch {
      return;
    }
    const active = (Array.isArray(jobs) ? jobs : jobs?.jobs ?? []).find(
      (job) => job.status === "queued" || job.status === "running",
    );
    if (!active || !["setup", "catalog_update"].includes(active.kind)) return;
    busy = true;
    backButton.disabled = true;
    stateLine.textContent = "Retomando a instalação que já estava em andamento…";
    const progress = createProgress({ label: "Progresso da instalação" });
    progressHost.replaceChildren(progress.element);
    try {
      const job = await pollJob(active.id, { onUpdate: (snapshot) => progress.update(snapshot) });
      if (job.status === "done") {
        await refreshStatus();
        stateLine.textContent = "Instalação concluída. Você já pode pesquisar.";
        actions.replaceChildren(searchButton, connectButton);
        backButton.disabled = false;
        renderSteps();
      } else {
        progress.showError(job.error || job.message || "A instalação não terminou.");
      }
    } catch (error) {
      if (error?.name !== "AbortError") {
        progress.showError(error?.message || "Não foi possível acompanhar a instalação.");
      }
    } finally {
      busy = false;
      backButton.disabled = false;
    }
  }

  /* ------------------------------------------------------------ connect */

  async function connectPanel() {
    const content = [];
    try {
      const response = await listIntegrations({ signal: ctx.signal });
      integrations = normalizeIntegrations(response);
    } catch (error) {
      if (!isNotFound(error)) throw error;
      content.push(
        h(
          "div",
          { class: "notice" },
          h("span", { class: "notice__icon" }, icon("spark", { size: 22 })),
          h(
            "div",
            {},
            h("h2", {}, "Não foi possível listar os aplicativos"),
            h(
              "p",
              {},
              "Você pode continuar e configurar os assistentes depois na tela Conectar assistentes de IA.",
            ),
          ),
        ),
      );
    }

    if (integrations.length) {
      const rank = (integration) =>
        integration.connected ? 0 : integration.detected === false ? 2 : 1;
      const ordered = [...integrations].sort((left, right) => rank(left) - rank(right));
      content.push(
        h(
          "ul",
          { class: "integration-grid", "aria-label": "Assistentes de IA" },
          ordered.map((integration) => {
            const name = integration.name || integration.id;
            const missing = integration.detected === false && !integration.connected;
            return h(
              "li",
              { class: "integration" },
              h(
                "div",
                { class: "integration__head" },
                h("h3", {}, name),
                h(
                  "span",
                  {
                    class: `integration__state ${
                      integration.connected
                        ? "integration__state--on"
                        : missing
                          ? "integration__state--missing"
                          : "integration__state--off"
                    }`,
                  },
                  integration.connected
                    ? "Conectado"
                    : missing
                      ? "Não instalado neste computador"
                      : "Instalado — não conectado",
                ),
              ),
              integration.connected
                ? h(
                    "p",
                    {},
                    `Conectado. Feche e abra o ${name} para ativar.`,
                  )
                : null,
              integration.kind === "auto" || integration.kind === "command"
                ? missing
                  ? h(
                      "p",
                      { class: "muted small" },
                      integration.install_url
                        ? h(
                            "a",
                            {
                              class: "text-link",
                              href: integration.install_url,
                              target: "_blank",
                              rel: "noopener noreferrer",
                            },
                            `Baixar o ${name}`,
                          )
                        : "Instale o aplicativo para conectar automaticamente.",
                    )
                  : (() => {
                      const note = h("p", { class: "muted small", "aria-live": "polite" });
                      const button = h(
                        "button",
                        {
                          class: integration.connected ? "btn btn--quiet" : "btn btn--primary",
                          type: "button",
                          onclick: async () => {
                            button.disabled = true;
                            try {
                              await connectIntegration(integration.id);
                              integration.connected = true;
                              button.textContent = "Conectado";
                              button.disabled = true;
                              note.textContent = `Conectado. Feche e abra o ${name} para ativar.`;
                              renderSteps();
                            } catch (connectError) {
                              note.textContent =
                                connectError?.message || "Não foi possível conectar agora.";
                              button.disabled = false;
                            }
                          },
                        },
                        integration.connected ? "Conectado" : "Conectar",
                      );
                      if (integration.connected) button.disabled = true;
                      return h("div", { class: "integration__actions" }, button, note);
                    })()
                : h(
                    "p",
                    { class: "muted small" },
                    integration.instructions ||
                      "Adicione manualmente o servidor MCP nas preferências do aplicativo.",
                  ),
            );
          }),
        ),
      );
    }

    content.push(
      h(
        "div",
        { class: "onboarding__actions" },
        h(
          "button",
          { class: "btn btn--primary", type: "button", onclick: () => go(4) },
          "Continuar",
        ),
        h(
          "button",
          { class: "btn btn--quiet", type: "button", onclick: () => go(4) },
          "Pular esta etapa",
        ),
      ),
    );

    panel.replaceChildren(
      h("section", { "aria-labelledby": "connect-step-title" },
        h("h2", { id: "connect-step-title" }, "Conectar assistentes de IA"),
        h(
          "p",
          {},
          "Esta etapa é opcional. Conecte agora um aplicativo instalado neste computador. Para usar o ChatGPT ou o Claude.ai na web, abra a tela Conectar assistentes de IA.",
        ),
        ...content,
      ),
    );
    if (step === 3) focusPanel();
  }

  /* --------------------------------------------------------------- done */

  function donePanel() {
    const missing = Boolean(status?.setup_required);
    const catalog = status?.catalog ?? {};
    const records = catalog.records ? formatNumber(catalog.records) : "";
    const summary = records
      ? `${records} registros${catalog.years ? ` entre ${formatYears(catalog.years)}` : ""} prontos para pesquisar.`
      : "A busca está pronta para os primeiros passos.";

    saveSettings({ onboarding_done: true }).catch(() => {
      /* the wizard still finishes when settings cannot be saved */
    });

    panel.replaceChildren(
      h(
        "div",
        { class: "done-panel" },
        h("span", { class: "done-panel__icon" }, icon("check", { size: 28 })),
        h("h2", {}, missing ? "Quase tudo pronto" : "Tudo pronto"),
        h(
          "p",
          {},
          missing
            ? "Ainda falta instalar o catálogo ou o modelo de embedding. Você pode voltar à instalação ou continuar e pesquisar quando terminar."
            : `${summary} Você pode salvar trabalhos na Minha seleção e exportar referências quando quiser.`,
        ),
        h(
          "div",
          { class: "onboarding__actions", style: { justifyContent: "center" } },
          missing
            ? h(
                "button",
                { class: "btn btn--primary", type: "button", onclick: () => go(2) },
                "Voltar à instalação",
              )
            : h("a", { class: "btn btn--primary", href: "#/" }, "Ir para a busca"),
          h("a", { class: "btn btn--ghost", href: "#/conectar" }, "Conectar assistentes de IA"),
        ),
      ),
    );
  }

  /* -------------------------------------------------------------- render */

  function renderPanel() {
    if (step === 1) welcomePanel();
    else if (step === 2) setupPanel();
    else if (step === 3) {
      panel.replaceChildren(
        h(
          "div",
          { class: "boot-loading", role: "status" },
          h("span", { class: "boot-loading__dot", "aria-hidden": "true" }),
          "Carregando assistentes…",
        ),
      );
      connectPanel().catch((error) => {
        if (error?.name === "AbortError") return;
        panel.replaceChildren(
          h("p", { role: "alert" }, error?.message || "Não foi possível carregar os assistentes."),
          h(
            "div",
            { class: "onboarding__actions" },
            h(
              "button",
              { class: "btn btn--primary", type: "button", onclick: () => go(4) },
              "Continuar",
            ),
          ),
        );
      });
    } else donePanel();
    if (step >= 4) renderSteps();
  }

  renderSteps();
  renderPanel();
  loadCatalogUpdate();

  return { title: "Primeiros passos" };
}
