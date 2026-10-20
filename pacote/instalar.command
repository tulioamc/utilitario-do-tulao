#!/bin/bash
clear
echo "=============================================="
echo "   Instalando o Utilitário do Tulão (só na 1ª vez)"
echo "=============================================="
echo
echo "Isso instala: Homebrew, Python, FFmpeg, gifsicle, a janela do app"
echo "(pywebview) e o baixador de vídeos do YouTube/Instagram/TikTok (yt-dlp)."
echo "Pode pedir a senha do seu Mac (ela não aparece enquanto você digita)."
echo "Precisa de internet e pode levar alguns minutos."
echo

carregar_brew() {
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}

falhou() {
  echo
  echo "Algo deu errado. Copie o texto acima e me mande para eu ajudar."
  read -n 1 -s -r -p "Aperte qualquer tecla para fechar..."
  exit 1
}

# 1) Homebrew
carregar_brew || true
if ! command -v brew >/dev/null 2>&1; then
  echo ">> Instalando o Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || falhou
  carregar_brew || falhou
fi

# 2) Programas
echo
echo ">> Instalando Python, FFmpeg e gifsicle..."
brew install python ffmpeg gifsicle || falhou

# 3) Janela do app (pywebview) e bibliotecas de download/fontes
echo
echo ">> Instalando bibliotecas do Python (janela do app, baixador de vídeo"
echo "   e leitura de fontes)..."
PY="$(brew --prefix)/bin/python3"
"$PY" -m pip install --break-system-packages --upgrade \
  pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-Quartz \
  yt-dlp fonttools || falhou

# 4) Conferência
echo
echo ">> Conferindo..."
command -v ffmpeg >/dev/null 2>&1 || falhou
command -v gifsicle >/dev/null 2>&1 || falhou
"$PY" -c "import webview, yt_dlp, fontTools" || falhou
gifsicle --version | head -1

echo
echo "Tudo pronto! Abrindo o programa..."
APP="$(cd "$(dirname "$0")/../.." && pwd)"
sleep 1
open "$APP"
