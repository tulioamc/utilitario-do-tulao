"""
Limpeza — mede o que ocupa espaço de verdade no disco e ajuda a liberar.

Ideias principais:
- O tamanho que conta é o ESPAÇO EM DISCO (st_blocks), não o tamanho do
  arquivo. Arquivo do Drive/Dropbox que está "somente online" tem tamanho
  normal mas ocupa 0 bytes no disco, então some sozinho das contas.
- "Liberar" = o mesmo "Remover Download" do Finder, feito em lote pelo
  comando do macOS `fileproviderctl evict`. Funciona para quem usa o
  sistema de nuvem do macOS (pastas em ~/Library/CloudStorage: Google
  Drive, Dropbox na versão nova, OneDrive...). O Dropbox no formato antigo
  (pasta ~/Dropbox de verdade, com o arquivo .dropbox dentro) não aceita
  esse comando: para ele o app mostra no Finder e você usa o menu do Dropbox.
- "Parado há X dias" usa a data mais recente entre último acesso e última
  modificação do arquivo.
- O modo automático roda sozinho uma vez por dia (LaunchAgent do macOS),
  mesmo com o app fechado.
"""
import json
import os
import plistlib
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOME = Path.home()
CLOUD_STORAGE = HOME / "Library/CloudStorage"
PASTA_CONFIG = HOME / "Library/Application Support/UtilitarioDoTulao"
ARQ_CONFIG = PASTA_CONFIG / "limpeza.json"
ARQ_ULTIMO = PASTA_CONFIG / "limpeza_ultimo.json"
LOG = HOME / "Library/Logs/UtilitarioDoTulao.log"
LABEL_AGENTE = "local.utilitariodotulao.limpeza"
PLIST_AGENTE = HOME / "Library/LaunchAgents" / f"{LABEL_AGENTE}.plist"
FILEPROVIDERCTL = shutil.which("fileproviderctl") or "/usr/bin/fileproviderctl"

MB = 1024 * 1024
DIA = 86400
LIMITE_ARQUIVO_GUARDADO = 10 * MB  # arquivos a partir disso entram na lista detalhada

CONFIG_PADRAO = {
    "avisar": False,
    "avisar_mb": 500,
    "avisar_dias": 60,
    "liberar_auto": False,
    "liberar_dias": 30,
    "hora": 11,
}


def _log(msg):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Limpeza] {msg}\n")
    except Exception:
        pass


def bonito(caminho):
    s = str(caminho)
    nuvem = str(CLOUD_STORAGE) + "/"
    if s.startswith(nuvem):
        partes = s[len(nuvem):].split("/", 1)
        nome = partes[0]
        conta = nome.split("-", 1)[1] if "-" in nome else ""
        cabeca = f"{_nome_nuvem(nome)} ({conta})" if conta else _nome_nuvem(nome)
        return cabeca + (" › " + partes[1].replace("/", " › ") if len(partes) > 1 else "")
    h = str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def ultimo_uso(st):
    return max(st.st_atime, st.st_mtime)


def espaco_em_disco(st):
    return getattr(st, "st_blocks", 0) * 512


# ==========================================================================
# Nuvens
# ==========================================================================

def _nome_nuvem(nome_pasta):
    n = nome_pasta.lower()
    if n.startswith("googledrive"):
        return "Google Drive"
    if n.startswith("dropbox"):
        return "Dropbox"
    if n.startswith("onedrive"):
        return "OneDrive"
    if n.startswith("box"):
        return "Box"
    return nome_pasta.split("-")[0]


