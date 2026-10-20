#!/bin/bash
# Duplo clique: cria "Utilitário do Tulão.dmg" nesta mesma pasta.
cd "$(dirname "$0")" || exit 1
bash criar_dmg.sh
echo
read -n 1 -s -r -p "Aperte qualquer tecla para fechar..."
