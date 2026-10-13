#!/usr/bin/env bash
# data 브랜치(상태)를 DIR로 가져온다.
# 브랜치가 "없을 때만" 빈 상태로 시작한다. 브랜치가 있는데 받지 못하면(네트워크·GitHub 장애) 실패해서,
# 빈 상태가 강제 push되어 최대 48회분의 기록이 지워지는 일을 막는다.
set -u
url="$1"; dir="$2"; branch="${3:-data}"
git ls-remote --exit-code --heads "$url" "$branch" > /dev/null 2>&1
rc=$?
if [ "$rc" -eq 2 ]; then
  echo "no '$branch' branch yet: starting with empty state"
  mkdir -p "$dir"
  exit 0
elif [ "$rc" -ne 0 ]; then
  echo "cannot reach remote (git ls-remote exit $rc): refusing to continue with empty state" >&2
  exit 1
fi
git clone -q --depth 1 -b "$branch" "$url" "$dir" || { echo "clone of existing '$branch' failed: aborting" >&2; exit 1; }
