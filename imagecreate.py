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
    def __init__(self, isofile: os.PathLike, dmid: VolID, _key: Optional[str], bpassword: Optional[bytes] = None):
        self.isofile = Path(isofile)
        self.volid = dmid.get_volid()
        self.bpassword = bpassword
        self.comp_key = _key
        self.length = 0
        self.offset = 0
        self.cipher: Optional[str] = None
        self.sqfs_file = None if _key is None else self.isofile.with_suffix('.rootdir') / f'{dmid.get_dmid()}.sqfs'

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

    async def _maybe_encrypt(self):
        crypt_file = self.sqfs_file.with_suffix('.crypt')
        crypt_file.unlink(missing_ok=True)
        if self.comp_key:
            self.cipher = 'aes-xts-plain64'
            try:
                await _fallocate(crypt_file, os.path.getsize(self.sqfs_file))
                await self._cryptsetup_open(crypt_file)
                crypt_name = '{}_crypt'.format(self.sqfs_file.with_suffix('').name)
                crypt_dev = Path(f'/dev/mapper/{crypt_name}')
                with self.sqfs_file.open('rb') as sqf, crypt_dev.open('r+b') as blk:
                    shutil.copyfileobj(sqf, blk)
                await self._cryptsetup_close()
                crypt_file.replace(self.sqfs_file)
            finally:
                crypt_file.unlink(missing_ok=True)
        else:
            self.cipher = 'cipher_null'

    @asynccontextmanager
    async def _maybe_compress(self, data_dir):
        if self.sqfs_file:
            self.sqfs_file.parent.mkdir(exist_ok=True)
            self.sqfs_file.unlink(missing_ok=True)
            try:
                async with self._maybe_refresh_pw():
                    await self._mksquashfs(data_dir)
                    await self._maybe_encrypt()
                    yield os.fspath(self.sqfs_file.parent)
            finally:
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
            log_start, log_end = map(int, extent.logical_offset.split('..'))
            self.offset, phy_end = map(int, extent.physical_offset.split('..'))
            self.length = int(extent.length)
            assert log_start == 0 and log_end + 1 == self.length == phy_end - self.offset + 1
            print('Physical Offset', self.offset, phy_end)

    async def _mkisofs(self, *source: str):
        options = shlex.split('-as mkisofs -verbose -iso-level 4 -r -J -joliet-long -no-pad')
        await acall('xorriso', *options, '-V', self.volid, '-o', os.fspath(self.isofile),
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

    async def _cryptsetup_open(self, file):
        cmd = shlex.split('sudo -S -E sh -c')
        crypt_name = '{}_crypt'.format(self.sqfs_file.with_suffix('').name)
        options = shlex.split('cryptsetup open --type plain --hash sha512 --key-size 512 --key-file=- --cipher')
        options += (self.cipher, os.fspath(file), crypt_name)
        shell_cmd = 'echo -n "$_COMP_KEY" | ' + shlex.join(options)
        env = dict(os.environ, _COMP_KEY=self.comp_key)
        msg = await acall(*cmd, shell_cmd, capture=True, binput=self.bpassword, env=env)
        cmd = shlex.split('sudo -S chown')
        await acall(*cmd, getuser(), f'/dev/mapper/{crypt_name}', capture=True, binput=self.bpassword)
        return msg

    async def _cryptsetup_close(self):
        cmd = shlex.split('sudo -S cryptsetup close')
        crypt_name = '{}_crypt'.format(self.sqfs_file.with_suffix('').name)
        return await acall(*cmd, crypt_name, capture=True, binput=self.bpassword)
