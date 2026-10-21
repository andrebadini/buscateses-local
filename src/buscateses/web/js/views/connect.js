/** "Conectar assistentes de IA": local apps and the ChatGPT/Claude.ai address. */

import {
  connectIntegration,
  disconnectIntegration,
  getRemote,
  listIntegrations,
  rotateRemote,
  startRemote,
  stopRemote,
} from "../api.js";
import { copyButton } from "../components/copy.js";
import { showError, showToast } from "../components/toast.js";
import { h } from "../dom.js";

const TITLE = "Conectar assistentes de IA";

const INTRO =
  "Use o BuscaTeses dentro do assistente de IA que você já usa. A conexão usa o MCP, um padrão aberto para o assistente acessar ferramentas do computador. O acesso é somente leitura: o assistente consulta o catálogo e cita as fontes, mas não altera nada.";

const APP_ORDER = [
  "claude_desktop",
  "claude_code",
  "codex",
  "cursor",
  "gemini_cli",
  "lm_studio",
  "vscode",
];

const KIND_LABELS = {
  auto: "Configuração automática",
  command: "Comando",
  manual: "Configuração manual",
};

const EXAMPLES = [
  "Procure teses sobre letramento racial na educação infantil.",
  "Compare as metodologias de dois trabalhos sobre agricultura familiar no Cerrado.",
  "Quais dissertações entre 2015 e 2023 tratam de recuperação judicial de empresas?",
  "Pesquise nos meus documentos o que existe sobre método participativo e mostre as páginas.",
];

const REMOTE_EXPLANATION = [
  "O ChatGPT e o Claude.ai funcionam na nuvem. Para eles encontrarem o BuscaTeses, o aplicativo cria um endereço público temporário com a Cloudflare.",
  "Enquanto o endereço estiver ligado, qualquer pessoa que o tenha pode pesquisar no catálogo. Não compartilhe o endereço.",
  "O endereço funciona só enquanto o BuscaTeses estiver aberto e muda cada vez que é criado.",
];

const LIBRARY_WARNING =
  "Ao marcar, trechos dos seus documentos pessoais podem aparecer nas respostas do assistente. Deixe desmarcado se não quiser isso.";

const MENU_NOTE =
  "Os nomes dos menus podem mudar. Procure por “conector personalizado” ou “servidor MCP”.";

const LEARN_MORE =
  "https://github.com/andrebadini/buscateses-local/blob/main/docs/MCP.md";

const CLAUDE_STEPS = [
  [
    "Abra ",
    link("https://claude.ai/customize/connectors", "claude.ai/customize/connectors"),
    ".",
  ],
  ["Clique em “Adicionar conector personalizado” (em inglês, “Add custom connector”)."],
  ["Dê o nome BuscaTeses e cole o endereço copiado no campo de URL do servidor MCP."],
  [
    "Se aparecer a opção de login, escolha “Sem login” (“No sign-in”) e clique em “Adicionar”.",
  ],
  ["Numa conversa, clique em “+” → Conectores e ative o BuscaTeses."],
];

const CHATGPT_STEPS = [
  ["Abra ", link("https://chatgpt.com/plugins", "chatgpt.com/plugins"), "."],
  [
    "Clique em “+” e escolha “Adicionar servidor MCP personalizado” (“Add custom MCP server”).",
  ],
  ["Dê o nome BuscaTeses e cole o endereço em “Conexão” (“Connection”)."],
  [
    "Em autenticação, escolha “Sem autenticação” (“No authentication”).",
  ],
  [
    "Marque “Entendo e quero continuar” e clique em “Criar como plugin”. Depois instale o plugin.",
  ],
  ["Numa conversa nova, digite “@” e escolha BuscaTeses."],
];

function link(href, text) {
  return h(
    "a",
    {
      class: "text-link",
      href,
      target: "_blank",
      rel: "noopener noreferrer",
    },
    text,
    h("span", { class: "sr-only" }, " (link externo, abre em nova aba)"),
  );
}

function pageHead() {
  return [
    h("h1", {}, TITLE),
    h("p", { class: "page-head", style: { marginBottom: "24px" } }, INTRO),
  ];
}

function appRank(integration) {
  if (integration.connected) return 0;
  return integration.detected === false ? 2 : 1;
}

function sortApps(entries) {
  const position = (entry) => {
    const index = APP_ORDER.indexOf(entry.id);
    return index === -1 ? APP_ORDER.length : index;
  };
  return [...entries].sort(
    (left, right) => appRank(left) - appRank(right) || position(left) - position(right),
  );
}

