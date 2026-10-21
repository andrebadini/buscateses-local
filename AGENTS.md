# AGENTS.md — instruções para agentes de IA

Este arquivo orienta assistentes de IA (Claude Code, Codex, Cursor, Gemini CLI, Copilot e similares) que foram
pedidos para **instalar, configurar, diagnosticar ou modificar** o BuscaTeses. Ele vale tanto para quem está ajudando
uma pessoa usuária quanto para quem está contribuindo com código.

O público do BuscaTeses é formado por pesquisadores que, em geral, não programam. Explique o que vai fazer em
linguagem simples, peça confirmação antes de instalar programas ou alterar configurações de outros aplicativos e
nunca apague dados da pessoa sem pedir.

## 1. Instalar para uma pessoa usuária

### 1.1 Caminho recomendado: instalador oficial

Baixe o arquivo da versão mais recente em `https://github.com/andrebadini/buscateses-local/releases/latest/download/<arquivo>`:

| Sistema | Arquivo | Onde fica o comando de linha de comando depois de instalar |
| --- | --- | --- |
| macOS (Apple Silicon) | `BuscaTeses-macos-arm64.dmg` | `/Applications/BuscaTeses.app/Contents/MacOS/buscateses-cli` |
| macOS (Intel) | `BuscaTeses-macos-x86_64.dmg` | `/Applications/BuscaTeses.app/Contents/MacOS/buscateses-cli` |
| Windows 10/11 x64 | `BuscaTeses-windows-x64-setup.exe` | `%LOCALAPPDATA%\Programs\BuscaTeses\buscateses-cli.exe` |
| Linux x86_64 | `BuscaTeses-linux-x86_64.tar.gz` | `~/.local/bin/buscateses` |

Descubra a arquitetura com `uname -m` (macOS/Linux: `arm64` ou `x86_64`).

> Por que `buscateses-cli`? No macOS e no Windows o sistema de arquivos não diferencia maiúsculas de minúsculas. O
> aplicativo com janela se chama `BuscaTeses`; se o cliente de console se chamasse `buscateses`, os dois nomes
> apontariam para o mesmo arquivo. Por isso o executável de console recebe o sufixo `-cli` nesses sistemas. No Linux
> e nas instalações por código-fonte, o comando é simplesmente `buscateses`.

**macOS**

```bash
curl -L -o /tmp/BuscaTeses.dmg https://github.com/andrebadini/buscateses-local/releases/latest/download/BuscaTeses-macos-arm64.dmg
hdiutil attach /tmp/BuscaTeses.dmg -nobrowse -mountpoint /tmp/buscateses-dmg
cp -R /tmp/buscateses-dmg/BuscaTeses.app /Applications/
hdiutil detach /tmp/buscateses-dmg
# O app não tem certificado pago da Apple. Com a autorização da pessoa, remova a quarentena
# para que o macOS não bloqueie a primeira abertura:
xattr -dr com.apple.quarantine /Applications/BuscaTeses.app
BT=/Applications/BuscaTeses.app/Contents/MacOS/buscateses-cli
```

**Windows (PowerShell)**

```powershell
$setup = "$env:TEMP\BuscaTeses-setup.exe"
Invoke-WebRequest -Uri "https://github.com/andrebadini/buscateses-local/releases/latest/download/BuscaTeses-windows-x64-setup.exe" -OutFile $setup
Start-Process -FilePath $setup -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -Wait
$BT = "$env:LOCALAPPDATA\Programs\BuscaTeses\buscateses-cli.exe"
```

O instalador não exige administrador. Se o SmartScreen bloquear uma execução manual, a pessoa deve clicar em
**Mais informações → Executar assim mesmo**.

**Linux**

```bash
curl -L -o /tmp/BuscaTeses.tar.gz https://github.com/andrebadini/buscateses-local/releases/latest/download/BuscaTeses-linux-x86_64.tar.gz
mkdir -p /tmp/buscateses && tar -xzf /tmp/BuscaTeses.tar.gz -C /tmp/buscateses
/tmp/buscateses/install.sh
BT=~/.local/bin/buscateses
```

