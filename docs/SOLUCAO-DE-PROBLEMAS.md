# Solução de problemas

As mensagens do BuscaTeses são escritas para explicar a causa. Comece sempre pelo diagnóstico:

```bash
buscateses status            # resumo legível
buscateses status --json     # o mesmo, em JSON (útil para pedir ajuda)
```

No macOS e no Windows (instaladores), use o caminho completo do executável de console:

```bash
/Applications/BuscaTeses.app/Contents/MacOS/buscateses-cli status     # macOS
```

```powershell
& "$env:LOCALAPPDATA\Programs\BuscaTeses\buscateses-cli.exe" status   # Windows
```

## O download do catálogo parou ou falhou

**Causa provável:** conexão interrompida, rede instável ou bloqueio de rede.

**Solução:**

1. Rode o `setup` de novo. O download é retomado de onde parou:

   ```bash
   buscateses setup --yes
   ```

2. Se falhar repetidamente, teste outra rede (por exemplo, um celular como roteador) e verifique se há proxy ou
   firewall bloqueando `huggingface.co`.
3. Se a mensagem citar arquivo corrompido, apague apenas a pasta de downloads da versão e tente de novo:

   ```bash
   # troque <versão> pela versão que aparece na mensagem
   rm -rf "<pasta de dados>/catalog/downloads/<versão>"
   buscateses setup --yes
   ```

O catálogo anterior continua funcionando enquanto a nova versão não termina.

## Falta de espaço em disco

**Causa:** o catálogo instalado ocupa cerca de 8 GB, o modelo ocupa cerca de 330 MB e a instalação precisa de espaço
temporário para o download (~3 GB). O BuscaTeses exige margem de segurança e recusa a instalação quando o disco está
cheio.

**Solução:**

1. Libere espaço até ter pelo menos **12 GB livres**.
2. Rode `buscateses setup --yes` de novo.
3. Se já houver uma versão antiga do catálogo, ela é removida ao final da atualização; não apague pastas manualmente
   durante a instalação.

## "A porta já está em uso"

**Causa:** outro BuscaTeses já está aberto, ou outro programa usa a porta 8101 (ou 8102, no MCP HTTP).

**Solução:**

- Se o app já está aberto, use a janela existente; o BuscaTeses não abre duas instâncias.
- Para usar outra porta:

  ```bash
  buscateses serve --port 8111
  buscateses mcp --http --port 8112
  ```

- Para descobrir o processo que ocupa a porta:

  ```bash
  lsof -nP -iTCP:8101 -sTCP:LISTEN      # macOS/Linux
  ```

  ```powershell
  Get-NetTCPConnection -LocalPort 8101 | Select-Object OwningProcess
  ```

## O antivírus bloqueia o programa ou o download

**Causa:** instaladores novos e sem assinatura comercial às vezes são marcados como suspeitos. O BuscaTeses não envia
dados e não instala serviços.

**Solução:**

1. Confirme que baixou o arquivo da página oficial de Releases do repositório.
2. Se o antivírus colocar o arquivo em quarentena, restaure-o e adicione uma exceção para a pasta de instalação.
3. Em empresas, o bloqueio pode ser uma política de TI; peça ao suporte para liberar o aplicativo.
4. Como alternativa, use a instalação pelo código-fonte (veja [INSTALACAO.md](INSTALACAO.md)).

## macOS: "não foi possível abrir porque não é possível verificar o desenvolvedor"

**Causa:** o app não tem o certificado pago da Apple (Gatekeeper).

**Solução:** na pasta **Aplicativos**, clique com o botão direito no BuscaTeses e escolha **Abrir**; confirme em
**Abrir**. Se a opção não aparecer, use **Ajustes do Sistema → Privacidade e Segurança → Abrir Mesmo Assim**.

Se você moveu o app para outra pasta e o aviso voltou, repita o procedimento. Evite usar `xattr` sem entender o comando.

## Windows: "O Windows protegeu o computador"

**Causa:** aviso do SmartScreen para programas sem certificado comercial.

**Solução:** clique em **Mais informações** e depois em **Executar assim mesmo**. O instalador não pede administrador.

## Windows: o app abre no navegador em vez da janela

