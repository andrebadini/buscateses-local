#!/usr/bin/env bash
# Instala o BuscaTeses a partir do código-fonte (macOS/Linux).
#
# Uso:
#   curl -LsSf https://raw.githubusercontent.com/andrebadini/buscateses-local/main/scripts/install.sh | sh
#   ./scripts/install.sh [--local] [--connect <cliente>] [--yes]
set -euo pipefail

REPOSITORY="git+https://github.com/andrebadini/buscateses-local"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR=""
CONNECT_CLIENT=""
ASSUME_YES=0

usage() {
  cat <<'TEXT'
Uso: install.sh [opções]

  --local              instala a partir deste diretório (desenvolvimento)
  --connect <cliente>  conecta o assistente de IA indicado (ex.: claude_desktop)
  --yes                não faz perguntas (usa as opções informadas)
  -h, --help           mostra esta ajuda
TEXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local)
      LOCAL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
      shift
      ;;
    --connect)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "Erro: --connect exige o identificador do cliente." >&2
        exit 2
      fi
      CONNECT_CLIENT="$2"
      shift 2
      ;;
    --yes|-y)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Erro: opção desconhecida: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "O uv não foi encontrado. Instalando o uv (gerenciador oficial)…"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    echo "Erro: instale curl ou wget para baixar o uv." >&2
    exit 1
  fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Erro: o uv foi instalado, mas não está no PATH. Abra um novo terminal e tente de novo." >&2
  exit 1
fi

if [[ -n "$LOCAL_DIR" ]]; then
  SPECIFICATION="${LOCAL_DIR}[desktop]"
  echo "Instalando o BuscaTeses a partir de ${LOCAL_DIR}…"
else
  SPECIFICATION="buscateses-local[desktop] @ $REPOSITORY"
  echo "Instalando o BuscaTeses a partir do repositório oficial…"
fi

if ! uv tool install --python 3.12 --force "$SPECIFICATION"; then
  echo "Erro: não foi possível instalar o BuscaTeses com o uv." >&2
  exit 1
fi

TOOL_BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
export PATH="$TOOL_BIN:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v buscateses >/dev/null 2>&1; then
  echo "Erro: o comando 'buscateses' não foi encontrado após a instalação." >&2
  exit 1
fi

echo "Baixando o modelo de busca e o catálogo (pode levar alguns minutos)…"
if ! buscateses setup --yes; then
  echo "Erro: a preparação inicial falhou. Execute 'buscateses setup' para tentar de novo." >&2
  exit 1
fi

if [[ -z "$CONNECT_CLIENT" && "$ASSUME_YES" -eq 0 ]]; then
  ANSWER=""
  read -r -p "Deseja conectar o BuscaTeses a um assistente de IA agora? [s/N] " ANSWER || ANSWER=""
  case "$ANSWER" in
    s|S|sim|Sim|y|Y|yes|Yes)
      buscateses connect --list || true
      read -r -p "Identificador do cliente (ex.: claude_desktop): " CONNECT_CLIENT || CONNECT_CLIENT=""
      ;;
  esac
fi

if [[ -n "$CONNECT_CLIENT" ]]; then
  if ! buscateses connect "$CONNECT_CLIENT"; then
    echo "Aviso: não foi possível conectar '$CONNECT_CLIENT'. Você pode tentar depois em 'buscateses connect'." >&2
  fi
fi

echo
echo "Tudo pronto! Abra com:"
echo "  buscateses"
echo
echo "Dica para assistentes de IA: configure o servidor MCP com 'buscateses connect <cliente>'."
