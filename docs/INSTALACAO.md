# Instalação e atualização

Este guia cobre a instalação do BuscaTeses em macOS, Windows e Linux, a instalação pelo código-fonte, a atualização do
aplicativo e do catálogo e a desinstalação completa. Se algo não funcionar, veja
[SOLUCAO-DE-PROBLEMAS.md](SOLUCAO-DE-PROBLEMAS.md).

## Antes de começar

| Item | Requisito |
| --- | --- |
| Sistema | macOS 11 ou mais recente (Apple Silicon ou Intel), Windows 10/11 64 bits ou Linux x86_64 |
| Disco | ~12 GB livres durante a instalação; ~8 GB ocupados pelo catálogo e ~330 MB pelo modelo |
| Memória | 8 GB de RAM recomendados |
| Internet | primeira instalação, verificações de atualização (opcionais) e VLibras (opcional, desligado por padrão) |
| Conta | nenhuma; o app não pede login e não envia telemetria |

O download do catálogo é de aproximadamente 3 GB. Durante a instalação, o BuscaTeses precisa de espaço para o download
e para o catálogo já montado; por isso reserve cerca de 12 GB livres. Ao final, os arquivos baixados são apagados.

## macOS

### Instalar

1. Baixe o arquivo certo para o seu Mac:
   - Apple Silicon (M1 ou mais recente): `BuscaTeses-macos-arm64.dmg`;
   - Intel: `BuscaTeses-macos-x86_64.dmg`.

   Para conferir o chip, abra o menu Apple → **Sobre este Mac**. Se aparecer "Chip Apple M…", use o arm64.
2. Abra o `.dmg` e arraste **BuscaTeses** para a pasta **Aplicativos**.
3. Na primeira abertura, o macOS pode avisar que o app não pôde ser verificado, porque ele não tem o certificado pago
   da Apple.

### Aviso do Gatekeeper

1. Na pasta **Aplicativos**, clique em **BuscaTeses** com o botão direito (ou Control + clique) e escolha **Abrir**.
2. Confirme em **Abrir**.
3. Se a opção não aparecer, abra **Ajustes do Sistema → Privacidade e Segurança**, role até a mensagem sobre o
   BuscaTeses e clique em **Abrir Mesmo Assim**.

Depois da primeira vez, o app abre normalmente com duplo clique.

> **Alternativa avançada:** quem usa o Terminal pode remover a marca de quarentena com
> `xattr -dr com.apple.quarantine /Applications/BuscaTeses.app`. Faça isso apenas se entende o comando e confia no
> arquivo baixado.

### Linha de comando no macOS

O executável de console fica dentro do aplicativo:

```bash
/Applications/BuscaTeses.app/Contents/MacOS/buscateses-cli --version
```

O nome tem o sufixo `-cli` porque o macOS não diferencia maiúsculas de minúsculas e `buscateses` colidiria com o
executável `BuscaTeses`. Se preferir, crie um atalho:

```bash
mkdir -p ~/.local/bin
ln -s /Applications/BuscaTeses.app/Contents/MacOS/buscateses-cli ~/.local/bin/buscateses-cli
```

## Windows

### Instalar

1. Baixe `BuscaTeses-windows-x64-setup.exe`.
2. Execute o arquivo. O instalador instala **só para o seu usuário**, sem pedir senha de administrador.
3. Ao final, o BuscaTeses abre. Se não abrir, procure **BuscaTeses** no menu Iniciar.

O programa fica em `%LOCALAPPDATA%\Programs\BuscaTeses`.

### Aviso do SmartScreen

Na primeira execução, o Windows pode mostrar **"O Windows protegeu o computador"**. Isso acontece porque o instalador
não tem uma assinatura digital comercial.

1. Clique em **Mais informações**.
2. Clique em **Executar assim mesmo**.

O instalador também mostra esse aviso em uma tela própria antes de concluir.

### WebView2