**Causa:** o componente WebView2 não está instalado ou está corrompido. O BuscaTeses usa a janela nativa quando
consegue e, quando não consegue, abre a interface no navegador padrão em vez de falhar.

**Solução:**

1. Instale o **Microsoft Edge WebView2 Runtime** (versão "Evergreen") no site da Microsoft.
2. Feche o BuscaTeses e abra de novo.
3. Enquanto isso, você pode usar a versão no navegador: mantenha o processo aberto e acesse
   `http://127.0.0.1:8101`. Para encerrar, feche a janela do terminal ou use **Sair** na interface.

## O assistente de IA não mostra as ferramentas

**Causa:** configuração não gravada, caminho do executável errado ou cliente que ainda não foi reiniciado.

**Solução:**

1. Rode:

   ```bash
   buscateses connect --list
   ```

   O cliente deve aparecer como **conectado**.

2. Confira se o caminho do comando existe. No macOS e no Windows ele termina em `buscateses-cli` (ou
   `buscateses-cli.exe`); no Linux, em `buscateses`.
3. Reinicie o cliente por completo (feche e abra, não apenas recarregue a janela).
4. Se você editou o arquivo de configuração à mão, valide o JSON/TOML. O BuscaTeses não altera arquivos inválidos e
   avisa quando isso acontece.
5. Para desfazer e tentar de novo:

   ```bash
   buscateses disconnect <cliente>
   buscateses connect <cliente>
   ```

