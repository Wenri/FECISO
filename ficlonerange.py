import os
import struct
from fcntl import ioctl

FICLONERANGE = 0x4020940d


def struct_file_clone_range(src_fd, src_offset, src_length, dst_offset):
    return struct.pack("qQQQ", src_fd, src_offset, src_length, dst_offset)


def file_clone_range(src_fd, dst_fd, s=0, length=0, d=None):
    return ioctl(
        dst_fd, FICLONERANGE,
        struct_file_clone_range(src_fd, s, length, d if d is not None else os.fstat(dst_fd).st_size)
    )
