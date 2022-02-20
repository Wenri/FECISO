import hashlib
import os
import sys
from getpass import getpass

x = 1000
h = hashlib.new('sm3')
h.update('crypt_name'.encode())
h.update('self.cipher'.encode())
h.update(x.to_bytes((x.bit_length() + 7) // 8, byteorder='little'))
x = getpass('Input your password: ')
x = hashlib.scrypt(x.encode(), salt=h.digest(), n=2 ** 20, r=8, p=1, maxmem=2 ** 31 - 1, dklen=64)
os.write(sys.stdout.fileno(), x)
