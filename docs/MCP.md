# Usar o BuscaTeses com assistentes de IA (MCP)

O **MCP** (Model Context Protocol) é um padrão aberto que permite a um assistente de IA usar ferramentas que rodam no
seu computador. Com ele, o Claude, o Codex, o Cursor e outros assistentes que rodam neste computador podem consultar o
catálogo de teses e dissertações (CAPES e BDTD) e a sua biblioteca pessoal e citar as fontes, sem enviar seus dados
para a nuvem. Para assistentes na
web (ChatGPT e Claude.ai), veja [Acesso remoto](#acesso-remoto-chatgpt-e-claudeai).

O servidor MCP do BuscaTeses é **somente leitura**: o assistente pode buscar e ler, mas não altera o catálogo nem a
biblioteca.

## Conexão com um clique

1. Abra o BuscaTeses e vá em **Conectar assistentes**.
2. Clique em **Conectar ao <nome>** no cartão do assistente que você usa.
3. Feche e abra o assistente para que ele carregue o servidor.

O BuscaTeses grava a configuração do cliente, cria um backup `*.bak-buscateses` e preserva as outras entradas. A tela
mostra quais clientes foram detectados e se já estão conectados. Para desfazer, clique em **Desconectar** ou use o
comando abaixo.

## Pelo terminal

```bash
buscateses connect --list            # estado de cada cliente
buscateses connect claude_desktop    # conecta
buscateses disconnect claude_desktop # desconecta
```

Clientes aceitos por `connect`:

| Identificador | Cliente | Como é configurado |
| --- | --- | --- |
| `claude_desktop` | Claude Desktop | edita o JSON `mcpServers` |
| `cursor` | Cursor | edita `~/.cursor/mcp.json` |
| `lm_studio` | LM Studio | edita `~/.lmstudio/mcp.json` |
| `gemini_cli` | Gemini CLI | edita `~/.gemini/settings.json` |
| `codex` | Codex CLI | edita `~/.codex/config.toml` |
| `claude_code` | Claude Code | executa `claude mcp add --scope user` |
| `vscode` | VS Code | configuração manual (trecho pronto) |

Para ChatGPT e Claude.ai, que rodam na web, veja [Acesso remoto](#acesso-remoto-chatgpt-e-claudeai).

O nome do executável depende da instalação:

| Instalação | Comando do servidor MCP |
| --- | --- |
| macOS (instalador) | `/Applications/BuscaTeses.app/Contents/MacOS/buscateses-cli mcp` |
| Windows (instalador) | `%LOCALAPPDATA%\Programs\BuscaTeses\buscateses-cli.exe mcp` |
| Linux (instalador) | `buscateses mcp` |
| Código-fonte | `buscateses mcp` ou `python -m buscateses mcp` |

## Configuração manual por cliente

Use o caminho absoluto do executável. Nos exemplos abaixo, troque `<COMANDO>` pelo caminho da tabela anterior.

### Claude Desktop

Arquivo:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "buscateses": {
      "command": "<COMANDO>",
      "args": ["mcp"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add --scope user buscateses -- <COMANDO> mcp
claude mcp list
```

### Codex CLI

Arquivo `~/.codex/config.toml` (preserve o restante do arquivo):

```toml
[mcp_servers.buscateses]
command = "<COMANDO>"
args = ["mcp"]
```

### Cursor

Arquivo `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "buscateses": {
      "command": "<COMANDO>",
      "args": ["mcp"]
    }
  }
}
```

### Gemini CLI

Arquivo `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "buscateses": {
      "command": "<COMANDO>",
      "args": ["mcp"]
    }
  }
}
```

### LM Studio

Arquivo `~/.lmstudio/mcp.json`:

```json
{
  "mcpServers": {
    "buscateses": {
      "command": "<COMANDO>",
      "args": ["mcp"]
    }
  }
}
```

### VS Code

Arquivo `.vscode/mcp.json` na pasta do projeto (ou nas configurações de MCP do VS Code):

```json
{
  "servers": {
    "buscateses": {
      "type": "stdio",
      "command": "<COMANDO>",
      "args": ["mcp"]
    }
  }
}
```

Depois de qualquer configuração manual, **reinicie o cliente** e confirme que a ferramenta `catalog_status` aparece.

## Ferramentas e parâmetros

Todas as ferramentas são declaradas como somente leitura (`readOnlyHint=true`) e não acessam a rede.

| Ferramenta | Parâmetros | Limite | O que devolve |
| --- | --- | --- | --- |
| `search_theses` | `query` (obrigatório), `year_min`, `year_max`, `degree`, `uf`, `institution`, `area`, `mode` (`hybrid`/`text`/`semantic`), `limit` | `limit` 1–50 (padrão 20) | Lista de trabalhos com trecho, metadados, URL e `matched_by` |
| `get_thesis` | `id` | — | Metadados completos, incluindo resumo, orientação e palavras-chave |
| `similar_theses` | `id`, `limit` | `limit` 1–20 (padrão 10) | Trabalhos semanticamente próximos |
| `search_library` | `query`, `document_id` (opcional), `limit` | `limit` 1–30 (padrão 20) | Trechos da biblioteca com documento e páginas |
| `list_library_documents` | — | — | Documentos com status, páginas, trechos e se têm vetores |
| `get_library_passage` | `chunk_id`, `context_chunks` | `context_chunks` 0–3 (padrão 1) | Trecho e vizinhos, com páginas |
| `catalog_status` | — | — | Versão do catálogo, número de registros e contadores da biblioteca |

Filtros de `search_theses`:

- `degree`: `Mestrado`, `Doutorado`, `Mestrado Profissional`, `Doutorado Profissional`;
- `uf`: sigla de duas letras (ex.: `MG`);
- `institution`: sigla da instituição (ex.: `UFMG`);
- `area`: grande área do conhecimento;
- `mode`: `hybrid` combina palavras e significado; `text` usa só palavras; `semantic` usa só significado.

Os campos `degree`, `uf` e `institution` aceitam vários valores separados por vírgula.

## Exemplos de pedidos

```text
Use o BuscaTeses para encontrar teses de doutorado sobre educação inclusiva defendidas entre 2015 e 2024.
Apresente título, autor, ano, instituição e o link de cada uma.
```

```text
Busque no BuscaTeses trabalhos sobre agricultura familiar no Cerrado. Depois, use similar_theses no mais
relevante para encontrar mais 10 trabalhos próximos e explique o que eles têm em comum.
```

```text
Consulte catalog_status e me diga a versão do catálogo e quantos documentos há na minha biblioteca.
```

```text
Pesquise na minha biblioteca o conceito de "letramento científico" e traga os trechos com a página.
```

> Assistentes podem errar ou inventar referências. Confira título, autor, ano e link no BuscaTeses antes de citar.

## Modo HTTP com token

O modo padrão é **stdio**: o próprio cliente inicia o processo. No modo **Streamable HTTP**, o BuscaTeses abre um
endpoint local que outros programas podem acessar.

```bash
buscateses mcp --http --port 8102
```

O servidor escuta apenas em `127.0.0.1`. Se nenhum token for informado, um token aleatório é gerado, salvo em
`config.json` (`mcp_http.token`) e mostrado no terminal. A interface e a API **não exibem o token de volta**: as
Configurações mostram apenas se ele está definido, e deixar o campo em branco mantém o valor atual. O endpoint é:

```text
http://127.0.0.1:8102/mcp
```

Toda requisição precisa do cabeçalho:

```text
Authorization: Bearer <token>
```

Para definir um token específico:

```bash
buscateses mcp --http --port 8102 --token meu-token-longo
```

Para testar **somente no seu computador**, sem token:

```bash
buscateses mcp --http --allow-no-token
```

> `--allow-no-token` desliga a autenticação. Nunca use essa opção com um túnel ou em uma rede compartilhada.

## Acesso remoto (ChatGPT e Claude.ai)

O ChatGPT e o Claude.ai rodam na nuvem e não alcançam `127.0.0.1` da sua máquina. O BuscaTeses resolve isso com um
**endereço público temporário** criado pelo Cloudflare Quick Tunnel: sem conta, sem configuração e sem instalar nada
à mão. O próprio aplicativo baixa o conector oficial na primeira vez.

### Pela interface

1. Abra **Conectar assistentes** e vá até **Assistentes na web: ChatGPT e Claude.ai**.
2. Deixe desmarcada a opção da biblioteca pessoal (veja abaixo) e clique em **Criar endereço público**.
3. Aguarde o download do conector (cerca de 30 MB) e a criação do endereço. A tela mostra o progresso.
4. Copie o endereço com **Copiar endereço** e siga os passos que aparecem na tela para o Claude.ai ou o ChatGPT.
5. Ao terminar, clique em **Desligar**. O endereço deixa de funcionar imediatamente.

O endereço tem o formato `https://<nome>.trycloudflare.com/mcp/<segredo>` e muda cada vez que é criado. Ele funciona
somente enquanto o BuscaTeses estiver aberto (ou o comando `remote start` estiver rodando).

