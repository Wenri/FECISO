#!/usr/bin/env bash

set -euo pipefail

IMG_DEV=/media/nvmem/test.iso
ISO_S=302217216
HASH_S=2385920
FEC_ROOTS=24

if [[ -f "$IMG_DEV" ]]; then
  IMG_DEV="$(losetup -f --show "$IMG_DEV")"
fi

echo "Using Device $IMG_DEV"

ROOT_HASH=$(od -j$((ISO_S + 512)) -N16 -tx1 -An "$IMG_DEV" | tr -d '\n ')

echo "Root Hash is $ROOT_HASH"

veritysetup --ignore-corruption --hash-offset=$ISO_S "--fec-device=$IMG_DEV" \
  --fec-offset=$((ISO_S + HASH_S)) --fec-roots=$FEC_ROOTS open "$IMG_DEV" bootsh "$IMG_DEV" \
  "$ROOT_HASH"
