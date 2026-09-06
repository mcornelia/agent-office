#!/bin/zsh
set -eu

destination="$HOME/Applications/Agent Office.app"
if (( $# == 2 )) && [[ $1 == --destination ]]; then
  destination=$2
elif (( $# != 0 )); then
  print -u2 "Usage: $0 [--destination PATH]"
  exit 2
fi

controller="$destination/Contents/Resources/runtime/desktop/agent_office_ctl.py"
if [[ -f "$controller" ]]; then
  /usr/bin/python3 "$controller" disable-login
  /usr/bin/python3 "$controller" stop || true
else
  login_item="$HOME/Library/LaunchAgents/com.mcornelia.agent-office.launcher.plist"
  if [[ -e "$login_item" ]]; then
    print -u2 "App controller is unavailable; remove this login item manually: $login_item"
    exit 1
  fi
fi

if [[ -d "$destination" ]]; then
  timestamp=$(/bin/date +%Y%m%d-%H%M%S)
  trashed="$HOME/.Trash/Agent Office $timestamp.app"
  /bin/mv "$destination" "$trashed"
  print "Moved application to Trash: $trashed"
else
  print "Application is not installed at: $destination"
fi
print "Private runtime data remains in: $HOME/Library/Application Support/Agent Office"
