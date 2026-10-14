#!/usr/bin/env bash
# Общая установка Home Manager и agents-контейнера; профиль wiki исключён.
set -euo pipefail
package=$1
files=(index.ts core.mjs core.test.mjs SKILL.md README.md)
for file in "${files[@]}"; do
  if [ ! -f "$package/$file" ]; then
    printf 'Pi: missing wiki-ask package file: %s/%s\n' "$package" "$file" >&2
    exit 1
  fi
done

mkdir -p "$HOME/.pi/agent" "$HOME/.pi/profiles/"{general,code}
shopt -s nullglob dotglob
for agent_dir in "$HOME/.pi/agent" "$HOME"/.pi/profiles/*; do
  [ -d "$agent_dir" ] || continue
  wiki_dir="$HOME/.pi/profiles/wiki"
  # Пропускаем также псевдоним/симлинк, ведущий в профиль wiki.
  if [ "$agent_dir" = "$wiki_dir" ] || { [ -d "$wiki_dir" ] && [ "$agent_dir" -ef "$wiki_dir" ]; }; then
    continue
  fi
  mkdir -p "$agent_dir/extensions"
  target="$agent_dir/extensions/wiki-ask"
  if [ -L "$target" ] || [ ! -e "$target" ]; then
    ln -sfnT "$package" "$target"
    continue
  fi

  # Принимаем только полностью совпадающую локальную установку без лишних
  # файлов/ссылок. Резервная копия вне extensions не обнаруживается pi повторно.
  backup="$agent_dir/wiki-ask.before-home-manager"
  identical=false
  if [ -d "$target" ] && [ ! -e "$backup" ] && [ ! -L "$backup" ]; then
    entries=("$target"/*)
    if [ "${#entries[@]}" -eq "${#files[@]}" ]; then
      identical=true
      for file in "${files[@]}"; do
        if [ ! -f "$target/$file" ] || [ -L "$target/$file" ] ||
           ! source_hash=$(sha256sum < "$package/$file") ||
           ! target_hash=$(sha256sum < "$target/$file") ||
           [ "$source_hash" != "$target_hash" ]; then
          identical=false
          break
        fi
      done
    fi
  fi
  if [ "$identical" = true ]; then
    mv -T "$target" "$backup"
    ln -sfnT "$package" "$target"
  else
    printf 'Pi: preserving local wiki-ask extension %s\n' "$target" >&2
  fi
done
