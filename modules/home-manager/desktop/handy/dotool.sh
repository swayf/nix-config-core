# Included in the Handy-only dotool wrapper, after opts/real_dotool/query_kitty.
input=$(cat)

# A terminal's text paste is not the raw Ctrl+V that agents use for images.
# Query focus at injection time, not when recording starts. Never rewrite
# transcript payloads, explicit alternative chords or arbitrary dotool input.
if [[ $input == 'key ctrl+v' && $query_kitty == 1 ]]; then
  if focused=$(gdbus call --session --dest org.gnome.Shell \
    --object-path /org/home_manager/HandyFocus \
    --method org.home_manager.HandyFocus.IsKitty --timeout 1 2>/dev/null) \
    && [[ $focused == '(true,)' ]]; then
    input='key ctrl+shift+v'
  fi
fi

printf '%s\n' "$input" | {
  if [[ $opts == *altwin:ctrl_alt_win* ]]; then
    sed -E '/^(key|keydown|keyup) /{
      s/\b(ctrl|control)\b/@a@/g
      s/\balt\b/@s@/g
      s/\bsuper\b/@c@/g
      s/@a@/alt/g
      s/@s@/super/g
      s/@c@/ctrl/g
    }'
  else
    cat
  fi
} | "$real_dotool" "$@"

# Handy restores the clipboard shortly after dotool exits. Allow the target
# time to finish reading, without holding keys or delaying direct typing.
case "$input" in
  'key ctrl+v'|'key ctrl+shift+v'|'key shift+insert')
    sleep 1
    ;;
esac
