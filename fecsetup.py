import os
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Final

import numpy as np
from beartype import beartype


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
def truncate(isofile: os.PathLike, s_size: str):
    args = ['truncate', '--no-create', f'--size={s_size}', os.fspath(isofile)]
    subprocess.check_call(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class FECSetup:
    _BLK_SZ: Final[int] = 2048
    _SB_SZ: Final[int] = 512
    _HASH_SZ: Final[int] = 16
    _HASH_DIV: Final[int] = _BLK_SZ // _HASH_SZ

    def __init__(self, isofile: os.PathLike):
        self.isofile = Path(isofile)
        self.iso_s = (os.path.getsize(self.isofile) + self._BLK_SZ - 1) // self._BLK_SZ
        self.hash_s = self._hs(self.iso_s)

    @beartype
    def _hs(self, ds: int, superblock=True) -> int:
        h = int(superblock)
        while ds:
            ds, rem = divmod(ds, self._HASH_DIV)
            h += ds + 1
        return h

    @beartype
    def _veriysetup(self, hashfile: os.PathLike, recfile: os.PathLike, fec_roots: int = 24) -> OrderedDict:
        args = ['veritysetup', 'format', '--salt=-', '--hash=md5', f'--fec-roots={fec_roots}',
                f'--data-block-size={self._BLK_SZ}', f'--hash-block-size={self._BLK_SZ}',
                f'--fec-device={os.fspath(recfile)}', os.fspath(self.isofile), os.fspath(hashfile)]
        msg = subprocess.check_output(args, text=True, stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        ret = OrderedDict()
        for s in msg.splitlines():
            k, *v = s.split(':', maxsplit=1)
            ret[k.strip()] = v[0].strip() if v else None
        return ret

    @beartype
    def formatfec(self) -> int:
        truncate(self.isofile, f'%{self._BLK_SZ}')
        assert os.path.getsize(self.isofile) == self.iso_s * self._BLK_SZ

        hashfile = self.isofile.with_suffix('.hash')
        fecfile = self.isofile.with_suffix('.fec')
        hashfile.unlink(missing_ok=True)
        fecfile.unlink(missing_ok=True)
        msg = self._veriysetup(hashfile, fecfile)
        assert os.path.getsize(hashfile) == self.hash_s * self._BLK_SZ

        with self.isofile.open('r+b') as isofd, hashfile.open('rb') as hashfd, fecfile.open('rb') as fecfd:
            isofd.seek(self.iso_s * self._BLK_SZ)
            shutil.copyfileobj(hashfd, isofd)
            isofd.seek((self.iso_s + self.hash_s) * self._BLK_SZ)
            shutil.copyfileobj(fecfd, isofd)

        hashfile.unlink()
        fecfile.unlink()

        root_hash = bytes.fromhex(msg['Root hash'])
        assert len(root_hash) == self._HASH_SZ
        assert int(msg['Data blocks']) == self.iso_s
        assert int(msg['Data block size']) == self._BLK_SZ and int(msg['Hash block size']) == self._BLK_SZ
        assert msg['Salt'] == '-'

        truncate(self.isofile, '%32K')

        root_off = self.iso_s * self._BLK_SZ + self._SB_SZ
        with self.isofile.open('r+b') as isofd:
            r = np.fromfile(isofd, dtype=np.uint64, count=self._HASH_SZ // 8, offset=root_off)
            assert not r.any()
            isofd.seek(root_off)
            isofd.write(root_hash)

        print(msg['Root hash'])
        print(self.iso_s, self.hash_s)
        return 0