def detectar_nuvens():
    """Pastas de nuvem do Mac. `liberavel` diz se o app consegue tirar os
    arquivos do disco sozinho (precisa ser do sistema de nuvem do macOS)."""
    nuvens = []
    vistos = set()
    if CLOUD_STORAGE.is_dir():
        for d in sorted(CLOUD_STORAGE.iterdir()):
            if d.name.startswith(".") or not d.is_dir() or d.is_symlink():
                continue
            conta = d.name.split("-", 1)[1] if "-" in d.name else ""
            # "GoogleDrive-x@y (10-06-2026 14:47)" = conta antiga desconectada
            antiga = "(" in conta and conta.endswith(")")
            nuvens.append({
                "servico": _nome_nuvem(d.name), "conta": conta, "caminho": str(d),
                "caminho_bonito": bonito(d), "liberavel": True, "antiga": antiga,
            })
            vistos.add(os.path.realpath(d))
    # Dropbox no formato antigo: pasta comum na pasta pessoal com o marcador .dropbox
    try:
        for d in sorted(HOME.iterdir()):
            if d.is_symlink() or not d.is_dir() or d.name.startswith("."):
                continue
            if (d / ".dropbox").exists() and os.path.realpath(d) not in vistos:
                nuvens.append({
                    "servico": "Dropbox", "conta": d.name, "caminho": str(d),
                    "caminho_bonito": bonito(d), "liberavel": False, "antiga": False,
                })
    except Exception:
        pass
    return nuvens


def nuvem_de(caminho, nuvens=None):
    real = os.path.realpath(caminho)
    melhor = None
    for n in nuvens if nuvens is not None else detectar_nuvens():
        raiz = os.path.realpath(n["caminho"])
        if real == raiz or real.startswith(raiz + "/"):
            if not melhor or len(raiz) > len(os.path.realpath(melhor["caminho"])):
                melhor = n
    return melhor


# ==========================================================================
# Varredura
# ==========================================================================

class Cancelado(Exception):
    pass


