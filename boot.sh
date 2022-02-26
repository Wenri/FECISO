#!/usr/bin/env bash
set -euo pipefail
: <<-VARS_END
ISO_SZ=<ISO Image Size in bytes>
HASH_SZ=<HASH Size in bytes>
DMID=<Device mapper name>
OFFSET=<Offset of compressed data in 512-byte sectors>
LENGTH=<Length of compressed data in 512-byte sectors>
CIPHER=<Cipher name for encrypt algorithm>
VARS_END
: <<'_MBR_SEP'
This block will be replaced with optional kwargs and be placed after MBR record.
_MBR_SEP
IMG_DEV="${BASH_SOURCE[0]}"
[[ "$(id -u)" -eq "0" ]] || exec sudo bash "$IMG_DEV"

function dm_verity() {
  local ROOT_HASH
  local FEC_ROOTS
  local _LOADHASH_PROG
  ROOT_HASH=$(od -j$((ISO_SZ + 512)) -N16 -tx1 -An "$IMG_DEV" | tr -d '\n ')
  FEC_ROOTS=$(od -j$((ISO_SZ + 528)) -N1 -tu1 -An "$IMG_DEV" | tr -d '\n ')
  echo "Root Hash is $ROOT_HASH, Fec Roots is $FEC_ROOTS"
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
  echo "Reading Hash into memory..."
  HASH_DEV="$(python3 -c "$_LOADHASH_PROG")"
  unset _LOADHASH_PROG
  echo "Using Hash Device $HASH_DEV"

  FEC_DEV="$(losetup -r --show -o "$((ISO_SZ + HASH_SZ))" -b 2048 -f "$IMG_DEV")"
  echo "Using FEC Device $FEC_DEV"

  if veritysetup -v --ignore-corruption "--fec-roots=$FEC_ROOTS" \
    "--fec-device=$FEC_DEV" open "$IMG_DEV" "$DMID" "$HASH_DEV" "$ROOT_HASH"; then
    DM_FILE="/dev/mapper/$DMID"
  fi
  if losetup -d "$HASH_DEV"; then unset HASH_DEV; fi
  if losetup -d "$FEC_DEV"; then unset FEC_DEV; fi
}
function dm_crypt() {
  local _GETPASS_PROG
  [[ -z ${_DISC_ID+x} ]] && read -r -p "Input Disc ID: " _DISC_ID
  IFS='' read -r -d '' _GETPASS_PROG <<EOF || :
import hashlib
import os
import sys
from getpass import getpass

x = $((LENGTH * 512))
h = hashlib.new('sm3')
h.update(rb"""$_DISC_ID""")
h.update(b'${DMID}_crypt')
h.update(b'$CIPHER')
h.update(x.to_bytes((x.bit_length() + 7) // 8, byteorder='little'))
x = r"""${_PASS-}""" or getpass(r"""${_HINT-}: """)
x = hashlib.scrypt(x.encode(), salt=h.digest(), n=2 ** 20, r=8, p=1, maxmem=2 ** 31 - 1, dklen=64)
os.write(sys.stdout.fileno(), x)
EOF
  if python3 -c "$_GETPASS_PROG" | cryptsetup open --readonly --type plain --hash plain --key-size 512 --key-file=- \
    --cipher "$CIPHER" --offset "$OFFSET" --size "$LENGTH" "$DM_FILE" "${DMID}_crypt"; then
    FS_DEV="/dev/mapper/${DMID}_crypt"
    if unsquashfs -stat "$FS_DEV"; then
      _OH_MY_GBC_NOCRYPT=1
    else
      echo "Your password may be wrong!"
    fi
  else
    _OH_MY_GBC_NOCRYPT=1
  fi
  unset _GETPASS_PROG
}
function mount_helper() {
  echo "Mapping at $FS_DEV -> $(readlink -e "$FS_DEV")"
  if sudo -u "$(logname)" udisksctl mount -b "$FS_DEV"; then
    cat <<-EOF
You may unmount the ISO file with:
> udisksctl unmount -b $FS_DEV
The device mapper is scheduled to deferred close automatically.
EOF
  elif [[ -n "$CIPHER" ]] && [[ -z ${_OH_MY_GBC_NOCRYPT+x} ]]; then
    echo "Mount failed, cleaning up..."
    echo "Set _OH_MY_GBC_FS_PRESERVE=1 to prevent this"
  else
    cat <<-EOF
You may mount the ISO file with:
> sudo mount $FS_DEV /mnt
With ejecting the disc, first umount the ISO, and then close the device mapping with:
EOF
    [[ "$FS_DEV" == "$DM_FILE" ]] || echo "> sudo cryptsetup close $FS_DEV"
    [[ "$DM_FILE" == "$IMG_DEV" ]] || echo "> sudo veritysetup close $DM_FILE"
    _OH_MY_GBC_FS_PRESERVE=1
  fi
}
function cleanup() {
  if [[ -z ${_OH_MY_GBC_FS_PRESERVE+x} ]]; then
    echo "Cleanup on exit"
    [[ -z ${FS_DEV+x} ]] || [[ "$FS_DEV" == "$DM_FILE" ]] || cryptsetup close --deferred "$FS_DEV" || :
    [[ -z ${DM_FILE+x} ]] || [[ "$DM_FILE" == "$IMG_DEV" ]] || cryptsetup close --deferred "$DM_FILE" || :
    [[ -z ${FEC_DEV+x} ]] || losetup -d "$FEC_DEV" || :
    [[ -z ${HASH_DEV+x} ]] || losetup -d "$HASH_DEV" || :
  else
    echo "_OH_MY_GBC_FS_PRESERVE is set. No cleanup"
  fi
}
if [[ -b "$IMG_DEV" ]]; then
  if grep -qs "$IMG_DEV" /proc/mounts; then udisksctl unmount -b "$IMG_DEV" || :; fi
  if [[ -z ${_OH_MY_GBC_NOCRYPT+x} ]] && [[ -n "$CIPHER" ]] && [[ -z ${_DISC_ID+x} ]] &&
    read -r _DISC_ID < <(cdrskin "dev=$IMG_DEV" -minfo | grep '^Product Id' | cut -d':' -f2-); then
    echo "Disc ID is $_DISC_ID"
  fi
fi
trap cleanup EXIT
DM_FILE="$IMG_DEV"
if [[ -z ${_OH_MY_GBC_NOVERITY+x} ]]; then dm_verity; fi
FS_DEV="$DM_FILE"
if [[ -z ${_OH_MY_GBC_NOCRYPT+x} ]] && [[ -n "$CIPHER" ]]; then dm_crypt; fi
mount_helper

exit 0
