#!/usr/bin/env bash
# Guarda la bitácora (bitacora/*.jsonl, bitacora/regla-eventos.md y datos/repasos/)
# fuera de la copia local, para que la rutina diaria, que corre cada vez en una
# máquina nueva, no la pierda.
#
#   scripts/bitacora.sh bajar   # trae la bitácora guardada a la copia local
#   scripts/bitacora.sh subir   # guarda la bitácora local
#
# Si existe la carpeta compartida del proyecto (/mnt/project-files), se guarda en
# /mnt/project-files/bitacora-paper/. Si no, en la rama claude/bitacora-paper del repo.
# Se pueden cambiar con BITACORA_CARPETA y BITACORA_RAMA.
set -euo pipefail

RAMA="${BITACORA_RAMA:-claude/bitacora-paper}"
CARPETA="${BITACORA_CARPETA:-/mnt/project-files/bitacora-paper}"
RAIZ="$(git rev-parse --show-toplevel)"
cd "$RAIZ"

usar_carpeta() {
  [ -d "$(dirname "$CARPETA")" ]
}

bajar_carpeta() {
  if [ ! -d "$CARPETA" ]; then
    echo "Todavía no hay bitácora en $CARPETA: se arranca vacía."
    return 0
  fi
  shopt -s nullglob
  for f in "$CARPETA"/bitacora/*.jsonl "$CARPETA"/bitacora/regla-eventos.md "$CARPETA"/datos/repasos/*; do
    local rel="${f#"$CARPETA"/}"
    mkdir -p "$(dirname "$rel")"
    cp "$f" "$rel"
    echo "bajado $rel"
  done
}

subir_carpeta() {
  shopt -s nullglob
  for f in bitacora/*.jsonl bitacora/regla-eventos.md datos/repasos/*; do
    mkdir -p "$CARPETA/$(dirname "$f")"
    cp "$f" "$CARPETA/$f"
  done
  echo "Bitácora guardada en $CARPETA."
}

bajar_rama() {
  if ! git fetch -q origin "$RAMA" 2>/dev/null; then
    echo "La rama $RAMA todavía no existe: se arranca con la bitácora vacía."
    return 0
  fi
  local archivos
  archivos="$(git ls-tree -r --name-only FETCH_HEAD)"
  for f in $archivos; do
    case "$f" in
      bitacora/*.jsonl|bitacora/regla-eventos.md|datos/repasos/*)
        mkdir -p "$(dirname "$f")"
        git show "FETCH_HEAD:$f" > "$f"
        echo "bajado $f";;
    esac
  done
}

subir_rama() {
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
  for f in bitacora/*.jsonl bitacora/regla-eventos.md datos/repasos/*; do
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
  bajar) if usar_carpeta; then bajar_carpeta; else bajar_rama; fi;;
  subir) if usar_carpeta; then subir_carpeta; else subir_rama; fi;;
  *) echo "Uso: $0 bajar|subir" >&2; exit 2;;
esac
