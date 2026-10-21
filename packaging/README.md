# Empacotamento do BuscaTeses

Este diretório gera os instaladores nativos. Tudo é produzido a partir de um
único comando:

```bash
python packaging/build.py
```

O script detecta o sistema operacional, instala `packaging/requirements-build.txt`,
gera os ícones ausentes, roda o PyInstaller, executa o teste de fumaça
(`--version`, `status --json` e `serve` em `127.0.0.1:8765`), monta o instalador
do sistema e grava `packaging/output/SHA256SUMS.txt`.

Use `--skip-deps`, `--skip-smoke` ou `--skip-icons` para pular etapas durante o
desenvolvimento.

## Artefatos (nomes sem versão, para links estáveis)

| Sistema | Arquivo |
|---|---|
| macOS arm64/x86_64 | `BuscaTeses-macos-<arm64\|x86_64>.dmg` |
| Windows x64 | `BuscaTeses-windows-x64-setup.exe` |
| Linux x86_64 | `BuscaTeses-linux-x86_64.tar.gz` |

O `release.yml` publica esses arquivos em
`https://github.com/andrebadini/buscateses-local/releases/latest/download/<arquivo>`.

## Executáveis

O PyInstaller gera dois executáveis no mesmo diretório:

- `BuscaTeses` (janela nativa, sem console);
- o cliente de console usado pelos assistentes de IA (`... mcp`).

macOS e Windows usam sistemas de arquivos que ignoram maiúsculas/minúsculas.
Neles, `BuscaTeses` e `buscateses` seriam o mesmo caminho, então o cliente de
console se chama `buscateses-cli`; no Linux ele se chama `buscateses`.
`buscateses.integrations.mcp_command()` resolve o nome correto em cada sistema,
de modo que a tela "Conectar IA" continua configurando os clientes com um clique.

## Windows (Inno Setup 6)

O `AppId` fixo é `{cbafab11-c1fd-4624-934f-c742f4aa5b81}`. Não o altere:
atualizações dependem dele para substituir a instalação anterior. A instalação
é por usuário (`PrivilegesRequired=lowest`), em
`%LOCALAPPDATA%\Programs\BuscaTeses`, e o desinstalador só apaga
`%LOCALAPPDATA%\BuscaTeses` (dados, modelo e catálogo) se o usuário confirmar.

## Ícones

`python packaging/icons/make_icons.py` gera os PNGs (16–1024), `icon.ico` e
`icon.icns` a partir de `src/buscateses/web/assets/brand/icon.svg`. Os arquivos
gerados são versionados para que o build não dependa de cairo/Chromium.