O BuscaTeses usa a janela nativa do Windows (WebView2). Windows 10 e 11 atualizados já trazem esse componente. Se ele
não estiver disponível, o app **não trava**: abre a interface no seu navegador padrão e continua funcionando. Nesse
caso, mantenha a janela do terminal/processo aberta enquanto usa e feche-a para encerrar o servidor.

Para instalar o WebView2 manualmente, baixe o "Evergreen Runtime" no site da Microsoft.

### Linha de comando no Windows

```powershell
& "$env:LOCALAPPDATA\Programs\BuscaTeses\buscateses-cli.exe" --version
```

No PowerShell, se quiser digitar apenas `buscateses-cli`, adicione a pasta ao `PATH` do usuário:

```powershell
[Environment]::SetEnvironmentVariable(
  "Path",
  "$env:LOCALAPPDATA\Programs\BuscaTeses;" + [Environment]::GetEnvironmentVariable("Path", "User"),
  "User"
)
```

## Linux

### Instalar

1. Baixe `BuscaTeses-linux-x86_64.tar.gz`.
2. Extraia e execute o instalador:

```bash
mkdir -p ~/Downloads/buscateses
tar -xzf ~/Downloads/BuscaTeses-linux-x86_64.tar.gz -C ~/Downloads/buscateses
cd ~/Downloads/buscateses
./install.sh
```

O instalador:

- copia o programa para `~/.local/opt/buscateses` (pode ser mudado com `BUSCATESES_INSTALL_DIR`);
- cria o atalho `~/.local/bin/buscateses`;
- adiciona o BuscaTeses ao menu de aplicativos.

Se `~/.local/bin` não estiver no `PATH`, adicione ao seu `~/.profile`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

O BuscaTeses tenta abrir a janela nativa; se as bibliotecas gráficas não estiverem disponíveis, abre a interface no
navegador padrão. No Linux, o comando de console chama-se `buscateses` e, como o executável está empacotado,
`buscateses` sem argumentos abre o app.

### Dependências gráficas

A janela nativa depende das bibliotecas GTK/WebKit disponíveis na distribuição. Se elas não estiverem instaladas, o
BuscaTeses abre no navegador automaticamente. Em distribuições Debian/Ubuntu, a janela nativa costuma funcionar com o
pacote `python3-gi` e as bibliotecas WebKitGTK já presentes no sistema.

## Instalação pelo código-fonte

