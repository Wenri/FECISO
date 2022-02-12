import io
import os
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Final

import numpy as np
from beartype import beartype

from bootsh import BootSh


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


@beartype
def mkisofs(*targs: str, **kwargs: str) -> int:
    args = ['xorriso', '-as', 'mkisofs', '-verbose', '-iso-level', '4', '-r', '-J', '-joliet-long', '-no-pad']
    for k, t in kwargs.items():
        args.append(f"-{k}")
        args.append(f'{t}')
    for t in targs:
        args.append(f"{t}")
    with subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
        for s in p.stdout:
            sys.stdout.write(s)
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, p.args)
    return p.returncode


@beartype
def truncate(isofile: os.PathLike, s_size: str) -> None:
    args = ['truncate', '--no-create', f'--size={s_size}', os.fspath(isofile)]
    subprocess.check_call(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class VolID:
    def __init__(self, s: str):
        s = s.strip()
        if len(s) > 15 or not s.isascii() or not s.isidentifier():
            raise ValueError(s)
        self.s = s

    def get_volid(self):
        return self.s.upper()

    def get_dmid(self):
        return self.s.lower()


class FECSetup:
    _BLK_SZ: Final[int] = 2048
    _SB_SZ: Final[int] = 512
    _HASH_SZ: Final[int] = 16
    _HASH_DIV: Final[int] = _BLK_SZ // _HASH_SZ

    def __init__(self, isofile: os.PathLike, dmid: VolID):
        self.isofile = Path(isofile)
        self.iso_s = (os.path.getsize(self.isofile) + self._BLK_SZ - 1) // self._BLK_SZ
        self.hash_s = self._hs(self.iso_s)
        self.fec_roots = 24
        self.sh = BootSh(
            ISO_S=self.iso_s * self._BLK_SZ,
            HASH_S=self.hash_s * self._BLK_SZ,
            FEC_ROOTS=self.fec_roots,
            DMID=f'"{dmid.get_dmid()}"'
        )

    @beartype
    def _hs(self, ds: int, superblock=True) -> int:
        h = int(superblock)
        while ds:
            ds, rem = divmod(ds, self._HASH_DIV)
            h += ds + 1
        return h

    @beartype
    def _veriysetup(self, hashfile: os.PathLike, recfile: os.PathLike) -> bytes:
        args = ['veritysetup', 'format', '--salt=-', '--hash=md5', f'--fec-roots={self.fec_roots}',
                f'--data-block-size={self._BLK_SZ}', f'--hash-block-size={self._BLK_SZ}',
                f'--fec-device={os.fspath(recfile)}', os.fspath(self.isofile), os.fspath(hashfile)]
        msg = subprocess.check_output(args, text=True, stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        ret = OrderedDict()
        for s in msg.splitlines():
            k, *v = s.split(':', maxsplit=1)
            ret[k.strip()] = v[0].strip() if v else None

        root_hash = bytes.fromhex(ret['Root hash'])
        assert len(root_hash) == self._HASH_SZ
        assert int(ret['Data blocks']) == self.iso_s
        assert int(ret['Data block size']) == self._BLK_SZ and int(ret['Hash block size']) == self._BLK_SZ
        assert ret['Salt'] == '-'

        return root_hash

    @beartype
    def _combine_with_root_hash(self, hashfile: Path, fecfile: Path, root_hash: bytes) -> None:
        root_off = self.iso_s * self._BLK_SZ + self._SB_SZ
        with self.isofile.open('r+b') as isofd:
            with hashfile.open('rb') as src:
                isofd.seek(self.iso_s * self._BLK_SZ)
                shutil.copyfileobj(src, isofd)
            hashfile.unlink()
            isofd.seek(root_off)
            assert not np.fromfile(isofd, dtype=np.uint64, count=self._HASH_SZ // 8).any()
            isofd.seek(root_off)
            isofd.write(root_hash)
            with fecfile.open('rb') as src:
                isofd.seek(0, io.SEEK_END)
                shutil.copyfileobj(src, isofd)
            fecfile.unlink()

        root_off = os.path.getsize(self.isofile)
        tail_rem = root_off % 32768
        if tail_rem:
            cnt, tail_rem = divmod(32768 - tail_rem, self._HASH_SZ)
            with self.isofile.open('r+b') as isofd:
                isofd.seek(root_off)
                if tail_rem:
                    isofd.write(bytes(tail_rem))
                for i in range(cnt):
                    isofd.write(root_hash)

    @beartype
    def patch_iso(self) -> None:
        iso_size = os.path.getsize(self.isofile)
        tail_rem = iso_size % self._BLK_SZ

        with self.isofile.open('r+b') as f:
            f.write(self.sh.get_header_bytes())
            f.seek(512)
            f.write(self.sh.get_body_bytes())
            if tail_rem:
                tail_rem = self._BLK_SZ - tail_rem
                f.seek(0, io.SEEK_END)
                f.write(bytes(tail_rem))

        assert iso_size + tail_rem == self.iso_s * self._BLK_SZ

    @beartype
    def formatfec(self) -> int:
        self.patch_iso()
        hashfile = self.isofile.with_suffix('.hash')
        fecfile = self.isofile.with_suffix('.fec')
        hashfile.unlink(missing_ok=True)
        fecfile.unlink(missing_ok=True)
        root_hash = self._veriysetup(hashfile, fecfile)

        assert os.path.getsize(hashfile) == self.hash_s * self._BLK_SZ
        fec_size = os.path.getsize(fecfile)

        self._combine_with_root_hash(hashfile, fecfile, root_hash)

        print('Root hash:', root_hash.hex())
        print('Data:', sizeof_fmt(self.iso_s * self._BLK_SZ),
              'Hash:', sizeof_fmt(self.hash_s * self._BLK_SZ),
              'Code:', sizeof_fmt(fec_size))

        iso_s, rem = divmod(os.path.getsize(self.isofile), self._BLK_SZ)
        assert not rem
        print('ISO Sectors:', iso_s, 'sectors')

        return 0
