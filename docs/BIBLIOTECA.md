# Minha biblioteca

A **Minha biblioteca** permite pesquisar dentro dos seus próprios livros, artigos, teses e anotações, usando a mesma
busca híbrida do catálogo: palavras exatas e significado. Os arquivos ficam apenas no seu computador.

## Formatos aceitos

| Formato | Extensão | Como o texto é lido |
| --- | --- | --- |
| PDF | `.pdf` | página a página, com a biblioteca `pypdfium2` |
| EPUB | `.epub` | na ordem de leitura do livro |
| Word | `.docx` | pelo conteúdo do documento |
| Texto | `.txt` | detecção automática de codificação |
| Markdown | `.md` | como texto |
| HTML | `.html`, `.htm` | texto visível, sem as tags |

Limites:

- até **500 MB por arquivo**;
- até **50 milhões de caracteres** extraídos por documento;
- a biblioteca guarda uma **cópia** do arquivo em `library/files/`; arquivos idênticos são armazenados uma única vez.

PDFs protegidos por senha, corrompidos ou fora desses formatos são recusados com uma mensagem explicando o motivo.

Ao abrir o arquivo de um documento da biblioteca:

- **PDF:** aparece no visualizador do navegador/janela, servido como `application/pdf` e `Content-Disposition:
  inline`. O BuscaTeses não envia `Content-Security-Policy` nesse caso: o visualizador de PDF do Chromium/WebView2
  não funciona dentro de uma política `sandbox`. A resposta continua restrita a `127.0.0.1`, usa
  `X-Content-Type-Options: nosniff` e só entrega o arquivo depois da checagem de caminho;
- **HTML, Markdown e TXT:** aparecem como **texto simples**, servidos como `text/plain` com
  `Content-Security-Policy: sandbox`; as tags não são interpretadas e scripts não executam;
- **EPUB e DOCX:** são baixados como anexo para você abrir no programa de sua preferência.

Essas regras valem também para arquivos enviados por você: o conteúdo não é executado dentro da interface do
BuscaTeses. O envio é feito um arquivo por vez, com o progresso de cada um; requisições acima de 520 MB são
recusadas antes de o corpo ser lido.

## Como adicionar

Pela interface: abra **Minha biblioteca**, arraste os arquivos para a área indicada (ou use o botão de escolher
arquivos). Cada arquivo entra em uma fila e mostra o progresso: extração, divisão em trechos e geração dos vetores.

Pelo terminal:

```bash
buscateses library add ~/Documents/tese.pdf ~/Documents/livro.epub
buscateses library list
buscateses library reindex --yes     # gera vetores que faltam
```

Depois de adicionar, você pode editar **título, autor e ano** na interface. Esses dados ajudam a identificar o trecho
nas citações. Sem edição, o BuscaTeses tenta usar os metadados do arquivo ou o nome do arquivo.

## Como a busca funciona

1. **Extração por página.** O texto é lido mantendo a página de origem (no EPUB, a ordem de leitura).
2. **Divisão em trechos.** O texto é cortado em trechos de cerca de **1.000 caracteres**, com sobreposição de **150
   caracteres**. A sobreposição evita perder uma frase que caiu na divisa entre dois trechos.
3. **Índice textual e vetores.** Cada trecho entra em um índice de palavras (SQLite FTS5) e ganha um vetor gerado pelo
   EmbeddingGemma, que roda localmente.
4. **Busca híbrida.** Os resultados das duas buscas são combinados (RRF, uma técnica de fusão de rankings). Você pode
   escolher entre buscar por palavras, por significado ou pelos dois.
5. **Resultados por trecho.** A interface mostra o trecho com destaque, o título do documento e a **página**. Quando há
   vários trechos do mesmo documento, são mostrados no máximo **3**, para dar espaço a outros documentos.

A busca respeita a página: um trecho pode começar em uma página e terminar na seguinte; por isso a interface mostra
"página X" ou "páginas X–Y".

## Privacidade

- Os arquivos são copiados para a pasta de dados local e **não são enviados para nenhum servidor**.
- A extração, a indexação e a geração de vetores acontecem no seu computador.
- O servidor web escuta apenas em `127.0.0.1`; outros computadores da rede não conseguem acessá-lo.
- O servidor MCP abre a biblioteca em **modo somente leitura**: assistentes podem consultar, mas não alterar nem apagar
  documentos.
- Ao remover um documento, a cópia do arquivo e os vetores correspondentes são apagados.

## PDFs digitalizados (sem texto)

Muitos PDFs antigos são apenas imagens digitalizadas. Nesse caso, não há texto para extrair e o BuscaTeses marca o
documento com o status **precisa de OCR** ("needs_ocr"), explicando o que fazer. **O BuscaTeses não faz OCR.**

A sugestão é converter o PDF em um PDF com texto usando uma ferramenta gratuita antes de adicioná-lo:

- **OCRmyPDF** (Windows, macOS e Linux; projeto livre): reconhece o texto e gera um novo PDF pesquisável;
- **Tesseract** com a interface gráfica de sua preferência;
- recursos de OCR de alguns leitores de PDF e programas de digitalização.

Exemplo com o OCRmyPDF:

```bash
ocrmypdf --language por --deskew --clean original.pdf pesquisavel.pdf
```

Depois, adicione `pesquisavel.pdf` à biblioteca. O texto reconhecido pode conter erros; revise trechos importantes antes
de citar.

## Dicas

- Prefira PDFs com texto selecionável (dá para selecionar e copiar o texto com o mouse).
- Nomeie os arquivos de forma descritiva; isso melhora o título sugerido.
- Preencha título, autor e ano para que as citações fiquem corretas.
- Se você trocar o motor de embedding nas configurações, rode **reindexar** para gerar os vetores com o novo modelo.
- Documentos muito grandes levam mais tempo; acompanhe pela fila da biblioteca.
- A busca por significado funciona melhor com frases e conceitos do que com palavras isoladas muito comuns.

## Remover documentos

Pela interface: abra **Minha biblioteca**, escolha o documento e use **Remover**.

Pelo terminal:

```bash
buscateses library list                 # copie o id do documento
buscateses library remove <id> --yes
```

A remoção apaga o registro, os trechos, os vetores e a cópia do arquivo. Não é possível desfazer.

## Uso pelo MCP

Com o servidor MCP conectado, o assistente de IA pode consultar a biblioteca com as ferramentas:

| Ferramenta | O que faz |
| --- | --- |
| `search_library` | Busca trechos na biblioteca, opcionalmente em um único documento. |
| `list_library_documents` | Lista os documentos com metadados e status. |
| `get_library_passage` | Devolve um trecho com até 3 trechos vizinhos para dar contexto. |

Exemplo de pedido: *"Pesquise na minha biblioteca o que os meus livros dizem sobre letramento científico e traga os
trechos com a página."*

Configuração do MCP em [MCP.md](MCP.md).
