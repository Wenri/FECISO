import os
import sys
from subprocess import run, DEVNULL

fd = os.memfd_create("$DMID")
with os.fdopen(fd, 'w+b', closefd=False) as memf, open('opt.devstr', 'rb') as isof:
    ret = os.sendfile(memf.fileno(), isof.fileno(), '$ISO_SZ', '$HASH_SZ')
assert '$HASH_SZ' == ret
args = ['losetup', '-r', '--show', '-b', '2048', '-f', f'/dev/fd/{fd}']
sys.exit(run(args, stdin=DEVNULL, pass_fds=(fd,)).returncode)
