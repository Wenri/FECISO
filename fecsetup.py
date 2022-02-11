import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Final

import numpy as np

from ficlonerange import file_clone_range


def truncate(isofile, size):
    args = ['truncate', '--no-create', f'--size={size}', os.fspath(isofile)]
    subprocess.check_call(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class FECSetup:
    _BLK_SZ: Final = 4096
    _SB_SZ: Final = 512
    _HASH_SZ: Final = 16
    _HASH_DIV: Final = _BLK_SZ // _HASH_SZ

    def __init__(self, isofile):
        self.isofile = Path(isofile)
        self.iso_s = (os.path.getsize(self.isofile) + self._BLK_SZ - 1) // self._BLK_SZ
        self.hash_s = self._hs(self.iso_s)

    def _hs(self, ds, superblock=True):
        h = int(superblock)
        while ds:
            ds, rem = divmod(ds, self._HASH_DIV)
            h += ds + bool(rem)
        return h

    def _veriysetup(self, hashfile, recfile, fec_roots=24):
        args = ['veritysetup', 'format', '--salt=-', '--hash=md5', f'--fec-roots={fec_roots}',
                f'--fec-device={os.fspath(recfile)}', os.fspath(self.isofile), os.fspath(hashfile)]
        msg = subprocess.check_output(args, text=True, stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        ret = OrderedDict()
        for s in msg.splitlines():
            k, *v = s.split(':', maxsplit=1)
            ret[k.strip()] = v[0].strip() if v else None
        return ret

    def formatfec(self):
        truncate(self.isofile, f'%{self._BLK_SZ}')
        assert os.path.getsize(self.isofile) == self.iso_s * self._BLK_SZ

        hashfile = self.isofile.with_suffix('.hash')
        fecfile = self.isofile.with_suffix('.fec')
        hashfile.unlink(missing_ok=True)
        fecfile.unlink(missing_ok=True)
        msg = self._veriysetup(hashfile, fecfile)
        assert os.path.getsize(hashfile) == self.hash_s * self._BLK_SZ

        with self.isofile.open('r+b') as isofd, hashfile.open('rb') as hashfd, fecfile.open('rb') as fecfd:
            file_clone_range(hashfd.fileno(), isofd.fileno(), d=self.iso_s * self._BLK_SZ)
            file_clone_range(fecfd.fileno(), isofd.fileno(), d=(self.iso_s + self.hash_s) * self._BLK_SZ)

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

        print(r)
        return msg
