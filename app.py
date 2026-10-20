#!/usr/bin/env python3
"""
Utilitário do Tulão — versão em HTML (pywebview)

Backend Python (lógica de conversão/download, reaproveitada do app original
em Tkinter) + janela nativa do macOS mostrando web/index.html. A interface
toda é HTML/CSS/JS; o Python só executa ffmpeg/gifsicle/yt-dlp/fontTools por
trás e expõe métodos pro JavaScript chamar (via `window.pywebview.api`).
"""
import glob
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import urllib.parse
import importlib
from pathlib import Path

try:
    import yt_dlp
except Exception:
    yt_dlp = None
YTDLP_DISPONIVEL = yt_dlp is not None

try:
    from fontTools.ttLib import TTFont
except Exception:
    TTFont = None

APP_VERSION = "3.4.0"
GITHUB_REPO = "tulioamc/utilitario-do-tulao"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

def _garantir_path_binarios():
    # Um .app aberto pelo Finder/LaunchServices (duplo clique, ou o próprio
    # launcher do bundle) NÃO herda o PATH do shell (.zshrc/.zprofile nunca
    # roda), então ffmpeg/gifsicle instalados via Homebrew podem existir no
    # Mac mas não serem encontrados por este processo — o `shutil.which`
    # abaixo simplesmente não os acha, e features que dependem deles falham
    # (no caso do YouTube, o yt-dlp não consegue juntar vídeo+áudio e acaba
    # só com o arquivo de áudio). Garantimos aqui os locais mais comuns.
    extras = [
        "/opt/homebrew/bin", "/opt/homebrew/sbin",   # Homebrew (Apple Silicon)
        "/usr/local/bin", "/usr/local/sbin",         # Homebrew (Intel) / padrão
        "/opt/local/bin", "/opt/local/sbin",         # MacPorts
        str(Path.home() / ".local/bin"),
    ]
    atuais = (os.environ.get("PATH") or "").split(os.pathsep)
    for caminho in extras:
        if caminho not in atuais and os.path.isdir(caminho):
            atuais.append(caminho)
    os.environ["PATH"] = os.pathsep.join(p for p in atuais if p)


_garantir_path_binarios()

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
GIFSICLE = shutil.which("gifsicle")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CORES_PADRAO = {
    "branco": "#ffffff",
    "vermelho": "#ef4444",
    "roxo": "#7c3aed",
    "cinza_claro": "#f5f5f7",
    "cinza_escuro": "#111111",
}


def _caminho_config_cores():
    pasta = Path.home() / "Library/Application Support/UtilitarioDoTulao"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / "cores.json"


def _ler_config_interface():
    # Arquivo guarda tanto as cores quanto outras preferências de interface
    # (ex.: modo_escuro) — lê tudo bruto pra nunca perder uma chave que
    # outra função não conhece.
    try:
        caminho = _caminho_config_cores()
        if caminho.exists():
            return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception as exc:
        _log(f"Config de interface: falha ao carregar ({exc}).")
    return {}


def _escrever_config_interface(dados):
    _caminho_config_cores().write_text(json.dumps(dados, indent=2), encoding="utf-8")


_COR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ==========================================================================
# Utilidades comuns
# ==========================================================================

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-800:] or "Erro desconhecido")
    return r.stdout


def caminho_livre(caminho):
    caminho = Path(caminho)
    if not caminho.exists():
        return caminho
    n = 2
    while True:
        candidato = caminho.with_name(f"{caminho.stem}({n:02d}){caminho.suffix}")
        if not candidato.exists():
            return candidato
        n += 1


def formatar_tamanho(nbytes):
    if not nbytes:
        return "—"
    if nbytes >= 1024 * 1024:
        return f"{nbytes / 1024 / 1024:.1f} MB"
    return f"{nbytes / 1024:.0f} KB"


def duracao_midia(src):
    if not FFPROBE:
        return None
    try:
        out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=nw=1:nk=1", str(src)])
        return float(out.strip())
    except Exception:
        return None


def _log(mensagem):
    try:
        log = Path.home() / "Library/Logs/UtilitarioDoTulao.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mensagem}\n")
    except Exception:
        pass


# ==========================================================================
# GIF: conversão de vídeo
# ==========================================================================

EXTENSOES_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}

DITHERS = {
    "Bayer (leve, recomendado)": "bayer:bayer_scale=5",
    "Sem dithering (mais leve)": "none",
    "Floyd-Steinberg (pesado)": "floyd_steinberg",
    "Sierra (qualidade máxima)": "sierra2_4a",
}


def detectar_gifsicle():
    if not GIFSICLE:
        return None, False
    try:
        m = re.search(r"(\d+)\.(\d+)", run([GIFSICLE, "--version"]))
        versao = (int(m.group(1)), int(m.group(2))) if m else None
        return versao, "--lossy" in run([GIFSICLE, "--help"])
    except Exception:
        return None, False


GIFSICLE_VERSAO, GIFSICLE_LOSSY = detectar_gifsicle()
GAMMA_LOSSY = ["--gamma=1"] if GIFSICLE_VERSAO and GIFSICLE_VERSAO >= (1, 96) else []


