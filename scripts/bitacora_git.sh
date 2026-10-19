#!/usr/bin/env bash
# Guarda la bitácora (bitacora/*.jsonl y datos/repasos/) en una rama aparte del repo,
# para que la rutina diaria, que corre cada vez en una máquina nueva, no la pierda.
#
#   scripts/bitacora_git.sh bajar   # trae la bitácora de la rama a la copia local
#   scripts/bitacora_git.sh subir   # guarda la bitácora local en la rama y la pushea
#
# La rama se puede cambiar con BITACORA_RAMA. Solo contiene archivos de datos.
set -euo pipefail

RAMA="${BITACORA_RAMA:-claude/bitacora-paper}"
RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"

bajar() {
  if ! git fetch -q origin "$RAMA" 2>/dev/null; then
    echo "La rama $RAMA todavía no existe: se arranca con la bitácora vacía."
    return 0
  fi
  local archivos
  archivos="$(git ls-tree -r --name-only FETCH_HEAD)"
  for f in $archivos; do
    case "$f" in
      bitacora/*.jsonl|datos/repasos/*)
        mkdir -p "$(dirname "$f")"
        git show "FETCH_HEAD:$f" > "$f"
        echo "bajado $f";;
    esac
  done
}

subir() {
  tmp="$(mktemp -d)"
  trap 'git worktree remove --force "$tmp" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
  if git fetch -q origin "$RAMA" 2>/dev/null; then
    git worktree add -q --detach "$tmp" FETCH_HEAD
  else
    git worktree add -q --detach "$tmp" HEAD
    git -C "$tmp" checkout -q --orphan bitacora-tmp
    git -C "$tmp" rm -rq --cached . >/dev/null
    find "$tmp" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  fi
  shopt -s nullglob
  for f in bitacora/*.jsonl datos/repasos/*; do
    mkdir -p "$tmp/$(dirname "$f")"
    cp "$f" "$tmp/$f"
  done
  git -C "$tmp" add -A -f bitacora datos 2>/dev/null || true
  if git -C "$tmp" diff --cached --quiet; then
    echo "La bitácora no cambió."
    return 0
  fi
  git -C "$tmp" commit -q -m "Bitácora $(date -u +%Y-%m-%d)"
  git -C "$tmp" push -q origin "HEAD:refs/heads/$RAMA"
  echo "Bitácora guardada en $RAMA."
}

case "${1:-}" in
  bajar) bajar;;
  subir) subir;;
  *) echo "Uso: $0 bajar|subir" >&2; exit 2;;
esac
