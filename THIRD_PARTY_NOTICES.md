# Avisos de terceiros

O BuscaTeses é distribuído sob a licença [MIT](LICENSE). Ele usa, referencia ou baixa os componentes abaixo. Esta lista
é informativa; em caso de dúvida, prevalecem as licenças originais de cada projeto.

## Fontes

| Fonte | Uso | Licença | Copyright |
| --- | --- | --- | --- |
| [Literata](https://github.com/googlefonts/literata) | texto da interface | SIL Open Font License 1.1 | The Literata Project Authors |
| [Atkinson Hyperlegible Next](https://github.com/brailleinstitute/Atkinson-Hyperlegible-Next) | títulos e destaques | SIL Open Font License 1.1 | Braille Institute of America, Inc. |

Os arquivos `.woff2` fazem parte do aplicativo. O texto completo da licença está em
[`LICENSES/OFL-1.1.txt`](LICENSES/OFL-1.1.txt).

Resumo da OFL 1.1: as fontes podem ser usadas, estudadas, modificadas e redistribuídas livremente, inclusive embutidas
em softwares, desde que não sejam vendidas isoladamente, que o aviso de copyright e a licença acompanhem o software e
que nomes reservados não sejam usados em versões modificadas.

## Modelo de embedding

| Componente | Detalhes |
| --- | --- |
| [EmbeddingGemma 300M](https://huggingface.co/google/embeddinggemma-300m) | modelo do Google, sujeito aos [Termos de Uso do Gemma](https://ai.google.dev/gemma/terms) |
| [EmbeddingGemma 300M ONNX](https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX) | conversão ONNX mantida pela comunidade `onnx-community` |

O modelo **não é redistribuído** no repositório nem nos instaladores. Ele é baixado pelo próprio aplicativo na
primeira execução e fica na pasta de dados da pessoa usuária. Ao baixar e usar o modelo, você concorda com os Termos
de Uso do Gemma.

## Dados

| Componente | Licença | Observação |
| --- | --- | --- |
| Dados abertos da CAPES — Catálogo de Teses e Dissertações (1987–2024) | Creative Commons Atribuição (CC-BY) | fonte: <https://dadosabertos.capes.gov.br> |
| Catálogo de Teses e Dissertações da CAPES (2025 em diante) | metadados públicos; consulte os termos da fonte | fonte: <https://catalogodeteses.capes.gov.br/catalogo-teses/> |
| BDTD — Biblioteca Digital Brasileira de Teses e Dissertações (2025 em diante) | metadados públicos; a licença do texto completo varia por repositório | fonte: <https://bdtd.ibict.br/> |
| Dataset normalizado BuscaTeses | CC-BY para os dados oriundos dos dados abertos da CAPES | <https://huggingface.co/datasets/andrebadini/catalogo-teses-capes> |

A licença MIT do código não se aplica aos dados. Ao redistribuir ou publicar resultados, atribua as fontes:

> Fonte: CAPES — Catálogo de Teses e Dissertações, dados abertos sob licença Creative Commons Atribuição.
> Fonte (2025 em diante): CAPES — Catálogo de Teses e Dissertações e BDTD/IBICT.
> Dataset normalizado: BuscaTeses, <https://huggingface.co/datasets/andrebadini/catalogo-teses-capes>.

Os metadados da BDTD são públicos e mantidos pelo IBICT e pelas bibliotecas digitais participantes. O BuscaTeses não
redistribui o texto completo dos trabalhos e não tem vínculo com a CAPES ou o IBICT.

## VLibras

O widget de acessibilidade em Libras é carregado de <https://vlibras.gov.br> e **não é redistribuído** com o
aplicativo. O VLibras é um projeto do Governo Federal brasileiro; consulte o site oficial para os termos e a licença
do serviço.

## Cloudflare Tunnel (cloudflared)

O acesso do ChatGPT e do Claude.ai usa o `cloudflared`, baixado **em tempo de execução** de uma versão fixa do
repositório oficial da Cloudflare. O binário não é redistribuído no repositório nem nos instaladores; ele fica na
pasta `bin/` da pasta de dados e é conferido por SHA-256 antes de ser executado.

| Componente | Uso | Licença | Copyright |
| --- | --- | --- | --- |
| [cloudflared](https://github.com/cloudflare/cloudflared) | túnel HTTPS temporário | Apache-2.0 | Cloudflare, Inc. |

O uso do Quick Tunnel também está sujeito aos termos do serviço Cloudflare. O BuscaTeses não controla a
disponibilidade do serviço.

## Dependências Python

As versões exatas são resolvidas na instalação, conforme `pyproject.toml`.

| Pacote | Uso | Licença |
| --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi) | API local | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | servidor ASGI | BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | uploads | Apache-2.0 |
| [platformdirs](https://github.com/platformdirs/platformdirs) | pasta de dados | MIT |
| [filelock](https://github.com/tox-dev/filelock) | trava de instância | Unlicense |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | download do modelo e do catálogo | Apache-2.0 |
| [NumPy](https://numpy.org) | vetores | BSD-3-Clause |
| [PyArrow](https://arrow.apache.org/docs/python/) | leitura de Parquet | Apache-2.0 |
| [onnxruntime](https://github.com/microsoft/onnxruntime) | execução do modelo ONNX | MIT |
| [tokenizers](https://github.com/huggingface/tokenizers) | tokenização | Apache-2.0 |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | servidor MCP | MIT |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | extração de PDF | Apache-2.0 (PDFium: BSD-3-Clause) |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | detecção de codificação | MIT |
| [Requests](https://github.com/psf/requests) | HTTP | Apache-2.0 |
| [pywebview](https://github.com/r0x0r/pywebview) | janela nativa (extra `desktop`) | BSD-3-Clause |

## Dependências de desenvolvimento e build

Estas não fazem parte do aplicativo em execução, mas são usadas para testar ou gerar os instaladores:

| Pacote/Ferramenta | Uso | Licença |
| --- | --- | --- |
| [pytest](https://github.com/pytest-dev/pytest) | testes | MIT |
| [httpx](https://github.com/encode/httpx) | cliente HTTP nos testes | BSD-3-Clause |
| [Ruff](https://github.com/astral-sh/ruff) | lint e formatação | MIT |
| [fpdf2](https://github.com/py-pdf/fpdf2) | PDFs sintéticos nos testes | LGPL-3.0 |
| [Playwright](https://github.com/microsoft/playwright) | testes de interface | Apache-2.0 |
| [axe-core](https://github.com/dequelabs/axe-core) | verificações de acessibilidade | MPL-2.0 |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | empacotamento | GPL-2.0-or-later, com exceção para o bootloader |
| [Pillow](https://github.com/python-pillow/Pillow) | ícones | HPND |
| [CairoSVG](https://github.com/Kozea/CairoSVG) | geração de ícones (opcional) | LGPL-3.0 |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | instalador do Windows | licença do Inno Setup (freeware) |
| [DuckDB](https://duckdb.org) | pipeline do dataset | MIT |
| [SentenceTransformers](https://github.com/UKPLab/sentence-transformers) | embeddings locais no pipeline (opcional) | Apache-2.0 |
| [PyTorch](https://pytorch.org) | backend opcional do pipeline | BSD-3-Clause |

## Marcas e nomes

CAPES, Claude, Anthropic, ChatGPT, OpenAI, Codex, Cursor, Gemini, Google, EmbeddingGemma, LM Studio, VLibras e demais
nomes citados pertencem aos seus respectivos titulares. O BuscaTeses é um projeto independente e **não é afiliado** a
nenhuma dessas instituições.

Se você acredita que algum crédito está incorreto ou faltando, abra uma issue ou use o canal privado de
[SECURITY.md](SECURITY.md).
