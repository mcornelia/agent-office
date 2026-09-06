#!/bin/zsh
set -eu

desktop_dir=${0:A:h}
repo_root=${desktop_dir:h}
build_dir="$desktop_dir/build"
app="$build_dir/Agent Office.app"
contents="$app/Contents"
runtime="$contents/Resources/runtime"

if [[ $(uname -s) != Darwin ]]; then
  print -u2 "Agent Office.app must be built on macOS."
  exit 1
fi
if ! command -v xcrun >/dev/null 2>&1; then
  print -u2 "Xcode Command Line Tools are required (xcrun was not found)."
  exit 1
fi

rm -rf -- "$app"
mkdir -p "$contents/MacOS" "$runtime/desktop"
xcrun clang -fobjc-arc -fmodules-cache-path="$build_dir/ModuleCache" -mmacosx-version-min=13.0 "$desktop_dir/AgentOfficeApp.m" \
  -o "$contents/MacOS/Agent Office" \
  -framework Cocoa -framework WebKit
cp "$desktop_dir/Info.plist" "$contents/Info.plist"
cp "$repo_root/LICENSE" "$contents/Resources/LICENSE"
backend_port=${AGENT_OFFICE_BACKEND_PORT:-4318}
public_host=${AGENT_OFFICE_PUBLIC_HOST:-}
if [[ $backend_port != <1-65535> ]]; then
  print -u2 "AGENT_OFFICE_BACKEND_PORT must be an integer from 1 to 65535."
  exit 2
fi
/usr/bin/plutil -replace AgentOfficeBackendPort -integer "$backend_port" "$contents/Info.plist"
/usr/bin/plutil -replace AgentOfficePublicHost -string "$public_host" "$contents/Info.plist"
cp "$repo_root/server.py" "$repo_root/desktop_status.py" "$repo_root/communications.py" "$repo_root/job_board.py" "$repo_root/index.html" "$runtime/"
cp "$desktop_dir/agent_office_ctl.py" "$runtime/desktop/"
/usr/bin/printf '%s\n' "$repo_root" > "$runtime/source-root.txt"
chmod 755 "$contents/MacOS/Agent Office" "$runtime/server.py" "$runtime/desktop/agent_office_ctl.py"

signing_identity=${AGENT_OFFICE_SIGNING_IDENTITY:--}
codesign --force --sign "$signing_identity" --timestamp=none "$app"
print "$app"
