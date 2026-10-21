# Registro de mudanças

Todas as mudanças relevantes deste projeto são registradas neste arquivo.

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o projeto usa
[Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não publicado]

### Planejado

- Integração opcional de OCR para PDFs digitalizados.
- Melhorias contínuas de acessibilidade e cobertura de testes.

## [1.0.0] - 2026-10-21

Primeira versão pública.

### Adicionado

- **Catálogo local da CAPES** com teses e dissertações de 1987 a 2024, incluindo busca textual (SQLite FTS5) e
  semântica (EmbeddingGemma 300M em ONNX, projeção Matryoshka de 256 dimensões).
- **Busca híbrida** que combina palavras e significado com fusão RRF, com modos Inteligente, Palavras exatas e Por
  significado.
- **Filtros de pesquisador** por ano, nível, estado, instituição, grande área e área do conhecimento, com facetas e
  contagem de resultados.
- **Fichas completas** com metadados, resumo, trabalhos semelhantes e link para o texto completo quando informado pela
  CAPES.
- **Exportação** em CSV, BibTeX, RIS e referência ABNT, além de "Minha seleção" (favoritos).
- **Normalização de caixa e acentos** dos registros antigos da CAPES, preservando siglas e nomes próprios.
- **Minha biblioteca**: ingestão de PDF, EPUB, DOCX, TXT, Markdown e HTML, com extração por página, trechos com
  sobreposição, busca híbrida e indicação de páginas; documentos digitalizados são marcados como "precisa de OCR".
- **Servidor MCP somente leitura** em stdio e Streamable HTTP (com token Bearer), com as ferramentas
  `search_theses`, `get_thesis`, `similar_theses`, `search_library`, `list_library_documents`,
  `get_library_passage` e `catalog_status`.
- **Integração com assistentes de IA**: conexão com um clique para Claude Desktop, Claude Code, Codex, Cursor,
  Gemini CLI e LM Studio, com backup das configurações e suporte a configuração manual para VS Code, ChatGPT e
  Claude web.
- **Aplicativo desktop** com janela nativa (pywebview), instância única, escolha automática de porta e fallback para o
  navegador quando a janela não está disponível.
- **Assistente de primeiros passos** com download do modelo e do catálogo, progresso real e estimativa de tempo.
- **Atualização do catálogo** com download retomável, verificação de tamanho e SHA-256, montagem em diretório
  temporário, troca atômica e rollback para a versão anterior.
- **CLI** com os comandos `app`, `serve`, `setup`, `update`, `status`/`doctor`, `search`, `library`, `mcp`, `connect`,
  `disconnect` e `version`.
- **Empacotamento nativo** para macOS (arm64 e x86_64), Windows x64 (Inno Setup por usuário) e Linux x86_64
  (tar.gz com instalador), com teste de fumaça e `SHA256SUMS.txt`.
- **Instalação pelo código-fonte** com `scripts/install.sh` e `scripts/install.ps1`, usando `uv`.
- **Acessibilidade** com meta WCAG 2.2 AA: navegação por teclado, foco visível, regiões `aria-live`, temas claro e
  escuro, alto contraste, redução de movimento, alvos de 24 px e widget VLibras.
- **Dataset público** normalizado no Hugging Face, com metadados, embeddings de 768 e 256 dimensões, manifesto
  versionado e dataset card.
- **Documentação pública** em português e inglês, incluindo instalação, biblioteca, MCP, arquitetura, dados,
  acessibilidade, solução de problemas e guia de desenvolvimento.
- **Formulários de issue**, modelo de pull request, Dependabot e política de segurança.

### Segurança

- Servidor HTTP restrito a `127.0.0.1`, com validação de `Host`, `Origin` e cabeçalho CSRF `X-BuscaTeses`.
- Servidor MCP HTTP com autenticação Bearer obrigatória por padrão e comparação de token em tempo constante.
- MCP somente leitura e biblioteca aberta em modo somente leitura.
- Campos pessoais e identificadores internos removidos do dataset publicado.
- Sem telemetria, contas ou envio de dados para servidores externos.

### Notas

- O app não inclui o texto completo das teses; quando a CAPES informa um link, ele é exibido na ficha.
- PDFs digitalizados precisam de OCR externo antes de entrar na biblioteca.
- O macOS pode exibir o aviso do Gatekeeper e o Windows o aviso do SmartScreen na primeira abertura, porque os
  instaladores não usam certificados comerciais.

[1.0.0]: https://github.com/andrebadini/buscateses-local/releases/tag/v1.0.0
