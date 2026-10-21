# BuscaTeses — Especificação técnica (fonte da verdade para implementação)

> **Documento interno de design.** Este arquivo descreve decisões técnicas e
> formatos internos do BuscaTeses. Ele é a referência para quem mantém o
> código, não um guia para pessoas usuárias. A documentação pública começa no
> [`README.md`](../../README.md); os guias de uso ficam em [`docs/`](../).

Projeto open source (MIT) para busca inteligente, executada localmente (sem servidor remoto), no Catálogo de Teses e
Dissertações da CAPES (dados abertos, CC-BY) e em documentos próprios do usuário
("Minha biblioteca"). Público-alvo: pesquisadores leigos em programação.

Repositório: github.com/andrebadini/buscateses-local · Dataset: huggingface.co/datasets/andrebadini/catalogo-teses-capes
Nome do produto: **BuscaTeses**. Pacote Python: `buscateses` (distribuição `buscateses-local`). Python 3.11–3.13 (empacotado com 3.12).
Idioma da interface: pt-BR. Código, identificadores e docstrings em inglês; mensagens ao usuário em pt-BR.

## 0. Princípios
- Funciona para leigos: nenhuma etapa obrigatória fora do app (sem LM Studio, sem terminal).
- Tudo local; o servidor web escuta somente em 127.0.0.1. Sem telemetria, sem contas.
- Degradação graciosa: sem modelo de embedding → busca textual continua funcionando, com aviso.
- Código limpo: módulos pequenos, tipados, sem estado global oculto, testes com fixtures sintéticas.
- NUNCA incluir no repositório: livros, PDFs, dados privados, tokens, caminhos absolutos da máquina do autor.

## 1. Estrutura do repositório
```
src/buscateses/
  __init__.py          (__version__)
  __main__.py          (python -m buscateses)
  cli.py               (argparse; subcomandos abaixo)
  config.py            (Paths, Settings: leitura/escrita de config.json; env overrides)
  jobs.py              (JobManager: tarefas em thread com progresso consultável)
  embeddings/
    __init__.py        (get_embedder(settings) -> Embedder)
    base.py            (Protocol Embedder; prompts; truncate_normalize())
    local_onnx.py      (LocalOnnxEmbedder + download do modelo)
    openai_compat.py   (OpenAICompatibleEmbedder: LM Studio, Ollama, TEI, etc.)
  catalog/
    __init__.py
    manifest.py        (modelo e validação do manifest.json do dataset)
    installer.py       (download HF + verificação + build local + troca atômica + rollback)
    builder.py         (Parquet -> SQLite/FTS5 + vectors.npy + facets)
    search.py          (CatalogSearch: textual, semântica, híbrida RRF, filtros, facetas, similares)
  library/
    __init__.py
    extract.py         (PDF via pypdfium2, EPUB, DOCX, TXT/MD/HTML -> páginas de texto)
    chunking.py        (chunks ~1000 chars, overlap 150, preservando página)
    store.py           (library.sqlite + vectors.npy; CRUD; busca híbrida)
    ingest.py          (pipeline de ingestão como Job)
  exporters.py         (CSV, BibTeX, RIS, referência ABNT)
  favorites.py         (lista "Minha seleção" em user.sqlite)
  integrations.py      (configuração automática do MCP em clientes de IA)
  mcp_server.py        (FastMCP: stdio e Streamable HTTP, somente leitura)
  remote.py            (endereço público temporário: cloudflared + MCP HTTP com segredo no caminho)
  api/
    __init__.py        (create_app(): FastAPI + estáticos + segurança)
    security.py        (Host/Origin/CSRF header; token MCP HTTP)
    routes_*.py        (status, setup, search, theses, export, favorites, library, settings, integrations, remote)
  desktop.py           (janela nativa pywebview + servidor em thread; fallback navegador)
  web/                 (frontend estático, sem build: index.html, css/, js/ (ES modules), assets/)
tools/dataset/         (pipeline reprodutível de geração do dataset — ver §3)
packaging/             (PyInstaller spec, Inno Setup .iss, script DMG, ícones)
scripts/               (install.sh, install.ps1 — instalação via código-fonte)
tests/                 (pytest; fixtures sintéticas; nunca dependem de rede ou do dataset real)
docs/                  (documentação para usuários)
```

