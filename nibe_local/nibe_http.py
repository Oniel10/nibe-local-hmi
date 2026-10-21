"""Bounded HTTP/1 body reader, including Supervisor's streaming/chunked POST proxy."""
import re


class BodyReader:
    def __init__(self, handler, limit):
        self.source = handler.rfile
        self.limit = limit
        self.done = False
        self.chunk_left = 0
        self.need_crlf = False
        self.announced = 0
        headers = handler.headers
        lengths = headers.get_all('Content-Length', [])
        encodings = headers.get_all('Transfer-Encoding', [])
        if len(lengths) > 1 or len(encodings) > 1 or lengths and encodings:
            raise ValueError('Mehrdeutige Nachrichtenlänge.')
        self.chunked = bool(encodings)
        if self.chunked:
            if encodings[0].strip().lower() != 'chunked':
                raise ValueError('Unbekannte Übertragungsart.')
            self.remaining = None
        else:
            value = lengths[0] if lengths else '0'
            if not re.fullmatch(r'\d{1,12}', value):
                raise ValueError('Ungültige Nachrichtenlänge.')
            self.remaining = int(value)
            if self.remaining > limit:
                raise ValueError('Nachricht zu groß.')

    def exact(self, count):
        value = self.source.read(count)
        if len(value) != count:
            raise ValueError('Nachricht unvollständig.')
        return value

    def read(self, count):
        if not isinstance(count, int) or count < 0:
            raise ValueError('Eine begrenzte Leselänge ist erforderlich.')
        if self.done or not count:
            return b''
        if not self.chunked:
            value = self.exact(min(count, self.remaining))
            self.remaining -= len(value)
            self.done = self.remaining == 0
            return value
        out = bytearray()
        while len(out) < count and not self.done:
            if not self.chunk_left:
                if self.need_crlf and self.exact(2) != b'\r\n':
                    raise ValueError('Ungültige Chunk-Grenze.')
                line = self.source.readline(128)
                if not re.fullmatch(rb'[0-9a-fA-F]{1,8}\r\n', line):
                    raise ValueError('Ungültige Chunk-Länge.')
                size = int(line.strip(), 16)
                self.announced += size
                if self.announced > self.limit:
                    raise ValueError('Nachricht zu groß.')
                if not size:
                    # Supervisor emits no trailers. Reject unexpected framing rather than consuming unbounded headers.
                    if self.exact(2) != b'\r\n':
                        raise ValueError('Nicht unterstützter Nachrichtenanhang.')
                    self.done = True
                    break
                self.chunk_left = size
                self.need_crlf = True
            size = min(count - len(out), self.chunk_left)
            out.extend(self.exact(size))
            self.chunk_left -= size
        return bytes(out)

    def ensure_complete(self):
        if self.read(1):
            raise ValueError('Datei größer als angegeben.')


def form_bytes(handler, limit):
    reader = BodyReader(handler, limit)
    value = reader.read(limit + 1)
    reader.ensure_complete()
    if not value:
        raise ValueError('Leeres Formular.')
    return value
