#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v buildozer >/dev/null 2>&1; then
  python3 -m pip install --user --upgrade buildozer cython
fi

buildozer android debug

echo
echo "APK output:"
ls -lh bin/*.apk