### Pelo terminal

```bash
buscateses remote start                 # cria o endereço e mostra a URL
buscateses remote start --include-library
buscateses remote status                # estado atual, sem manter o terminal preso
buscateses remote stop                  # desliga o endereço
```

`remote start` mantém o terminal aberto até `Ctrl+C` (ou `remote stop` em outro terminal). O endereço também é
desligado quando o aplicativo é fechado.

### Como o endereço é protegido

- O caminho inclui um segredo aleatório (`/mcp/<segredo>`), gerado na primeira vez e guardado em `config.json`
  (`remote.secret`). Qualquer outro caminho responde **404**.
- **Gerar novo endereço** (ou `POST /api/remote/rotate`) troca o segredo. O endereço antigo para de funcionar na hora.
- A proteção contra DNS rebinding fica desativada **apenas nesse endpoint público**, porque o túnel reescreve o
  cabeçalho `Host`. A barreira passa a ser o segredo no caminho. O endpoint local com token continua com a proteção
  ligada.
- O servidor é somente leitura. Por padrão, as ferramentas da biblioteca pessoal **não** são expostas. Marque
  **Permitir que o assistente pesquise também a minha biblioteca pessoal** (ou use `--include-library`) apenas se
  aceitar que trechos dos seus documentos apareçam nas respostas.
