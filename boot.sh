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

function dm_verity() {
  ROOT_HASH=$(od -j$((ISO_SZ + 512)) -N16 -tx1 -An "$IMG_DEV" | tr -d '\n ')
  FEC_ROOTS=$(od -j$((ISO_SZ + 528)) -N1 -tu1 -An "$IMG_DEV" | tr -d '\n ')
  echo "Root Hash is $ROOT_HASH, Fec Roots is $FEC_ROOTS"
  echo "Reading Hash into memory..."

  IFS='' read -r -d '' _LOADHASH_PROG <<EOF || :
import os
import sys
from subprocess import run, DEVNULL

fd = os.memfd_create("$DMID")
with os.fdopen(fd, 'w+b', closefd=False) as memf, open('$IMG_DEV', 'rb') as isof:
    ret = os.sendfile(memf.fileno(), isof.fileno(), $ISO_SZ, $HASH_SZ)
assert $HASH_SZ == ret
args = ['losetup', '-r', '--show', '-b', '2048', '-f', f'/dev/fd/{fd}']
sys.exit(run(args, pass_fds=(fd,)).returncode)
EOF
  HASH_DEV="$(python3 -c "$_LOADHASH_PROG")"
  unset _LOADHASH_PROG
  echo "Using Hash Device $HASH_DEV"

  FEC_DEV="$(losetup -r --show -o "$((ISO_SZ + HASH_SZ))" -b 2048 -f "$IMG_DEV")"
  echo "Using FEC Device $FEC_DEV"

  veritysetup -v --ignore-corruption "--fec-roots=$FEC_ROOTS" \
    "--fec-device=$FEC_DEV" open "$IMG_DEV" "$DMID" "$HASH_DEV" "$ROOT_HASH"
  losetup -d "$HASH_DEV"
  losetup -d "$FEC_DEV"
}
function dm_crypt() {
  IFS='' read -r -d '' _GETPASS_PROG <<EOF || :
import hashlib
import os
import sys
from getpass import getpass

x = $((LENGTH * 512))
h = hashlib.new('sm3')
h.update('${DMID}_crypt'.encode())
h.update('$CIPHER'.encode())
h.update(x.to_bytes((x.bit_length() + 7) // 8, byteorder='little'))
x = getpass('Input your password: ')
x = hashlib.scrypt(x.encode(), salt=h.digest(), n=2 ** 20, r=8, p=1, maxmem=2 ** 31 - 1, dklen=64)
os.write(sys.stdout.fileno(), x)
EOF
  python3 -c "$_GETPASS_PROG" | cryptsetup open --readonly --type plain --hash plain --key-size 512 --key-file=- \
    --cipher "$CIPHER" --offset "$OFFSET" --size "$LENGTH" "$DM_FILE" "${DMID}_crypt"
  unset _GETPASS_PROG
  unsquashfs -stat "/dev/mapper/${DMID}_crypt"
}
function mount_helper() {
  echo "Mapping at $FS_DEV -> $(readlink -e "$FS_DEV")"
  if sudo -u "$(logname)" udisksctl mount -b "$FS_DEV"; then
    cat <<-EOF
You may unmount the ISO file with:
> udisksctl unmount -b $FS_DEV
The device mapper is scheduled to deferred close automatically.
EOF
    [[ "$FS_DEV" == "$DM_FILE" ]] || cryptsetup close --deferred "$FS_DEV"
    [[ "$DM_FILE" == "$IMG_DEV" ]] || cryptsetup close --deferred "$DM_FILE"
  else
    cat <<-EOF
You may mount the ISO file with:
> sudo mount $FS_DEV /mnt
With ejecting the disc, first umount the ISO, and then close the device mapping with:
EOF
    [[ "$FS_DEV" == "$DM_FILE" ]] || echo "> sudo cryptsetup close $FS_DEV"
    [[ "$DM_FILE" == "$IMG_DEV" ]] || echo "> sudo veritysetup close $DM_FILE"
  fi
}

# shellcheck disable=SC2015
[[ -b "$IMG_DEV" ]] && grep -qs "$IMG_DEV" /proc/mounts && udisksctl unmount -b "$IMG_DEV" || :
if [[ -z ${_OH_MY_GBC_NOVERITY+x} ]]; then
  dm_verity
  DM_FILE="/dev/mapper/$DMID"
else
  DM_FILE="$IMG_DEV"
fi
if [[ -n "$CIPHER" ]]; then
  dm_crypt
  FS_DEV="/dev/mapper/${DMID}_crypt"
else
  FS_DEV="$DM_FILE"
fi
mount_helper

exit 0
