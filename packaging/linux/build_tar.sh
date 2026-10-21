#!/usr/bin/env bash
# Build the portable Linux tarball consumed by packaging/linux/install.sh.
#
# Usage: build_tar.sh [BuscaTeses-package-dir] [output-dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SOURCE="${1:-$ROOT/packaging/output/pyinstaller/BuscaTeses}"
OUT_DIR="${2:-$ROOT/packaging/output}"

case "$(uname -m)" in
  x86_64|amd64) ARCH="x86_64" ;;
  aarch64|arm64) ARCH="aarch64" ;;
  *)
    echo "Erro: arquitetura não suportada: $(uname -m)" >&2
    exit 1
    ;;
esac

if [[ ! -d "$SOURCE" ]]; then
  echo "Erro: pasta do PyInstaller não encontrada: $SOURCE" >&2
  exit 1
fi
for executable in BuscaTeses buscateses; do
  if [[ ! -x "$SOURCE/$executable" ]]; then
    echo "Erro: executável ausente no pacote: $executable" >&2
    exit 1
  fi
done

mkdir -p "$OUT_DIR"
ARCHIVE="$OUT_DIR/BuscaTeses-linux-$ARCH.tar.gz"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/buscateses-tar.XXXXXX")"
cleanup() {
  rm -rf "$STAGING"
}
trap cleanup EXIT

echo "Preparando o pacote Linux…"
cp -a "$SOURCE" "$STAGING/BuscaTeses"
cp "$SCRIPT_DIR/install.sh" "$STAGING/install.sh"
chmod +x "$STAGING/install.sh"
if [[ -f "$ROOT/packaging/icons/icon-256.png" ]]; then
  cp "$ROOT/packaging/icons/icon-256.png" "$STAGING/BuscaTeses/icon.png"
fi

rm -f "$ARCHIVE"
tar -C "$STAGING" -czf "$ARCHIVE" BuscaTeses install.sh
echo "Pacote criado: $ARCHIVE"
