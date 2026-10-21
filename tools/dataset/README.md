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
  --emb tools/dataset/work/emb768 \
  --out tools/dataset/work/release \
  --version 2026.10.21
```

Copia os metadados, alinha os embeddings por `id`, gera a projeção de 256
dimensões e o `manifest.json`. Falha se algum `id` não tiver embedding; use
`--allow-missing` apenas em caráter excepcional (a contagem é registrada no
manifesto). O vetor de sonda é calculado pelo backend indicado em
`--probe-backend` (aceita os mesmos parâmetros do `embed.py`).

Tempo esperado: 5–15 min.

### 5. Publicar (`publish.py`)

```bash
export HF_TOKEN=...
.venv/bin/python tools/dataset/publish.py \
  --release tools/dataset/work/release \
  --repo andrebadini/catalogo-teses-capes
```

Antes de enviar, valida estritamente:

- allowlist de arquivos (`README.md`, `manifest.json` e `*.parquet` nas três
  pastas);
- ausência de colunas proibidas nos Parquet;
- tamanho e SHA-256 de cada arquivo contra o manifesto.

Use `--dry-run` para executar todas as verificações sem acessar a rede.

Tempo esperado: 10–30 min (depende do upload de ~1–2 GB).

## Estrutura de saída

```text
tools/dataset/work/
  raw/            CSVs originais + _sources.json
  stage/          Parquet intermediário e banco temporário do DuckDB
  metadata/       part-000.parquet, part-001.parquet, ...
  emb768/         unit-00000.parquet, unit-00001.parquet, ...
  release/
    metadata/            part-XXX.parquet
    embeddings-768/      part-XXX.parquet
    embeddings-256/      part-XXX.parquet
    manifest.json
    README.md
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
