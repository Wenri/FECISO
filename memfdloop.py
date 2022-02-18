#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path
from subprocess import run, DEVNULL


def _hs(ds, superblock=True, _hash_div=128):
    h = int(superblock)
    while ds:
        ds, rem = divmod(ds, _hash_div)
        h += ds + 1
    return h


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('iso_s', type=int)
    p.add_argument('devstr', type=Path)
    p.add_argument('dmid', type=str)
    return p.parse_args()


def main(opt):
    sector_size = 2048
    total_size = _hs(opt.iso_s // sector_size) * sector_size
    fd = os.memfd_create(opt.dmid)
    with os.fdopen(fd, 'w+b', closefd=False) as memf, open(opt.devstr, 'rb') as isof:
        ret = os.sendfile(memf.fileno(), isof.fileno(), opt.iso_s, total_size)
    assert total_size == ret
    args = ['losetup', '-r', '--show', '-b', '2048', '-f', f'/dev/fd/{fd}']
    return run(args, stdin=DEVNULL, pass_fds=(fd,)).returncode


if __name__ == '__main__':
    sys.exit(main(parse_args()))
