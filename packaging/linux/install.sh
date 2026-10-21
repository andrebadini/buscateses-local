#!/usr/bin/env bash
# Instala o BuscaTeses no perfil do usuário (sem administrador).
#
# Execute a partir da pasta extraída do .tar.gz:
#   ./install.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SRC_DIR/BuscaTeses"
PREFIX="${BUSCATESES_INSTALL_DIR:-$HOME/.local/opt/buscateses}"
BIN_DIR="$HOME/.local/bin"
APPLICATIONS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

if [[ ! -d "$SOURCE" ]]; then
  echo "Erro: pasta BuscaTeses não encontrada ao lado deste instalador." >&2
  exit 1
fi
for executable in BuscaTeses buscateses; do
  if [[ ! -x "$SOURCE/$executable" ]]; then
    echo "Erro: executável ausente no pacote: $executable" >&2
    exit 1
  fi
done

echo "Instalando em ${PREFIX}…"
mkdir -p "$(dirname "$PREFIX")" "$BIN_DIR" "$APPLICATIONS_DIR" "$ICON_DIR"
TEMP_TARGET="$PREFIX.new.$$"
rm -rf "$TEMP_TARGET"
cp -a "$SOURCE" "$TEMP_TARGET"
chmod +x "$TEMP_TARGET/BuscaTeses" "$TEMP_TARGET/buscateses"
rm -rf "$PREFIX"
mv "$TEMP_TARGET" "$PREFIX"

ln -sf "$PREFIX/buscateses" "$BIN_DIR/buscateses"

if [[ -f "$PREFIX/icon.png" ]]; then
  cp "$PREFIX/icon.png" "$ICON_DIR/buscateses.png"
fi

DESKTOP_FILE="$APPLICATIONS_DIR/buscateses.desktop"
cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=BuscaTeses
Comment=Busca local no Catálogo de Teses e Dissertações da CAPES
Exec="$PREFIX/BuscaTeses"
Icon=buscateses
Terminal=false
Categories=Education;Utility;
StartupWMClass=BuscaTeses
DESKTOP
chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo
echo "BuscaTeses instalado."
echo "  Aplicativo: procure \"BuscaTeses\" no menu de aplicativos"
echo "  Terminal:   $BIN_DIR/buscateses"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo
  echo "Aviso: $BIN_DIR não está no PATH. Adicione esta linha ao seu ~/.profile:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo
echo "Para desinstalar, remova $PREFIX, $BIN_DIR/buscateses e $DESKTOP_FILE."