## 2. Diretório de dados do usuário
`platformdirs.user_data_dir("BuscaTeses", appauthor=False)` (macOS: ~/Library/Application Support/BuscaTeses;
Windows: %LOCALAPPDATA%\BuscaTeses; Linux: ~/.local/share/BuscaTeses). Override: env `BUSCATESES_HOME`.
```
config.json                 settings (ver §6)
catalog/current.json        {"version": "2026.10.21", "dir": "releases/2026.10.21"}
catalog/releases/<v>/       catalog.sqlite, vectors.npy (float16 [N,256], linha i == theses.rowid i+1), manifest.json, BUILD_OK
                            (reinstalações da mesma versão usam <v>+f<format>[-N]; current.json aponta para o diretório real)
catalog/downloads/<v>/      parquet baixados (apagados após build bem-sucedido)
models/embeddinggemma-300m/ tokenizer.json, onnx/model_quantized.onnx, onnx/model_quantized.onnx_data, READY
library/library.sqlite, library/vectors.npy (+ vectors_ids), library/files/<sha256>.<ext>
user.sqlite                 favoritos
logs/buscateses.log         (rotativo, 5 MB x 3)
runtime.json                {"pid":..., "port":..., "started_at":...} (instância única)
bin/cloudflared-<versão>    conector baixado sob demanda e verificado por SHA-256 (remote)
remote.json                 estado publicado por `buscateses remote start` (porta, pid, URL; removido ao parar)
```

## 3. Dataset público (gerado por tools/dataset, publicado no Hugging Face)
Fonte: dadosabertos.capes.gov.br, pacotes CKAN:
`1987-a-2012-catalogo-de-teses-e-dissertacoes-brasil`, `catalogo-de-teses-e-dissertacoes-de-2013-a-2016`,
`2017-2020-catalogo-de-teses-e-dissertacoes-da-capes`, `2021-a-2024-catalogo-de-teses-e-dissertacoes-brasil`.
Licença da fonte: Creative Commons Attribution (cc-by). CSVs `;`, codificação cp1252/latin-1, CRLF, aspas possíveis.

Dois esquemas de entrada:
- 1987–2012 (`dados_YYYY.csv`): AnoBase;CodigoPrograma;Regiao;Uf;SiglaIes;NomeIes;NomePrograma;GrandeAreaCodigo;GrandeAreaDescricao;AreaConhecimentoCodigo;AreaConhecimento;AreaAvaliacao;DocumentoDiscente;Autor;TituloTese;Nivel;DataDefesa;PalavrasChave;Volume;NumeroPaginas;BibliotecaDepositaria;Idioma;ResumoTese;LinhaPesquisa;URLTextoCompleto;DocumentoOrientador_1;Orientador_1;...;Orientador_4;DocumentoCoOrientador_1;CoOrientador_1;...;CoOrientador_4
- 2013+ (`br-capes-btd-*.csv`): AN_BASE;CD_PROGRAMA;NM_PROGRAMA;[CD_ENTIDADE_CAPES;]SG_ENTIDADE_ENSINO;NM_ENTIDADE_ENSINO;ID_ADD_PRODUCAO_INTELECTUAL;ID_PRODUCAO_INTELECTUAL;NM_PRODUCAO;ID_SUBTIPO_PRODUCAO;NM_SUBTIPO_PRODUCAO;ID_AREA_CONCENTRACAO;NM_AREA_CONCENTRACAO;ID_LINHA_PESQUISA;NM_LINHA_PESQUISA;...;DT_TITULACAO;DS_PALAVRA_CHAVE;DS_ABSTRACT;DS_KEYWORD;...;NM_DISCENTE;...;NM_GRAU_ACADEMICO;NM_ORIENTADOR;...;NM_REGIAO;SG_UF_IES;...;NM_GRANDE_AREA_CONHECIMENTO;...;NM_AREA_CONHECIMENTO;...;NM_AREA_AVALIACAO;NR_VOLUME;NR_PAGINAS;NM_IDIOMA;DS_RESUMO;DS_URL_TEXTO_COMPLETO;...
  (mapear colunas por NOME, nunca por posição; colunas extras ignoradas)

Campos PROIBIDOS na saída (dados pessoais/identificadores): DocumentoDiscente, DocumentoOrientador_*, DocumentoCoOrientador_*, ID_PESSOA_*, DT_MATRICULA, NM_TP_VINCULO, expectativa de atuação, categoria docente. Nomes de autor/orientador SÃO publicados (são públicos no catálogo).

