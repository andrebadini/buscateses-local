# Política de segurança

O BuscaTeses roda localmente e não tem servidor remoto, contas ou telemetria. Ainda assim, levamos a sério falhas que
possam expor dados, permitir execução de código ou quebrar o isolamento local.

## Como reportar uma vulnerabilidade

**Não abra uma issue pública.** Use o canal privado do GitHub:

1. Acesse <https://github.com/andrebadini/buscateses-local/security/advisories/new>.
2. Descreva o problema, o impacto e os passos para reproduzir.
3. Inclua a versão do BuscaTeses, o sistema operacional e, se possível, um PoC mínimo.
4. Evite anexar dados pessoais, conteúdo de bibliotecas ou tokens reais.

Você receberá uma confirmação de recebimento. Trabalharemos para avaliar e corrigir o problema e combinaremos com você
a divulgação, quando aplicável. Pedimos que não divulgue publicamente antes de uma correção ou de um prazo combinado.

## Escopo

Estão no escopo, entre outros:

- execução remota de código ou escalada de privilégios;
- leitura ou escrita de arquivos fora da pasta de dados;
- bypass do isolamento em `127.0.0.1` (DNS rebinding, CSRF, CORS);
- bypass da autenticação Bearer do MCP HTTP ou do segredo no caminho do endereço público;
- injeção em consultas SQL/FTS5, caminhos, HTML ou comandos;
- exposição de tokens, chaves ou dados pessoais;
- falhas no processo de build/release que permitam substituir instaladores ou artefatos;
- vulnerabilidades em dependências que afetem o app de forma explorável.

## O que não é uma vulnerabilidade

- **Acesso físico ou por outro processo do mesmo usuário.** Qualquer programa rodando como você pode ler sua pasta de
  dados; isso é inerente a aplicativos locais.
- **Servidor exposto por túnel.** Se você publicar o MCP HTTP na internet sem token, com `--allow-no-token` ou
  compartilhar o endereço público do Cloudflare, a exposição é uma escolha de configuração. Veja os avisos em
  [docs/MCP.md](docs/MCP.md).
- **Dados da CAPES.** Erros, duplicatas ou omissões no catálogo devem ser tratados como problema de dados, não de
  segurança. A fonte oficial é a CAPES.
- **Conteúdo de documentos da biblioteca.** O app não valida a veracidade nem a segurança do conteúdo dos PDFs; abra
  arquivos de origem confiável.
- **Avisos de Gatekeeper/SmartScreen.** São esperados porque o app não tem certificado comercial.
- **Engenharia social ou phishing** sem relação com o código.
- **Relatos automatizados de scanners** sem impacto demonstrado no BuscaTeses.

## Conteúdo da biblioteca e recursos opcionais

- **PDF.** O arquivo é entregue apenas por `127.0.0.1`, com `X-Content-Type-Options: nosniff`, e aberto inline para
  usar o visualizador do navegador. Nesse caso não há `Content-Security-Policy` com `sandbox`, porque o visualizador
  do Chromium/WebView2 não renderiza dentro dela. O isolamento continua sendo o servidor local e a checagem de
  caminho; abra somente arquivos de origem confiável.
- **HTML, Markdown e TXT.** São servidos como `text/plain` com `Content-Security-Policy: sandbox`, portanto o
  navegador não interpreta tags nem executa scripts do documento.
- **VLibras (opcional).** Vem desligado por padrão. Quando você ativa o botão **Libras** ou a opção em
  **Configurações → Acessibilidade**, o app carrega scripts de `vlibras.gov.br` (que redireciona para o CDN
  `cdn.jsdelivr.net`) e o tradutor passa a rodar **na mesma página** do BuscaTeses, com acesso ao conteúdo exibido.
  Com a opção desligada, nenhum conteúdo da página é enviado ao VLibras. A política de segurança de conteúdo (CSP)
  libera apenas essas origens e os recursos necessários ao widget; ative apenas se aceitar esse carregamento.

## Endereço público (Cloudflare Quick Tunnel)

A tela **Conectar assistentes** pode criar um endereço temporário para o ChatGPT e o Claude.ai. O modelo de ameaça é:

- **O endereço é a credencial.** A URL inclui um segredo aleatório de 24 bytes no caminho (`/mcp/<segredo>`); qualquer
  outro caminho responde **404**. Quem tiver a URL completa pode usar as ferramentas enquanto o endereço estiver
  ligado. O segredo fica em `config.json` (`remote.secret`), nunca é devolvido pela API e pode ser trocado com
  **Gerar novo endereço**.
- **Somente leitura.** As ferramentas não alteram o catálogo nem a biblioteca e não recebem caminhos de arquivo.
- **Biblioteca desligada por padrão.** As ferramentas da biblioteca pessoal só são registradas quando a pessoa marca a
  opção (ou usa `--include-library`); sem isso, o endpoint público nem as lista.
- **DNS rebinding.** A proteção de `Host` é desativada apenas nesse endpoint, porque o túnel reescreve o cabeçalho
  `Host` para o hostname público. A barreira passa a ser o segredo no caminho. O endpoint local com Bearer token
  continua com a proteção ligada.
- **Ciclo de vida.** O endereço é desligado ao fechar o aplicativo, ao clicar em **Desligar** ou ao encerrar
  `buscateses remote start`. A URL muda a cada criação.
- **Conector.** O `cloudflared` é baixado de uma versão fixa do repositório oficial da Cloudflare e conferido por
  SHA-256 antes de ser executado.

Não compartilhe o endereço nem o coloque em capturas de tela. Se desconfiar de uso indevido, clique em **Gerar novo
endereço** e atualize o conector no assistente.

## Versões suportadas

A versão mais recente publicada em Releases recebe correções de segurança. Se você usa uma versão antiga, atualize
antes de reportar.

## Boas práticas para quem usa

- Baixe o app apenas da página oficial de Releases ou do código-fonte do repositório.
- Mantenha o servidor e o MCP em `127.0.0.1`; não use `--allow-no-token` em redes compartilhadas.
- Use um token longo e exclusivo para o MCP HTTP e desligue o túnel quando não estiver usando.
- Trate o endereço público do ChatGPT/Claude.ai como uma senha: não o compartilhe e clique em **Desligar** ao terminar.
  Marque a opção da biblioteca pessoal apenas quando realmente precisar.
- Confira `SHA256SUMS.txt` ao baixar instaladores por canais alternativos.

Obrigado por ajudar a manter o BuscaTeses seguro.
