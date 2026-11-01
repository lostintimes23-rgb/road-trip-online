"""Just enough WebSocket (RFC 6455) to push a shared world to the page.

The standard library has no WebSocket server, and the relay does not need one: serve.py's
HTTP handler takes its connection over after the upgrade and reads and writes frames here.
Only what a relay uses is implemented - text messages, ping/pong, close and fragments.
"""

import base64
import hashlib
import os
import struct

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_MESSAGE = 1 << 16     # a pose is under a hundred bytes; anything near this is not the page
CONT, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class Closed(Exception):
    """The peer is gone: the frame stream ended, or it sent something that ends it."""


def accept_key(key):
    """The Sec-WebSocket-Accept value for a client's key."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode('utf-8')).digest()).decode('ascii')


def handshake(handler):
    """Answer an upgrade request on this handler, or return False if it is not one."""
    key = handler.headers.get('Sec-WebSocket-Key', '')
    if not key or 'websocket' not in handler.headers.get('Upgrade', '').lower():
        return False
    handler.wfile.write(
        b'HTTP/1.1 101 Switching Protocols\r\n'
        b'Upgrade: websocket\r\n'
        b'Connection: Upgrade\r\n'
        b'Sec-WebSocket-Accept: ' + accept_key(key).encode('ascii') + b'\r\n\r\n')
    handler.close_connection = True
    return True


def frame(payload, opcode=TEXT, mask=False):
    """One complete frame. A client masks what it sends; the server does not."""
    data = payload.encode('utf-8') if isinstance(payload, str) else bytes(payload)
    length = len(data)
    head = bytearray([0x80 | opcode])
    flag = 0x80 if mask else 0x00
    if length < 126:
        head.append(flag | length)
    elif length < 65536:
        head.append(flag | 126)
        head += struct.pack('!H', length)
    else:
        head.append(flag | 127)
        head += struct.pack('!Q', length)
    if not mask:
        return bytes(head) + data
    key = os.urandom(4)
    return bytes(head) + key + bytes(b ^ key[i & 3] for i, b in enumerate(data))


def _exact(stream, count):
    data = b''
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            raise Closed('the stream ended')
        data += chunk
    return data


def read(stream, client=True):
    """(last, opcode, payload) of the next frame; `client` is whether it came from a client."""
    head = _exact(stream, 2)
    last, opcode = bool(head[0] & 0x80), head[0] & 0x0F
    masked, length = head[1] & 0x80, head[1] & 0x7F
    if length == 126:
        length = struct.unpack('!H', _exact(stream, 2))[0]
    elif length == 127:
        length = struct.unpack('!Q', _exact(stream, 8))[0]
    if length > MAX_MESSAGE:
        raise Closed('a message that long is not one of ours')
    if client and not masked:
        raise Closed('a client must mask what it sends')
    key = _exact(stream, 4) if masked else b''
    data = _exact(stream, length) if length else b''
    if masked:
        data = bytes(b ^ key[i & 3] for i, b in enumerate(data))
    return last, opcode, data


def messages(stream, reply=None, client=True):
    """Yield text messages until the peer closes; answers pings through `reply` if given."""
    parts, kind = [], None
    while True:
        last, opcode, data = read(stream, client)
        if opcode == PING:
            if reply:
                reply(frame(data, PONG))
            continue
        if opcode == PONG:
            continue
        if opcode == CLOSE:
            if reply:
                reply(frame(data[:2], CLOSE))
            raise Closed('the peer closed')
        if opcode == CONT:
            if kind is None:
                raise Closed('a continuation with nothing to continue')
        elif opcode in (TEXT, BINARY):
            if kind is not None:
                raise Closed('a new message before the last one ended')
            kind = opcode
        else:
            raise Closed('unknown opcode %d' % opcode)
        parts.append(data)
        if sum(len(part) for part in parts) > MAX_MESSAGE:
            raise Closed('a message that long is not one of ours')
        if last:
            data = b''.join(parts)
            if kind == TEXT:
                yield data.decode('utf-8', 'replace')
            parts, kind = [], None
