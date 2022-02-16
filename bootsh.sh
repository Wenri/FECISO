#!/usr/bin/env bash
set -euo pipefail
: <<-VARS_END
ISO_S=
HASH_S=
DMID=
VARS_END
: <<_MBR_SEP

_MBR_SEP
IMG_DEV="${BASH_SOURCE[0]}"
[[ "$(id -u)" -eq "0" ]] || exec sudo bash "$IMG_DEV"

ROOT_HASH=$(od -j$((ISO_S + 512)) -N16 -tx1 -An "$IMG_DEV" | tr -d '\n ')
FEC_ROOTS=$(od -j$((ISO_S + 528)) -N1 -tu1 -An "$IMG_DEV" | tr -d '\n ')
echo "Root Hash is $ROOT_HASH, Fec Roots is $FEC_ROOTS"

EXTRA_CLEANUP=""
if [[ -f "$IMG_DEV" ]]; then
  IMG_DEV="$(losetup -f --show "$IMG_DEV")"
  EXTRA_CLEANUP=" && sudo losetup -d $IMG_DEV"
fi
echo "Using Device $IMG_DEV"

veritysetup -v --ignore-corruption --hash-offset=$ISO_S "--fec-device=$IMG_DEV" \
  --fec-offset=$((ISO_S + HASH_S)) --fec-roots=$FEC_ROOTS open "$IMG_DEV" "$DMID" "$IMG_DEV" \
  "$ROOT_HASH"

DM_FILE="/dev/mapper/$DMID"

cat <<-EOF
Mapping at $DM_FILE -> $(readlink -e "$DM_FILE")
You may mount the ISO file with:
> sudo mount $DM_FILE /mnt

With ejecting the disc, first umount the ISO, and then close the device mapping with:
> sudo veritysetup close ${DM_FILE}${EXTRA_CLEANUP}
EOF

exit 0
