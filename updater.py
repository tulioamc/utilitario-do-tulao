#!/usr/bin/env python3
"""Updater do Utilitário do Tulão para macOS.

Este processo roda separado do aplicativo principal, permitindo substituir o
.app depois que ele for fechado. Não requer token do GitHub para Releases
públicas.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def baixar(url, destino):
    req = urllib.request.Request(url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "Utilitario-do-Tulao-Updater",
    })
    with urllib.request.urlopen(req, timeout=120) as r, open(destino, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        recebido = 0
        while True:
            bloco = r.read(1024 * 1024)
            if not bloco:
                break
            f.write(bloco)
            recebido += len(bloco)
    if total and recebido != total:
        raise RuntimeError("download incompleto")
    if recebido < 1024:
        raise RuntimeError("arquivo de atualização inválido")


def localizar_app(pasta):
    encontrados = list(Path(pasta).glob("*.app"))
    if not encontrados:
        encontrados = list(Path(pasta).rglob("*.app"))
    if not encontrados:
        raise RuntimeError("o pacote baixado não contém um .app")
    return encontrados[0]


def executar(args):
    app = Path(args.app).resolve()
    if not app.exists() or app.suffix != ".app":
        raise RuntimeError("aplicativo atual não encontrado")

    # Dá tempo para o processo principal sair e liberar arquivos.
    time.sleep(1.5)

    tmp = Path(tempfile.mkdtemp(prefix="tulao-update-"))
    zip_path = tmp / "update.zip"
    extraido = tmp / "extraido"
    backup = app.with_name(app.name + ".backup")
    try:
        baixar(args.url, zip_path)
        extraido.mkdir()
        with zipfile.ZipFile(zip_path) as z:
            if z.testzip() is not None:
                raise RuntimeError("ZIP corrompido")
            z.extractall(extraido)
            # zipfile.extractall() NÃO restaura as permissões Unix (bit de
            # execução) guardadas no zip — isso é uma limitação conhecida do
            # Python, não um bug deste programa. Sem este passo, todo
            # executável extraído (launcher, instalar.command) perde o "x" e
            # o macOS recusa abrir o app recém-atualizado. Restaura à mão a
            # partir do que o zip realmente guarda para cada arquivo.
            for info in z.infolist():
                if info.is_dir():
                    continue
                permissoes = (info.external_attr >> 16) & 0o777
                if permissoes:
                    alvo = extraido / info.filename
                    try:
                        os.chmod(alvo, permissoes)
                    except Exception:
                        pass
        nova_app = localizar_app(extraido)

        # Mantém a versão anterior até a nova existir no mesmo diretório.
        if backup.exists():
            shutil.rmtree(backup)
        os.rename(app, backup)
        try:
            shutil.copytree(nova_app, app, symlinks=True)
        except Exception:
            if app.exists():
                shutil.rmtree(app)
            os.rename(backup, app)
            raise

        # Segurança extra: garante o bit de execução nos pontos críticos,
        # mesmo que a restauração de permissões acima falhe por algum motivo.
        for relativo in ("Contents/MacOS/launcher", "Contents/Resources/instalar.command"):
            alvo = app / relativo
            if alvo.exists():
                try:
                    atual = alvo.stat().st_mode
                    os.chmod(alvo, atual | 0o111)
                except Exception:
                    pass

        # Remove o atributo de quarentena do Gatekeeper: sem isso, o macOS
        # trata a cópia recém-baixada como nunca aprovada antes e bloqueia
        # a abertura ("não pode ser aberto"), mesmo que a versão anterior já
        # tivesse sido liberada pelo usuário.
        try:
            subprocess.run(["xattr", "-cr", str(app)], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # Se a nova cópia existir, a antiga pode ser removida.
        shutil.rmtree(backup, ignore_errors=True)
        subprocess.Popen(["open", str(app)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        # O rollback acima cobre falhas depois do rename. Falhas anteriores não
        # alteram a instalação atual.
        log = Path.home() / "Library/Logs/UtilitarioDoTulao.log"
        try:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"[Updater] Falha: {exc}\n")
        except Exception:
            pass
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--repo", required=False)
    args = parser.parse_args()
    try:
        executar(args)
    except Exception:
        # O app antigo já foi fechado; registrar no log e não abrir a versão
        # nova se a instalação não for confiável.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