def dimensoes_video(src):
    if not FFPROBE:
        return None
    try:
        out = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                   "-show_entries",
                   "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
                   "-of", "json", str(src)])
        info = json.loads(out)["streams"][0]
        w, h = int(info["width"]), int(info["height"])
        rot = None
        for lado in info.get("side_data_list") or []:
            if "rotation" in lado:
                rot = lado["rotation"]
        if rot is None:
            rot = (info.get("tags") or {}).get("rotate")
        if rot is not None and abs(int(float(rot))) % 180 == 90:
            w, h = h, w
        return w, h
    except Exception:
        return None


def gerar_gif(src, saida, fps, otimizacao, dither, largura, cores,
              inicio=None, duracao=None, altura=None):
    entrada = []
    if inicio is not None:
        entrada += ["-ss", f"{inicio:.3f}"]
    if duracao is not None:
        entrada += ["-t", f"{duracao:.3f}"]
    entrada += ["-i", str(src)]

    filtros = f"fps={fps}"
    if largura and altura:
        filtros += f",scale={largura}:{altura}:flags=lanczos"
    elif largura:
        filtros += f",scale='min({largura},iw)':-1:flags=lanczos"
    elif altura:
        filtros += f",scale=-1:{altura}:flags=lanczos"

    with tempfile.TemporaryDirectory() as tmp:
        paleta = os.path.join(tmp, "paleta.png")
        bruto = os.path.join(tmp, "bruto.gif")

        run([FFMPEG, "-y", *entrada, "-vf",
             f"{filtros},palettegen=max_colors={cores}:stats_mode=diff", paleta])
        run([FFMPEG, "-y", *entrada, "-i", paleta, "-lavfi",
             f"{filtros}[x];[x][1:v]paletteuse=dither={dither}:diff_mode=rectangle",
             "-loop", "0", bruto])

        if GIFSICLE:
            cmd = [GIFSICLE, "-O3"]
            lossy = round(otimizacao * 2)
            if lossy > 0 and GIFSICLE_LOSSY:
                cmd += [f"--lossy={lossy}", *GAMMA_LOSSY]
            cmd += [bruto, "-o", str(saida)]
            run(cmd)
        else:
            shutil.copy(bruto, str(saida))


# ==========================================================================
# Áudio
# ==========================================================================

EXTENSOES_AUDIO = {".wav", ".mp3", ".opus", ".ogg", ".oga"}
TAXAS_AMOSTRAGEM = {"Original": None, "22050 Hz": 22050, "32000 Hz": 32000,
                    "44100 Hz": 44100, "48000 Hz": 48000}
FORMATOS_AUDIO = {
    "MP3": (".mp3", "libmp3lame"),
    "WAV (sem perdas)": (".wav", "pcm_s16le"),
    "OPUS": (".opus", "libopus"),
    "OGG (Vorbis)": (".ogg", "libvorbis"),
    "OGA (Vorbis)": (".oga", "libvorbis"),
}
FORMATOS_SEM_BITRATE = {"WAV (sem perdas)"}


def detectar_codecs_audio():
    if not FFMPEG:
        return set()
    try:
        saida = run([FFMPEG, "-hide_banner", "-encoders"])
    except Exception:
        return set()
    return {nome for nome in ("libmp3lame", "libopus", "libvorbis", "pcm_s16le")
            if nome in saida}


CODECS_AUDIO_DISPONIVEIS = detectar_codecs_audio()


def formatos_disponiveis():
    return {label: (ext, codec) for label, (ext, codec) in FORMATOS_AUDIO.items()
            if codec in CODECS_AUDIO_DISPONIVEIS}


def info_audio(src):
    if not FFPROBE:
        return {}
    try:
        out = run([FFPROBE, "-v", "error", "-select_streams", "a:0",
                   "-show_entries",
                   "format=duration,bit_rate:stream=sample_rate,channels",
                   "-of", "json", str(src)])
        d = json.loads(out)
        fmt = d.get("format") or {}
        s0 = (d.get("streams") or [{}])[0]
        return dict(
            duracao=float(fmt.get("duration") or 0),
            bitrate=int(fmt.get("bit_rate") or 0),
            taxa=int(s0.get("sample_rate") or 0),
            canais=int(s0.get("channels") or 0),
        )
    except Exception:
        return {}


def estimar_tamanho_audio(label, bitrate_kbps, taxa, duracao, canais):
    if not duracao:
        return None
    if label in FORMATOS_SEM_BITRATE:
        sr = taxa or 44100
        ch = canais or 2
        return int(sr * 2 * ch * duracao) + 44
    overhead = 1.02 if "Vorbis" in label else 1.01
    return bitrate_kbps * 1000 / 8 * duracao * overhead


OPUS_TAXAS_VALIDAS = (8000, 12000, 16000, 24000, 48000)


def gerar_audio(src, saida, label, bitrate_kbps=None, taxa=None):
    _, codec = FORMATOS_AUDIO[label]
    if codec == "libopus" and taxa and taxa not in OPUS_TAXAS_VALIDAS:
        taxa = min(OPUS_TAXAS_VALIDAS, key=lambda x: abs(x - taxa))
    cmd = [FFMPEG, "-y", "-i", str(src), "-vn", "-map_metadata", "0", "-c:a", codec]
    if label == "MP3":
        cmd += ["-id3v2_version", "3"]
    if label not in FORMATOS_SEM_BITRATE and bitrate_kbps:
        cmd += ["-b:a", f"{bitrate_kbps}k"]
    if taxa:
        cmd += ["-ar", str(taxa)]
    cmd += [str(saida)]
    run(cmd)