function appStateLabel(integration) {
  if (integration.connected) return "Conectado";
  if (integration.detected === false) return "Não instalado neste computador";
  return "Instalado — não conectado";
}

function statusBadge(integration) {
  const connected = Boolean(integration.connected);
  const missing = integration.detected === false && !connected;
  return h(
    "span",
    {
      class: `integration__state ${
        connected
          ? "integration__state--on"
          : missing
            ? "integration__state--missing"
            : "integration__state--off"
      }`,
    },
    appStateLabel(integration),
  );
}

function serializedSnippet(snippet) {
  if (snippet === null || snippet === undefined) return "";
  if (typeof snippet === "string") return snippet;
  try {
    return JSON.stringify(snippet, null, 2);
  } catch {
    return String(snippet);
  }
}

function snippetBlock(snippet, title) {
  const text = serializedSnippet(snippet);
  if (!text) return null;
  return h(
    "div",
    {},
    title ? h("h3", {}, title) : null,
    h(
      "div",
      { class: "snippet-box" },
      h("pre", {}, h("code", {}, text)),
      copyButton(text, {
        label: "Copiar",
        successMessage: "Configuração copiada.",
        class: "btn btn--ghost btn--small copy-inline",
      }),
    ),
  );
}

function installLink(integration, name) {
  const url = integration.install_url;
  if (!url) {
    return h(
      "p",
      { class: "muted small" },
      `O ${name} não está instalado neste computador.`,
    );
  }
  return h(
    "p",
    { class: "muted small" },
    `O ${name} não está instalado neste computador. `,
    link(url, `Baixar o ${name}`),
  );
}

function technicalDetails(integration, kind) {
  const children = [];
  if (integration.config_path) {
    children.push(
      h(
        "p",
        { class: "muted small" },
        "Arquivo de configuração: ",
        h("code", {}, integration.config_path),
      ),
    );
  }
  if (integration.snippet) {
    const label = kind === "command" ? "Comando" : "Configuração";
    children.push(snippetBlock(integration.snippet, label));
  }
  if (!children.length) return null;
  return h(
    "details",
    { class: "integration__details" },
    h("summary", {}, "Detalhes técnicos"),
    ...children,
  );
}

function connectedHint(integration, name) {
  if (!integration.connected) return null;
  return h(
    "div",
    { class: "integration__connected" },
    h("p", {}, `Conectado. Feche e abra o ${name} para ativar.`),
    h("p", { class: "muted small" }, `Peça, por exemplo: “${EXAMPLES[0]}”`),
  );
}

function integrationCard(integration, reload) {
  const name = integration.name || integration.id;
  const notice = h("div", { class: "integration__notice", "aria-live": "polite" });
  const actions = h("div", { class: "integration__actions" });
  const kind = integration.kind || "manual";
  const missing = integration.detected === false && !integration.connected;

  if (missing) {
    notice.append(installLink(integration, name));
  }

  if ((kind === "auto" || kind === "command") && !missing) {
    const button = h(
      "button",
      {
        class: integration.connected ? "btn btn--quiet" : "btn btn--primary",
        type: "button",
      },
      integration.connected ? "Desconectar" : `Conectar ao ${name}`,
    );
    button.addEventListener("click", async () => {
      button.disabled = true;
      notice.replaceChildren();
      try {
        if (integration.connected) {
          await disconnectIntegration(integration.id);
          showToast(`${name} desconectado.`);
        } else {
          await connectIntegration(integration.id);
          showToast(`${name} conectado.`);
        }
        await reload();
      } catch (error) {
        showError(error);
      } finally {
        button.disabled = false;
      }
    });
    actions.append(button);
  }

  if (kind === "manual" && integration.instructions) {
    notice.append(h("p", { class: "muted small" }, integration.instructions));
  }

  return h(
    "li",
    { class: "integration" },
    h(
      "div",
      { class: "integration__head" },
      h("h2", {}, name),
      statusBadge(integration),
    ),
    h("p", { class: "muted small" }, KIND_LABELS[kind] ?? "Integração"),
    connectedHint(integration, name),
    notice,
    technicalDetails(integration, kind),
    actions,
  );
}

function exampleSection() {
  return h(
    "section",
    { class: "section", "aria-labelledby": "examples-title" },
    h("h2", { id: "examples-title" }, "Experimente perguntar"),
    h(
      "p",
      { class: "muted" },
      "Depois de conectar, peça em linguagem natural. O assistente consulta o catálogo e cita título, autoria e ano.",
    ),
    h(
      "ul",
      { class: "examples" },
      EXAMPLES.map((example) => h("li", {}, `“${example}”`)),
    ),
  );
}

