#!/usr/bin/env bash
set -euo pipefail
: <<-VARS_END
ISO_S=
HASH_S=
FEC_ROOTS=
DMID=
VARS_END
: <<_MBR_SEP

_MBR_SEP
IMG_DEV="${BASH_SOURCE[0]}"
[ "$(id -u)" -eq "0" ] || exec sudo bash "$IMG_DEV"

ROOT_HASH=$(od -j$((ISO_S + 512)) -N16 -tx1 -An "$IMG_DEV" | tr -d '\n ')
echo "Root Hash is $ROOT_HASH"

if [[ -f "$IMG_DEV" ]]; then
  IMG_DEV="$(losetup -f --show "$IMG_DEV")"
fi
echo "Using Device $IMG_DEV"

exec veritysetup --ignore-corruption --hash-offset=$ISO_S "--fec-device=$IMG_DEV" \
  --fec-offset=$((ISO_S + HASH_S)) --fec-roots=$FEC_ROOTS open "$IMG_DEV" "$DMID" "$IMG_DEV" \
  "$ROOT_HASH"

exit 0