class Varredura:
    """Percorre uma pasta medindo o espaço real em disco de cada subpasta.
    Guarda os totais para o app poder navegar sem varrer de novo."""

    def __init__(self, raiz):
        self.raiz = str(Path(raiz).expanduser())
        self.total = {}        # pasta -> bytes em disco (somando tudo dentro)
        self.qtd = {}          # pasta -> nº de arquivos dentro
        self.soltos = {}       # pasta -> bytes dos arquivos pequenos diretos
        self.filhos = {}       # pasta -> [subpastas]
        self.arquivos = {}     # pasta -> [dict de arquivos >= 10 MB diretos]
        self.pai = {}
        self.sem_acesso = 0
        self.n_arquivos = 0
        self.inicio = time.time()
        self.fim = None

    def executar(self, progresso=None, cancelar=None):
        ordem = []
        pilha = [self.raiz]
        self.pai[self.raiz] = None
        ultimo_aviso = 0
        while pilha:
            if cancelar is not None and cancelar.is_set():
                raise Cancelado()
            pasta = pilha.pop()
            ordem.append(pasta)
            self.total[pasta] = 0
            self.qtd[pasta] = 0
            self.soltos[pasta] = 0
            self.filhos[pasta] = []
            self.arquivos[pasta] = []
            try:
                it = os.scandir(pasta)
            except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
                self.sem_acesso += 1
                continue
            with it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            self.filhos[pasta].append(e.path)
                            self.pai[e.path] = pasta
                            pilha.append(e.path)
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    disco = espaco_em_disco(st)
                    self.n_arquivos += 1
                    self.total[pasta] += disco
                    self.qtd[pasta] += 1
                    if disco >= LIMITE_ARQUIVO_GUARDADO:
                        self.arquivos[pasta].append({
                            "caminho": e.path, "nome": e.name, "disco": disco,
                            "tamanho": st.st_size, "uso": ultimo_uso(st),
                        })
                    else:
                        self.soltos[pasta] += disco
            if progresso and time.time() - ultimo_aviso > 0.4:
                ultimo_aviso = time.time()
                progresso({"arquivos": self.n_arquivos, "atual": bonito(pasta)})
        # soma de baixo pra cima
        for pasta in reversed(ordem):
            p = self.pai.get(pasta)
            if p is not None and p in self.total:
                self.total[p] += self.total[pasta]
                self.qtd[p] += self.qtd[pasta]
        self.fim = time.time()
        return self

    # -- consultas ---------------------------------------------------------

    def contem(self, caminho):
        return caminho in self.total

    def listar(self, pasta, nuvens):
        itens = []
        for f in self.filhos.get(pasta, []):
            itens.append({"tipo": "pasta", "caminho": f, "nome": os.path.basename(f),
                          "disco": self.total.get(f, 0), "qtd": self.qtd.get(f, 0)})
        for a in self.arquivos.get(pasta, []):
            if a["disco"] > 0:
                itens.append({"tipo": "arquivo", "caminho": a["caminho"], "nome": a["nome"],
                              "disco": a["disco"], "uso": a["uso"]})
        if self.soltos.get(pasta, 0) > 0:
            itens.append({"tipo": "soltos", "caminho": pasta, "nome": "Arquivos menores (abaixo de 10 MB)",
                          "disco": self.soltos[pasta]})
        itens = [i for i in itens if i["disco"] > 0]
        itens.sort(key=lambda i: -i["disco"])
        for i in itens:
            n = nuvem_de(i["caminho"], nuvens) if i["tipo"] != "soltos" else None
            i["nuvem"] = n["servico"] if n else None
            i["liberavel"] = bool(n and n["liberavel"])
        return itens

    def grandes_parados(self, min_bytes, dias, nuvens, so_nuvem=False):
        limite = time.time() - dias * DIA
        out = []
        for lista in self.arquivos.values():
            for a in lista:
                if a["disco"] >= min_bytes and a["uso"] <= limite:
                    n = nuvem_de(a["caminho"], nuvens)
                    if so_nuvem and not n:
                        continue
                    out.append({**a, "nuvem": n["servico"] if n else None,
                                "liberavel": bool(n and n["liberavel"]),
                                "dias": int((time.time() - a["uso"]) // DIA)})
        out.sort(key=lambda a: -a["disco"])
        return out

    def descontar(self, caminho, liberado):
        """Atualiza os totais depois de liberar/apagar algo, sem varrer de novo."""
        if liberado <= 0:
            return
        pasta = caminho if caminho in self.total else os.path.dirname(caminho)
        for lista in self.arquivos.values():
            for a in lista:
                if a["caminho"] == caminho or a["caminho"].startswith(caminho.rstrip("/") + "/"):
                    a["disco"] = 0
        while pasta is not None and pasta in self.total:
            self.total[pasta] = max(0, self.total[pasta] - liberado)
            pasta = self.pai.get(pasta)


# ==========================================================================
# Liberar (Remover Download) e Lixo
# ==========================================================================

def _arquivos_baixados(caminho):
    """Arquivos dentro de `caminho` que estão ocupando disco."""
    if os.path.isfile(caminho) or os.path.islink(caminho):
        try:
            st = os.stat(caminho, follow_symlinks=False)
            if espaco_em_disco(st) > 0:
                yield caminho, espaco_em_disco(st)
        except OSError:
            pass
        return
    for raiz, pastas, arquivos in os.walk(caminho):
        for nome in arquivos:
            p = os.path.join(raiz, nome)
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            if espaco_em_disco(st) > 0:
                yield p, espaco_em_disco(st)


def _evict(caminho):
    try:
        r = subprocess.run([FILEPROVIDERCTL, "evict", caminho], capture_output=True,
                           text=True, timeout=120)
        if r.returncode != 0:
            return (r.stderr or r.stdout or "erro").strip()[-200:]
    except Exception as e:
        return str(e)
    return None


def liberar(caminhos, progresso=None, cancelar=None, filtro=None):
    """Tira do disco (deixa só online) tudo o que estiver baixado nos caminhos.
    `filtro(caminho, st)` opcional para escolher quais arquivos liberar."""
    nuvens = detectar_nuvens()
    alvos, nao_suportados = [], []
    for c in caminhos:
        n = nuvem_de(c, nuvens)
        if not n:
            nao_suportados.append({"caminho": c, "motivo": "Não está no Drive nem no Dropbox."})
            continue
        if not n["liberavel"]:
            nao_suportados.append({"caminho": c, "motivo": "Dropbox no formato antigo: use o menu do Dropbox no Finder."})
            continue
        for p, disco in _arquivos_baixados(c):
            if filtro and not filtro(p):
                continue
            alvos.append((c, p, disco))
    total = len(alvos)
    feitos = 0
    liberado_por_raiz = {}
    falhas = []
    lock = threading.Lock()

    def um(item):
        nonlocal feitos
        raiz, p, disco = item
        if cancelar is not None and cancelar.is_set():
            return
        erro = _evict(p)
        depois = 0
        try:
            depois = espaco_em_disco(os.stat(p, follow_symlinks=False))
        except OSError:
            pass
        with lock:
            feitos += 1
            if erro and depois > 0:
                falhas.append({"caminho": p, "motivo": erro})
            else:
                liberado_por_raiz[p] = disco - depois
            if progresso:
                progresso({"feitos": feitos, "total": total, "atual": bonito(p)})

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(um, alvos))
    return {
        "liberado": sum(liberado_por_raiz.values()),
        "por_arquivo": liberado_por_raiz,
        "arquivos": total - len(falhas),
        "falhas": falhas[:50],
        "n_falhas": len(falhas),
        "nao_suportados": nao_suportados,
    }


def mover_para_lixo(caminhos):
    movidos, falhas = [], []
    for c in caminhos:
        try:
            antes = sum(d for _, d in _arquivos_baixados(c))
            ok = False
            try:
                from Foundation import NSFileManager, NSURL  # pyobjc
                url = NSURL.fileURLWithPath_(c)
                ok, _res, _err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(url, None, None)
            except Exception:
                ok = False
            if not ok:
                script = f'tell application "Finder" to delete (POSIX file {json.dumps(c)})'
                r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
                ok = r.returncode == 0
            if ok:
                movidos.append({"caminho": c, "disco": antes})
            else:
                falhas.append({"caminho": c, "motivo": "O Finder não deixou mover."})
        except Exception as e:
            falhas.append({"caminho": c, "motivo": str(e)})
    return {"movidos": movidos, "falhas": falhas}


# ==========================================================================
# Configuração e modo automático
# ==========================================================================

def ler_config():
    cfg = dict(CONFIG_PADRAO)
    try:
        if ARQ_CONFIG.exists():
            cfg.update({k: v for k, v in json.loads(ARQ_CONFIG.read_text("utf-8")).items() if k in CONFIG_PADRAO})
    except Exception as e:
        _log(f"config ilegível: {e}")
    cfg["agendado"] = PLIST_AGENTE.exists()
    return cfg


def salvar_config(novo, python, script_app):
    cfg = dict(CONFIG_PADRAO)
    cfg.update({k: novo[k] for k in CONFIG_PADRAO if k in (novo or {})})
    cfg["avisar_mb"] = max(10, int(cfg["avisar_mb"]))
    cfg["avisar_dias"] = max(1, int(cfg["avisar_dias"]))
    cfg["liberar_dias"] = max(1, int(cfg["liberar_dias"]))
    cfg["hora"] = min(23, max(0, int(cfg["hora"])))
    PASTA_CONFIG.mkdir(parents=True, exist_ok=True)
    ARQ_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if cfg["avisar"] or cfg["liberar_auto"]:
        instalar_agente(python, script_app, cfg["hora"])
    else:
        remover_agente()
    return ler_config()


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def instalar_agente(python, script_app, hora):
    PLIST_AGENTE.parent.mkdir(parents=True, exist_ok=True)
    dados = {
        "Label": LABEL_AGENTE,
        "ProgramArguments": [str(python), str(script_app), "--limpeza-automatica"],
        "StartCalendarInterval": {"Hour": int(hora), "Minute": 7},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
    }
    with PLIST_AGENTE.open("wb") as f:
        plistlib.dump(dados, f)
    uid = str(os.getuid())
    _launchctl("bootout", f"gui/{uid}/{LABEL_AGENTE}")
    r = _launchctl("bootstrap", f"gui/{uid}", str(PLIST_AGENTE))
    if r.returncode != 0:
        _launchctl("load", "-w", str(PLIST_AGENTE))  # macOS mais antigo
    _log(f"agendamento instalado ({hora}h)")


def remover_agente():
    if PLIST_AGENTE.exists():
        _launchctl("bootout", f"gui/{os.getuid()}/{LABEL_AGENTE}")
        try:
            PLIST_AGENTE.unlink()
        except Exception:
            pass
        _log("agendamento removido")


def notificar(titulo, texto):
    script = f"display notification {json.dumps(texto)} with title {json.dumps(titulo)} sound name \"Glass\""
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20)
    except Exception:
        pass


def formatar_bytes(n):
    n = float(n or 0)
    for un in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or un == "TB":
            return f"{n:.0f} {un}" if un in ("B", "KB") else f"{n:.1f} {un}".replace(".", ",")
        n /= 1024


def _pastas_para_aviso():
    """Onde procurar arquivos grandes parados: pasta pessoal, menos a
    Biblioteca (só entra a parte das nuvens)."""
    out = []
    try:
        for d in HOME.iterdir():
            if d.name.startswith(".") or d.is_symlink() or not d.is_dir():
                continue
            if d.name == "Library":
                continue
            out.append(str(d))
    except Exception:
        pass
    if CLOUD_STORAGE.is_dir():
        out.append(str(CLOUD_STORAGE))
    return out


def rodar_automatico(cfg=None, avisar_usuario=True):
    cfg = cfg or ler_config()
    resumo = {"quando": time.time(), "liberado": 0, "liberados": 0, "parados": [], "erro": None}
    try:
        if cfg.get("liberar_auto"):
            limite = time.time() - cfg["liberar_dias"] * DIA
            raizes = [n["caminho"] for n in detectar_nuvens() if n["liberavel"]]

            def velho(p):
                try:
                    return ultimo_uso(os.stat(p, follow_symlinks=False)) <= limite
                except OSError:
                    return False

            r = liberar(raizes, filtro=velho)
            resumo["liberado"] = r["liberado"]
            resumo["liberados"] = r["arquivos"]
            _log(f"automático: liberou {formatar_bytes(r['liberado'])} em {r['arquivos']} arquivo(s); {r['n_falhas']} falha(s)")

        if cfg.get("avisar"):
            nuvens = detectar_nuvens()
            parados = []
            for pasta in _pastas_para_aviso():
                v = Varredura(pasta).executar()
                parados += v.grandes_parados(cfg["avisar_mb"] * MB, cfg["avisar_dias"], nuvens)
            parados.sort(key=lambda a: -a["disco"])
            resumo["parados"] = parados[:300]
            _log(f"automático: {len(parados)} arquivo(s) grande(s) parado(s)")

        PASTA_CONFIG.mkdir(parents=True, exist_ok=True)
        ARQ_ULTIMO.write_text(json.dumps(resumo), encoding="utf-8")

        if avisar_usuario:
            partes = []
            if resumo["liberado"] > 0:
                partes.append(f"Liberei {formatar_bytes(resumo['liberado'])} do Drive/Dropbox.")
            if resumo["parados"]:
                tot = sum(a["disco"] for a in resumo["parados"])
                partes.append(f"{len(resumo['parados'])} arquivo(s) grande(s) parado(s) ocupando {formatar_bytes(tot)}. "
                              "Abra o Utilitário › Limpeza.")
            if partes:
                notificar("Utilitário do Tulão · Limpeza", " ".join(partes))
    except Exception as e:
        resumo["erro"] = str(e)
        _log(f"automático falhou: {e}")
    return resumo


def ler_ultimo():
    try:
        if ARQ_ULTIMO.exists():
            return json.loads(ARQ_ULTIMO.read_text("utf-8"))
    except Exception:
        pass
    return None