- Enquanto o endereço estiver ligado, **qualquer pessoa que o tenha** pode pesquisar no catálogo. Não compartilhe.

### Configuração e API

`POST /api/remote/start` recebe `{"include_library": false}`; `POST /api/remote/stop` e `POST /api/remote/rotate` não
recebem corpo. `GET /api/remote` devolve `state` (`off`, `downloading`, `starting`, `online`, `error`), `url`,
`include_library`, `message` e `cloudflared_installed`. O segredo nunca é devolvido pela API.

O conector é baixado de uma versão fixa do repositório oficial da Cloudflare e conferido por SHA-256 antes de ser
executado; ele fica em `bin/` na pasta de dados. Licença Apache-2.0 (veja [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)).
Para remover o conector, feche o BuscaTeses e apague essa pasta.

### Túneis manuais (avançado)

Se você já usa um túnel próprio, também é possível apontar o modo HTTP com token para ele. Esse modo é independente do
endereço criado pela interface.

```bash
buscateses mcp --http --port 8102
```

O servidor escuta apenas em `127.0.0.1`, exige `Authorization: Bearer <token>` e rejeita, por padrão, cabeçalhos
`Host` diferentes de `127.0.0.1` e `localhost` (proteção contra DNS rebinding). Para usar um túnel, informe o hostname
público com `--allowed-host` — sem `https://`, sem barra e sem caminho:

```bash
buscateses mcp --http --port 8102 --allowed-host <hostname-publico>
```

O hostname também pode ficar salvo em `config.json` (`mcp_http.allowed_hosts`) ou ser enviado em `PUT /api/settings`.
Não use `*`; para HTTPS na porta padrão, use só `maquina.exemplo.com`.

Com **Tailscale Funnel**:

```bash
tailscale funnel --bg 8102
tailscale funnel status                # mostra https://<máquina>.<tailnet>.ts.net/
buscateses mcp --http --port 8102 --allowed-host <máquina>.<tailnet>.ts.net
tailscale funnel reset                 # desliga
```

Com **Cloudflare Tunnel** instalado por você:

```bash
cloudflared tunnel --url http://127.0.0.1:8102
buscateses mcp --http --port 8102 --allowed-host <nome>.trycloudflare.com
```

O Funnel e o Quick Tunnel **não adicionam autenticação**: qualquer pessoa com a URL chega ao servidor, e o token
Bearer continua sendo a única barreira. Para um endereço estável, prefira um túnel nomeado com uma política do
**Cloudflare Access**.

### Alertas de segurança

- O endereço público da interface já inclui um segredo no caminho. Ainda assim, trate-o como uma senha.
- **Não** use `--allow-no-token` em um túnel manual.
- Libere em `--allowed-host` apenas o hostname do seu túnel; não use curinga (`*`). Remova o host quando o túnel não
  for mais usado.
- O servidor é somente leitura, mas a biblioteca pessoal pode conter textos sensíveis.
- Desligue o endereço assim que terminar.
- Para trocar o token do modo manual, edite `mcp_http.token` em `config.json` (ou apague o valor), reinicie o servidor
  e reconecte o cliente.
- Em caso de dúvida, não exponha: use o modo stdio, que nunca abre uma porta de rede.

## Requisitos e diagnóstico

- O catálogo precisa estar instalado (`buscateses setup` ou pela tela **Primeiros passos**).
- Sem o modelo de embedding, as buscas continuam no modo textual; o assistente verá `warnings` com
  `semantic_unavailable`.
- Se o assistente não mostrar as ferramentas: confira `buscateses connect --list`, o caminho do comando, o backup da
  configuração e reinicie o cliente.
- Logs: `logs/buscateses.log` na pasta de dados.

Mais problemas em [SOLUCAO-DE-PROBLEMAS.md](SOLUCAO-DE-PROBLEMAS.md).
