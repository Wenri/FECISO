import asyncio
import io
import os
import shutil
import struct
from collections import OrderedDict
from pathlib import Path
from typing import Final, Optional

import numpy as np
import psutil
from beartype import beartype
from tqdm import tqdm

from bootsh import BootSh
from capacity import DiscCapacity, NumberSegments, sizeof_fmt, VolID
from imagecreate import acall


class FECSetup:
    _BLK_SZ: Final[int] = 2048
    _SB_SZ: Final[int] = 512
    _HASH_SZ: Final[int] = 16
    _HASH_DIV: Final[int] = _BLK_SZ // _HASH_SZ
    _CLUSTER_SZ: Final[int] = 64 * 1024

    def __init__(self, isofile: os.PathLike, dmid: VolID, offset: int = 0, length: int = 0,
                 cipher: Optional[str] = None):
        self.isofile = Path(isofile)
        self.iso_s = (os.path.getsize(self.isofile) + self._BLK_SZ - 1) // self._BLK_SZ
        self.hash_s = self._hs(self.iso_s)
        self.free_s = DiscCapacity(self.iso_s + self.hash_s)
        print('Assuming Disc Type:', self.free_s.disc_name)

        self.fec_roots = self._checkfecsize()
        if self.fec_roots <= 0:
            print('Fec is not possible given current disc type')
            self.fec_roots = 24

        self.sh = BootSh(
            ISO_SZ=self.iso_s * self._BLK_SZ, HASH_SZ=self.hash_s * self._BLK_SZ, DMID=dmid.get_dmid(),
            OFFSET=offset * 4, LENGTH=length * 4, CIPHER=cipher
        )
        cpu_count = psutil.cpu_count(logical=False)
        fec_preview_count = min(self.fec_roots - 1, cpu_count) if cpu_count else self.fec_roots - 1
        self.fec_preview_set = tuple(round(a.item()) for a in np.linspace(self.fec_roots, 2, num=fec_preview_count))

    @beartype
    def _hs(self, ds: int, superblock=True) -> int:
        h = int(superblock)
        while ds:
            ds, rem = divmod(ds, self._HASH_DIV)
            h += ds + 1
        return h

    @beartype
    def _fec_len(self, ds: int, fec_roots: int) -> int:
        fec_data_bits = 255 - fec_roots
        h = ds * self._BLK_SZ
        h = (h + fec_data_bits - 1) // fec_data_bits
        h = h * fec_roots
        return h

    @beartype
    def _combine_with_root_hash(self, hashfile: Path, fecfile: Path, root_hash: bytes, sel_roots: int) -> None:
        root_off = self.iso_s * self._BLK_SZ + self._SB_SZ
        with self.isofile.open('r+b') as isofd:
            with hashfile.open('rb') as src:
                isofd.seek(self.iso_s * self._BLK_SZ)
                shutil.copyfileobj(src, isofd)
            isofd.seek(root_off)
            assert not np.fromfile(isofd, dtype=np.uint64, count=(self._BLK_SZ - self._SB_SZ) // 8).any()
            isofd.seek(root_off)
            isofd.write(root_hash)
            isofd.write(struct.pack("B", sel_roots))
            with fecfile.open('rb') as src:
                isofd.seek(0, io.SEEK_END)
                shutil.copyfileobj(src, isofd)

        root_off = os.path.getsize(self.isofile)
        tail_rem = root_off % self._CLUSTER_SZ
        if tail_rem:
            cnt, tail_rem = divmod(self._CLUSTER_SZ - tail_rem, self._HASH_SZ)
            with self.isofile.open('r+b') as isofd:
                isofd.seek(root_off)
                if tail_rem:
                    isofd.write(bytes(tail_rem))
                for i in range(cnt):
                    isofd.write(root_hash)

    @beartype
    def _patch_iso(self) -> None:
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
    def _checkfecsize(self) -> int:
        disc_s = self.free_s.total_s
        if disc_s < 0:
            return -1
        fec_len = np.fromiter(
            (self._fec_len(self.iso_s + self.hash_s, r) for r in range(24, 1, -1)), dtype=np.int_, count=24 - 1)
        fec_len += self._BLK_SZ - 1
        fec_len //= self._BLK_SZ
        fec_len -= disc_s - self.iso_s - self.hash_s
        idx = np.flatnonzero(fec_len <= 0)
        if len(idx):
            return 24 - idx.item(0)
        return 0

    async def _veriysetup(self, hashfile: Path, fecfile: Path, fec_roots: int, queue: asyncio.Semaphore) -> bytes:
        hashfile.unlink(missing_ok=True)
        fecfile.unlink(missing_ok=True)

        args = ['veritysetup', 'format', '--salt=-', '--hash=md5', f'--fec-roots={fec_roots}',
                f'--data-block-size={self._BLK_SZ}', f'--hash-block-size={self._BLK_SZ}',
                f'--fec-device={os.fspath(fecfile)}', os.fspath(self.isofile), os.fspath(hashfile)]

        async with queue:
            msg = await acall(*args, capture=True)

        assert os.path.getsize(hashfile) == self.hash_s * self._BLK_SZ

        ret = OrderedDict()
        for s in msg.decode().splitlines():
            k, *v = s.split(':', maxsplit=1)
            ret[k.strip()] = v[0].strip() if v else None

        root_hash = bytes.fromhex(ret['Root hash'])
        assert len(root_hash) == self._HASH_SZ
        assert int(ret['Data blocks']) == self.iso_s
        assert int(ret['Data block size']) == self._BLK_SZ and int(ret['Hash block size']) == self._BLK_SZ
        assert ret['Salt'] == '-'

        return root_hash

    async def _try_different_fecroots(self):
        q = asyncio.BoundedSemaphore(value=os.cpu_count())

        co_list = set(self._veriysetup(self.isofile.with_suffix(f'.hash_{i}'), self.isofile.with_suffix(f'.fec_{i}'),
                                       i, q) for i in self.fec_preview_set)
        root_hash = None
        total_s = self.hash_s * self._BLK_SZ * (self.fec_roots - 1)
        total_s += sum(self._fec_len(self.iso_s + self.hash_s, i) for i in self.fec_preview_set)
        with tqdm(total=total_s, unit='B', dynamic_ncols=True, unit_scale=True, leave=False,
                  desc=f'Roots({self.fec_roots}-2,{len(self.fec_preview_set)})') as pbar:
            while True:
                done, co_list = await asyncio.wait(co_list, timeout=1)
                for t in done:
                    if root_hash is None:
                        root_hash = t.result()
                    else:
                        assert t.result() == root_hash
                ps = 0
                for i in range(self.fec_roots, 1, -1):
                    hashfile = self.isofile.with_suffix(f'.hash_{i}')
                    if hashfile.exists():
                        ps += os.path.getsize(hashfile)
                    fecfile = self.isofile.with_suffix(f'.fec_{i}')
                    if fecfile.exists():
                        ps += os.path.getsize(fecfile)
                pbar.update((ps if ps < pbar.total else pbar.total) - pbar.n)
                if not co_list:
                    pbar.update(pbar.total - pbar.n)
                    break

        print('Rec Calc Done.')
        return root_hash

    @beartype
    def _select_lucky_fec(self):
        disc_s = self.free_s.total_s
        prev_str = None
        for i in self.fec_preview_set:
            fecfile = self.isofile.with_suffix(f'.fec_{i}')
            fec_s = (os.path.getsize(fecfile) + self._BLK_SZ - 1) // self._BLK_SZ
            size_s = (disc_s - self.iso_s - self.hash_s - fec_s) * self._BLK_SZ
            if prev_str != size_s:
                if prev_str:
                    print(prev_str, end=' ')
                prev_str = NumberSegments(size_s)
            prev_str.add_val(i)

        print(prev_str)
        while True:
            try:
                sel_roots = int(input('Select your lucky number: '))
                if sel_roots in self.fec_preview_set:
                    break
            except ValueError:
                pass
            print('Your selection must be one of', self.fec_preview_set)

        hashfile = self.isofile.with_suffix(f'.hash_{sel_roots}')
        fecfile = self.isofile.with_suffix(f'.fec_{sel_roots}')

        return hashfile, fecfile, sel_roots

    def _clean_different_fecroots(self):
        for i in range(self.fec_roots, 1, -1):
            self.isofile.with_suffix(f'.hash_{i}').unlink(missing_ok=True)
            self.isofile.with_suffix(f'.fec_{i}').unlink(missing_ok=True)

    async def formatfec(self) -> int:
        self._patch_iso()

        try:
            root_hash = await self._try_different_fecroots()
            hashfile, fecfile, sel_roots = self._select_lucky_fec()
            fec_size = os.path.getsize(fecfile)
            self._combine_with_root_hash(hashfile, fecfile, root_hash, sel_roots)
        finally:
            self._clean_different_fecroots()

        print('Root hash:', root_hash.hex())
        print('Data:', sizeof_fmt(self.iso_s * self._BLK_SZ),
              'Hash:', sizeof_fmt(self.hash_s * self._BLK_SZ),
              'Code:', sizeof_fmt(fec_size))

        iso_s, rem = divmod(os.path.getsize(self.isofile), self._BLK_SZ)
        assert not rem
        print('ISO Sectors:', iso_s, 'sectors')

        return 0
