# Desenvolvimento

Guia para quem contribui com o código do BuscaTeses. A especificação técnica completa está em
[`dev/ESPECIFICACAO.md`](dev/ESPECIFICACAO.md). Para a visão de arquitetura, veja
[ARQUITETURA.md](ARQUITETURA.md); para o fluxo de contribuição, [CONTRIBUTING.md](../CONTRIBUTING.md).

## Ambiente

Requisitos:

- Python **3.11 a 3.13** (o empacotamento usa 3.12);
- [uv](https://docs.astral.sh/uv/) para criar o ambiente e instalar dependências;
- Node.js 20+ apenas para os testes de interface (Playwright).

```bash
uv venv
uv pip install -p .venv/bin/python -e ".[dev,desktop]"
```

Para trabalhar também com o pipeline do dataset:

```bash
uv pip install -p .venv/bin/python -e ".[dev,desktop,dataset]"
```

O projeto não tem etapa de build no frontend: os arquivos em `src/buscateses/web/` são servidos como estão.

## Estrutura

| Caminho | Conteúdo |
| --- | --- |
| `src/buscateses/` | pacote principal (API, busca, catálogo, biblioteca, MCP, integrações, desktop, CLI) |
| `src/buscateses/web/` | interface: HTML, CSS, JS (ES modules) e fontes |
| `src/buscateses/data/` | léxico de truecasing e dados auxiliares |
| `tools/dataset/` | pipeline que gera o dataset público |
| `packaging/` | PyInstaller, DMG, Inno Setup, ícones e scripts de build |
| `scripts/` | instaladores por código-fonte (`install.sh`, `install.ps1`) |
| `tests/` | testes pytest com dados sintéticos |
| `e2e/` | testes Playwright + axe (interface e acessibilidade) |
| `docs/` | documentação pública; `docs/dev/` é interno |

## Comandos

```bash
# testes rápidos, sem rede
.venv/bin/python -m pytest -q -m "not slow"

# um arquivo ou teste específico
.venv/bin/python -m pytest -q tests/test_search.py
.venv/bin/python -m pytest -q tests/test_search.py::test_nome

# lint
.venv/bin/ruff check src tests packaging tools
.venv/bin/ruff format --check src tests packaging tools

# servidor local para desenvolvimento
.venv/bin/python -m buscateses serve --no-browser

# app com janela nativa (ou navegador, se a janela falhar)
.venv/bin/python -m buscateses

# diagnóstico
.venv/bin/python -m buscateses status
.venv/bin/python -m buscateses status --json
```

Testes de interface:

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

O `playwright.config.js` sobe automaticamente `tests/e2e/serve_fixture.py` em `127.0.0.1:8199`, com dados
sintéticos. Nenhum teste precisa de rede ou do catálogo real.

## Testes

- Os testes usam fixtures sintéticas (`tests/conftest.py`) e não acessam a rede.
- O marcador `slow` identifica testes que baixam o modelo ONNX real ou rodam benchmarks. Eles só executam com
  `BUSCATESES_RUN_SLOW=1`:

  ```bash
  BUSCATESES_RUN_SLOW=1 BUSCATESES_HOME=/tmp/buscateses-test-home \
    .venv/bin/python -m pytest -q -m slow
  ```

- O CI roda `pytest -q -m "not slow"` em Linux, macOS e Windows, com Python 3.11, 3.12 e 3.13, além do `ruff` e do
  Playwright.
- Use `BUSCATESES_HOME` para isolar a pasta de dados em testes manuais e scripts.
- Ao corrigir um bug, adicione um teste que falha antes da correção.

### Teste do endereço público (túnel real)

O workflow **Teste do endereço público** (`tunnel-e2e.yml`) abre um Cloudflare Quick Tunnel real em uma VM
descartável e exercita o MCP pela URL pública: handshake, `tools/list`, busca no catálogo, acesso sem o segredo
(404), rotação do segredo e `include_library=True` com `search_library`. Ele baixa o `cloudflared` oficial e confere
o SHA-256; nenhum segredo do repositório é usado.

O workflow é **somente manual** e não faz parte do CI comum. Para disparar:

```bash
gh workflow run tunnel-e2e.yml
gh run watch
```

Ele roda em `ubuntu-latest`, `macos-14` e `windows-latest` com Python 3.12, com limite de 15 minutos para o script.
O log de cada sistema fica no artefato `tunnel-e2e-<sistema>`. O script é `tests/e2e/tunnel_smoke.py`; não o execute
fora de uma VM descartável sem necessidade, pois ele usa a rede e abre um endereço público temporário.

## Estilo

- Type hints em todo o código público; `from __future__ import annotations` nos módulos.
- Docstrings curtas em **inglês**; mensagens para a pessoa usuária em **português do Brasil**, claras e acionáveis.
- Funções e módulos pequenos, sem estado global oculto; dependências injetáveis para facilitar testes.
- Linha de 100 caracteres (`ruff`), imports ordenados.
- Não adicione dependências pesadas ao app (PyTorch, bancos externos); o instalador precisa continuar pequeno.
- Nunca versione dados (`*.parquet`, `*.sqlite`, `*.npy`), PDFs, livros, tokens ou caminhos pessoais.
- O servidor deve escutar somente em `127.0.0.1`; o MCP deve continuar somente leitura.
- Mudanças de interface devem manter WCAG 2.2 AA e passar no `axe`.

## Documentação

A documentação pública fica em `docs/`. O teste `tests/test_docs.py` verifica se os links relativos dos arquivos
Markdown apontam para arquivos existentes e se o README cita os nomes reais dos instaladores. Rode-o ao alterar
documentação ou nomes de artefatos.

O `docs/dev/ESPECIFICACAO.md` é o documento interno de design: ele descreve formatos e decisões e deve ser atualizado
junto com mudanças estruturais.

## Empacotamento

```bash
python packaging/build.py
```

O script detecta o sistema, instala `packaging/requirements-build.txt`, gera os ícones ausentes, roda o PyInstaller,
executa o teste de fumaça (`--version`, `status --json` e `serve` em uma porta livre) e monta o instalador em
`packaging/output/`, com `SHA256SUMS.txt`.

Opções úteis durante o desenvolvimento:

```bash
python packaging/build.py --skip-deps     # não reinstala dependências
python packaging/build.py --skip-smoke    # pula o teste de fumaça
python packaging/build.py --skip-icons    # não regenera ícones
```

Detalhes de cada plataforma, nomes dos executáveis e funcionamento do Inno Setup estão em
[`packaging/README.md`](../packaging/README.md).

## Release (manual)

As releases são **sempre acionadas manualmente**. Não existe publicação automática em `push` nem em tag: os pushes do
mantenedor usam `[skip ci]` e as tags são criadas pelo próprio workflow, no commit em que ele foi disparado. Todos os
jobs de build rodam em `github.sha` (o commit selecionado no momento do disparo).

Antes de disparar:

1. Atualize `__version__` em `src/buscateses/__init__.py`.
2. Registre as mudanças no `CHANGELOG.md`, em uma seção `## [X.Y.Z] - AAAA-MM-DD` (as notas dessa seção são anexadas à
   release; se o arquivo não existir, o workflow usa um texto padrão).
3. Envie o commit e deixe o CI de `pull_request`/manual validar o que for necessário.

O formato do cabeçalho importa: o workflow extrai as notas com um `awk` que reconhece `## [X.Y.Z]` (ou `## X.Y.Z`) e
para na próxima seção `## `. Use exatamente:

```markdown
## [1.0.0] - 2026-10-21

### Adicionado
- ...
```

Dispare a release informando a tag (obrigatória, com prefixo `v`) e, opcionalmente, `prerelease`:

```bash
gh workflow run release.yml -f tag=v1.0.0
# pré-release:
gh workflow run release.yml -f tag=v1.1.0-rc1 -f prerelease=true
```

Acompanhe a execução com:

```bash
gh run watch
```

O workflow valida primeiro se a versão em `src/buscateses/__init__.py` corresponde à tag sem o `v` (`v1.0.0` ↔
`1.0.0`) e falha antes de qualquer build quando não corresponde. Ao final, ele cria a tag em `github.sha` e o GitHub
Release correspondente (via `softprops/action-gh-release@v2`), anexando todos os instaladores e o `SHA256SUMS.txt`
consolidado.

### Checklist rápido de release

- [ ] `__version__` atualizado e igual à tag sem `v`
- [ ] `CHANGELOG.md` com a seção da versão no formato acima
- [ ] CI verde no commit que será usado
- [ ] `gh workflow run release.yml -f tag=vX.Y.Z`
- [ ] instaladores e `SHA256SUMS.txt` anexados ao Release
- [ ] smoke test do app em pelo menos um sistema de cada plataforma

## Publicar uma nova versão do dataset

O dataset é publicado separadamente do app, em `andrebadini/catalogo-teses-capes`. O processo completo está em
[`tools/dataset/README.md`](../tools/dataset/README.md) e em [DADOS.md](DADOS.md). Em resumo:

```bash
.venv/bin/python tools/dataset/harvest.py --out tools/dataset/work/raw
.venv/bin/python tools/dataset/normalize.py --raw tools/dataset/work/raw --out tools/dataset/work/metadata
.venv/bin/python tools/dataset/build_truecase_lexicon.py --metadata tools/dataset/work/metadata
.venv/bin/python tools/dataset/embed.py --metadata tools/dataset/work/metadata --out tools/dataset/work/emb768 \
  --backend openai --url http://127.0.0.1:8000/v1 --batch 32
.venv/bin/python tools/dataset/finalize.py \
  --metadata tools/dataset/work/metadata --emb tools/dataset/work/emb768 \
  --out tools/dataset/work/release --version 2026.10.21
.venv/bin/python tools/dataset/publish.py --release tools/dataset/work/release \
  --repo andrebadini/catalogo-teses-capes --dry-run
.venv/bin/python tools/dataset/publish.py --release tools/dataset/work/release \
  --repo andrebadini/catalogo-teses-capes
```

O `publish.py` valida a allowlist de arquivos, a ausência de colunas proibidas e os SHA-256 antes de enviar. Use
`--dry-run` sempre antes de publicar.

## Solução de problemas de desenvolvimento

| Sintoma | O que verificar |
| --- | --- |
| `ModuleNotFoundError: buscateses` | instale com `-e` ou rode com `PYTHONPATH=src`. |
| Teste de interface não sobe | confira se o `.venv` existe em `tests/e2e/serve_fixture.py` e a porta 8199. |
| `ruff` reclama de imports | rode `ruff check --fix`. |
| Build do PyInstaller falha | confira `packaging/output/` e as mensagens do teste de fumaça. |
| Inno Setup não encontrado | instale o Inno Setup 6 ou defina `INNO_SETUP_COMPILER`. |