### 3.1 metadata/*.parquet (zstd, ~250k linhas por arquivo, ordenado por year, id)
| coluna | tipo | origem 2013+ | origem 1987–2012 |
|---|---|---|---|
| id | string | `capes:{ID_ADD_PRODUCAO_INTELECTUAL}` | `capes87:{sha1(ano|CodigoPrograma|Autor|TituloTese normalizados)[:16]}` |
| year | int16 | AN_BASE | AnoBase |
| defense_date | string ISO `YYYY-MM-DD` ou null | DT_TITULACAO | DataDefesa (dd/mm/aaaa) |
| title | string | NM_PRODUCAO | TituloTese |
| author | string | NM_DISCENTE | Autor |
| advisors | string ("A; B") | NM_ORIENTADOR | Orientador_1..4 + CoOrientador_1..4 (co-orientadores com sufixo " (coorientador)") |
| degree | string normalizado: Mestrado, Doutorado, Mestrado Profissional, Doutorado Profissional, Profissionalizante → "Mestrado Profissional" | NM_GRAU_ACADEMICO / NM_SUBTIPO_PRODUCAO | Nivel |
| institution | string | NM_ENTIDADE_ENSINO | NomeIes |
| institution_acronym | string | SG_ENTIDADE_ENSINO | SiglaIes |
| uf | string(2) | SG_UF_IES | Uf |
| region | string | NM_REGIAO | Regiao |
| program | string | NM_PROGRAMA | NomePrograma |
| program_code | string | CD_PROGRAMA | CodigoPrograma |
| great_area | string | NM_GRANDE_AREA_CONHECIMENTO | GrandeAreaDescricao |
| knowledge_area | string | NM_AREA_CONHECIMENTO | AreaConhecimento |
| evaluation_area | string | NM_AREA_AVALIACAO | AreaAvaliacao |
| concentration_area | string | NM_AREA_CONCENTRACAO | null |
| research_line | string | NM_LINHA_PESQUISA | LinhaPesquisa |
| keywords | string ("a; b; c") | DS_PALAVRA_CHAVE | PalavrasChave |
| keywords_en | string | DS_KEYWORD | null |
| abstract | string | DS_RESUMO | ResumoTese |
| abstract_en | string | DS_ABSTRACT | null |
| language | string | NM_IDIOMA | Idioma |
| pages | int32 | NR_PAGINAS | NumeroPaginas |
| library | string | DS_BIBLIOTECA_DEPOSITARIA | BibliotecaDepositaria |
| url | string (só http/https válidos) | DS_URL_TEXTO_COMPLETO | URLTextoCompleto |
| source | string | nome do arquivo CSV | nome do arquivo CSV |
Limpeza: strip, colapsar espaços, valores vazios/"-"/"NAO INFORMADO"/"NÃO INFORMADO"/"null" → null; títulos e resumos preservam acentuação; palavras-chave com separadores `;`, `,` (quando sem `;`), ou blocos de espaços múltiplos (1987–2012) → "; ". Deduplicar por id (manter a linha com mais campos preenchidos e o arquivo mais recente).

### 3.2 Embeddings
Modelo: `google/embeddinggemma-300m` (768 dims, normalizado). Texto de documento:
`title: {title or "none"} | text: {abstract or keywords or ""}` (truncado pelo tokenizador a 2048 tokens).
Prompt de consulta: `task: search result | query: {query}`.
- `embeddings-768/*.parquet`: id string, embedding fixed_size_list<float16>[768] (mesma ordem/particionamento do metadata).
- `embeddings-256/*.parquet`: id string, embedding fixed_size_list<float16>[256] = primeiros 256 valores re-normalizados (Matryoshka). É o que o app baixa.

### 3.3 manifest.json (raiz do repositório HF; versão = tag git no HF)
```json
{"schema": 1, "version": "2026.10.21", "created_at": "...", "revision": "2026.10.21",
 "records": 0, "years": [1987, 2024],
 "source": {"name": "CAPES — Catálogo de Teses e Dissertações", "url": "https://dadosabertos.capes.gov.br", "license": "CC-BY"},
 "embedding": {"model": "google/embeddinggemma-300m", "onnx_repo": "onnx-community/embeddinggemma-300m-ONNX",
   "app_dims": 256, "full_dims": 768, "document_template": "title: {title} | text: {text}",
   "query_template": "task: search result | query: {query}",
   "probe": {"text": "task: search result | query: educação inclusiva", "vector_768_head": [16 floats]}},
 "files": [{"path": "metadata/part-000.parquet", "kind": "metadata", "rows": 0, "bytes": 0, "sha256": "..."},
           {"path": "embeddings-256/part-000.parquet", "kind": "embeddings-256", ...},
           {"path": "embeddings-768/part-000.parquet", "kind": "embeddings-768", ...}],
 "min_app_version": "1.0.0"}
```

