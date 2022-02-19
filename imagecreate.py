import asyncio
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import namedtuple
from contextlib import asynccontextmanager
from getpass import getuser
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


async def _refresh_rootpassword(refresh_done, refresh_intvl=10):
    _, aws = await asyncio.wait({refresh_done.wait()}, timeout=refresh_intvl)
    while aws and not refresh_done.is_set():
        await acall('sudo', '-S', '-v', capture=True)
        _, aws = await asyncio.wait(aws, timeout=refresh_intvl)


async def _fallocate(file, size):
    cmd = shlex.split('fallocate -x -l')
    return await acall(*cmd, str(size), os.fspath(file), capture=True)


class ImageCreate:
    def __init__(self, isofile: os.PathLike, dmid: VolID, comp_key: Optional[str], bpassword: Optional[bytes] = None):
        self.isofile = Path(isofile)
        self.dmid = dmid
        self.bpassword = bpassword
        self.comp_key = comp_key
        self.sqfs_file: Optional[Path] = None
        if comp_key:
            squash_dir = self.isofile.with_suffix('.rootdir')
            self.sqfs_file = squash_dir / f'{self.dmid.get_dmid()}.sqfs'

    @asynccontextmanager
    async def _maybe_refresh_pw(self):
        if self.bpassword:
            yield
        else:
            refresh_done = asyncio.Event()
            task = asyncio.create_task(_refresh_rootpassword(refresh_done))
            yield
            refresh_done.set()
            await task

    @asynccontextmanager
    async def _maybe_compress(self, data_dir):
        if self.sqfs_file:
            crypt_file = self.sqfs_file.with_suffix('.crypt')
            self.sqfs_file.parent.mkdir(exist_ok=True)
            self.sqfs_file.unlink(missing_ok=True)
            crypt_file.unlink(missing_ok=True)
            try:
                async with self._maybe_refresh_pw():
                    await self._mksquashfs(data_dir)
                    await _fallocate(crypt_file, os.path.getsize(self.sqfs_file))
                    await self._cryptsetup_open(crypt_file)
                    crypt_dev = Path(f'/dev/mapper/{self.dmid.get_dmid()}_crypt')
                    with self.sqfs_file.open('rb') as sqf, crypt_dev.open('r+b') as blk:
                        shutil.copyfileobj(sqf, blk)
                    await self._cryptsetup_close()
                    crypt_file.replace(self.sqfs_file)
                    yield os.fspath(self.sqfs_file.parent)
            finally:
                crypt_file.unlink(missing_ok=True)
                self.sqfs_file.unlink(missing_ok=True)
                self.sqfs_file.parent.rmdir()
        else:
            yield data_dir

    async def create_output(self, data_dir: Path):
        self.isofile.unlink(missing_ok=True)

        async with self._maybe_compress(data_dir) as data_dir:
            await self._mkisofs(data_dir)

        if self.sqfs_file:
            await self._mount_iso(self.sqfs_file.parent)
            try:
                extent, = await self._filefrag(self.sqfs_file)
            finally:
                await self._umount_iso(self.sqfs_file.parent)
            phy_start, phy_end = map(int, extent.physical_offset.split('..'))
            print('physical_offset', phy_start, phy_end)

    async def _mkisofs(self, *source: str):
        options = shlex.split('-as mkisofs -verbose -iso-level 4 -r -J -joliet-long -no-pad')
        await acall('xorriso', *options, '-V', self.dmid.get_volid(), '-o', os.fspath(self.isofile),
                    *source, capture=True, forward=True)

    async def _mksquashfs(self, *source):
        options = shlex.split('-b 1M -all-root -comp zstd -Xcompression-level 22')
        return await acall('mksquashfs', *source, self.sqfs_file, *options)

    async def _mount_iso(self, mountpoint):
        cmd = shlex.split('sudo -S mount')
        mountpoint.mkdir(exist_ok=True)
        await acall(*cmd, os.fspath(self.isofile), mountpoint, capture=True, binput=self.bpassword)
        return mountpoint

    async def _umount_iso(self, mountpoint: Path):
        cmd = shlex.split('sudo -S umount')
        await acall(*cmd, mountpoint, capture=True, binput=self.bpassword)
        mountpoint.rmdir()

    async def _filefrag(self, file):
        cmd = shlex.split('sudo -S filefrag -e')
        msg = await acall(*cmd, os.fspath(file), capture=True, binput=self.bpassword)
        fs_type, blocks, kw, *extents, summary = msg.decode().splitlines()

        m = re.search(r"\(([\w\s]+)\)", blocks)
        _, block_size, _ = m.group(1).rsplit(maxsplit=2)
        assert int(block_size) == 2048

        _, n_ext = summary.rsplit(':', maxsplit=1)
        n_ext, _ = n_ext.split(maxsplit=1)
        assert len(extents) == int(n_ext)

        T = namedtuple('file_extents', kw.replace(':', ' '), defaults=(None,))
        return [T(*map(str.strip, s.split(':'))) for s in extents]

    async def _cryptsetup_open(self, file, cipher='aes-xts-plain64'):
        cmd = shlex.split('sudo -S -E sh -c')
        options = shlex.split('cryptsetup open --type plain --hash sha512 --key-size 512 --key-file=- --cipher')
        options += (cipher, os.fspath(file), f'{self.dmid.get_dmid()}_crypt')
        shell_cmd = 'echo -n "$_COMP_KEY" | ' + shlex.join(options)
        env = dict(os.environ, _COMP_KEY=self.comp_key)
        msg = await acall(*cmd, shell_cmd, capture=True, binput=self.bpassword, env=env)
        cmd = shlex.split('sudo -S chown')
        await acall(*cmd, getuser(), f'/dev/mapper/{self.dmid.get_dmid()}_crypt', capture=True, binput=self.bpassword)
        return msg

    async def _cryptsetup_close(self):
        cmd = shlex.split('sudo -S cryptsetup close')
        return await acall(*cmd, f'{self.dmid.get_dmid()}_crypt', capture=True, binput=self.bpassword)