Use esta opção se não houver instalador para a sua plataforma ou se você preferir acompanhar o código. É necessário
ter internet e o [uv](https://docs.astral.sh/uv/) instalado (os scripts instalam o uv se ele faltar).

### Scripts prontos

macOS e Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/andrebadini/buscateses-local/main/scripts/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/andrebadini/buscateses-local/main/scripts/install.ps1 | iex
```

Os scripts instalam o BuscaTeses com o `uv`, baixam o modelo e o catálogo (`buscateses setup --yes`) e,
opcionalmente, conectam um assistente de IA. Opções úteis:

```bash
./scripts/install.sh --local                  # instala a partir deste diretório (desenvolvimento)
./scripts/install.sh --connect claude_desktop # conecta o assistente indicado
./scripts/install.sh --yes                    # não faz perguntas
```

No Windows: `.\scripts\install.ps1 -Local -Connect claude_desktop -Yes`.

### Instalação manual

```bash
git clone https://github.com/andrebadini/buscateses-local.git
cd buscateses-local
uv tool install --python 3.12 ".[desktop]"
buscateses setup --yes
buscateses
```

Nesse modo, o comando é `buscateses`: sem argumentos, ele abre o app, tanto na instalação por código-fonte quanto nos
instaladores empacotados. `buscateses app` continua disponível como alternativa explícita, e `buscateses --help` mostra
a lista de comandos.

## Primeiro uso

Ao abrir o app, a tela **Primeiros passos** baixa o modelo e o catálogo e mostra o progresso. Se preferir o terminal:

```bash
buscateses setup --yes                    # modelo + catálogo
buscateses setup --yes --only model       # só o modelo (~330 MB)
buscateses setup --yes --only catalog     # só o catálogo (~3 GB de download)
buscateses status                         # confere o que está pronto
```

O `setup` é idempotente: se for interrompido, rode de novo e ele continua de onde parou.

## Atualizar o aplicativo

- **macOS:** baixe o novo `.dmg`, arraste o app para **Aplicativos** e confirme a substituição. Os dados continuam na
  pasta de dados.
- **Windows:** execute o novo instalador. Ele substitui a versão anterior e mantém os dados.
- **Linux:** extraia o novo `.tar.gz` e rode `./install.sh` de novo.
- **Código-fonte:** repita a instalação (`uv tool install --force ...`) ou rode o script novamente.

O BuscaTeses verifica se há uma versão nova do aplicativo na API de releases do GitHub (essa verificação pode ser
desligada em **Configurações**). Não há atualização automática: baixe o novo instalador na página de Releases.

## Atualizar o catálogo

Quando uma nova versão do catálogo é publicada, o BuscaTeses avisa. Para atualizar:

- no app, abra **Configurações → Verificar atualizações**;
- ou no terminal:

```bash
buscateses update --yes
buscateses status
```

A atualização baixa a nova versão, monta o índice e só então troca o catálogo em uso. Se algo falhar, a versão anterior
continua funcionando. A release anterior é mantida temporariamente para permitir esse retorno.

## Desinstalar

Antes de remover o app, desconecte os assistentes de IA para não deixar configurações apontando para um programa
inexistente:

```bash
buscateses disconnect claude_desktop   # repita para cada cliente conectado
buscateses connect --list
```

### macOS

1. Arraste **BuscaTeses** da pasta **Aplicativos** para o **Lixo**.
2. Apague a pasta de dados (isso remove catálogo, modelo, biblioteca, favoritos e configurações):

```bash
rm -rf ~/Library/Application\ Support/BuscaTeses
```

### Windows

1. Abra **Configurações → Aplicativos → Aplicativos instalados**, encontre **BuscaTeses** e escolha **Desinstalar**.
2. O desinstalador pergunta se você quer apagar os dados. Responda **Sim** para remover também o catálogo, o modelo e a
   biblioteca; responda **Não** para manter tudo e poder reinstalar depois.
3. Se preferir apagar manualmente depois, remova `%LOCALAPPDATA%\BuscaTeses`.

### Linux

```bash
rm -rf ~/.local/opt/buscateses
rm -f ~/.local/bin/buscateses
rm -f ~/.local/share/applications/buscateses.desktop
rm -rf ~/.local/share/BuscaTeses
```

### Instalação por código-fonte

```bash
uv tool uninstall buscateses-local
rm -rf ~/.local/share/BuscaTeses        # Linux
rm -rf ~/Library/Application\ Support/BuscaTeses   # macOS
```

No Windows, remova `%LOCALAPPDATA%\BuscaTeses`.

## Usar sem internet

Depois que o modelo e o catálogo estão instalados, o BuscaTeses funciona **totalmente offline**: busca, filtros,
favoritos, exportação, biblioteca pessoal e servidor MCP.

O que ainda tenta usar a internet — e pode ser desligado ou evitado:

- a verificação de novas versões do app e do catálogo, que pode ser desligada em **Configurações → Atualizações do
  aplicativo**;
- o **VLibras**, que é opcional, vem **desligado por padrão** e só carrega o serviço externo `vlibras.gov.br` depois que
  você ativa o botão **Libras** no cabeçalho ou a opção em **Configurações → Acessibilidade** (a interface funciona sem
  ele);
- links de **texto completo**, que abrem o site de origem quando você clica.

Não há telemetria: buscas, favoritos, exportações e documentos da biblioteca nunca são enviados para fora do computador.
Chaves de API e tokens do MCP ficam apenas na pasta de dados local e não são exibidos de volta pela interface.

Para preparar um computador sem internet: instale e baixe tudo em uma máquina conectada e copie a pasta de dados para o
mesmo caminho na outra máquina. O caminho pode ser mudado com a variável `BUSCATESES_HOME`.