# ==========================================================================
# YouTube
# ==========================================================================

def sanitizar_nome_arquivo(nome):
    nome = re.sub(r'[\\/:*?"<>|]', "-", nome or "video")
    nome = re.sub(r"\s+", " ", nome).strip()
    return nome[:150] or "video"


def formatar_duracao(segundos):
    if not segundos:
        return "—"
    segundos = int(segundos)
    h, r = divmod(segundos, 3600)
    m, s_ = divmod(r, 60)
    return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"


def extrair_info_youtube(url):
    opts = dict(quiet=True, no_warnings=True, noplaylist=True, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("Não foi possível obter informações do vídeo.")
    if "entries" in info:
        info = next((e for e in info["entries"] if e), None)
        if info is None:
            raise RuntimeError("Não encontrei nenhum vídeo nesse link.")

    alturas = {}
    for f in info.get("formats") or []:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        tam = f.get("filesize") or f.get("filesize_approx")
        if h not in alturas or (tam and not alturas.get(h)):
            alturas[h] = tam

    tam_audio = None
    for f in info.get("formats") or []:
        if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none"):
            tam = f.get("filesize") or f.get("filesize_approx")
            if tam and (tam_audio is None or tam > tam_audio):
                tam_audio = tam

    resolucoes = []
    for h in sorted(alturas, reverse=True):
        tam = alturas[h]
        total = (tam + tam_audio) if (tam and tam_audio) else None
        resolucoes.append({"altura": h, "tamanho": total, "tamanho_fmt": formatar_tamanho(total)})

    return dict(
        titulo=info.get("title") or "video",
        duracao=info.get("duration"),
        duracao_fmt=formatar_duracao(info.get("duration")),
        canal=info.get("uploader") or info.get("channel"),
        thumbnail=info.get("thumbnail"),
        resolucoes=resolucoes,
    )


def baixar_youtube(url, destino, altura, hook):
    # Prioriza o codec de vídeo avc1 (H.264): é o que toca sem problema em
    # qualquer player (QuickTime incluso). Sem essa preferência, o yt-dlp às
    # vezes escolhe AV1/VP9 — o arquivo final tem vídeo de verdade, mas
    # alguns players não decodificam esses codecs e mostram só o áudio
    # tocando com a tela preta/sem imagem, dando a impressão de que "baixou
    # sem vídeo".
    if altura:
        fmt = (f"bestvideo[height<={altura}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
               f"bestvideo[height<={altura}][ext=mp4]+bestaudio[ext=m4a]/"
               f"bestvideo[height<={altura}]+bestaudio/best[height<={altura}]")
    else:
        fmt = ("bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
               "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
               "bestvideo+bestaudio/best")
    opts = dict(quiet=True, no_warnings=True, noplaylist=True, format=fmt,
                merge_output_format="mp4", outtmpl=str(destino),
                progress_hooks=[hook])
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def atualizar_ytdlp():
    run([sys.executable, "-m", "pip", "install", "--break-system-packages",
         "--upgrade", "yt-dlp"])
    global yt_dlp, YTDLP_DISPONIVEL
    try:
        if yt_dlp is None:
            yt_dlp = importlib.import_module("yt_dlp")
        else:
            yt_dlp = importlib.reload(yt_dlp)
        YTDLP_DISPONIVEL = True
    except Exception:
        pass


# ==========================================================================
# Instagram / TikTok
# ==========================================================================

def extrair_info_social(url):
    opts = dict(quiet=True, no_warnings=True, noplaylist=False, skip_download=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("Não foi possível obter informações desse link.")

    brutos = info.get("entries") or [info]
    itens_info = [i for i in brutos if i]
    if not itens_info:
        raise RuntimeError("Não encontrei nenhum vídeo ou foto nesse link.")

    itens = []
    for it in itens_info:
        eh_video = it.get("vcodec") not in (None, "none")
        ext = it.get("ext") or ("mp4" if eh_video else "jpg")
        itens.append(dict(url=it.get("webpage_url") or it.get("url") or url, ext=ext,
                          tamanho=it.get("filesize") or it.get("filesize_approx"),
                          video=eh_video))

    principal = itens_info[0]
    chave = (principal.get("extractor_key") or principal.get("extractor") or "").lower()
    if "instagram" in chave:
        plataforma = "Instagram"
    elif "tiktok" in chave:
        plataforma = "TikTok"
    else:
        plataforma = principal.get("extractor_key") or "desconhecida"

    return dict(
        titulo=principal.get("title") or principal.get("description") or "video",
        autor=principal.get("uploader") or principal.get("channel")
              or principal.get("uploader_id"),
        duracao_fmt=formatar_duracao(principal.get("duration")),
        plataforma=plataforma,
        thumbnail=principal.get("thumbnail"),
        itens=itens,
    )


def baixar_social_item(url_item, destino, hook):
    fmt = ("best[vcodec^=avc1]/"
           "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
           "best")
    opts = dict(quiet=True, no_warnings=True, noplaylist=True, format=fmt,
                merge_output_format="mp4", outtmpl=str(destino), progress_hooks=[hook])
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url_item])


