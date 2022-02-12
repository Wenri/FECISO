from io import StringIO
from pathlib import Path


class BootSh:
    _HERE_DOC = ': <<-'
    __SEP_DOC = ': <<_'

    def __init__(self, **kwargs):
        bootsh = Path(__file__)
        with bootsh.with_suffix('.sh').open('r') as tmpl:
            self.header = self._build_header(tmpl, **kwargs)
            self.body = tmpl.read()

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
                        print(f'{k}={kwargs[k]}', file=strf)
                elif s.startswith(self._HERE_DOC):
                    replace_str = s[len(self._HERE_DOC):]
                else:
                    strf.write(s)
                    if s.startswith(self.__SEP_DOC):
                        break
            return strf.getvalue()

    def get_header_bytes(self):
        b = self.header.encode()
        assert len(b) <= 218
        return b

    def get_body_bytes(self):
        b = self.body.encode()
        assert len(b) <= 0x8000 - 512
        return b