## 4. Catálogo local (catalog/)
O formato local atual é o **v2**: resumos comprimidos com zlib (nível 6), índice FTS5 *contentless* e vetores
`float16` [N,256]. Catálogos v1 (texto puro, vetores float32 e FTS com *external content*) continuam legíveis, mas são
reconstruídos na próxima atualização.

installer: `check_update()` lê manifest de `https://huggingface.co/datasets/{repo}/resolve/main/manifest.json`;
`install(version)` baixa apenas `kind in {metadata, embeddings-256}` (hf_hub_download, repo_type="dataset",
revision=manifest.revision, local_dir=downloads/<v>), verifica bytes+sha256, verifica espaço livre (≥ 2,5× o
download), chama builder, grava BUILD_OK, troca `current.json` atomicamente (escrita em tmp + os.replace),
mantém a release anterior até a nova abrir com sucesso; depois remove releases antigas (mantém só a anterior).
Reinstalar a mesma versão do dataset em um formato diferente gera diretórios `<versão>+f<format>` (e `-1`, `-2`… se
necessário), fechando o catálogo em uso antes da troca e reabrindo-o no MCP quando `current.json` muda.
Progresso reportado ao Job (stage: "download" | "verify" | "build_db" | "build_index" | "finalize", bytes/rows).
Download retomável (o cache do hf_hub retoma; se falhar, nova tentativa até 3x).
Env `BUSCATESES_DATASET_REPO` (padrão andrebadini/catalogo-teses-capes) e `BUSCATESES_DATASET_URL` (base alternativa para testes: diretório local ou http).

builder: lê parquet em lotes (pyarrow), cria `catalog.sqlite`:
- `theses(rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, year, defense_date, title, author, advisors, degree, institution, institution_acronym, uf, region, program, program_code, great_area, knowledge_area, evaluation_area, concentration_area, research_line, keywords, keywords_en, abstract_z BLOB, abstract_en_z BLOB, language, pages, library, url)`
  — resumos comprimidos com zlib (nível 6); utilitários `pack_text`/`unpack_text` (`None` ↔ `NULL`).
- índices em year, degree, uf, institution_acronym, great_area, knowledge_area
- `theses_fts` FTS5 contentless (`content=''`) colunas (title, keywords, abstract, author, advisors, program, institution), `tokenize="unicode61 remove_diacritics 2"`; bm25 pesos (8, 4, 1, 3, 2, 1, 1). O texto original (descomprimido) é inserido com o mesmo rowid.
- `facets(kind TEXT, value TEXT, label TEXT, count INTEGER)` para degree, uf, great_area, knowledge_area, institution_acronym (label = "SIGLA — Nome"), year
- `meta(key, value)` com version, records, built_at, format_version (2), vector_dtype (float16), dimensions
- `vectors.npy`: np.lib.format.open_memmap float16 [N,256], linha = rowid-1 (alinhado pela ordem de inserção; validar que todo id do metadata tem embedding; ausentes ou com norma zero → vetor zero e contados no meta "missing_vectors").
PRAGMAs de build: journal_mode=OFF, synchronous=OFF, page_size 4096; ao final `INSERT INTO theses_fts(theses_fts) VALUES('optimize')`, `ANALYZE`, `VACUUM` apenas se houver espaço livre relevante (≥ 32 MB). Catálogos `format_version` 1 (texto puro, float32, FTS external content) continuam legíveis, mas são reconstruídos na próxima atualização.

