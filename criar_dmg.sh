#!/bin/bash
# Cria "Utilitário do Tulão.dmg" a partir de app.py + da pasta web/ (e do
# icone.png). Deixe tudo na mesma pasta.
# Uso:  bash criar_dmg.sh
set -e
cd "$(dirname "$0")"

NOME="Utilitário do Tulão"

if [ ! -f app.py ] || [ ! -d web ]; then
  echo "Erro: coloque o app.py e a pasta web/ na mesma pasta deste script."
  exit 1
fi
if ! command -v hdiutil >/dev/null 2>&1; then
  echo "Erro: este script precisa rodar em um Mac (usa o hdiutil)."
  exit 1
fi

TMP="$(mktemp -d)"
STAGE="$TMP/dmg"
APP="$STAGE/$NOME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp app.py "$APP/Contents/Resources/app.py"
cp -r web "$APP/Contents/Resources/web"

# O verificador de atualização (dentro do programa) chama o updater.py que
# precisa estar ao lado do app.py dentro do .app; sem ele, o botão
# "FECHAR E ATUALIZAR" aparece mas não faz nada.
if [ -f updater.py ]; then
  cp updater.py "$APP/Contents/Resources/updater.py"
else
  echo "Aviso: updater.py não encontrado; a atualização automática pelo GitHub não vai funcionar."
fi

# --------------------------------------------------------------------- ícone
if [ -f icone.png ]; then
  cp icone.png "$APP/Contents/Resources/icone.png"
  ICONSET="$TMP/icone.iconset"
  mkdir -p "$ICONSET"
  ok=1
  for par in "16 16x16" "32 16x16@2x" "32 32x32" "64 32x32@2x" \
             "128 128x128" "256 128x128@2x" "256 256x256" "512 256x256@2x" \
             "512 512x512" "1024 512x512@2x"; do
    set -- $par
    sips -z "$1" "$1" icone.png --out "$ICONSET/icon_$2.png" >/dev/null 2>&1 || ok=0
  done
  if [ "$ok" = 1 ] && iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/icone.icns" 2>/dev/null; then
    echo "Ícone aplicado."
  else
    echo "Aviso: não foi possível gerar o ícone; o app usará o ícone padrão."
  fi
else
  echo "Aviso: icone.png não encontrado; o app usará o ícone padrão."
fi

# ---------------------------------------------------------------- Info.plist
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Utilitário do Tulão</string>
  <key>CFBundleDisplayName</key><string>Utilitário do Tulão</string>
  <key>CFBundleIdentifier</key><string>local.utilitariodotulao</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icone</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# ------------------------------------------------------------------ launcher
# Abre o programa (janela em HTML via pywebview). Se faltar algo (Homebrew,
# ffmpeg, gifsicle, pywebview ou yt-dlp), abre o instalador no Terminal.
cat > "$APP/Contents/MacOS/launcher" <<'LAUNCHER'
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"

PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if [ -x "$c" ]; then PY="$c"; break; fi
done

if [ -z "$PY" ] \
   || ! command -v ffmpeg >/dev/null 2>&1 \
   || ! command -v gifsicle >/dev/null 2>&1 \
   || ! "$PY" -c "import webview, yt_dlp, fontTools" >/dev/null 2>&1; then
  chmod +x "$DIR/instalar.command"
  open -a Terminal "$DIR/instalar.command"
  exit 0
fi

exec "$PY" "$DIR/app.py" "$@" >> "$HOME/Library/Logs/UtilitarioDoTulao.log" 2>&1
LAUNCHER

# ---------------------------------------------------------------- instalador
cat > "$APP/Contents/Resources/instalar.command" <<'INSTALADOR'
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

carregar_brew || true
if ! command -v brew >/dev/null 2>&1; then
  echo ">> Instalando o Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || falhou
  carregar_brew || falhou
fi

echo
echo ">> Instalando Python, FFmpeg e gifsicle..."
brew install python ffmpeg gifsicle || falhou

echo
echo ">> Instalando bibliotecas do Python (janela do app, baixador de vídeo"
echo "   e leitura de fontes)..."
PY="$(brew --prefix)/bin/python3"
"$PY" -m pip install --break-system-packages --upgrade \
  pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-Quartz \
  yt-dlp fonttools || falhou

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
INSTALADOR

chmod +x "$APP/Contents/MacOS/launcher" "$APP/Contents/Resources/instalar.command"

# ------------------------------------------------------------ conteúdo do DMG
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/LEIA-ME.txt" <<'LEIAME'
Utilitário do Tulão

Sete ferramentas em um só app, em abas:
  - GIF:              transforma vídeos (MP4, MOV, MKV, WEBM...) em GIF otimizado
  - Áudio:             converte entre WAV, MP3, OPUS, OGG e OGA
  - YouTube:           baixa vídeos do YouTube em MP4, na resolução escolhida
  - Instagram/TikTok:  baixa vídeos (e fotos de carrossel) em MP4/JPG
  - Pinterest:         baixa as imagens de um board
  - Renomear:          renomeia vários arquivos de uma vez
  - Fontes:            corrige metadados de arquivos de fonte

1. Arraste "Utilitário do Tulão" para a pasta Applications (ao lado).
2. Abra o programa pela pasta Applications (ou pelo Launchpad).
3. Na PRIMEIRA vez, ele abre o Terminal e instala tudo o que precisa
   (Homebrew, Python, FFmpeg, gifsicle, pywebview, yt-dlp). Precisa de
   internet e da senha do Mac.
4. Ao terminar, o programa abre sozinho. Depois é só escolher a aba que
   quiser usar.

Se o macOS disser que o app é de "desenvolvedor não identificado":
clique com o botão direito no app, escolha Abrir e confirme.
Erros ficam registrados em ~/Library/Logs/UtilitarioDoTulao.log
LEIAME

# --------------------------------------------------------------------- DMG
rm -f "$NOME.dmg"
hdiutil create -volname "$NOME" -srcfolder "$STAGE" -ov -format UDZO "$NOME.dmg"
rm -rf "$TMP"

echo
echo "Pronto! Criado: $(pwd)/$NOME.dmg"
open -R "$NOME.dmg" 2>/dev/null || true