### 1.2 Alternativa: a partir do código-fonte (qualquer sistema com internet)

Use quando não houver instalador para a plataforma ou quando a pessoa preferir. Requer o [uv](https://docs.astral.sh/uv/)
(o script o instala se faltar):

```bash
curl -LsSf https://raw.githubusercontent.com/andrebadini/buscateses-local/main/scripts/install.sh | sh     # macOS/Linux
```

```powershell
irm https://raw.githubusercontent.com/andrebadini/buscateses-local/main/scripts/install.ps1 | iex          # Windows
```

Ou manualmente: `uv tool install --python 3.12 "buscateses-local[desktop] @ git+https://github.com/andrebadini/buscateses-local"`.
Nesse caso o comando é simplesmente `buscateses`, e ele já abre o aplicativo; `buscateses app` continua funcionando
como alternativa explícita.

### 1.3 Preparar os dados (obrigatório, uma vez)

```bash
"$BT" setup --yes
```

Baixa o modelo de embedding (~330 MB, transforma textos em vetores para a busca por significado) e o catálogo
(~2,8 GB) e monta o índice local. Pode levar de 10 a 40 minutos.
Execute em segundo plano se a sua ferramenta tiver limite de tempo e acompanhe com `"$BT" status`. O comando é
idempotente: se for interrompido, rode de novo e ele continua de onde parou. Espaço necessário: ~12 GB.

### 1.4 Conectar ao assistente de IA da pessoa (MCP)

```bash
"$BT" connect --list            # mostra clientes detectados e o estado de cada um
"$BT" connect claude_desktop    # ou: claude_code, codex, cursor, gemini_cli, lm_studio
```

O comando edita a configuração do cliente com backup (`*.bak-buscateses`) e preserva as demais entradas. A pessoa
precisa **fechar e abrir** o cliente para ativar. Para desfazer: `"$BT" disconnect <cliente>`.

Clientes disponíveis: `claude_desktop`, `cursor`, `lm_studio`, `gemini_cli`, `codex`, `claude_code` (automáticos ou por
comando) e `vscode` (configuração manual, com o trecho pronto para copiar).

Configuração manual equivalente (JSON no formato `mcpServers`):

```json
{
  "mcpServers": {
    "buscateses": { "command": "<caminho absoluto de buscateses ou buscateses-cli>", "args": ["mcp"] }
  }
}
```

Para ChatGPT e Claude.ai, que rodam na web, o próprio aplicativo cria um endereço público temporário com o Cloudflare
Quick Tunnel (sem conta e sem instalar nada à mão). Na interface, use **Conectar assistentes → Assistentes na web →
Criar endereço público**. Pelo terminal:

```bash
"$BT" remote start [--include-library]   # mantém o terminal aberto e mostra a URL
"$BT" remote status
"$BT" remote stop
```

O endereço inclui um segredo no caminho e só funciona enquanto o app estiver aberto; a biblioteca pessoal só é exposta
com `--include-library`. Explique o risco à pessoa antes de ligar e desligue ao terminar. Detalhes em `docs/MCP.md`.

### 1.5 Verificar

```bash
"$BT" status                                   # catálogo, modelo de embedding e biblioteca devem aparecer como prontos
"$BT" search "educação inclusiva" --limit 3    # deve listar três trabalhos
```

Depois, mostre à pessoa como abrir o app: **Aplicativos → BuscaTeses** (macOS), **Menu Iniciar → BuscaTeses** (Windows)
ou `buscateses` (Linux e instalações por código-fonte; `buscateses app` também funciona). A interface abre em uma
janela própria; também funciona no navegador em `http://127.0.0.1:8101` quando o app está aberto.

### 1.6 Problemas comuns

| Sintoma | O que fazer |
| --- | --- |
| `setup` falha por falta de espaço | Libere espaço e rode `setup --yes` de novo. |
| Download interrompido | Rode `setup --yes` de novo; o download é retomado. |
| "porta em uso" | Outro BuscaTeses já está aberto; use-o ou feche-o. `serve --port 8111` usa outra porta. |
| Assistente não mostra as ferramentas | Confirme com `connect --list`, reinicie o cliente e verifique se o caminho do comando existe. |
| Busca por significado desligada | O modelo de embedding não foi baixado: `setup --yes --only model`. |
| Endereço público não é criado | Verifique a internet e se a rede bloqueia `*.trycloudflare.com`; tente de novo. Detalhes em `docs/SOLUCAO-DE-PROBLEMAS.md`. |

Mais em `docs/SOLUCAO-DE-PROBLEMAS.md`. Logs em `<pasta de dados>/logs/buscateses.log`.

## 2. Pastas e arquivos da pessoa usuária

- Dados: `~/Library/Application Support/BuscaTeses` (macOS), `%LOCALAPPDATA%\BuscaTeses` (Windows),
  `~/.local/share/BuscaTeses` (Linux). Pode ser alterada com a variável `BUSCATESES_HOME`.
- A biblioteca pessoal (`library/`) contém cópias de arquivos da pessoa. **Nunca** os envie para fora do computador nem
  os apague sem pedido explícito.
- O app não tem telemetria, contas nem banco remoto. A internet é usada para baixar o modelo de embedding e o catálogo,
  verificar atualizações (pode ser desligado em **Configurações**) e, **somente se a pessoa ativar**, criar o endereço
  público do ChatGPT/Claude.ai (baixa o `cloudflared` e abre o túnel) e carregar o VLibras (tradutor de Libras do governo
  federal). O VLibras vem **desligado por padrão** e é ativado pelo botão “Libras” no cabeçalho ou em
  **Configurações → Acessibilidade**; quando desligado, nenhum conteúdo da página é enviado a `vlibras.gov.br`.
- Chaves de API, tokens do MCP e o segredo do endereço público (`remote.secret`) ficam na pasta de dados e nunca são
  exibidos de volta pela interface/API (apenas indicadores como “chave configurada”); um campo de segredo deixado em
  branco mantém o valor guardado.
- O conector `cloudflared` fica em `bin/` na pasta de dados e só existe depois que a pessoa cria um endereço público.
- Na biblioteca, PDFs abrem no visualizador; HTML, Markdown e TXT são exibidos como **texto simples** (sem executar
  scripts) e EPUB/DOCX são baixados como anexo.

## 3. Contribuir com código

Mapa do repositório:

| Caminho | Conteúdo |
| --- | --- |
| `src/buscateses/` | aplicativo: API FastAPI, busca, catálogo, biblioteca, MCP, integrações, desktop, CLI |
| `src/buscateses/web/` | interface (HTML, CSS e JavaScript sem etapa de build) |
| `tools/dataset/` | pipeline que gera o dataset público a partir dos dados abertos da CAPES |
| `packaging/` | PyInstaller, DMG, Inno Setup e scripts de build dos instaladores |
| `tests/`, `e2e/` | testes Python (pytest) e de interface/acessibilidade (Playwright + axe) |
| `docs/` | documentação para usuários e desenvolvedores |

Comandos:

```bash
uv venv && uv pip install -e ".[dev,desktop]"
uv run pytest -m "not slow"          # testes rápidos, sem rede
uv run ruff check src tests tools
npm ci && npx playwright test        # interface e acessibilidade
uv run buscateses serve --no-browser # servidor local em http://127.0.0.1:8101
```

Regras:

- Mantenha o servidor ligado somente em `127.0.0.1` e as ferramentas MCP somente leitura.
- Textos para a pessoa usuária em português do Brasil, simples e acionáveis; código e comentários em inglês.
- Não adicione dependências pesadas (PyTorch, bancos externos) ao app; o instalador precisa continuar pequeno.
- Nunca versione dados (`*.parquet`, `*.sqlite`, `*.npy`), PDFs, livros, tokens ou caminhos pessoais.
- Interface: WCAG 2.2 AA; rode os testes do Playwright antes de abrir um pull request.
