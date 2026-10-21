/** "Sobre": data sources, AI model, licenses and disclaimer. */

import { h } from "../dom.js";
import { formatYears } from "../format.js";
import { loadStatus, state } from "../state.js";

function externalLink(href, text) {
  return h(
    "a",
    { href, target: "_blank", rel: "noopener noreferrer" },
    text,
    h("span", { class: "sr-only" }, " (link externo, abre em nova aba)"),
  );
}

function section(title, ...children) {
  return h("section", {}, h("h2", {}, title), ...children);
}

/**
 * @param {HTMLElement} outlet
 * @param {Record<string, any>} ctx
 */
export async function renderAbout(outlet, ctx) {
  const status = await loadStatus().catch(() => state.status);
  const version = status?.app?.version || "—";
  const catalogYears = formatYears(status?.catalog?.years);
  const catalogPeriod = catalogYears ? ` O catálogo instalado reúne trabalhos defendidos entre ${catalogYears}.` : "";

  outlet.replaceChildren(
    h("div", { class: "about" },
      h("h1", {}, "Sobre o BuscaTeses"),
      h(
        "p",
        { class: "page-head" },
        "Uma forma local e cuidadosa de pesquisar teses e dissertações brasileiras, com apoio de busca por significado e de assistentes de IA.",
      ),
      section(
        "O que é",
        h(
          "p",
          {},
          "O BuscaTeses é um aplicativo de código aberto que instala o catálogo no seu computador e oferece busca por palavras, filtros e pesquisa semântica. A interface, o índice e o modelo de embedding funcionam sem conexão depois da instalação.",
        ),
      ),
      section(
        "Fontes dos dados",
        h(
          "p",
          {},
          "Os metadados vêm de três fontes públicas: os ",
          externalLink("https://dadosabertos.capes.gov.br/", "dados abertos da CAPES"),
          " (sob ",
          externalLink(
            "https://creativecommons.org/licenses/by/4.0/deed.pt-br",
            "Creative Commons Atribuição 4.0",
          ),
          "), o ",
          externalLink(
            "https://catalogodeteses.capes.gov.br/catalogo-teses/",
            "Catálogo de Teses e Dissertações da CAPES",
          ),
          " e a ",
          externalLink("https://bdtd.ibict.br/", "BDTD (IBICT)"),
          ` para os anos mais recentes.${catalogPeriod}`,
        ),
        h(
          "ul",
          {},
          h(
            "li",
            {},
            "O BuscaTeses não redistribui os arquivos completos: o link leva ao texto publicado na fonte original.",
          ),
          h(
            "li",
            {},
            "Ao reutilizar os metadados, mantenha a atribuição à CAPES e, nos registros da BDTD, ao IBICT e à biblioteca digital de origem.",
          ),
          h(
            "li",
            {},
            "Os anos mais recentes têm cobertura parcial, pois novos trabalhos são registrados ao longo do ano.",
          ),
          h(
            "li",
            {},
            "Informações podem estar incompletas ou desatualizadas; confira sempre na fonte antes de citar.",
          ),
        ),
      ),
      section(
        "Modelo de embedding",
        h(
          "p",
          {},
          "A busca por significado usa o modelo ",
          externalLink(
            "https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX",
            "EmbeddingGemma",
          ),
          ", da Google, que transforma textos em vetores e roda no seu computador. O uso do modelo segue os ",
          externalLink("https://ai.google.dev/gemma/terms", "termos do Gemma"),
          ". Nenhum texto da sua pesquisa é enviado a serviços externos por causa do modelo de embedding local.",
        ),
        h(
          "p",
          {},
          "Você também pode apontar o aplicativo para um servidor de embeddings compatível com OpenAI, como LM Studio ou Ollama, executado por você.",
        ),
      ),
      section(
        "Licenças",
        h(
          "ul",
          {},
          h("li", {}, "O código do BuscaTeses é distribuído sob a licença MIT."),
          h(
            "li",
            {},
            "As fontes Literata e Atkinson Hyperlegible Next são usadas sob a ",
            externalLink("https://openfontlicense.org/", "SIL Open Font License 1.1"),
            ".",
          ),
        ),
      ),
      section(
        "Privacidade",
        h(
          "p",
          {},
          "O servidor escuta apenas neste computador (127.0.0.1) e os dados ficam na pasta do aplicativo. As integrações com assistentes de IA são somente leitura. O BuscaTeses não envia telemetria.",
        ),
      ),
      section(
        "Aviso",
        h(
          "p",
          { class: "disclaimer" },
          "O BuscaTeses é um projeto independente e não tem vínculo, patrocínio ou aprovação da CAPES, do IBICT, do Ministério da Educação ou da Google. Os nomes e marcas citados pertencem aos seus respectivos titulares.",
        ),
      ),
      h("p", { class: "muted small" }, `Versão ${version} · feito para pesquisar com calma.`),
    ),
  );

  return { title: "Sobre" };
}