6. Se o assistente for o ChatGPT ou o Claude.ai, use **Conectar assistentes → Assistentes na web → Criar endereço
   público**; veja [MCP.md](MCP.md#acesso-remoto-chatgpt-e-claudeai).

## O endereço público não é criado

**Causa:** falta de internet, firewall ou proxy da rede bloqueando a Cloudflare, download do conector interrompido ou
outra instância do BuscaTeses usando a porta local.

**Solução:**

1. Confirme que o computador tem internet e que a rede permite acessar `*.trycloudflare.com` e baixar arquivos de
   `github.com/cloudflare/cloudflared/releases`.
2. Clique em **Tentar de novo**. O download do conector recomeça de onde parou.
3. Redes corporativas com proxy autenticado podem impedir o túnel. Nesse caso, use um assistente que rode neste
   computador (Claude Desktop, Claude Code, Codex, Cursor, Gemini CLI ou LM Studio) ou peça ao administrador da rede
   para liberar o acesso.
4. Feche outras janelas do BuscaTeses e tente de novo. Se continuar, reinicie o aplicativo.
5. O conector fica em `bin/`, na pasta de dados. Se o download estiver corrompido, feche o app, apague essa pasta e
   crie o endereço novamente.

## O endereço público mudou ou parou de funcionar

Cada **Criar endereço público** gera uma URL nova, e **Gerar novo endereço** invalida a anterior na hora. Fechar o
BuscaTeses também desliga o endereço. Abra **Conectar assistentes**, crie o endereço de novo e atualize a URL no
conector do ChatGPT ou do Claude.ai. Se o assistente disser que não encontrou o servidor, confira se o BuscaTeses
continua aberto e se o endereço na tela é o mesmo que está no conector.

## A busca por significado está desligada

**Causa:** o modelo de embedding não foi baixado, ou o motor configurado não está disponível.

**Solução:**

1. Baixe o modelo:

   ```bash
   buscateses setup --yes --only model
   ```

2. Confira em `buscateses status` se o modelo aparece como **pronto**.
3. Se você escolheu LM Studio ou Ollama em **Configurações → Motor de embedding**, confirme que o servidor está ligado e que o
   teste de compatibilidade passou. O modelo precisa devolver vetores de 768 dimensões.
4. Enquanto o modelo não estiver disponível, a busca textual continua funcionando; o app mostra um aviso.

## PDF sem texto (documento digitalizado)

**Causa:** o PDF é uma imagem digitalizada e não tem camada de texto. O BuscaTeses marca o documento como
**precisa de OCR**.

**Solução:** passe o arquivo por um programa de OCR antes de adicioná-lo, por exemplo o **OCRmyPDF** (gratuito):

```bash
ocrmypdf --language por --deskew --clean original.pdf pesquisavel.pdf
```

Depois, adicione o novo PDF à biblioteca. O BuscaTeses não faz OCR sozinho. Veja
[BIBLIOTECA.md](BIBLIOTECA.md#pdfs-digitalizados-sem-texto).

## O VLibras não aparece

O tradutor de Libras é **opcional e vem desligado por padrão**. Para ativá-lo, use o botão **Libras** no cabeçalho ou
**Configurações → Acessibilidade**. Ao ativar, o BuscaTeses carrega o serviço externo `vlibras.gov.br`; é preciso ter
internet. Se o serviço estiver fora do ar, a interface continua funcionando normalmente. Para desligar, use o mesmo
botão ou a mesma opção nas Configurações.

## Um arquivo HTML ou TXT da biblioteca abre como texto

Isso é intencional. PDFs abrem no visualizador; HTML, Markdown e TXT são mostrados como **texto simples**, sem
interpretar as tags nem executar scripts; EPUB e DOCX são baixados como anexo. Assim, um arquivo enviado para a
biblioteca não consegue executar código dentro da interface.

## A biblioteca não encontra um trecho que eu sei que existe

**Causa:** o documento pode ter sido indexado sem vetores, o texto pode estar em uma imagem ou a busca está restrita a
um documento.

**Solução:**

1. Veja o status do documento em **Minha biblioteca** (ou `buscateses library list`).
2. Rode `buscateses library reindex --yes` para gerar vetores que faltam.
3. Tente a busca por **palavras exatas**.
4. Confirme que o filtro de documento não está ativo.
5. Se o PDF não tiver texto, faça OCR.

## Atualização do catálogo falhou

**Causa:** rede, espaço ou arquivo corrompido.

**Solução:**

1. Rode `buscateses status` e confira a versão atual; a anterior continua ativa.
2. Libere espaço e rode `buscateses update --yes`.
3. Se a mensagem citar um arquivo corrompido, remova apenas `catalog/downloads/<versão>` e tente de novo.
4. Persistindo, baixe manualmente o dataset no Hugging Face e aponte o app para ele com a variável
   `BUSCATESES_DATASET_URL` (uso avançado; veja [DESENVOLVIMENTO.md](DESENVOLVIMENTO.md)).

## O app não abre no Linux

**Causa:** falta de bibliotecas gráficas (GTK/WebKit) ou `~/.local/bin` fora do `PATH`.

**Solução:**

1. Rode o app pelo terminal para ver a mensagem:

   ```bash
   ~/.local/opt/buscateses/BuscaTeses
   ```

2. Se a janela nativa falhar, o BuscaTeses abre no navegador. Você também pode usar
   `buscateses serve --no-browser` e acessar `http://127.0.0.1:8101`.
3. Confira o `PATH` e o atalho:

   ```bash
   echo "$PATH" | tr ':' '\n' | grep -F "$HOME/.local/bin"
   ```

## Como pedir ajuda sem expor dados pessoais

Ao abrir uma issue, envie apenas o necessário:

1. **Versão e sistema:** `buscateses status --json` (o campo `data_dir` mostra um caminho pessoal; apague-o se quiser).
2. **O que aconteceu:** o passo a passo, a mensagem de erro exata e o que você esperava.
3. **Trecho do log:** as últimas linhas de `logs/buscateses.log`, na pasta de dados:
   - macOS: `~/Library/Application Support/BuscaTeses/logs/buscateses.log`
   - Windows: `%LOCALAPPDATA%\BuscaTeses\logs\buscateses.log`
   - Linux: `~/.local/share/BuscaTeses/logs/buscateses.log`

**Não envie:**

- o conteúdo da sua biblioteca ou trechos de documentos;
- termos de busca que identifiquem você ou sua pesquisa;
- caminhos com seu nome de usuário (troque por `<usuario>`);
- tokens do MCP (`config.json`) ou capturas de tela com eles.

Se precisar mostrar um trecho de configuração, apague `api_key` e `mcp_http.token`.

Abra a issue em <https://github.com/andrebadini/buscateses-local/issues>. Para falhas de segurança, use o canal privado
descrito em [SECURITY.md](../SECURITY.md).