search (CatalogSearch, somente leitura, thread-safe com uma conexão por thread):
- `search(SearchQuery) -> SearchResult`. SearchQuery: q, mode ("hybrid"|"text"|"semantic"), scope ("all"|"title"|"abstract"|"author"|"advisor"|"keywords"), filtros (year_min, year_max, degree[], uf[], institution[] (siglas), great_area[], knowledge_area[]), sort ("relevance"|"year_desc"|"year_asc"), page (≥1), page_size (1–100, padrão 20).
- Consulta FTS segura: tokenizar a entrada (letras/dígitos), suportar "frases entre aspas" e termos com `-` para exclusão; montar expressão FTS5 com aspas em cada token (nunca repassar sintaxe crua); escopo vira filtro de coluna `{title}: ...`. Primeiro AND; se 0 resultados, OR (indicar `relaxed: true`).
- Canal textual: até 400 candidatos por bm25 com filtros SQL. Canal semântico: vetor da consulta (256) · vectors.npy, aplicando máscara de filtros (máscaras pré-computadas por year/degree/uf/etc. via arrays numpy carregados uma vez: year int16, códigos categóricos); top 400 via argpartition; processar em blocos de 262144 linhas para limitar RAM.
- Fusão RRF k=60 (pesos 1.0 / 1.0); relevância final; ordenações por ano aplicadas sobre o conjunto fundido (modo híbrido) ou sobre todos os resultados textuais (modo texto).
- `total`: modo texto = contagem exata FTS; híbrido/semântico = tamanho do conjunto fundido, com `total_is_estimate: true`.
- Resultado: id, title, author, advisors, year, degree, institution, institution_acronym, uf, program, great_area, knowledge_area, keywords, snippet (gerado em Python, pois FTS contentless não oferece snippet(): janela de ~320 chars com mais ocorrências dos termos positivos, casando sem diferenciar caixa/acentos via NFD, cortada em limites de palavra, com "…" nas pontas e marcadores `[[`/`]]` → frontend converte em <mark>; depois truecase; sem termos → início do resumo), url, score, matched_by ["texto","significado"].
- facetas da busca atual: contagens sobre o conjunto de candidatos (degree, uf, great_area, year por década) + facetas globais via tabela facets.
- `get(id)` registro completo; `similar(id, k=10)` pelo vetor do registro (exclui ele mesmo).
- Sem modelo de embedding: modo híbrido cai para texto, `warnings: ["semantic_unavailable"]`.

