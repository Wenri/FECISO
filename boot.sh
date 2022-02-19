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
echo "Reading Hash into memory..."

HASH_DEV=$(
  python3 - "$ISO_S" "$IMG_DEV" "$DMID" <<EOF
import argparse
import os
import sys
from pathlib import Path
from subprocess import run, DEVNULL

def _hs(ds, superblock=True, _hash_div=128):
    h = int(superblock)
    while ds:
        ds, rem = divmod(ds, _hash_div)
        h += ds + 1
    return h

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('iso_s', type=int)
    p.add_argument('devstr', type=Path)
    p.add_argument('dmid', type=str)
    return p.parse_args()

def main(opt):
    sector_size = 2048
    total_size = _hs(opt.iso_s // sector_size) * sector_size
    fd = os.memfd_create(opt.dmid)
    with os.fdopen(fd, 'w+b', closefd=False) as memf, open(opt.devstr, 'rb') as isof:
        ret = os.sendfile(memf.fileno(), isof.fileno(), opt.iso_s, total_size)
    assert total_size == ret
    args = ['losetup', '-r', '--show', '-b', '2048', '-f', f'/dev/fd/{fd}']
    return run(args, stdin=DEVNULL, pass_fds=(fd,)).returncode

if __name__ == '__main__':
    sys.exit(main(parse_args()))
EOF
)
echo "Using Hash Device $HASH_DEV"

FEC_DEV="$(losetup -r --show -o "$((ISO_S + HASH_S))" -b 2048 -f "$IMG_DEV")"
echo "Using FEC Device $FEC_DEV"

veritysetup -v --ignore-corruption "--fec-roots=$FEC_ROOTS" \
  "--fec-device=$FEC_DEV" open "$IMG_DEV" "$DMID" "$HASH_DEV" "$ROOT_HASH" || :
losetup -d "$HASH_DEV"
losetup -d "$FEC_DEV"

DM_FILE="/dev/mapper/$DMID"

cat <<-EOF
Mapping at $DM_FILE -> $(readlink -e "$DM_FILE")
You may mount the ISO file with:
> sudo mount $DM_FILE /mnt

With ejecting the disc, first umount the ISO, and then close the device mapping with:
> sudo veritysetup close ${DM_FILE}
EOF

exit 0
