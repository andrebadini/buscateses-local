/** "Configurações": appearance, embedding engine, catalog and data location. */

import {
  isVLibrasEnabled,
  setVLibrasEnabled,
  updateVLibrasControls,
} from "../accessibility.js";
import {
  getCatalogUpdate,
  getSettings,
  openDataDir,
  saveSettings,
  startCatalogUpdate,
  startSetup,
  testEmbedding,
} from "../api.js";
import { createProgress, pollJob } from "../components/progress.js";
import { showError, showToast } from "../components/toast.js";
import { h } from "../dom.js";
import { formatBytes, formatDate, formatNumber, formatYear } from "../format.js";
import { icon } from "../icons.js";
import { loadStatus, state } from "../state.js";
import { getThemePreference, setThemePreference } from "../theme.js";

function pageHead() {
  return [
    h("h1", {}, "Configurações"),
    h(
      "p",
      { class: "page-head", style: { marginBottom: "24px" } },
      "Ajuste a aparência, o motor de embedding e os dados do BuscaTeses. As preferências ficam neste computador.",
    ),
  ];
}

/* --------------------------------------------------------------- aparência */

function appearanceCard() {
  const feedback = h("p", { class: "muted small", role: "status" }, "");
  const options = [
    ["system", "Seguir o sistema", "Alterna entre claro e escuro conforme o seu computador."],
    ["light", "Claro", "Fundo de papel, tinta escura e dourado discreto."],
    ["dark", "Escuro", "Fundo profundo, com contraste preservado para leitura."],
  ];
  const current = getThemePreference();

  const rows = options.map(([value, label, description]) => {
    const radio = h("input", {
      type: "radio",
      name: "appearance",
      value,
      checked: current === value,
    });
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      setThemePreference(value);
      feedback.textContent = "Tema salvo neste navegador.";
    });
    return h(
      "label",
      { class: "radio-row" },
      radio,
      h("span", {}, h("strong", {}, label), h("span", {}, description)),
    );
  });

  return h(
    "section",
    { class: "settings-card", "aria-labelledby": "appearance-title" },
    h("h2", { id: "appearance-title" }, "Aparência"),
    h(
      "p",
      {},
      "A escolha fica salva neste navegador. Fora daqui, o BuscaTeses segue a preferência do sistema.",
    ),
    h("div", { role: "radiogroup", "aria-labelledby": "appearance-title" }, ...rows),
    feedback,
  );
}

/* ------------------------------------------------------------ acessibilidade */

function accessibilityCard() {
  const enabled = isVLibrasEnabled();
  const feedback = h("p", { class: "field__hint", role: "status" }, "");
  const checkbox = h("input", {
    type: "checkbox",
    id: "libras-enabled",
    "data-libras-toggle": "",
    checked: enabled,
  });
  checkbox.addEventListener("change", () => {
    const value = setVLibrasEnabled(checkbox.checked);
    updateVLibrasControls(value);
    feedback.textContent = value
      ? "Tradutor de Libras ativado. O botão “Libras” também fica no cabeçalho."
      : "Tradutor de Libras desativado.";
  });

  return h(
    "section",
    { class: "settings-card", "aria-labelledby": "accessibility-title" },
    h("h2", { id: "accessibility-title" }, "Acessibilidade"),
    h(
      "label",
      { class: "checkbox-row", for: "libras-enabled" },
      checkbox,
      h(
        "span",
        {},
        h("strong", {}, "Tradutor de Libras (VLibras)"),
        h(
          "span",
          {},
          "O VLibras é um recurso do governo federal carregado da internet (vlibras.gov.br) somente quando você ativa esta opção.",
        ),
      ),
    ),
    feedback,
    h(
      "p",
      { class: "settings-note" },
      "Com a opção desligada, nenhuma parte da página é enviada ao site do VLibras. Você também pode ativar ou desativar pelo botão “Libras” no cabeçalho.",
    ),
  );
}

/* --------------------------------------------------------------- embeddings */

