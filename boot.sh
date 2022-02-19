#!/usr/bin/env bash
set -euo pipefail
: <<-VARS_END
ISO_SZ=
HASH_SZ=
DMID=
OFFSET=
LENGTH=
CIPHER=
VARS_END
: <<_MBR_SEP

_MBR_SEP
IMG_DEV="${BASH_SOURCE[0]}"
[[ "$(id -u)" -eq "0" ]] || exec sudo bash "$IMG_DEV"

ROOT_HASH=$(od -j$((ISO_SZ + 512)) -N16 -tx1 -An "$IMG_DEV" | tr -d '\n ')
FEC_ROOTS=$(od -j$((ISO_SZ + 528)) -N1 -tu1 -An "$IMG_DEV" | tr -d '\n ')
echo "Root Hash is $ROOT_HASH, Fec Roots is $FEC_ROOTS"
echo "Reading Hash into memory..."

HASH_DEV=$(
  python3 - <<EOF
import os
import sys
from subprocess import run, DEVNULL

fd = os.memfd_create("$DMID")
with os.fdopen(fd, 'w+b', closefd=False) as memf, open("$IMG_DEV", 'rb') as isof:
    ret = os.sendfile(memf.fileno(), isof.fileno(), $ISO_SZ, $HASH_SZ)
assert $HASH_SZ == ret
args = ['losetup', '-r', '--show', '-b', '2048', '-f', f'/dev/fd/{fd}']
sys.exit(run(args, stdin=DEVNULL, pass_fds=(fd,)).returncode)
EOF
)
echo "Using Hash Device $HASH_DEV"

FEC_DEV="$(losetup -r --show -o "$((ISO_SZ + HASH_SZ))" -b 2048 -f "$IMG_DEV")"
echo "Using FEC Device $FEC_DEV"

veritysetup -v --ignore-corruption "--fec-roots=$FEC_ROOTS" \
  "--fec-device=$FEC_DEV" open "$IMG_DEV" "$DMID" "$HASH_DEV" "$ROOT_HASH" || :
losetup -d "$HASH_DEV"
losetup -d "$FEC_DEV"

DM_FILE="/dev/mapper/$DMID"

if [[ -n "$CIPHER" ]]; then
  cryptsetup open --readonly --type plain --hash sha512 --key-size 512 --cipher "$CIPHER" \
    --offset "$OFFSET" --size "$LENGTH" "$DM_FILE" "${DMID}_crypt"
fi

cat <<-EOF
Mapping at $DM_FILE -> $(readlink -e "$DM_FILE")
You may mount the ISO file with:
> sudo mount $DM_FILE /mnt

With ejecting the disc, first umount the ISO, and then close the device mapping with:
> sudo veritysetup close ${DM_FILE}
EOF

exit 0
