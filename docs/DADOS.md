# Os dados

O BuscaTeses usa os **dados abertos do Catálogo de Teses e Dissertações da CAPES** e publica uma versão normalizada,
com vetores semânticos, no Hugging Face. Este documento explica a origem, a licença, o esquema e como reproduzir o
processo.

- Dataset: <https://huggingface.co/datasets/andrebadini/catalogo-teses-capes>
- Fonte: <https://dadosabertos.capes.gov.br>

## Origem

Os dados vêm de quatro pacotes CKAN da CAPES:

| Período | Pacote |
| --- | --- |
| 1987–2012 | [1987-a-2012-catalogo-de-teses-e-dissertacoes-brasil](https://dadosabertos.capes.gov.br/dataset/1987-a-2012-catalogo-de-teses-e-dissertacoes-brasil) |
| 2013–2016 | [catalogo-de-teses-e-dissertacoes-de-2013-a-2016](https://dadosabertos.capes.gov.br/dataset/catalogo-de-teses-e-dissertacoes-de-2013-a-2016) |
| 2017–2020 | [2017-2020-catalogo-de-teses-e-dissertacoes-da-capes](https://dadosabertos.capes.gov.br/dataset/2017-2020-catalogo-de-teses-e-dissertacoes-da-capes) |
| 2021–2024 | [2021-a-2024-catalogo-de-teses-e-dissertacoes-brasil](https://dadosabertos.capes.gov.br/dataset/2021-a-2024-catalogo-de-teses-e-dissertacoes-brasil) |

Os arquivos originais são CSV separados por ponto e vírgula, em codificação cp1252/latin-1, com quebras de linha CRLF.
Os dois períodos usam esquemas de colunas diferentes; o pipeline mapeia as colunas **pelo nome**, nunca pela posição, e
ignora colunas extras.

## Licença e atribuição

Os dados da CAPES são publicados sob **Creative Commons Atribuição (CC-BY)**. Ao usar o dataset, atribua a fonte:

> Fonte: CAPES — Catálogo de Teses e Dissertações, dados abertos sob licença Creative Commons Atribuição.
> Dataset normalizado: BuscaTeses, <https://huggingface.co/datasets/andrebadini/catalogo-teses-capes>.

O BuscaTeses é um projeto independente, **sem vínculo com a CAPES**. A licença MIT do código não se aplica aos dados.

## Cobertura por década

| Década | Registros |
| --- | ---: |
| 1980 (1987–1989) | 14.482 |
| 1990 | 120.881 |
| 2000 | 372.824 |
| 2010 | 732.156 |
| 2020 (2020–2024) | 430.185 |
| **Total** | **1.670.528** |

## Esquema das colunas

Cada linha de `metadata/*.parquet` representa uma tese ou dissertação. Os arquivos são ordenados por `(year, id)` e
divididos em partes de aproximadamente 250 mil linhas.

| Coluna | Tipo | Descrição |
| --- | --- | --- |
| `id` | string | identificador estável (`capes:<id>` ou `capes87:<hash>`) |
| `year` | int16 | ano de defesa/base |
| `defense_date` | string | data de defesa em `AAAA-MM-DD`, quando disponível |
| `title` | string | título do trabalho |
| `author` | string | nome da pessoa autora |
| `advisors` | string | orientação, separada por `;` (coorientação marcada com `(coorientador)`) |
| `degree` | string | `Mestrado`, `Doutorado`, `Mestrado Profissional`, `Doutorado Profissional` |
| `institution` | string | nome da instituição |
| `institution_acronym` | string | sigla da instituição |
| `uf` | string | unidade da federação (duas letras) |
| `region` | string | região do Brasil |
| `program` | string | nome do programa |
| `program_code` | string | código do programa na CAPES |
| `great_area` | string | grande área do conhecimento |
| `knowledge_area` | string | área do conhecimento |
| `evaluation_area` | string | área de avaliação |
| `concentration_area` | string | área de concentração |
| `research_line` | string | linha de pesquisa |
| `keywords` | string | palavras-chave separadas por `;` |
| `keywords_en` | string | palavras-chave em inglês, quando existirem |
| `abstract` | string | resumo |
| `abstract_en` | string | resumo em inglês, quando existir |
| `language` | string | idioma do trabalho |
| `pages` | int32 | número de páginas |
| `library` | string | biblioteca depositária |
| `url` | string | link para o texto completo, apenas `http`/`https` válidos |
| `source` | string | arquivo CSV de origem |

Colunas extras dos CSVs são descartadas. Valores vazios, `-`, `NAO INFORMADO`, `NÃO INFORMADO` e `null` viram nulo.
Títulos e resumos preservam a acentuação original.

### Embeddings

- `embeddings-768/*.parquet`: `id` + vetor `float16[768]`, alinhado linha a linha com `metadata`.
- `embeddings-256/*.parquet`: `id` + projeção Matryoshka (`float16[256]`), que é o que o app baixa.

O texto usado para gerar o vetor é
`title: {título ou "none"} | text: {resumo ou palavras-chave ou ""}`, truncado pelo tokenizador em 2048 tokens.
Antes, título, resumo e palavras-chave passam pelo *truecasing* (restauração de caixa e acentos).

### Manifesto

`manifest.json` descreve a versão, os anos cobertos, a licença da fonte, o modelo de embedding, o vetor de sonda e
cada arquivo com tamanho e SHA-256. O app usa esse arquivo para verificar se há atualização e para validar o download.

## Campos removidos por privacidade

Os seguintes campos dos CSVs **não** entram na saída, por serem dados pessoais ou identificadores internos:

- `DocumentoDiscente`;
- `DocumentoOrientador_1` a `DocumentoOrientador_4`;
- `DocumentoCoOrientador_1` a `DocumentoCoOrientador_4`;
- `ID_PESSOA_*`;
- `DT_MATRICULA`;
- `NM_TP_VINCULO`;
- campos de expectativa de atuação;
- categoria docente.

Nomes de autoria e orientação **são publicados**, porque já são públicos no catálogo da CAPES.

## Como foi gerado

O pipeline fica em [`tools/dataset`](../tools/dataset) e tem cinco etapas:

```mermaid
flowchart LR
    A["harvest.py<br/>baixa os CSVs"] --> B["normalize.py<br/>limpa, mapeia e deduplica"]
    B --> C["build_truecase_lexicon.py<br/>gera o léxico de caixa"]
    C --> D["embed.py<br/>gera vetores de 768"]
    D --> E["finalize.py<br/>projeta 256 e monta a release"]
    E --> F["publish.py<br/>valida e envia ao Hugging Face"]
```

1. **Coleta** (`harvest.py`): consulta a API CKAN dos quatro pacotes e baixa os CSVs com retomada.
2. **Normalização** (`normalize.py`): detecta o esquema pelo cabeçalho, limpa valores, converte datas e páginas,
   deduplica por `id` com DuckDB e grava Parquet com zstd.
3. **Léxico** (`build_truecase_lexicon.py`): gera `src/buscateses/data/truecase.json` a partir dos próprios metadados.
4. **Embeddings** (`embed.py`): gera vetores de 768 dimensões, localmente (SentenceTransformers) ou em um servidor
   compatível com a API OpenAI, em unidades retomáveis.
5. **Montagem e publicação** (`finalize.py` e `publish.py`): alinha os vetores por `id`, gera a projeção de 256,
   escreve o manifesto e o dataset card e valida allowlist, colunas proibidas e SHA-256 antes do envio.

## Como reproduzir ou atualizar

Pré-requisitos: Python 3.11–3.13, ~15 GB livres, 16 GB de RAM recomendados e, para embeddings locais,
`sentence-transformers` + `torch` (ou um servidor OpenAI-compatível).

```bash
uv pip install -p .venv/bin/python -r tools/dataset/requirements.txt

.venv/bin/python tools/dataset/harvest.py --out tools/dataset/work/raw
.venv/bin/python tools/dataset/normalize.py --raw tools/dataset/work/raw --out tools/dataset/work/metadata
.venv/bin/python tools/dataset/build_truecase_lexicon.py --metadata tools/dataset/work/metadata
.venv/bin/python tools/dataset/embed.py --metadata tools/dataset/work/metadata --out tools/dataset/work/emb768 \
  --backend openai --url http://127.0.0.1:8000/v1 --batch 32
.venv/bin/python tools/dataset/finalize.py \
  --metadata tools/dataset/work/metadata --emb tools/dataset/work/emb768 \
  --out tools/dataset/work/release --version 2026.10.21
.venv/bin/python tools/dataset/publish.py --release tools/dataset/work/release \
  --repo andrebadini/catalogo-teses-capes
```

Para publicar é preciso um `HF_TOKEN` com permissão de escrita. Use `--dry-run` no `publish.py` para validar sem
acessar a rede. O guia detalhado, com tempos esperados e solução de problemas, está em
[`tools/dataset/README.md`](../tools/dataset/README.md).

Para uma nova versão: rode o pipeline com os CSVs novos, escolha uma versão no formato `AAAA.MM.DD`, publique e o app
passará a oferecê-la na próxima verificação (o cache de atualização dura seis horas).

## Usar o dataset no Hugging Face

### pandas

```python
import pandas as pd

url = (
    "https://huggingface.co/datasets/andrebadini/catalogo-teses-capes/"
    "resolve/main/metadata/part-000.parquet"
)
df = pd.read_parquet(url)
print(df[["title", "author", "year", "institution"]].head())
```

Para ler todos os arquivos de uma vez, use `pd.read_parquet` em um diretório montado localmente, ou a biblioteca
`datasets` (abaixo).

### DuckDB

```sql
INSTALL httpfs;
LOAD httpfs;

SELECT year, count(*) AS trabalhos
FROM read_parquet('hf://datasets/andrebadini/catalogo-teses-capes/metadata/*.parquet')
GROUP BY year
ORDER BY year;
```

### datasets

```python
from datasets import load_dataset

ds = load_dataset(
    "andrebadini/catalogo-teses-capes",
    data_files="metadata/*.parquet",
)
print(ds["train"][0]["title"])
```

Para juntar metadados e vetores, carregue as duas pastas e faça a junção por `id`, mantendo a ordem dos arquivos.

## Limitações conhecidas

- **Só metadados.** O dataset não inclui o texto completo das teses; quando a CAPES informa, há um link em `url`.
- **Depende da fonte.** A cobertura e a qualidade variam por período; registros antigos podem ter campos vazios e
  resumos ausentes.
- **Textos em caixa alta.** Muitos registros antigos estão em maiúsculas e sem acentos. O app os apresenta
  normalizados, mas as colunas publicadas preservam o valor original.
- **Duplicatas.** A deduplicação é por `id`; registros genuinamente diferentes com o mesmo identificador podem ter sido
  unificados.
- **Datas.** `defense_date` pode ser nula ou estar incompleta em registros antigos.
- **Idioma.** A maior parte do catálogo está em português; resumos em inglês existem apenas em parte dos registros.
- **Embeddings.** Os vetores refletem o modelo `google/embeddinggemma-300m` e a data de geração; comparar vetores de
  versões diferentes do dataset não é recomendado.
- **Licença dos dados.** CC-BY exige atribuição; a licença MIT do BuscaTeses cobre apenas o código.