function engineCard(settings, status) {
  const embedding = settings?.embedding ?? {};
  const initialEngine = embedding.engine === "local" ? "local" : "external";
  const feedback = h("p", { class: "result-badge", role: "status", hidden: true }, "");
  const modelReady = Boolean(status?.model?.ready);
  const modelDownloading = Boolean(status?.model?.downloading);
  const modelState = h(
    "p",
    { class: "field__hint", role: "status" },
    modelReady
      ? "O modelo de embedding local está instalado e pronto para uso."
      : modelDownloading
        ? "O modelo de embedding local está sendo baixado em outra tarefa."
        : "O modelo de embedding local ainda não foi instalado.",
  );
  const modelProgress = h("div", {});
  const modelButton = h(
    "button",
    { class: "btn btn--ghost", type: "button" },
    icon("download", { size: 16 }),
    h("span", {}, "Baixar modelo de embedding"),
  );
  modelButton.hidden = modelReady || modelDownloading;
  modelButton.addEventListener("click", async () => {
    modelButton.disabled = true;
    modelButton.hidden = true;
    const progress = createProgress({
      label: "Progresso do download do modelo de embedding",
    });
    modelProgress.replaceChildren(progress.element);
    try {
      const { job_id: jobId } = await startSetup(["model"]);
      const job = await pollJob(jobId, { onUpdate: (snapshot) => progress.update(snapshot) });
      if (job.status === "done") {
        showToast("Modelo de embedding instalado.");
        await loadStatus(true).catch(() => state.status);
        modelState.textContent =
          "O modelo de embedding local está instalado e pronto para uso.";
        modelProgress.replaceChildren();
      } else {
        progress.showError(job.error || job.message || "O download não terminou. Tente de novo.");
        modelButton.hidden = false;
      }
    } catch (error) {
      if (error?.name !== "AbortError") {
        progress.showError(
          error?.status === 409
            ? "Já existe uma instalação em andamento. Acompanhe em Primeiros passos."
            : error?.message || "Não foi possível baixar o modelo de embedding.",
        );
        modelButton.hidden = false;
      }
    } finally {
      modelButton.disabled = false;
    }
  });

  const variantSelect = h(
    "select",
    { class: "select", id: "local-variant" },
    h(
      "option",
      { value: "quantized", selected: embedding.local_variant !== "fp32" },
      "Equilibrada — cerca de 300 MB",
    ),
    h(
      "option",
      { value: "fp32", selected: embedding.local_variant === "fp32" },
      "Máxima — arquivos maiores e mais memória",
    ),
  );

  const localPanel = h(
    "div",
    { class: "engine-panel" },
    h(
      "div",
      { class: "field" },
      h("label", { for: "local-variant" }, "Qualidade do modelo de embedding local"),
      variantSelect,
      h(
        "p",
        { class: "field__hint" },
        "O EmbeddingGemma é o modelo de embedding local. Ele é baixado uma única vez e usado sem conexão.",
      ),
    ),
    modelState,
    modelButton,
    modelProgress,
  );

  const baseUrlInput = h("input", {
    class: "input",
    id: "embed-url",
    type: "url",
    inputmode: "url",
    placeholder: "http://127.0.0.1:1234/v1",
    value: embedding.base_url || "",
    autocomplete: "off",
  });
  const modelInput = h("input", {
    class: "input",
    id: "embed-model",
    type: "text",
    placeholder: "embeddinggemma-300m",
    value: embedding.model || "",
    autocomplete: "off",
  });
  const keyInput = h("input", {
    class: "input",
    id: "embed-key",
    type: "password",
    placeholder: "Opcional",
    value: embedding.api_key || "",
    autocomplete: "new-password",
  });

  const externalPanel = h(
    "div",
    { class: "engine-panel", hidden: initialEngine !== "external" },
    h(
      "div",
      { class: "field" },
      h("label", { for: "embed-url" }, "Endereço do servidor"),
      baseUrlInput,
      h(
        "p",
        { class: "field__hint" },
        "Exemplos: http://127.0.0.1:1234/v1 (LM Studio) ou http://127.0.0.1:11434/v1 (Ollama).",
      ),
    ),
    h(
      "div",
      { class: "field" },
      h("label", { for: "embed-model" }, "Nome do modelo de embedding"),
      modelInput,
      h(
        "p",
        { class: "field__hint" },
        "Use um modelo de embedding compatível com 768 dimensões.",
      ),
    ),
    h(
      "div",
      { class: "field" },
      h("label", { for: "embed-key" }, "Chave de acesso (opcional)"),
      keyInput,
      h("p", { class: "field__hint" }, "Guardada apenas neste computador."),
    ),
  );

  const localRadio = h("input", {
    type: "radio",
    name: "engine",
    value: "local",
    checked: initialEngine === "local",
  });
  const externalRadio = h("input", {
    type: "radio",
    name: "engine",
    value: "external",
    checked: initialEngine === "external",
  });

  const syncPanels = () => {
    localPanel.hidden = !localRadio.checked;
    externalPanel.hidden = !externalRadio.checked;
  };
  localRadio.addEventListener("change", syncPanels);
  externalRadio.addEventListener("change", syncPanels);
  syncPanels();

  const saveButton = h(
    "button",
    { class: "btn btn--primary", type: "button" },
    "Salvar configurações",
  );
  const testButton = h(
    "button",
    { class: "btn btn--ghost", type: "button" },
    icon("spark", { size: 16 }),
    h("span", {}, "Testar motor"),
  );

  const collect = () => ({
    embedding: {
      engine: localRadio.checked ? "local" : "openai",
      local_variant: variantSelect.value,
      base_url: baseUrlInput.value.trim(),
      model: modelInput.value.trim(),
      api_key: keyInput.value,
    },
  });

  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;
    feedback.hidden = true;
    try {
      const saved = await saveSettings(collect());
      state.settings = saved;
      await loadStatus(true).catch(() => state.status);
      showToast("Configurações salvas.");
      feedback.hidden = false;
      feedback.className = "result-badge result-badge--ok";
      feedback.textContent = "Configurações salvas.";
    } catch (error) {
      showError(error);
    } finally {
      saveButton.disabled = false;
    }
  });

  testButton.addEventListener("click", async () => {
    testButton.disabled = true;
    feedback.hidden = false;
    feedback.className = "result-badge";
    feedback.textContent = "Testando o motor…";
    try {
      // Persist first so the probe uses the values currently on screen.
      state.settings = await saveSettings(collect());
      await loadStatus(true).catch(() => state.status);
      const result = await testEmbedding();
      feedback.className = `result-badge ${result?.ok ? "result-badge--ok" : "result-badge--error"}`;
      const dimensions = result?.dimensions
        ? ` (${formatNumber(result.dimensions)} dimensões)`
        : "";
      feedback.textContent = `${result?.message || (result?.ok ? "O motor respondeu." : "O motor não respondeu.")}${dimensions}`;
    } catch (error) {
      feedback.className = "result-badge result-badge--error";
      feedback.textContent = error?.message || "Não foi possível testar o motor.";
    } finally {
      testButton.disabled = false;
    }
  });

  return h(
    "section",
    { class: "settings-card", "aria-labelledby": "engine-title" },
    h("h2", { id: "engine-title" }, "Motor de embedding"),
    h(
      "p",
      {},
      "O motor de embedding transforma textos em vetores para a busca por significado. Assim, você encontra trabalhos parecidos mesmo quando usa outras palavras.",
    ),
    h(
      "div",
      { role: "radiogroup", "aria-labelledby": "engine-title" },
      h(
        "label",
        { class: "radio-row" },
        localRadio,
        h(
          "span",
          {},
          h("strong", {}, "Neste computador (recomendado)"),
          h("span", {}, "Usa o EmbeddingGemma local. Nada sai do computador."),
        ),
      ),
      h(
        "label",
        { class: "radio-row" },
        externalRadio,
        h(
          "span",
          {},
          h("strong", {}, "Servidor externo"),
          h("span", {}, "LM Studio, Ollama ou outro servidor compatível com OpenAI."),
        ),
      ),
    ),
    localPanel,
    externalPanel,
    h("div", { class: "button-row" }, saveButton, testButton),
    feedback,
    h(
      "p",
      { class: "settings-note" },
      "Depois de trocar o motor, use “Testar motor” para confirmar que ele responde. A busca continua funcionando por texto enquanto isso.",
    ),
  );
}

