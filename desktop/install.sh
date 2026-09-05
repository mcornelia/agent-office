#!/bin/zsh
set -eu

desktop_dir=${0:A:h}
destination="$HOME/Applications/Agent Office.app"
enable_login=false

while (( $# )); do
  case "$1" in
    --destination) destination=$2; shift 2 ;;
    --enable-login) enable_login=true; shift ;;
    *) print -u2 "Usage: $0 [--destination PATH] [--enable-login]"; exit 2 ;;
  esac
done

if [[ -e "$destination" ]]; then
  print -u2 "Refusing to overwrite existing application: $destination"
  print -u2 "Run desktop/uninstall.sh first if you intend to replace it."
  exit 1
fi

"$desktop_dir/build_app.sh" >/dev/null
mkdir -p "${destination:h}"
/usr/bin/ditto "$desktop_dir/build/Agent Office.app" "$destination"

if $enable_login; then
  /usr/bin/python3 "$destination/Contents/Resources/runtime/desktop/agent_office_ctl.py" \
    enable-login --app "$destination"
fi

print "Installed: $destination"
print "Start at login: $enable_login"
print "Open with: /usr/bin/open '$destination'"
