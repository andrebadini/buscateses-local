# Pipeline do dataset BuscaTeses

Este diretório gera, de forma reprodutível, a release pública do catálogo de
teses e dissertações da CAPES publicada em
`huggingface.co/datasets/andrebadini/catalogo-teses-capes` e consumida pelo
aplicativo [BuscaTeses](https://github.com/andrebadini/buscateses-local).

O resultado contém:

- `metadata/part-XXX.parquet` — metadados normalizados (esquema da ESPECIFICACAO §3.1),
  ordenados por `(year, id)`.
- `embeddings-768/part-XXX.parquet` — vetores de 768 dimensões
  (`float16`) alinhados linha a linha com os metadados.
- `embeddings-256/part-XXX.parquet` — projeção Matryoshka: os 256 primeiros
  valores, re-normalizados em L2.
- `manifest.json` — contagens, anos, hashes SHA-256, versão e vetor de sonda.
- `README.md` — dataset card do Hugging Face.

## Pré-requisitos

- Python 3.11–3.13 (o projeto empacota com 3.12).
- ~15 GB de espaço livre em disco (6,3 GB de CSVs + staging + release).
- 16 GB de RAM recomendados; o `normalize.py` limita o DuckDB a 4 GB.
- Para embeddings locais: `sentence-transformers` + `torch` e, de
  preferência, GPU/MPS.
- Para embeddings remotos: um servidor compatível com a API OpenAI
  (`POST /v1/embeddings`), por exemplo Hugging Face Text Embeddings Inference
  (TEI), LM Studio ou Ollama.
- Para publicar: `HF_TOKEN` com permissão de escrita no repositório de destino.

Instalação das dependências:

```bash
uv pip install -p .venv/bin/python -r tools/dataset/requirements.txt
# opcional, backend local:
uv pip install -p .venv/bin/python sentence-transformers torch
```

Os comandos abaixo assumem que você está na raiz do repositório.

## Passo a passo

### 1. Coletar os CSVs (`harvest.py`)

```bash
.venv/bin/python tools/dataset/harvest.py --out tools/dataset/work/raw
```

Consulta a API CKAN (`package_show`) dos quatro pacotes da ESPECIFICACAO §3 e baixa
todos os recursos CSV com retomada (`Range`) e cinco tentativas por arquivo.
Ao final grava `work/raw/_sources.json` com URL, tamanho, SHA-256 e
`last_modified` de cada arquivo.

Em algumas redes o host `dadosabertos.capes.gov.br` não resolve; force o IP:

```bash
.venv/bin/python tools/dataset/harvest.py \
  --out tools/dataset/work/raw --capes-ip 200.130.18.200
```

Tempo esperado: 10–40 min (depende da rede; são ~6,3 GB).

### 2. Normalizar (`normalize.py`)

```bash
.venv/bin/python tools/dataset/normalize.py \
  --raw tools/dataset/work/raw \
  --out tools/dataset/work/metadata
```

- Detecta o esquema pelo cabeçalho (legado 1987–2012 ou moderno 2013+).
- Mapeia colunas **por nome**, limpa valores nulos, datas, palavras-chave,
  grau, URLs e páginas. Sentinelas de páginas que não cabem em `int32` (por
  exemplo `9999999999`) são gravadas como nulo.
- Converte cada CSV em um Parquet intermediário em `work/stage/`, deduplica
  por `id` com DuckDB (mantém a linha com mais campos preenchidos; desempate
  pelo arquivo mais recente) e grava as partes finais com zstd.
- Campos pessoais/identificadores nunca são copiados para a saída.
- Grava `work/normalize_report.json` com linhas lidas/descartadas por arquivo,
  duplicatas removidas, total, contagem por ano e percentual de nulos.

Use `--rows-per-part N` para mudar o tamanho das partes (padrão 250 000).

Tempo esperado: 10–25 min. Pico de RAM: ~4 GB (DuckDB) + ~0,5 GB durante a
conversão de um arquivo.

## Trabalhos recentes (2025 em diante)

Os pacotes de dados abertos da CAPES terminam em 2024. Os anos seguintes são
coletados de duas fontes públicas, por API:

- **Catálogo de Teses e Dissertações da CAPES**
  (`https://catalogodeteses.capes.gov.br/catalogo-teses/rest/busca`) — retorna
  20 registros por página, independentemente de `registrosPorPagina`;
- **BDTD/IBICT** (`https://bdtd.ibict.br/vufind/api/v1/search`) — cada consulta
  alcança no máximo 1.000 registros (páginas mais profundas repetem a última
  janela). Anos maiores são divididos recursivamente pelo prefixo do
  identificador (`id:U*`, `id:USP_*`...), até cada parte ter no máximo 1.000
  registros. Os prefixos são disjuntos, e a soma das partes é conferida contra
  o `resultCount` em cada nível; a coleta falha se não fechar.

### Coleta (`harvest_recent.py`)

```bash
.venv/bin/python tools/dataset/harvest_recent.py \
  --out tools/dataset/work/recent/raw \
  --years 2025 2026
```

Sem `--years`, o coletor usa o ano seguinte ao último ano dos metadados
(`--metadata tools/dataset/work/metadata`) até o ano corrente. Cada página é
gravada em
`raw/<fonte>/<ano>/<partição>/page-00001.json.gz` e a execução é retomável:
páginas já baixadas são reaproveitadas. O coletor usa o User-Agent
`BuscaTeses-dataset`, **0,5 requisição por segundo** por padrão (a BDTD
responde 429 a rajadas mais rápidas) e até cinco tentativas com espera
exponencial para 429/5xx/timeout. Cada consulta leva no máximo dois
`filter[]` (o ano e **um** prefixo de identificador), porque a API também
responde 429 quando recebe mais de uma restrição extra. O plano de
partições fica em `raw/<fonte>/<ano>/_plan.json` e é reutilizado enquanto o
total do ano não mudar, para que uma execução interrompida retome exatamente
as mesmas partes. Ao final grava `raw/_sources_recent.json` com URL,
parâmetros, total esperado e total coletado por fonte/ano/partição.

Para um teste rápido, use `--max-pages N` e `--sources bdtd` (ou `capes`).

### Detalhes da Plataforma Sucupira (`enrich_sucupira.py`)

A busca do Catálogo da CAPES não traz resumo, palavras-chave nem orientação.
Esses campos estão na página pública de cada trabalho na Plataforma Sucupira
(`viewTrabalhoConclusao.jsf?id_trabalho=<id>`). O script visita cada página uma
vez e guarda **apenas** os campos do esquema público (resumo, abstract,
palavras-chave, idioma, biblioteca, área de concentração, linha de pesquisa,
código do programa e orientação) em `raw/sucupira/<ano>/<id>.json.gz`. Nomes
da banca, vínculos e descrições de projeto que aparecem na mesma página são
ignorados e nunca gravados.

```bash
.venv/bin/python tools/dataset/enrich_sucupira.py \
  --raw tools/dataset/work/recent/raw --rate 1
```

A execução é retomável. O `normalize_recent.py` usa esses detalhes quando
existem e descarta valores de preenchimento como `xx`.

### Normalização (`normalize_recent.py`)

```bash
.venv/bin/python tools/dataset/normalize_recent.py \
  --raw tools/dataset/work/recent/raw \
  --existing tools/dataset/work/metadata \
  --out tools/dataset/work/recent/metadata
```

O script lê o raw e os metadados existentes e grava uma parte numerada depois
da última existente (por exemplo, `work/recent/metadata/part-007.parquet`),
com o mesmo esquema, compressão zstd e ordenação por `(year, id)`. Também grava
`work/recent/normalize_recent_report.json`.

Regras principais:

- mantém apenas anos a partir do seguinte ao último metadado (ou `--first-year`);
- BDTD: `id` vira `bdtd:<id>`, autores em `Sobrenome, Nome` são invertidos,
  `dc.date.issued` define o ano e a data, e `Mestrado/Doutorado Profissional`
  é detectado na citação;
- CAPES: `id` vira `capes-catalogo:<id>`, com datas, grau, páginas e URL
  normalizados;
- instituição/sigla/UF/região e código/áreas do programa são preenchidos a
  partir dos metadados históricos quando há correspondência exata de nome;
- áreas CNPq da BDTD são traduzidas para os rótulos já usados no dataset;
- descarta registros já publicados (mesmo `id` na URL da CAPES ou mesmo título
  normalizado com ao menos um sobrenome de autoria em comum);
- entre fontes, prefere o registro do Catálogo de Teses da CAPES e completa os
  campos ausentes com a BDTD; entre redes da BDTD, mantém o mais completo.

Depois da normalização, rode o `embed.py` também sobre
`work/recent/metadata`, com um diretório de saída separado (por exemplo,
`work/recent/emb768`). O léxico de truecasing pode continuar sendo o gerado a
partir dos metadados históricos.

### 3. Gerar embeddings (`embed.py`)

Com um servidor OpenAI-compatível (ex.: TEI em `http://127.0.0.1:8000/v1`):

```bash
export TEI_API_KEY=...   # se o servidor exigir
.venv/bin/python tools/dataset/embed.py \
  --metadata tools/dataset/work/metadata \
  --out tools/dataset/work/emb768 \
  --backend openai --url http://127.0.0.1:8000/v1 \
  --api-key-env TEI_API_KEY --concurrency 3 --batch 32
```

Localmente com SentenceTransformers:

```bash
export HF_TOKEN=...      # necessário para baixar google/embeddinggemma-300m
.venv/bin/python tools/dataset/embed.py \
  --metadata tools/dataset/work/metadata \
  --out tools/dataset/work/emb768 \
  --backend st --device mps --batch 32
```

O trabalho é dividido em unidades de 8192 linhas. Cada processo reivindica uma
unidade criando atomicamente `unit-XXXXX.lock`; unidades concluídas são puladas
e locks com mais de 2 h sem saída podem ser retomados. É seguro rodar vários
processos ao mesmo tempo no mesmo diretório (por exemplo, um com `--backend
openai` e outro com `--backend st`). Use `--max-units N` para um teste rápido.

O texto do documento é
`title: {título ou "none"} | text: {resumo ou palavras-chave ou ""}`,
truncado pelo tokenizador em até 512 tokens (tokens especiais incluídos).

Antes de montar o texto, título, resumo e palavras-chave passam por
**truecasing** (`buscateses.textcase`): os registros em CAIXA ALTA da CAPES
viram caixa normal, com acentos restaurados nos anos antigos, siglas e nomes
próprios preservados. Isso reduz o número de tokens e melhora a recuperação
sem alterar os metadados publicados.

O léxico usado pelo truecasing é gerado a partir dos próprios metadados:

```bash
.venv/bin/python tools/dataset/build_truecase_lexicon.py \
  --metadata tools/dataset/work/metadata
```

O comando grava `src/buscateses/data/truecase.json` (amostragem determinística
de até 600 mil textos; saída limitada a 40 mil entradas e 1,5 MB). Rode-o de
novo apenas quando os metadados mudarem.

Tempo esperado: 30–90 min com GPU/MPS; 2–6 h em CPU; depende do servidor remoto.

### 4. Montar a release (`finalize.py`)

```bash
.venv/bin/python tools/dataset/finalize.py \
  --metadata tools/dataset/work/metadata \
  --metadata tools/dataset/work/recent/metadata \
  --emb tools/dataset/work/emb768 \
  --emb tools/dataset/work/recent/emb768 \
  --out tools/dataset/work/release \
  --version 2026.10.21
```

`--metadata` e `--emb` podem ser repetidos para combinar diretórios. O
finalize junta todas as partes, ordena por `(year, id)`, alinha os embeddings
por `id`, gera a projeção de 256 dimensões e o `manifest.json` (que passa a
incluir a chave `sources`, com nome, URL, número de registros e observação de
licença por fonte). Com um único diretório, as fronteiras das partes são
preservadas; com vários, as partes são re-agrupadas em blocos de 250 mil
linhas. Falha se algum `id` não tiver embedding; use `--allow-missing` apenas
em caráter excepcional (a contagem é registrada no manifesto). O vetor de
sonda é calculado pelo backend indicado em `--probe-backend` (aceita os mesmos
parâmetros do `embed.py`).

No fim, o finalize também escreve o card do dataset (`README.md`), as fontes SVG
dos gráficos em `assets/` e o cache `card_stats.json`.

Tempo esperado: 5–15 min (um pouco mais ao combinar diretórios).

### 5. Gerar o card e as imagens (`card.py`)

```bash
uv pip install -p .venv/bin/python playwright   # uma vez; usa o Chromium do Playwright
.venv/bin/python -m playwright install chromium   # uma vez
.venv/bin/python tools/dataset/card.py --release tools/dataset/work/release \
  --app-banner docs/assets
```

O card é gerado a partir dos próprios arquivos da release: o `card.py` lê os
metadados com o DuckDB (contagens por ano e fonte, grandes áreas, nível, região,
instituições e completude dos campos), desenha o banner e os gráficos em SVG e
os converte para PNG em 2x com as fontes do app (Literata e Atkinson
Hyperlegible Next). O card tem banner, selos, números principais, exemplos de
uso (DuckDB, `datasets` e busca semântica), gráficos, esquema, completude por
fonte, detalhes dos embeddings, usos, limitações, versões, citação (BibTeX e
ABNT) e uma seção completa em inglês. `--app-banner` também atualiza o banner do
README do GitHub (`docs/assets/banner.png` e `banner.en.png`) com o número de
registros. A cada release, acrescente uma linha em `RELEASE_NOTES` no
`card.py`. `--no-png` gera só os SVG; `--refresh` recalcula as estatísticas.

### 6. Publicar (`publish.py`)

```bash
export HF_TOKEN=...
.venv/bin/python tools/dataset/publish.py \
  --release tools/dataset/work/release \
  --repo andrebadini/catalogo-teses-capes
```

Antes de enviar, valida estritamente:

- allowlist de arquivos (`README.md`, `manifest.json`, `*.parquet` nas três
  pastas e `assets/*.png`; `card_stats.json` e `assets/*.svg` ficam só na
  máquina local);
- presença de todas as imagens que o card usa em `assets/`;
- ausência de colunas proibidas nos Parquet;
- tamanho e SHA-256 de cada arquivo contra o manifesto.

Use `--dry-run` para executar todas as verificações sem acessar a rede. Para
atualizar só o card de uma release já publicada (sem criar tag), use
`--card-only`: valida a release inteira e envia apenas `README.md` e
`assets/*.png`.

Tempo esperado: 10–30 min (depende do upload de ~1–2 GB).

## Estrutura de saída

```text
tools/dataset/work/
  raw/            CSVs originais + _sources.json
  stage/          Parquet intermediário e banco temporário do DuckDB
  metadata/       part-000.parquet, part-001.parquet, ...
  emb768/         unit-00000.parquet, unit-00001.parquet, ...
  recent/
    raw/          páginas JSON.gz + _sources_recent.json
    metadata/     part-007.parquet (numerada após as existentes)
    emb768/       unidades dos trabalhos recentes
    normalize_recent_report.json
  release/
    metadata/            part-XXX.parquet
    embeddings-768/      part-XXX.parquet
    embeddings-256/      part-XXX.parquet
    assets/              banner e gráficos do card (PNG publicados, SVG locais)
    manifest.json
    README.md
    card_stats.json      estatísticas do card (local)
  normalize_report.json
```

`tools/dataset/work/` está no `.gitignore`: nenhum dado derivado é versionado.

## Solução de problemas

- **`dadosabertos.capes.gov.br` não resolve**: use `--capes-ip`.
- **Download interrompido**: rode o `harvest.py` de novo; ele retoma via `Range`.
- **Embedding com dimensão errada**: o backend precisa devolver exatamente 768
  valores; confirme que o modelo servido é o EmbeddingGemma.
- **Falta de memória no normalize**: reduza `--rows-per-part` (não altera o
  consumo do DuckDB) ou aumente o espaço livre; o staging mantém o uso de
  memória sob controle.
- **`publish.py` recusa um arquivo**: a release contém algo fora da allowlist;
  remova o arquivo antes de publicar.
- **Coleta recente lenta ou travada**: o `harvest_recent.py` força IPv4, que
  costuma ser mais estável para esses servidores. Se uma resposta demorar,
  interrompa e rode de novo: as páginas já baixadas são reaproveitadas.
- **BDTD recusa páginas profundas**: a API só serve os primeiros 1.000
  registros de cada consulta. O coletor divide o ano pelo prefixo do
  identificador até cada parte caber; se a soma das partes não fechar com o
  total da API, ele falha com uma mensagem clara em vez de publicar dados
  incompletos.
- **BDTD responde 429 (muitas requisições)**: use `--rate 0.5` (padrão) ou
  menos; o plano e as páginas já baixadas são reaproveitados. O coletor já
  tenta cinco vezes com espera exponencial. Consultas com mais de uma
  restrição além do ano também provocam 429; o coletor sempre as agrupa em uma
  única expressão booleana.
- **Ano recente com total 0 na CAPES**: o catálogo pode ainda não ter publicado
  aquele ano; a BDTD costuma estar disponível antes. Rode a coleta novamente
  mais tarde.