/* ------------------------------------------------------------------ catálogo */

function catalogCard(status, settings) {
  const infoHost = h("div", { "aria-live": "polite" });
  const progressHost = h("div", {});
  const catalog = status?.catalog ?? {};
  const updatesEnabled = settings?.updates?.check_catalog !== false;

  const facts = h(
    "dl",
    { class: "data-facts" },
    h("dt", {}, "Versão instalada"),
    h("dd", {}, catalog.version || "Não instalado"),
    h("dt", {}, "Registros"),
    h("dd", {}, catalog.installed ? formatNumber(catalog.records) : "—"),
    h("dt", {}, "Data do catálogo"),
    h("dd", {}, catalog.built_at ? formatDate(catalog.built_at) : "—"),
    h("dt", {}, "Anos cobertos"),
    h(
      "dd",
      {},
      Array.isArray(catalog.years) && catalog.years.length >= 2
        ? `${formatYear(catalog.years[0])} a ${formatYear(catalog.years[1])}`
        : "—",
    ),
  );

  const checkButton = h(
    "button",
    { class: "btn btn--ghost", type: "button" },
    "Verificar atualizações",
  );
  const updateButton = h(
    "button",
    { class: "btn btn--primary", type: "button", hidden: true },
    icon("download", { size: 16 }),
    h("span", {}, "Atualizar catálogo"),
  );

  const renderUpdateInfo = (info) => {
    infoHost.replaceChildren();
    updateButton.hidden = true;
    if (!info) {
      // Offline or 503: stay quiet, the installed catalog keeps working.
      return;
    }
    if (info.available) {
      const details = [];
      if (info.records_delta) {
        details.push(`${formatNumber(Math.abs(info.records_delta))} registros a mais`);
      }
      if (info.download_bytes) details.push(formatBytes(info.download_bytes));
      infoHost.append(
        h(
          "p",
          {},
          `A versão ${info.latest || "mais recente"} está disponível.`,
          details.length ? ` (${details.join(", ")})` : "",
        ),
      );
      updateButton.hidden = false;
    } else {
      infoHost.append(
        h(
          "p",
          { class: "muted small" },
          `O catálogo está atualizado${info.current ? ` (versão ${info.current})` : ""}.`,
        ),
      );
    }
  };

  const check = async () => {
    checkButton.disabled = true;
    infoHost.replaceChildren(h("p", { class: "muted small" }, "Verificando o catálogo…"));
    try {
      const info = await getCatalogUpdate();
      renderUpdateInfo(info);
    } catch (error) {
      console.debug("Catalog update check unavailable.", error?.message ?? error);
      renderUpdateInfo(null);
    } finally {
      checkButton.disabled = false;
    }
  };

  updateButton.addEventListener("click", async () => {
    updateButton.disabled = true;
    checkButton.disabled = true;
    const progress = createProgress({ label: "Progresso da atualização do catálogo" });
    progressHost.replaceChildren(progress.element);
    try {
      const { job_id: jobId } = await startCatalogUpdate();
      const job = await pollJob(jobId, { onUpdate: (snapshot) => progress.update(snapshot) });
      if (job.status === "done") {
        showToast("Catálogo atualizado.");
        await loadStatus(true).catch(() => state.status);
        await check();
      } else {
        progress.showError(job.error || job.message || "A atualização não terminou.");
      }
    } catch (error) {
      if (error?.name !== "AbortError") {
        progress.showError(error?.message || "Não foi possível atualizar o catálogo.");
      }
    } finally {
      updateButton.disabled = false;
      checkButton.disabled = false;
    }
  });

  checkButton.addEventListener("click", check);

  const node = h(
    "section",
    { class: "settings-card", "aria-labelledby": "catalog-title" },
    h("h2", { id: "catalog-title" }, "Catálogo"),
    h(
      "p",
      {},
      "O catálogo reúne os metadados das teses e dissertações. Atualizar baixa apenas o que mudou desde a sua versão.",
    ),
    facts,
    h("div", { class: "button-row" }, checkButton, updateButton),
    infoHost,
    progressHost,
    updatesEnabled
      ? null
      : h(
          "p",
          { class: "settings-note" },
          "A verificação automática de atualizações está desligada. Você ainda pode verificar manualmente.",
        ),
  );

  if (updatesEnabled) {
    queueMicrotask(check);
  } else {
    infoHost.append(
      h("p", { class: "muted small" }, "Use “Verificar atualizações” para consultar o catálogo."),
    );
  }
  return node;
}