## 5. Embeddings no app
Protocol `Embedder`: `name`, `dims_full` (768), `ready() -> bool`, `embed_queries(list[str]) -> np.ndarray[n,768]`, `embed_documents(list[tuple[title,text]]) -> np.ndarray[n,768]` (normalizados). `truncate_normalize(m, dims)`.
- LocalOnnxEmbedder (padrão): arquivos de `onnx-community/embeddinggemma-300m-ONNX` (tokenizer.json, onnx/model_quantized.onnx, onnx/model_quantized.onnx_data) baixados com `hf_hub_download(local_dir=models/embeddinggemma-300m)` (arquivos reais, sem symlinks; onnxruntime exige o .onnx_data no mesmo diretório). Saída `sentence_embedding`; entradas `input_ids`, `attention_mask` (int64), padding id 0, truncamento 2048. Lotes ordenados por comprimento, batch 16. `intra_op_num_threads = max(1, os.cpu_count()-1)`. Carregamento preguiçoso e thread-safe. Variante configurável (`quantized` padrão ~300 MB; `fp32` opcional).
- OpenAICompatibleEmbedder: base_url (ex.: http://127.0.0.1:1234/v1 do LM Studio, http://127.0.0.1:11434/v1 do Ollama), model, api_key opcional; aplica os mesmos prompts; valida 768 dims; teste de compatibilidade compara a sonda do manifest (cosseno ≥ 0.97) e avisa se o modelo não for o EmbeddingGemma.
- Mensagens de erro em pt-BR, acionáveis.

## 6. Configuração (config.json; tudo opcional)
```json
{"embedding": {"engine": "local", "local_variant": "quantized", "base_url": "", "model": "", "api_key": ""},
 "server": {"port": 8101}, "updates": {"check_catalog": true, "check_app": true},
 "mcp_http": {"enabled": false, "port": 8102, "token": "", "allowed_hosts": []},
 "remote": {"secret": "", "include_library": false}, "onboarding_done": false}
```
Env overrides: BUSCATESES_HOME, BUSCATESES_PORT, BUSCATESES_DATASET_REPO, BUSCATESES_DATASET_URL, BUSCATESES_EMBED_ENGINE, BUSCATESES_EMBED_URL, BUSCATESES_EMBED_MODEL.

## 7. Minha biblioteca
Formatos: .pdf, .epub, .docx, .txt, .md, .html/.htm (até 500 MB cada). Arquivo copiado para library/files/<sha256>.<ext> (dedupe por sha256).
Extração por página (PDF: pypdfium2 `get_textpage().get_text_range()`; EPUB: ordem do spine no OPF, HTML→texto com html.parser; DOCX: word/document.xml via zipfile; TXT/MD: charset-normalizer). PDF sem texto (< 30 chars/página em média) → status "needs_ocr" com mensagem clara (OCR fora do escopo da v1).
Chunks: alvo 1000 chars, overlap 150, quebra em parágrafo/frase; guarda page_start/page_end.
Tabelas: documents(id TEXT PK uuid4 hex, sha256 UNIQUE, filename, title, author, year, kind, pages, chars, chunks, status ("queued"|"processing"|"ready"|"error"|"needs_ocr"), error, added_at), chunks(rowid, doc_id, ord, page_start, page_end, text), chunks_fts(text, content='chunks'). Vetores 768 float32 em `vectors.npy` + `vector_rowids.npy` (reescritos a cada ingestão; remoção filtra).
Ingestão como Job (fila serial): extrair → chunk → embed (lotes de 16, progresso por chunk) → gravar. Título/autor/ano extraídos de metadados (PDF info, EPUB dc:title/dc:creator) ou nome do arquivo; editáveis.
Busca: híbrida (FTS + vetor) com RRF; resultados por trecho com título do documento, páginas, snippet; agrupar até 3 trechos por documento; filtro por doc_id.

## 8. API HTTP (FastAPI, JSON, prefixo /api)
Segurança: aceitar apenas Host `127.0.0.1:<porta>`/`localhost:<porta>`; métodos mutáveis exigem header `X-BuscaTeses: 1` e Origin ausente ou igual ao host; CORS desativado; uploads multipart com limite.
- GET /api/status → {app:{version}, catalog:{installed, version, records, built_at, years, missing_vectors}, model:{engine, ready, label, downloading}, library:{documents, chunks}, setup_required, warnings[]}
- POST /api/setup {components:["model","catalog"]} → {job_id}; GET /api/jobs/{id}; GET /api/jobs?active=1
  Job JSON: {id, kind, status:"queued"|"running"|"done"|"error"|"cancelled", stage, message (pt-BR), progress 0..1|null, bytes_done, bytes_total, started_at, finished_at, error}
- GET /api/catalog/update → {available, current, latest, records_delta, download_bytes}; POST /api/catalog/update → {job_id}
- GET /api/search?q&mode&scope&year_min&year_max&degree&uf&institution&great_area&knowledge_area&sort&page&page_size (listas repetindo o parâmetro)
  → {query, mode_used, total, total_is_estimate, page, page_size, pages, took_ms, relaxed, warnings[], results[], facets{degree[], uf[], great_area[], decade[]}}
- GET /api/facets → facetas globais (para os filtros)
- GET /api/theses/{id} ; GET /api/theses/{id}/similar?k=10
- GET /api/export?format=csv|bibtex|ris|abnt&ids=...  (ou os mesmos parâmetros de /api/search, até 1000 itens) → arquivo com Content-Disposition
- GET/POST/DELETE /api/favorites (POST {id}; DELETE /api/favorites/{id})
- Biblioteca: POST /api/library/upload (multipart files[]) → {jobs:[...], skipped:[{filename, reason}]}; GET /api/library/documents; PATCH /api/library/documents/{id} {title?,author?,year?}; DELETE /api/library/documents/{id}; GET /api/library/documents/{id}/file (PDF inline; HTML/MD/TXT como `text/plain`; EPUB/DOCX como anexo; todas as respostas com `X-Content-Type-Options: nosniff` e CSP `sandbox`); GET /api/library/search?q&doc_id&page&page_size
- GET/PUT /api/settings ; POST /api/settings/test-embedding. O GET nunca devolve `embedding.api_key`, `mcp_http.token` nem `remote.secret`: apenas `has_api_key`, `token_set` e `remote.secret_set`. Campos secretos vazios em um PUT preservam o valor gravado.
- GET /api/integrations → [{id, name, kind:"auto"|"command"|"manual", detected, connected, config_path, snippet, instructions}] ; POST /api/integrations/{id}/connect ; POST /api/integrations/{id}/disconnect
- GET /api/remote → {state:"off"|"downloading"|"starting"|"online"|"error", url, include_library, message, cloudflared_installed}; POST /api/remote/start {include_library} ; POST /api/remote/stop ; POST /api/remote/rotate (409 quando já há uma operação em andamento)
- GET / e estáticos de web/ (cache-control no-cache para index.html)

## 9. MCP (somente leitura)
`buscateses mcp` (stdio) e `buscateses mcp --http [--port 8102] [--token ...]` (Streamable HTTP em 127.0.0.1, Bearer obrigatório salvo `--allow-no-token`).
Ferramentas (com outputSchema e readOnlyHint): search_theses(query, year_min?, year_max?, degree?, uf?, institution?, area?, mode?, limit≤50), get_thesis(id), similar_theses(id, limit≤20), search_library(query, document_id?, limit≤30), list_library_documents(), get_library_passage(chunk_id, context_chunks≤3), catalog_status().
Instruções do servidor (≤ 512 chars) explicam o que existe e sugerem citar título/autor/ano/URL.

Modo remoto (`remote.py`, usado pela interface e por `buscateses remote`): FastMCP em thread própria, escutando em
127.0.0.1 a partir da porta 8102, com `stateless_http=True` e `json_response=True`; rota única `/mcp/<segredo>` (outros
caminhos → 404). O segredo tem 24 bytes (`secrets.token_urlsafe`) e fica em `settings.remote.secret`. O túnel é o
`cloudflared` oficial em versão fixa (SHA-256 por plataforma, baixado em `bin/`); a URL pública final é
`https://<host>.trycloudflare.com/mcp/<segredo>` e é capturada em até 40 s. A proteção de DNS rebinding é desativada
somente nesse endpoint (o túnel reescreve `Host`); a barreira é o segredo no caminho. As ferramentas da biblioteca só
são registradas com `remote.include_library` (padrão `false`). O endereço é desligado em `atexit`, ao fechar o desktop e
em **Desligar**. Estados: `off`, `downloading`, `starting`, `online`, `error`.

## 10. Integrações automáticas (integrations.py)
Comando MCP a registrar: app empacotado → caminho absoluto do executável de console (`buscateses-cli`/`buscateses-cli.exe`
no macOS/Windows e `buscateses` no Linux, ao lado do executável GUI) com args ["mcp"]; instalação via pip →
[sys.executable, "-m", "buscateses", "mcp"]. Quando a pasta de dados não é a padrão, a entrada inclui `BUSCATESES_HOME`.
Clientes: claude_desktop (JSON mcpServers; macOS ~/Library/Application Support/Claude/claude_desktop_config.json; Windows %APPDATA%\Claude\claude_desktop_config.json; Linux ~/.config/Claude/claude_desktop_config.json),
cursor (~/.cursor/mcp.json), lm_studio (~/.lmstudio/mcp.json), gemini_cli (~/.gemini/settings.json), codex (~/.codex/config.toml: bloco `[mcp_servers.buscateses]` com command/args; editar texto preservando o resto),
claude_code (executa `claude mcp add --scope user buscateses -- <cmd...>` se `claude` estiver no PATH; senão kind=command com o comando para copiar),
vscode (manual: snippet). ChatGPT e Claude.ai não entram em `integrations.py`: usam o endereço público de `remote.py`.
Antes de alterar qualquer arquivo: backup `<arquivo>.bak-buscateses`; JSON inválido → não alterar e explicar. `disconnect` remove só a entrada "buscateses". Avisar que o cliente precisa ser reiniciado.

## 11. Desktop (desktop.py) e CLI
`buscateses` sem argumentos abre o app, tanto na instalação empacotada quanto na instalação por código-fonte (o
subcomando `app` continua disponível como alternativa). A ajuda continua em `buscateses --help`. Subcomandos: app, serve [--host 127.0.0.1] [--port] [--no-browser], setup [--yes] [--only model|catalog], update, status (alias doctor; diagnóstico legível e `--json`), search "consulta" [--limit] [--json], library add <arquivos...> | list | remove <id>, mcp [--http ...], remote start [--include-library]|stop|status, connect <cliente>|--list, disconnect <cliente>, version.
`remote start` mantém o processo e o túnel vivos até `Ctrl+C` (ou `remote stop`), publica `remote.json` e imprime a URL;
`remote stop` encerra o processo registrado (SIGTERM, espera até 5 s) e apaga `remote.json`.
`app`: instância única (runtime.json + verificação de porta; se já rodando, apenas abrir a janela/navegador); escolhe porta livre a partir de 8101; inicia uvicorn em thread; abre janela pywebview "BuscaTeses" (1280×840, mín. 960×640); se pywebview falhar, abre o navegador padrão e mantém o processo (Ctrl+C/encerrar pela UI: POST /api/app/quit). Fechar a janela encerra o servidor.

## 12. Frontend (web/, sem etapa de build)
HTML semântico + CSS (variáveis, tema claro/escuro/sistema) + JS ES modules (`js/main.js`, `js/api.js`, `js/router.js`, `js/views/*.js`, `js/components/*.js`). Rotas por hash: `#/` (buscar), `#/tese/<id>`, `#/biblioteca`, `#/selecao`, `#/conectar`, `#/configuracoes`, `#/sobre`, `#/primeiros-passos`.
Identidade: verde-petróleo (#0F766E) + tinta (#0F172A), fontes **Literata** (títulos e leitura longa) e
**Atkinson Hyperlegible Next** (interface), ambas locais em `web/assets/fonts/` e licenciadas sob SIL OFL 1.1.
Telas: assistente de primeiros passos (boas-vindas → baixar modelo e catálogo com progresso real e tempo estimado → conectar assistentes (opcional) → pronto); busca com filtros laterais (drawer no celular), chips de filtros ativos, modos Inteligente/Exata/Por significado, cartões de resultado com destaque, ações (abrir detalhes, salvar, copiar ABNT, abrir texto completo), paginação, exportação; detalhe com resumo completo, metadados, "trabalhos semelhantes"; biblioteca com arrastar-e-soltar, fila com progresso, lista de documentos, busca nos documentos com trechos e página; conectar assistentes (cartões por cliente com estado "Conectado"/"Instalado — não conectado"/"Não instalado neste
computador", link oficial de download e, para ChatGPT/Claude.ai, painel de endereço público com progresso, cópia da URL,
desligar e gerar novo endereço); configurações (motor de embedding, servidor externo com teste, atualizações, local dos dados, tema); sobre (licenças, fontes, aviso de não afiliação à CAPES).
Acessibilidade: WCAG 2.2 AA (skip link, landmarks, foco visível, teclado, aria-live, diálogos acessíveis, contraste,
prefers-reduced-motion, alvos ≥24px). O **VLibras é opcional e fica desligado por padrão**: pode ser ativado pelo botão
“Libras” no cabeçalho ou em **Configurações → Acessibilidade**, é lembrado em `localStorage` e só então carrega o
script de `vlibras.gov.br`; uma falha de rede não bloqueia a interface e nenhum trecho da página é enviado quando a
opção está desligada.

Rede: o app só acessa a internet para baixar modelo/catálogo, verificar atualizações (desligável em **Configurações**:
catálogo e aplicativo) e, se ativado, criar o endereço público (download do `cloudflared` e túnel Cloudflare) e carregar
o VLibras. Não há telemetria, contas ou envio de buscas e documentos; a biblioteca pessoal só é exposta no endereço
público quando a pessoa marca a opção.

## 13. Normalização de caixa (textcase.py)
`truecase(text)` converte registros gritados (≥ 8 letras e ≥ 75% maiúsculas) para caixa de frase, restaurando acentos
quando o texto original não os tem, preservando siglas, números, numerais romanos, nomes próprios e topônimos do
léxico `data/truecase.json`. Textos já em caixa normal voltam inalterados.

Títulos mistos em que apenas um trecho está gritado também são tratados: o texto é dividido em segmentos por
`. `, `: `, ` - `, ` — `, `? ` e `! `. Se houver ao menos um segmento gritado com **≥ 12 letras** e ao menos um
segmento em caixa normal (contendo minúsculas), somente os segmentos gritados passam por `truecase`; os demais são
preservados byte a byte. Isso corrige títulos como
`... RIO GRANDE DO SUL. Um modelo para o cultivo` → `... Rio Grande do Sul. Um modelo para o cultivo`.

`name_case(text)` aplica capitalização de nomes a autores, orientadores, instituições, programas e áreas, item a item
em listas separadas por `;`, mantendo partículas (`de`, `da`, `dos`…) minúsculas e preservando anotações como
`(coorientador)`. `display_record(record)` aplica as duas funções aos campos de exibição.

## 14. Empacotamento e release
`packaging/build.py` roda o PyInstaller e monta, para cada sistema, o app com janela e um cliente de console separado:
`buscateses-cli` (macOS/Windows, dentro do app ou ao lado do executável) e `buscateses` (Linux). O sufixo `-cli` evita
colisão de maiúsculas/minúsculas com o executável `BuscaTeses` em sistemas de arquivos que não diferenciam caixa.
Artefatos: `BuscaTeses-macos-arm64.dmg`, `BuscaTeses-macos-x86_64.dmg`, `BuscaTeses-windows-x64-setup.exe` e
`BuscaTeses-linux-x86_64.tar.gz`, com `SHA256SUMS.txt`.

A release é **acionada manualmente** (`workflow_dispatch` no `.github/workflows/release.yml`): a pessoa informa a tag
`vX.Y.Z`, o workflow confere `__version__`, constrói os quatro instaladores em paralelo, recalcula o `SHA256SUMS.txt`,
extrai as notas do `CHANGELOG.md` (ou usa um texto padrão), cria a tag e publica o GitHub Release. Não há publicação
automática a cada push.