function stepsList(steps, label) {
  return h(
    "ol",
    { class: "web-guide__steps", "aria-label": label },
    steps.map((parts) => h("li", {}, ...parts)),
  );
}

function guideBlock(title, availability, steps, label) {
  return h(
    "div",
    { class: "web-guide__block" },
    h("h3", {}, title),
    h("p", { class: "muted small" }, availability),
    stepsList(steps, label),
  );
}

function webGuide() {
  return h(
    "section",
    { class: "section web-guide", "aria-labelledby": "web-guide-title" },
    h("h2", { id: "web-guide-title" }, "Como configurar o assistente"),
    guideBlock(
      "No Claude.ai",
      "Funciona no Claude.ai Free (1 conector), Pro e Max. Em Team e Enterprise, o administrador do espaço precisa adicionar o conector.",
      CLAUDE_STEPS,
      "Passos no Claude.ai",
    ),
    guideBlock(
      "No ChatGPT",
      "Funciona no ChatGPT na web. No Pro, é preciso dar permissão de leitura. Em Business, Enterprise e Edu, o administrador do espaço precisa liberar o uso.",
      CHATGPT_STEPS,
      "Passos no ChatGPT",
    ),
    h(
      "p",
      { class: "muted small" },
      MENU_NOTE,
      " ",
      link(LEARN_MORE, "Saiba mais"),
    ),
  );
}

