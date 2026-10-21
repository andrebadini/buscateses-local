#!/usr/bin/env bash
# Build the BuscaTeses macOS disk image (ad-hoc signed, not notarized).
#
# Usage: build_dmg.sh [BuscaTeses.app] [output-dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP="${1:-$ROOT/packaging/output/pyinstaller/BuscaTeses.app}"
OUT_DIR="${2:-$ROOT/packaging/output}"

case "$(uname -m)" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64) ARCH="x86_64" ;;
  *)
    echo "Erro: arquitetura não suportada: $(uname -m)" >&2
    exit 1
    ;;
esac

if [[ ! -d "$APP" ]]; then
  echo "Erro: bundle não encontrado: $APP" >&2
  exit 1
fi
for executable in BuscaTeses buscateses-cli; do
  if [[ ! -x "$APP/Contents/MacOS/$executable" ]]; then
    echo "Erro: executável ausente no bundle: Contents/MacOS/$executable" >&2
    exit 1
  fi
done

mkdir -p "$OUT_DIR"
DMG="$OUT_DIR/BuscaTeses-macos-$ARCH.dmg"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/buscateses-dmg.XXXXXX")"
cleanup() {
  rm -rf "$STAGING"
}
trap cleanup EXIT

echo "Assinando o aplicativo ad-hoc (sem certificado da Apple)…"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "Preparando o conteúdo do DMG…"
ditto "$APP" "$STAGING/BuscaTeses.app"
ln -s /Applications "$STAGING/Applications"

cat > "$STAGING/Como abrir pela primeira vez.txt" <<'TEXT'
BuscaTeses — como abrir pela primeira vez
=========================================

1. Arraste o ícone BuscaTeses para a pasta Aplicativos.
2. Abra a pasta Aplicativos e clique duas vezes em BuscaTeses.
3. Na primeira abertura, o macOS pode avisar que o aplicativo não pôde ser
   verificado porque não foi assinado com um certificado da Apple. Para abrir:
   - clique com o botão direito (ou Control+clique) no ícone e escolha "Abrir";
   - ou abra Ajustes do Sistema > Privacidade e Segurança, encontre a mensagem
     sobre o BuscaTeses e clique em "Abrir mesmo assim".
   Depois disso, o aplicativo abre normalmente com um duplo clique.

Não é preciso instalar nada além deste aplicativo. Na primeira execução, a tela
"Primeiros passos" baixa o modelo de busca e o catálogo da CAPES. A tela
"Conectar IA" configura o assistente de IA escolhido com um clique.

O BuscaTeses é um projeto de código aberto (MIT), sem telemetria e sem contas.
TEXT

rm -f "$DMG"
echo "Criando ${DMG}…"
hdiutil create \
  -volname "BuscaTeses" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null
hdiutil verify "$DMG" >/dev/null

echo "DMG criado: $DMG"