# ==========================================================================
# Pinterest
# ==========================================================================

PINTEREST_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0.0.0 Safari/537.36")

_PINIMG_RE = re.compile(
    r'https://i\.pinimg\.com/(?:\d+x\d*|originals)/[0-9a-zA-Z/_-]+\.(?:jpg|jpeg|png|gif)',
    re.I)


def _pinterest_para_original(url):
    return re.sub(r'/\d+x\d*/', '/originals/', url, count=1)


def _pinterest_buscar_html(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": PINTEREST_USER_AGENT,
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _pinterest_estado_inicial(html):
    # O Pinterest embute o estado inicial do Redux (board, pins da 1ª página
    # e o "bookmark"/cursor para buscar as próximas) num <script> JSON.
    m = re.search(
        r'<script[^>]*id="__PWS_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _pinterest_recursos(estado):
    try:
        return estado["props"]["initialReduxState"]["resources"]
    except Exception:
        return {}


def _pinterest_info_board(estado):
    recursos = _pinterest_recursos(estado)
    for entrada in (recursos.get("BoardResource") or {}).values():
        dados = (entrada or {}).get("data") or {}
        if dados.get("id"):
            return {"board_id": dados["id"], "board_url": dados.get("url") or ""}
    return None


def _pinterest_bookmark_inicial(estado):
    recursos = _pinterest_recursos(estado)
    bookmark = None
    for entrada in (recursos.get("BoardFeedResource") or {}).values():
        b = (entrada or {}).get("bookmark")
        if b:
            bookmark = b
    return bookmark


def _pinterest_melhor_imagem(pin):
    imagens = (pin or {}).get("images") or {}
    for chave in ("orig", "736x", "564x", "474x", "236x", "170x", "136x136"):
        info = imagens.get(chave)
        if info and info.get("url"):
            return info["url"]
    return None


def _pinterest_proxima_pagina(url_board, board_url, board_id, bookmark):
    caminho = urllib.parse.urlparse(url_board).path or ("/" + board_url.strip("/") + "/")
    if not caminho.endswith("/"):
        caminho += "/"
    opcoes = {
        "board_id": board_id,
        "board_url": board_url or caminho,
        "currentFilter": -1,
        "field_set_key": "react_grid_pin",
        "filter_section_id": None,
        "sort": "default",
        "layout": "default",
        "page_size": 25,
        "redux_normalize_feed": True,
        "bookmarks": [bookmark],
    }
    params = urllib.parse.urlencode({
        "source_url": caminho,
        "data": json.dumps({"options": opcoes, "context": {}}),
    })
    url_api = f"https://www.pinterest.com/resource/BoardFeedResource/get/?{params}"
    req = urllib.request.Request(url_api, headers={
        "User-Agent": PINTEREST_USER_AGENT,
        "Accept": "application/json, text/javascript, */*, q=0.01",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Referer": url_board,
        "X-Pinterest-AppState": "active",
        "X-Requested-With": "XMLHttpRequest",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def extrair_pins_pinterest(url_board):
    html = _pinterest_buscar_html(url_board)
    titulo = "board"
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        titulo = re.split(r"\s*[|–-]\s*Pinterest", m.group(1))[0].strip() or "board"

    vistos, imagens = set(), []

    def registrar(bruto):
        orig = _pinterest_para_original(bruto)
        chave = orig.rsplit("/", 1)[-1]
        if chave not in vistos:
            vistos.add(chave)
            imagens.append(orig)
            return True
        return False

    for bruto in _PINIMG_RE.findall(html):
        registrar(bruto)

    # A página inicial só traz a 1ª "leva" de pins (o resto carrega via
    # scroll infinito/JS). Para pegar o board completo, seguimos paginando
    # pelo endpoint interno do Pinterest usando o "bookmark" (cursor) salvo
    # no estado inicial da página, igual o próprio site faz ao rolar.
    try:
        estado = _pinterest_estado_inicial(html)
        board = _pinterest_info_board(estado) if estado else None
        bookmark = _pinterest_bookmark_inicial(estado) if estado else None

        tentativas = 0
        while board and bookmark and bookmark != "-end-" and tentativas < 80:
            tentativas += 1
            resp = _pinterest_proxima_pagina(
                url_board, board["board_url"], board["board_id"], bookmark)
            resource_resp = resp.get("resource_response") or {}
            pins = resource_resp.get("data") or []
            if not pins:
                break
            for pin in pins:
                url_img = _pinterest_melhor_imagem(pin)
                if url_img:
                    registrar(url_img)
            bookmark = resource_resp.get("bookmark")
    except Exception as exc:
        # Paginação é "best effort": se o Pinterest mudar algo e isso
        # falhar, ainda devolvemos as imagens já encontradas na 1ª página.
        _log(f"Pinterest: paginação extra falhou ({exc}). Usando só a 1ª página.")

    if not imagens:
        raise RuntimeError(
            "Não encontrei nenhuma imagem nessa página. O board pode ser privado, "
            "o link pode estar errado, ou o Pinterest mudou a página (me avise).")

    return sanitizar_nome_arquivo(titulo), imagens


def baixar_arquivo_url(url, destino):
    req = urllib.request.Request(url, headers={"User-Agent": PINTEREST_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r, open(destino, "wb") as f:
        shutil.copyfileobj(r, f)


# ==========================================================================
# Renomear
# ==========================================================================

TIPOS_BLOCO_NOME = ["Texto", "Separador", "Número", "Letra"]


def _letra(index):
    n = index
    out = ""
    while True:
        out = chr(ord("A") + n % 26) + out
        n = n // 26 - 1
        if n < 0:
            break
    return out


def _ordenar(files, criterio):
    if criterio == "Mais novos primeiro":
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    if criterio == "Mais antigos primeiro":
        return sorted(files, key=lambda p: p.stat().st_mtime)
    if criterio == "Ordem de seleção":
        return files
    return sorted(files, key=lambda p: p.name.lower())


def _criterio_atual(sub_aba, blocks):
    if sub_aba == "montar":
        for b in blocks:
            if b.get("tipo") in {"Número", "Letra"}:
                return b.get("ordem", "Ordem Alfabética")
    return "Ordem Alfabética"


def nome_novo(p, index, sub_aba, blocks, retirar, substituir, manter_ext):
    if sub_aba == "montar":
        acumulado = ""
        for b in blocks:
            t = b["tipo"]
            if t == "Nome atual":
                acumulado += p.stem
            elif t == "Texto":
                acumulado += b.get("valor", "")
            elif t == "Separador":
                acumulado += b.get("valor", "_")
            elif t == "Número":
                acumulado += str(int(b.get("inicio", 1)) + index).zfill(int(b.get("digitos", 3)))
            elif t == "Letra":
                acumulado += _letra(index)
    elif sub_aba == "retirar":
        acumulado = p.stem
        buscar = retirar.get("buscar", "")
        if buscar:
            if retirar.get("case", False):
                acumulado = acumulado.replace(buscar, "")
            else:
                acumulado = re.sub(re.escape(buscar), "", acumulado, flags=re.IGNORECASE)
    else:
        acumulado = p.stem
        buscar = substituir.get("buscar", "")
        trocar = substituir.get("trocar", "")
        if buscar:
            if substituir.get("case", False):
                acumulado = acumulado.replace(buscar, trocar)
            else:
                acumulado = re.sub(re.escape(buscar), lambda m: trocar, acumulado, flags=re.IGNORECASE)
    return acumulado + (p.suffix if manter_ext else "")


# ==========================================================================
# Fontes
# ==========================================================================

def sugestao_peso(nome):
    n = Path(nome).stem.lower()
    regras = [
        (r"extra.?black|ultra.?black|heavy", "800"),
        (r"black", "900"),
        (r"extra.?bold|ultra.?bold", "800"),
        (r"semi.?bold|demi.?bold", "600"),
        (r"bold", "700"),
        (r"medium", "500"),
        (r"semi.?light|demi.?light", "350"),
        (r"extra.?light|ultra.?light", "200"),
        (r"light", "300"),
        (r"thin|hairline", "100"),
    ]
    for padrao, peso in regras:
        if re.search(padrao, n):
            return peso
    return "400"


def sugestao_subfamilia(nome):
    n = Path(nome).stem.lower()
    if "italic" in n or "oblique" in n:
        return "Italic"
    return "Regular"


def corrigir_fonte(caminho, pasta_saida, familia, subfamilia, peso):
    if TTFont is None:
        raise RuntimeError("fontTools não está instalado.")
    caminho = Path(caminho)
    ext = caminho.suffix.lower()
    if ext not in {".ttf", ".otf", ".ttc"}:
        raise RuntimeError(f"Formato não suportado: {ext}")
    if ext == ".ttc":
        raise RuntimeError("Coleções TTC ainda não são alteradas por segurança.")
    font = TTFont(str(caminho), recalcBBoxes=False, recalcTimestamp=False)
    try:
        if "name" not in font:
            raise RuntimeError("A fonte não possui tabela de nomes.")
        ps = re.sub(r"[^A-Za-z0-9_.-]", "", familia.replace(" ", "-"))
        if not ps:
            ps = "Font"
        ps_style = re.sub(r"[^A-Za-z0-9_-]", "", subfamilia.replace(" ", "-"))
        ps_name = ps if subfamilia.lower() in {"regular", "normal", "book"} else f"{ps}-{ps_style}"
        nomes = {1: familia, 2: subfamilia, 4: f"{familia} {subfamilia}".strip(),
                 6: ps_name, 16: familia, 17: subfamilia}
        for rec in font["name"].names:
            if rec.nameID in nomes:
                texto = nomes[rec.nameID]
                try:
                    rec.string = texto.encode(rec.getEncoding())
                except Exception:
                    try:
                        rec.string = texto.encode("utf-16-be")
                    except Exception:
                        pass
        italic = subfamilia.lower() in {"italic", "oblique"}
        bold = int(peso) >= 700
        if "OS/2" in font:
            try:
                font["OS/2"].usWeightClass = int(peso)
                fs = getattr(font["OS/2"], "fsSelection", 0)
                fs = fs | 0x01 if italic else fs & ~0x01
                fs = fs | 0x20 if bold else fs & ~0x20
                font["OS/2"].fsSelection = fs
            except Exception:
                pass
        if "head" in font:
            font["head"].macStyle = (1 if bold else 0) | (2 if italic else 0)
        pasta_saida.mkdir(parents=True, exist_ok=True)
        destino = pasta_saida / f"{caminho.stem} - {familia}{caminho.suffix}"
        destino = caminho_livre(destino)
        font.save(str(destino))
        return destino
    finally:
        font.close()


# ==========================================================================
# Atualização automática (GitHub Releases)
# ==========================================================================

def versao_tuple(valor):
    nums = re.findall(r"\d+", str(valor or ""))
    return tuple(int(x) for x in nums[:3]) or (0, 0, 0)


def obter_release_github():
    req = urllib.request.Request(GITHUB_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Utilitario-do-Tulao-Updater"
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def caminho_app_atual():
    try:
        p = Path(__file__).resolve()
        for parent in p.parents:
            if parent.suffix == ".app":
                return parent
    except Exception:
        pass
    return None


def encontrar_asset_app(release):
    for asset in release.get("assets", []):
        nome = asset.get("name", "")
        if nome.lower().endswith(".app.zip"):
            return asset
    return None


def verificar_atualizacao_app():
    try:
        release = obter_release_github()
        tag = release.get("tag_name", "")
        nova = tag.lstrip("vV")
        if versao_tuple(nova) <= versao_tuple(APP_VERSION):
            return None
        asset = encontrar_asset_app(release)
        if not asset:
            return None
        return {"version": nova, "tag": tag, "asset_name": asset.get("name"),
                "download_url": asset.get("browser_download_url"),
                "html_url": release.get("html_url")}
    except Exception:
        return None


def iniciar_updater(app_info):
    app_path = caminho_app_atual()
    if not app_path:
        _log("Falha ao iniciar: app não está rodando de dentro de um .app.")
        return False
    updater = Path(__file__).with_name("updater.py")
    if not updater.exists():
        _log(f"Falha ao iniciar: updater.py não encontrado em {updater}.")
        return False
    try:
        args = [sys.executable, str(updater), "--app", str(app_path),
                "--version", str(app_info["version"]), "--url", str(app_info["download_url"]),
                "--repo", GITHUB_REPO]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except Exception as e:
        _log(f"Falha ao iniciar subprocess do updater: {e!r}")
        return False


# ==========================================================================
# API exposta ao JavaScript
# ==========================================================================

def _ler_arquivos_iniciais():
    """Caminhos de arquivo passados por linha de comando (Ação Rápida do
    Finder 'Renomear com Utilitário' chama o app com os arquivos selecionados
    como argumentos). Ignora o que não for um arquivo existente."""
    out = []
    for arg in sys.argv[1:]:
        try:
            p = Path(arg)
            if p.is_file():
                out.append(str(p.resolve()))
        except Exception:
            pass
    return out


ARQUIVOS_INICIAIS = _ler_arquivos_iniciais()


class Api:
    def __init__(self):
        self._window = None
        self._cancelar = set()
        self._arquivos_iniciais = list(ARQUIVOS_INICIAIS)

    def set_window(self, window):
        self._window = window

    # -- Cores da interface ---------------------------------------------------

    def obter_cores(self):
        cores = dict(CORES_PADRAO)
        try:
            salvo = _ler_config_interface()
            for chave in CORES_PADRAO:
                valor = salvo.get(chave)
                if isinstance(valor, str) and _COR_RE.match(valor):
                    cores[chave] = valor
        except Exception as exc:
            _log(f"Cores: falha ao carregar ({exc}).")
        return cores

    def salvar_cores(self, cores):
        try:
            salvo = _ler_config_interface()
            for chave in CORES_PADRAO:
                valor = (cores or {}).get(chave)
                if isinstance(valor, str) and _COR_RE.match(valor):
                    salvo[chave] = valor
            _escrever_config_interface(salvo)
            return {"ok": True, "cores": self.obter_cores()}
        except Exception as e:
            return self._erro(e)

    def restaurar_cores_padrao(self):
        try:
            salvo = _ler_config_interface()
            for chave in CORES_PADRAO:
                salvo.pop(chave, None)
            _escrever_config_interface(salvo)
            return {"ok": True, "cores": dict(CORES_PADRAO)}
        except Exception as e:
            return self._erro(e)

    def obter_modo_escuro(self):
        try:
            return {"modo_escuro": bool(_ler_config_interface().get("modo_escuro", False))}
        except Exception as e:
            return self._erro(e)

    def definir_modo_escuro(self, ligado):
        try:
            salvo = _ler_config_interface()
            salvo["modo_escuro"] = bool(ligado)
            _escrever_config_interface(salvo)
            return {"ok": True, "modo_escuro": salvo["modo_escuro"]}
        except Exception as e:
            return self._erro(e)

    # -- helpers internos ---------------------------------------------------

    def _emit(self, evento, dados):
        """Manda um evento assíncrono pro JS (progresso, logs etc.)."""
        if not self._window:
            return
        try:
            payload = json.dumps(dados)
            self._window.evaluate_js(f"window.tulao && window.tulao.emit({json.dumps(evento)}, {payload})")
        except Exception:
            pass

    def _erro(self, e):
        _log(f"Erro: {e}\n{traceback.format_exc()}")
        return {"erro": str(e)}

    # -- diálogos nativos -----------------------------------------------

    def escolher_arquivo(self, tipos=None, multiplo=False):
        import webview
        tipos = tuple(tipos) if tipos else ()
        resultado = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=multiplo, file_types=tipos)
        if not resultado:
            return None if not multiplo else []
        return list(resultado) if multiplo else resultado[0]

    def escolher_pasta(self):
        import webview
        resultado = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return resultado[0] if resultado else None

    def abrir_pasta_no_finder(self, caminho):
        try:
            subprocess.run(["open", "-R", caminho])
        except Exception:
            pass

    # -- GIF --------------------------------------------------------------

    def gif_info(self, caminho):
        try:
            dim = dimensoes_video(caminho)
            dur = duracao_midia(caminho)
            tam = Path(caminho).stat().st_size
            return {"largura": dim[0] if dim else None, "altura": dim[1] if dim else None,
                    "duracao": dur, "duracao_fmt": formatar_duracao(dur),
                    "tamanho_fmt": formatar_tamanho(tam)}
        except Exception as e:
            return self._erro(e)

    def gif_converter(self, caminho, opcoes):
        try:
            src = Path(caminho)
            pasta = Path(opcoes.get("pasta") or src.parent)
            dither = DITHERS.get(opcoes.get("dither"), "bayer:bayer_scale=5")
            saida = caminho_livre(pasta / f"{src.stem}_{opcoes['fps']}fps_optimized.gif")
            gerar_gif(src, saida, fps=int(opcoes["fps"]), otimizacao=float(opcoes["otimizacao"]),
                      dither=dither, largura=opcoes.get("largura"), cores=int(opcoes.get("cores", 256)),
                      altura=opcoes.get("altura"))
            return {"ok": True, "caminho": str(saida), "tamanho_fmt": formatar_tamanho(saida.stat().st_size)}
        except Exception as e:
            return self._erro(e)

    # -- Áudio --------------------------------------------------------------

    def audio_formatos(self):
        return list(formatos_disponiveis().keys())

    def audio_info(self, caminho):
        try:
            info = info_audio(caminho)
            return {**info, "duracao_fmt": formatar_duracao(info.get("duracao")),
                    "tamanho_fmt": formatar_tamanho(Path(caminho).stat().st_size)}
        except Exception as e:
            return self._erro(e)

    def audio_converter(self, caminho, opcoes):
        try:
            src = Path(caminho)
            label = opcoes["formato"]
            ext, _ = FORMATOS_AUDIO[label]
            pasta = Path(opcoes.get("pasta") or src.parent)
            saida = caminho_livre(pasta / f"{src.stem}{ext}")
            taxa = TAXAS_AMOSTRAGEM.get(opcoes.get("taxa"), None)
            gerar_audio(src, saida, label, bitrate_kbps=opcoes.get("bitrate"), taxa=taxa)
            return {"ok": True, "caminho": str(saida), "tamanho_fmt": formatar_tamanho(saida.stat().st_size)}
        except Exception as e:
            return self._erro(e)

    # -- YouTube --------------------------------------------------------------

    def youtube_disponivel(self):
        return YTDLP_DISPONIVEL

    def youtube_info(self, url):
        try:
            if not YTDLP_DISPONIVEL:
                raise RuntimeError("yt-dlp não está instalado.")
            return extrair_info_youtube(url)
        except Exception as e:
            return self._erro(e)

    def youtube_baixar(self, url, pasta, altura, titulo):
        try:
            nome = sanitizar_nome_arquivo(titulo)
            sufixo = f"_{altura}p" if altura else ""
            destino = caminho_livre(Path(pasta) / f"{nome}{sufixo}.mp4")

            def hook(d):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    feito = d.get("downloaded_bytes") or 0
                    pct = (feito / total * 100) if total else 0
                    self._emit("youtube_progresso", {"pct": pct})
                elif d.get("status") == "finished":
                    self._emit("youtube_progresso", {"pct": 100})

            baixar_youtube(url, destino, altura, hook)
            return {"ok": True, "caminho": str(destino)}
        except Exception as e:
            return self._erro(e)

    def youtube_atualizar_ferramenta(self):
        try:
            atualizar_ytdlp()
            return {"ok": True}
        except Exception as e:
            return self._erro(e)

    # -- Instagram/TikTok --------------------------------------------------

    def social_info(self, url):
        try:
            if not YTDLP_DISPONIVEL:
                raise RuntimeError("yt-dlp não está instalado.")
            return extrair_info_social(url)
        except Exception as e:
            return self._erro(e)

    def social_baixar(self, itens, pasta, titulo):
        try:
            nome_base = sanitizar_nome_arquivo(titulo)
            resultados = []
            total = len(itens)
            for i, item in enumerate(itens):
                sufixo = f"_{i+1:02d}" if total > 1 else ""
                destino = caminho_livre(Path(pasta) / f"{nome_base}{sufixo}.{item['ext']}")

                def hook(d, idx=i):
                    if d.get("status") == "downloading":
                        total_b = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        feito = d.get("downloaded_bytes") or 0
                        pct = (feito / total_b * 100) if total_b else 0
                        self._emit("social_progresso", {"item": idx, "total": total, "pct": pct})

                baixar_social_item(item["url"], destino, hook)
                resultados.append(str(destino))
                self._emit("social_progresso", {"item": i, "total": total, "pct": 100, "concluido": True})
            return {"ok": True, "arquivos": resultados}
        except Exception as e:
            return self._erro(e)

    # -- Pinterest --------------------------------------------------------

    def pinterest_buscar(self, url):
        try:
            titulo, imagens = extrair_pins_pinterest(url)
            return {"titulo": titulo, "imagens": imagens}
        except Exception as e:
            return self._erro(e)

    def pinterest_baixar_todas(self, titulo, imagens, pasta_base):
        try:
            pasta = Path(pasta_base) / titulo
            pasta.mkdir(parents=True, exist_ok=True)
            total = len(imagens)
            ok = 0
            for i, url in enumerate(imagens):
                try:
                    ext = Path(url).suffix or ".jpg"
                    destino = caminho_livre(pasta / f"{titulo}_{i+1:03d}{ext}")
                    baixar_arquivo_url(url, destino)
                    ok += 1
                except Exception:
                    pass
                self._emit("pinterest_progresso", {"feito": i + 1, "total": total})
            return {"ok": True, "baixadas": ok, "total": total, "pasta": str(pasta)}
        except Exception as e:
            return self._erro(e)

    # -- Renomear --------------------------------------------------------

    def arquivos_iniciais(self):
        """Arquivos recebidos por linha de comando (ex: Ação Rápida do Finder
        'Renomear com Utilitário'). Devolvidos uma única vez para a janela
        já abrir na aba Renomear com eles carregados."""
        arquivos = self._arquivos_iniciais
        self._arquivos_iniciais = []
        return arquivos

    def renomear_listar(self, pasta):
        try:
            arquivos = sorted(
                [p for p in Path(pasta).iterdir() if p.is_file() and not p.name.startswith(".")],
                key=lambda p: p.name.lower())
            return [{"caminho": str(p), "nome": p.name} for p in arquivos]
        except Exception as e:
            return self._erro(e)

    def renomear_preview(self, estado):
        try:
            arquivos = [Path(c) for c in estado["arquivos"]]
            criterio = _criterio_atual(estado["sub_aba"], estado.get("blocks") or [])
            ordenado = _ordenar(arquivos, criterio)
            out = []
            for i, p in enumerate(ordenado):
                novo = nome_novo(p, i, estado["sub_aba"], estado.get("blocks") or [],
                                 estado.get("retirar") or {}, estado.get("substituir") or {},
                                 estado.get("manter_ext", True))
                out.append({"original": str(p), "novo": novo})
            return out
        except Exception as e:
            return self._erro(e)

    def renomear_aplicar(self, estado):
        try:
            arquivos = [Path(c) for c in estado["arquivos"]]
            if not arquivos:
                raise RuntimeError("Nenhum arquivo selecionado.")
            criterio = _criterio_atual(estado["sub_aba"], estado.get("blocks") or [])
            ordenado = _ordenar(arquivos, criterio)
            pares = [(p, p.with_name(nome_novo(p, i, estado["sub_aba"], estado.get("blocks") or [],
                                               estado.get("retirar") or {}, estado.get("substituir") or {},
                                               estado.get("manter_ext", True))))
                     for i, p in enumerate(ordenado)]
            if all(a == b for a, b in pares):
                return {"ok": True, "renomeados": 0}
            temporarios = []
            for i, (orig, dest) in enumerate(pares):
                if orig == dest:
                    continue
                if dest.exists() and dest not in [x[0] for x in pares]:
                    raise RuntimeError(f"O destino já existe: {dest.name}")
                tmp = orig.with_name(f".__tulao_tmp_{i}_{orig.name}")
                orig.rename(tmp)
                temporarios.append((tmp, dest))
            for tmp, dest in temporarios:
                tmp.rename(dest)
            return {"ok": True, "renomeados": len(temporarios)}
        except Exception as e:
            return self._erro(e)

    # -- Fontes --------------------------------------------------------

    def fontes_info(self, caminho):
        return {"peso": sugestao_peso(caminho), "subfamilia": sugestao_subfamilia(caminho),
                "familia": Path(caminho).stem}

    def fontes_corrigir(self, caminho, pasta_saida, familia, subfamilia, peso):
        try:
            destino = corrigir_fonte(caminho, Path(pasta_saida), familia, subfamilia, peso)
            return {"ok": True, "caminho": str(destino)}
        except Exception as e:
            return self._erro(e)

    # -- Atualização --------------------------------------------------------

    def versao_atual(self):
        return APP_VERSION

    def checar_atualizacao(self):
        return verificar_atualizacao_app()

    def aplicar_atualizacao(self, info):
        ok = iniciar_updater(info)
        if ok and self._window:
            threading.Timer(0.3, lambda: self._window.destroy()).start()
        return {"ok": ok}


def main():
    import webview
    api = Api()
    web_dir = Path(__file__).with_name("web")
    window = webview.create_window(
        "Utilitário do Tulão", str(web_dir / "index.html"),
        js_api=api, width=1180, height=760, min_size=(900, 600),
        background_color="#f5f5f7")
    api.set_window(window)

    def ao_iniciar():
        def checar():
            info = verificar_atualizacao_app()
            if info:
                api._emit("atualizacao_disponivel", info)
        threading.Thread(target=checar, daemon=True).start()

    webview.start(ao_iniciar, debug=False)


if __name__ == "__main__":
    main()