/* ------------------------------------------------------------- atualizações */

function updatesCard(settings) {
  const enabled = settings?.updates?.check_app !== false;
  const feedback = h("p", { class: "field__hint", role: "status" }, "");
  const checkbox = h("input", {
    type: "checkbox",
    id: "check-app-updates",
    checked: enabled,
  });
  checkbox.addEventListener("change", async () => {
    checkbox.disabled = true;
    feedback.textContent = "Salvando…";
    try {
      state.settings = await saveSettings({ updates: { check_app: checkbox.checked } });
      feedback.textContent = checkbox.checked
        ? "O BuscaTeses vai verificar novas versões uma vez por dia."
        : "A verificação de novas versões está desligada.";
    } catch (error) {
      checkbox.checked = !checkbox.checked;
      feedback.textContent = "";
      showError(error);
    } finally {
      checkbox.disabled = false;
    }
  });

  return h(
    "section",
    { class: "settings-card", "aria-labelledby": "updates-title" },
    h("h2", { id: "updates-title" }, "Atualizações do aplicativo"),
    h(
      "label",
      { class: "checkbox-row", for: "check-app-updates" },
      checkbox,
      h(
        "span",
        {},
        h("strong", {}, "Avisar quando houver uma versão nova"),
        h(
          "span",
          {},
          "A verificação consulta a página de versões no GitHub uma vez por dia e não envia dados pessoais.",
        ),
      ),
    ),
    feedback,
    h(
      "p",
      { class: "settings-note" },
      "Desligar esta opção não afeta as buscas nem a atualização do catálogo, que continua disponível em “Catálogo”.",
    ),
  );
}

