#!/usr/bin/env python3

import argparse
import asyncio
import subprocess
import sys
from getpass import getpass
from io import StringIO
from pathlib import Path

from beartype import beartype

from capacity import VolID, DiscID, PassHint
from fecsetup import FECSetup
from imagecreate import ImageCreate, acall


@beartype
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('data_dir', type=Path, help='data environment')
    parser.add_argument('-o', '--output', type=Path, required=True, help='output iso file')
    parser.add_argument('-V', '--volid', type=VolID, required=True, help='volume label')
    parser.add_argument('-C', '--compress', type=str, help='compress and encrypt data')
    parser.add_argument('-d', '--disc', type=DiscID, help='disc id')
    parser.add_argument('--hint', type=PassHint, help='password hint')
    parser.add_argument('--save_disc', action='store_true', help='save disc id')
    return parser.parse_args()


async def check_rootpassword(root_password=None):
    while True:
        try:
            await acall('sudo', '-S', '-v', capture=True, binput=root_password)
            break
        except subprocess.CalledProcessError:
            pass
        with StringIO() as buf:
            print(getpass('We need root password to mount ISO file: '), file=buf, flush=True)
            root_password = buf.getvalue().encode()
    return root_password


async def main(opt: argparse.Namespace) -> int:
    root_password = await check_rootpassword() if opt.compress else None
    img = ImageCreate(opt.output, dmid=opt.volid, _key=opt.compress, bpassword=root_password, disc=opt.disc)
    await img.create_output(opt.data_dir)
    if opt.hint is None and opt.compress is not None:
        opt.hint = PassHint()
    kwargs = {}
    if opt.save_disc or opt.disc != img.disc:
        kwargs['_DISC_ID'] = img.disc
    if opt.hint is not None:
        kwargs['_HINT'] = opt.hint
    img = FECSetup(opt.output, dmid=opt.volid, offset=img.offset, length=img.length, cipher=img.cipher, **kwargs)
    ret = await img.formatfec()
    return ret


if __name__ == '__main__':
    sys.exit(asyncio.run(main(parse_args())))
