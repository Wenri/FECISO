import os
import shlex
from io import StringIO
from pathlib import Path


class BootSh:
    _HERE_DOC = ': <<-'
    _SEP_DOC = ': <<_'

    def __init__(self, **kwargs):
        bootsh = Path(__file__).with_name('boot.sh')
        with bootsh.open('r') as tmpl:
            self.header, replace_str, kwargs = self._build_header(tmpl, **kwargs)
            self.body = self._build_body(tmpl, replace_str, **kwargs)

    def _build_header(self, tmpl, **kwargs):
        replace_str = None
        with StringIO() as strf:
            for s in tmpl:
                if replace_str:
                    if s == replace_str:
                        replace_str = None
                    else:
                        k, *v = s.split('=', maxsplit=1)
                        assert v
                        v = kwargs.pop(k := k.strip())
                        v = shlex.quote(str(v)) if v is not None else ''
                        print(f'{k}={v}', file=strf)
                elif s.startswith(self._HERE_DOC):
                    replace_str = s[len(self._HERE_DOC):]
                else:
                    strf.write(s)
                    if s.startswith(self._SEP_DOC):
                        replace_str = s[len(self._SEP_DOC) - 1:]
                        break
            return strf.getvalue(), replace_str, kwargs

    def _build_body(self, tmpl, replace_str, **kwargs):
        with StringIO() as strf:
            for s in tmpl:
                if replace_str:
                    if s == replace_str:
                        strf.write(os.linesep)
                        strf.write(s)
                        for k, v in kwargs.items():
                            v = shlex.quote(str(v)) if v is not None else ''
                            print(f'{k.strip()}={v}', file=strf)
                        replace_str = None
                else:
                    strf.write(s)
            return strf.getvalue()

    def get_header_bytes(self):
        b = self.header.encode()
        assert len(b) <= 218
        return b

    def get_body_bytes(self):
        b = self.body.encode()
        assert len(b) <= 0x8000 - 512
        return b