function urlBox(url) {
  return h(
    "div",
    { class: "remote-url" },
    h("label", { class: "sr-only", for: "remote-url-value" }, "Endereço do servidor MCP"),
    h("input", {
      id: "remote-url-value",
      class: "remote-url__input",
      type: "text",
      readonly: true,
      value: url,
      spellcheck: "false",
    }),
    copyButton(url, {
      label: "Copiar endereço",
      successMessage: "Endereço copiado.",
      class: "btn btn--primary",
    }),
  );
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function remotePanel(ctx) {
  const shell = h("div", { class: "remote-panel" });
  const checkbox = h("input", {
    type: "checkbox",
    id: "remote-include-library",
  });
  const options = h(
    "div",
    { class: "remote-panel__option" },
    h(
      "label",
      { class: "remote-panel__check", for: "remote-include-library" },
      checkbox,
      h("span", {}, "Permitir que o assistente pesquise também a minha biblioteca pessoal"),
    ),
    h("p", { class: "muted small" }, LIBRARY_WARNING),
  );
  const status = h("div", { class: "remote-panel__status", "aria-live": "polite" });
  const notice = h("div", { class: "remote-panel__notice", "aria-live": "polite" });
  shell.append(
    ...REMOTE_EXPLANATION.map((text) => h("p", {}, text)),
    options,
    status,
    notice,
  );

  let busy = false;
  let renderedSignature = null;

  const render = (remote) => {
    const state = remote?.state || "off";
    const url = remote?.url || "";
    // Only re-render when something visible changes; replacing the buttons
    // while the person is about to click them would lose the click.
    const message =
      state === "downloading" || state === "starting" || state === "error"
        ? remote?.message || ""
        : "";
    const signature = JSON.stringify([
      state,
      url,
      message,
      Boolean(remote?.include_library),
    ]);
    if (signature === renderedSignature) return;
    renderedSignature = signature;

    if (typeof remote?.include_library === "boolean") {
      checkbox.checked = remote.include_library;
    }
    checkbox.disabled = busy || state === "downloading" || state === "starting";

    if (state === "online" && url) {
      status.replaceChildren(
        h("p", { class: "remote-panel__label" }, "Endereço público ligado"),
        urlBox(url),
        h(
          "div",
          { class: "remote-panel__actions" },
          h(
            "button",
            { class: "btn btn--quiet", type: "button", "data-action": "stop" },
            "Desligar",
          ),
          h(
            "button",
            { class: "btn btn--quiet", type: "button", "data-action": "rotate" },
            "Gerar novo endereço",
          ),
        ),
      );
    } else if (state === "downloading" || state === "starting") {
      status.replaceChildren(
        h(
          "div",
          { class: "remote-panel__busy" },
          h("span", { class: "spinner", "aria-hidden": "true" }),
          h("span", {}, remote?.message || "Preparando o endereço público…"),
        ),
      );
    } else if (state === "error") {
      status.replaceChildren(
        h("p", { class: "remote-panel__error", role: "alert" }, remote?.message || ""),
        h(
          "button",
          { class: "btn btn--primary", type: "button", "data-action": "start" },
          "Tentar de novo",
        ),
      );
    } else {
      status.replaceChildren(
        h(
          "p",
          { class: "muted" },
          "Crie o endereço quando for usar o ChatGPT ou o Claude.ai.",
        ),
        h(
          "button",
          { class: "btn btn--primary", type: "button", "data-action": "start" },
          "Criar endereço público",
        ),
      );
    }
    status.querySelectorAll("button[data-action]").forEach((button) => {
      button.disabled = busy;
      button.addEventListener("click", () => handleAction(button.dataset.action));
    });
  };

  const setBusy = (value) => {
    busy = Boolean(value);
  };

  async function handleAction(action) {
    if (busy) return;
    setBusy(true);
    notice.replaceChildren();
    try {
      if (action === "start") {
        const started = await startRemote(checkbox.checked);
        render(started);
        await poll();
      } else if (action === "stop") {
        const stopped = await stopRemote();
        render(stopped);
        showToast("Endereço público desligado.");
      } else if (action === "rotate") {
        const rotated = await rotateRemote();
        render(rotated);
        if (rotated?.state !== "online") await poll();
      }
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
      status.querySelectorAll("button[data-action]").forEach((button) => {
        button.disabled = false;
      });
      checkbox.disabled = false;
    }
  }

  async function poll() {
    const deadline = Date.now() + 10 * 60 * 1000;
    while (Date.now() < deadline) {
      if (ctx.signal?.aborted) return;
      await delay(1200);
      let remote;
      try {
        remote = await getRemote({ signal: ctx.signal });
      } catch (error) {
        if (error?.name === "AbortError") return;
        throw error;
      }
      render(remote);
      if (["online", "off", "error"].includes(remote?.state)) return;
    }
    notice.replaceChildren(
      h(
        "p",
        { class: "muted small" },
        "O endereço ainda não ficou pronto. Verifique sua conexão e tente de novo.",
      ),
    );
  }

  render({ state: "off", include_library: false });
  getRemote({ signal: ctx.signal })
    .then((remote) => {
      if (!busy) render(remote);
    })
    .catch((error) => {
      if (error?.name !== "AbortError") showError(error);
    });

  return shell;
}

function webSection(ctx) {
  return h(
    "section",
    { class: "section", "aria-labelledby": "web-title" },
    h("h2", { id: "web-title" }, "Assistentes na web: ChatGPT e Claude.ai"),
    remotePanel(ctx),
    webGuide(),
  );
}

function appsSection(entries, reload, unavailable) {
  const children = [
    h("h2", { id: "apps-title" }, "Aplicativos neste computador"),
    h(
      "p",
      { class: "muted" },
      "Estes aplicativos rodam neste computador e encontram o BuscaTeses sozinhos. O acesso é somente leitura.",
    ),
  ];
  if (unavailable) {
    children.push(
      h(
        "p",
        { class: "muted" },
        "Não foi possível listar os aplicativos desta versão. Você ainda pode usar os assistentes na web.",
      ),
    );
  } else if (!entries.length) {
    children.push(
      h("p", { class: "muted" }, "Nenhum aplicativo compatível foi encontrado."),
    );
  } else {
    children.push(
      h(
        "ul",
        { class: "integration-grid", "aria-label": "Aplicativos neste computador" },
        sortApps(entries).map((entry) => integrationCard(entry, reload)),
      ),
    );
  }
  return h("section", { class: "section", "aria-labelledby": "apps-title" }, ...children);
}

/**
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 */
export async function renderConnect(outlet, ctx) {
  let integrations = [];
  let unavailable = false;
  try {
    const loaded = await listIntegrations({ signal: ctx.signal });
    integrations = Array.isArray(loaded) ? loaded : loaded?.integrations ?? [];
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    unavailable = true;
  }

  const listHost = h("div", { "aria-live": "polite" });

  const renderList = (entries, missing = false) => {
    listHost.replaceChildren(appsSection(entries, reload, missing));
  };

  async function reload() {
    try {
      const refreshed = await listIntegrations({ signal: ctx.signal });
      const entries = Array.isArray(refreshed)
        ? refreshed
        : refreshed?.integrations ?? [];
      renderList(entries);
    } catch (error) {
      if (error?.name !== "AbortError") showError(error);
    }
  }

  renderList(integrations, unavailable);

  outlet.replaceChildren(
    ...pageHead(),
    listHost,
    webSection(ctx),
    exampleSection(),
  );

  return { title: TITLE };
}