/* ---------------------------------------------------------------- dados/app */

function dataCard(status) {
  const library = status?.library ?? {};
  const home = status?.paths?.home || "—";
  const feedback = h("p", { class: "muted small", role: "status" });
  const openButton = h(
    "button",
    { class: "btn btn--ghost", type: "button" },
    icon("folder", { size: 16 }),
    h("span", {}, "Abrir pasta"),
  );
  openButton.disabled = !status?.paths?.home;
  openButton.addEventListener("click", async () => {
    openButton.disabled = true;
    feedback.textContent = "";
    try {
      await openDataDir();
      feedback.textContent = "Pasta aberta no gerenciador de arquivos.";
    } catch (error) {
      showError(error);
    } finally {
      openButton.disabled = false;
    }
  });

  return h(
    "section",
    { class: "settings-card", "aria-labelledby": "data-title" },
    h("h2", { id: "data-title" }, "Local dos dados"),
    h(
      "p",
      {},
      "Catálogo, modelo de embedding e documentos da biblioteca ficam numa pasta de dados do aplicativo, fora do instalador. Nada é enviado para a internet.",
    ),
    h(
      "dl",
      { class: "data-facts" },
      h("dt", {}, "Pasta"),
      h("dd", { class: "path-value" }, home),
      h("dt", {}, "Documentos"),
      h("dd", {}, formatNumber(library.documents ?? 0)),
      h("dt", {}, "Trechos"),
      h("dd", {}, formatNumber(library.chunks ?? 0)),
    ),
    h("div", { class: "button-row" }, openButton),
    feedback,
  );
}

function aboutCard(status) {
  return h(
    "section",
    { class: "settings-card", "aria-labelledby": "about-title" },
    h("h2", { id: "about-title" }, "Sobre o BuscaTeses"),
    h(
      "p",
      {},
      "Versão ",
      status?.app?.version || "—",
      ". Conheça as fontes dos dados, as licenças e o aviso de não afiliação à CAPES ou ao IBICT.",
    ),
    h(
      "div",
      { class: "button-row" },
      h("a", { class: "btn btn--ghost", href: "#/sobre" }, "Abrir Sobre"),
    ),
  );
}

/**
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 */
export async function renderSettings(outlet, ctx) {
  const [settings, status] = await Promise.all([
    getSettings({ signal: ctx.signal }).catch(() => state.settings ?? {}),
    loadStatus(true).catch(() => state.status ?? {}),
  ]);
  state.settings = settings;

  outlet.replaceChildren(
    ...pageHead(),
    h(
      "div",
      { class: "settings-grid" },
      appearanceCard(),
      accessibilityCard(),
      engineCard(settings, status),
      catalogCard(status, settings),
      updatesCard(settings),
      dataCard(status),
      aboutCard(status),
    ),
  );

  return { title: "Configurações" };
}
