"""The push side of the shared world: one socket per player, one world per place.

online.py already owns who is in which place and refuses impossible motion; this turns its
announcements into WebSocket messages, so a page sees another car move as it happens instead
of asking every half second. Each connection has its own writer thread and queue, so a page
that stops reading loses its own updates and nobody else's.
"""

import json
import queue
import threading

import online
import wsproto

IDLE_PING = 20.0     # a quiet socket gets a ping, so a peer that has gone away is noticed
MAX_QUEUE = 256      # a page this far behind loses cars, not the server


class Client:
    def __init__(self, name, token):
        self.name, self.token = name, token
        self.world, self.label, self.watch = '', '', False
        self.closed = False
        self.out = queue.Queue(maxsize=MAX_QUEUE)
        self.wlock = threading.Lock()

    def send(self, message):
        if self.closed:
            return
        try:
            self.out.put_nowait(message)
        except queue.Full:
            pass


class Hub:
    """Everyone connected, and the one path from online.py's announcements to them."""

    def __init__(self, online):
        self.online = online
        self.lock = threading.Lock()
        self.clients = set()
        online.listen(self.announce)

    # ---- from online.py ------------------------------------------------------------------
    def announce(self, world, event):
        """Queue an event for everyone in that place except the player it is about."""
        text = json.dumps(dict(event, world=world))
        name = event.get('name')
        with self.lock:
            targets = [client for client in self.clients
                       if client.world == world and client.name != name]
        for client in targets:
            client.send(text)

    # ---- one connection ------------------------------------------------------------------
    def serve(self, handler, token):
        """Take this HTTP connection over as a world socket for the account the token names."""
        name = self.online.account(token)
        if name is None:
            handler.send_json({'error': 'Sign in again.'}, 401)
            return
        if not wsproto.handshake(handler):
            handler.send_json({'error': 'This call is a WebSocket.'}, 400)
            return
        client = Client(name, token)
        writer = threading.Thread(target=self._pump, args=(handler, client), daemon=True)
        writer.start()
        with self.lock:
            self.clients.add(client)
        try:
            client.send(json.dumps({'t': 'hello', 'name': name}))
            for text in wsproto.messages(handler.rfile,
                                         reply=lambda data: self._write(handler, client, data)):
                self._message(client, text)
        except (wsproto.Closed, OSError):
            pass
        finally:
            self._drop(handler, client, writer)

    def _drop(self, handler, client, writer):
        with self.lock:
            self.clients.discard(client)
        # Closing the page, or a socket that died, takes the car out of the world at once
        # instead of leaving it parked until the idle timeout.
        try:
            self.online.leave(client.token)
        except Exception as error:
            print('online: could not leave the world (%s)' % error, flush=True)
        try:
            self._write(handler, client, wsproto.frame(b'\x03\xe8', wsproto.CLOSE))
        except OSError:
            pass
        client.closed = True
        try:
            client.out.put_nowait(None)
        except queue.Full:
            pass
        writer.join(1.0)

    def _pump(self, handler, client):
        """Write what the world sends this page, and ping it when nothing is happening."""
        while True:
            try:
                message = client.out.get(timeout=IDLE_PING)
            except queue.Empty:
                if not self._write(handler, client, wsproto.frame(b'', wsproto.PING)):
                    return
                continue
            if message is None:
                return
            if not self._write(handler, client, wsproto.frame(message)):
                return

    @staticmethod
    def _write(handler, client, data):
        try:
            with client.wlock:
                handler.wfile.write(data)
            return True
        except (OSError, ValueError):
            return False

    # ---- messages from the page ----------------------------------------------------------
    def _message(self, client, text):
        try:
            message = json.loads(text)
        except ValueError:
            return self._error(client, 'Send JSON.')
        if not isinstance(message, dict):
            return self._error(client, 'Send a message object.')
        kind = message.get('t')
        if kind == 'join':
            return self._join(client, message)
        if kind == 'pose':
            if not client.world:
                return self._error(client, 'Join a place before driving in it.')
            if client.watch:
                return self._error(client, 'A spectator shares no car.')
            pose = self._pose(message)
            if pose is None:
                return self._error(client, 'A pose is x, y, z and h.')
            result, status = self.online.report(client.token, client.world, pose, client.label)
            if status != 200:
                self._error(client, result.get('error', 'That pose was refused.'))
            return
        if kind == 'leave':
            client.world, client.label, client.watch = '', '', False
            self.online.leave(client.token)
            return
        if kind == 'worlds':
            return self._worlds(client)
        self._error(client, 'Unknown message %r.' % kind)

    def _join(self, client, message):
        """Arrive in a place: as a car if the page knows where it is, else as a spectator."""
        world = online.canonical_world(str(message.get('world') or '')[:32])
        if not world:
            return self._error(client, 'A join names the place.')
        client.world = world
        client.label = str(message.get('label') or '')[:40]
        client.watch = bool(message.get('watch'))
        pose = None if client.watch else self._pose(message.get('pose') or message)
        if pose is None:
            # A page sitting in a menu arrives without a pose and becomes a car with its first one.
            client.watch = True
            result, status = self.online.watch(client.token, world, True, client.label)
        else:
            # A join says what this page is: a driver stops watching, then reports its car.
            self.online.watch(client.token, world, False, client.label)
            result, status = self.online.report(client.token, world, pose, client.label)
        if status != 200:
            client.world = ''
            return self._error(client, result.get('error', 'That join was refused.'))
        client.send(json.dumps(dict(self.online.snapshot(world), t='world')))
        self._worlds(client)

    def _worlds(self, client):
        summary, _ = self.online.world_list()
        client.send(json.dumps({'t': 'worlds', 'worlds': summary['worlds'],
                                'accounts': summary['accounts']}))

    def _error(self, client, text):
        client.send(json.dumps({'t': 'error', 'error': text}))

    @staticmethod
    def _pose(value):
        if not isinstance(value, dict):
            return None
        try:
            return {key: float(value[key]) for key in ('x', 'y', 'z', 'h')}
        except (KeyError, TypeError, ValueError):
            return None
