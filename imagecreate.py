import asyncio
import os
import re
import shlex
import subprocess
import sys
from collections import namedtuple
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from capacity import VolID


async def acall(*args, capture=False, forward=False, stdin: Optional[int] = asyncio.subprocess.DEVNULL,
                binput: Optional[bytes] = None, **kwargs):
    proc = None
    if binput:
        stdin = asyncio.subprocess.PIPE
    if capture:
        kwargs.setdefault('stdout', asyncio.subprocess.PIPE)
        kwargs.setdefault('stderr', asyncio.subprocess.STDOUT)
    try:
        proc = await asyncio.subprocess.create_subprocess_exec(*args, stdin=stdin, **kwargs)
        if capture and forward:
            while not proc.stdout.at_eof():
                s = await proc.stdout.readline()
                sys.stdout.write(s.decode())
        msg, _ = await proc.communicate(binput)
    finally:
        if proc is not None and proc.returncode is None:
            proc.terminate()
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, args, proc.stdout, proc.stderr)
    return msg


class ImageCreate:
    def __init__(self, isofile: os.PathLike, dmid: VolID, compression: bool, bpassword: Optional[bytes] = None):
        self.isofile = Path(isofile)
        self.volid = dmid.get_volid()
        self.squash_file: Optional[Path] = None
        self.bpassword = bpassword
        if compression:
            squash_dir = self.isofile.with_suffix('.rootdir')
            self.squash_file = squash_dir / self.isofile.with_suffix('.sqfs').name

    @asynccontextmanager
    async def __maybe_compress(self, data_dir):
        if self.squash_file:
            self.squash_file.parent.mkdir(exist_ok=True)
            self.squash_file.unlink(missing_ok=True)
            try:
                await self._mksquashfs(data_dir)
                yield os.fspath(self.squash_file.parent)
            finally:
                self.squash_file.unlink()
                self.squash_file.parent.rmdir()
        else:
            yield data_dir

    async def create_output(self, data_dir: Path):
        self.isofile.unlink(missing_ok=True)

        async with self.__maybe_compress(data_dir) as data_dir:
            await self._mkisofs(data_dir)

        if self.squash_file:
            await self.mount_iso(self.squash_file.parent)
            try:
                extent, = await self.filefrag(self.squash_file)
            finally:
                await self.umount_iso(self.squash_file.parent)
            phy_start, phy_end = map(int, extent.physical_offset.split('..'))
            print('physical_offset', phy_start, phy_end)

    async def _mkisofs(self, *source: str):
        options = shlex.split('-as mkisofs -verbose -iso-level 4 -r -J -joliet-long -no-pad')
        await acall('xorriso', *options, '-V', self.volid, '-o', os.fspath(self.isofile),
                    *source, capture=True, forward=True)

    async def _mksquashfs(self, *source):
        options = shlex.split('-b 1M -nopad -all-root -comp zstd -Xcompression-level 22')
        return await acall('mksquashfs', *source, self.squash_file, *options)

    async def mount_iso(self, mountpoint):
        cmd = shlex.split('sudo -S mount')
        mountpoint.mkdir(exist_ok=True)
        await acall(*cmd, os.fspath(self.isofile), mountpoint, capture=True, stderr=None, binput=self.bpassword)
        return mountpoint

    async def umount_iso(self, mountpoint: Path):
        cmd = shlex.split('sudo -S umount')
        await acall(*cmd, mountpoint, capture=True, stderr=None, binput=self.bpassword)
        mountpoint.rmdir()

    async def filefrag(self, file):
        cmd = shlex.split('sudo -S filefrag -e')
        msg = await acall(*cmd, os.fspath(file), capture=True, stderr=None, binput=self.bpassword)
        fs_type, blocks, kw, *extents, summary = msg.decode().splitlines()

        m = re.search(r"\(([\w\s]+)\)", blocks)
        _, block_size, _ = m.group(1).rsplit(maxsplit=2)
        assert int(block_size) == 2048

        _, n_ext = summary.rsplit(':', maxsplit=1)
        n_ext, _ = n_ext.split(maxsplit=1)
        assert len(extents) == int(n_ext)

        T = namedtuple('file_extents', kw.replace(':', ' '), defaults=(None,))
        return [T(*map(str.strip, s.split(':'))) for s in extents]
