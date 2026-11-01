#!/usr/bin/env python3
"""Serves a web build and a disc image with HTTP range requests.

usage: serve.py <build dir> <disc image> [port] [--host address] [--record <dir for unrecorded VU1 programs>]

It also relays the messages that connect a host page to the guests it invites
(/room), so a friend can play along from another browser, and runs the online side
(/online): accounts, the memory card each account saves to, and the shared worlds whose
players' cars the pages exchange. /online/socket is the same world pushed over a WebSocket
instead of polled, so a page sees another car move as it happens.
"""

import http.server
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import urllib.parse

import online
import wsserver

RECORD = None
if '--record' in sys.argv:
    at = sys.argv.index('--record')
    RECORD = os.path.abspath(sys.argv[at + 1])
    del sys.argv[at:at + 2]
HOST = '127.0.0.1'
if '--host' in sys.argv:
    at = sys.argv.index('--host')
    HOST = sys.argv[at + 1]
    del sys.argv[at:at + 2]
DATA = None
if '--data' in sys.argv:
    at = sys.argv.index('--data')
    DATA = os.path.abspath(sys.argv[at + 1])
    del sys.argv[at:at + 2]
# A saved game state the page can boot from as the Online Adventure start (host/web.cpp loads
# ?state=<url> through ps2web_url before the game runs). Served at /online/start.
START_STATE = None
if '--start-state' in sys.argv:
    at = sys.argv.index('--start-state')
    START_STATE = os.path.abspath(sys.argv[at + 1])
    del sys.argv[at:at + 2]
BUILD = os.path.abspath(sys.argv[1])
DISC = os.path.abspath(sys.argv[2])
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 8620
ONLINE = online.Online(DATA or os.path.join(os.path.dirname(BUILD), 'online'))
HUB = wsserver.Hub(ONLINE)
ROOMS = {}
ROOMS_CHANGED = threading.Condition()
ROOM_PATH = re.compile(r'^/room(?:/([A-Z2-9]{6})/([a-z0-9]{1,16}))?$')
ROOM_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
ROOM_IDLE = 3600
TYPES = {'.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm', '.data': 'application/octet-stream'}


class Handler(http.server.BaseHTTPRequestHandler):
    def target(self):
        path = self.path.split('?', 1)[0]
        if path in ('/', '/index.html'):
            path = '/ps2web.html'
        if path == '/disc.iso':
            return DISC
        if path.endswith('/'):
            path += 'index.html'
        name = os.path.normpath(path.lstrip('/'))
        if name.startswith('..') or name == '.':
            return None
        full = os.path.join(BUILD, name)
        return full if os.path.isfile(full) else None

    def headers_for(self, path, length, encoding=None):
        self.send_header('Content-Type', TYPES.get(os.path.splitext(path)[1], 'application/octet-stream'))
        if encoding:
            self.send_header('Content-Encoding', encoding)
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.end_headers()

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def room_get(self, code, me):
        match = re.search(r'after=(\d+)', self.path)
        after = int(match.group(1)) if match else 0
        deadline = time.time() + 20
        with ROOMS_CHANGED:
            while True:
                room = ROOMS.get(code)
                if room is None:
                    self.send_json({'error': 'no such room'}, 404)
                    return
                room['seen'] = time.time()
                inbox = room['inboxes'].setdefault(me, [])
                left = deadline - time.time()
                if len(inbox) > after or left <= 0:
                    messages = inbox[after:]
                    break
                ROOMS_CHANGED.wait(left)
        self.send_json({'messages': messages, 'next': after + len(messages)})

    def online_request(self, post):
        """One /online/ call: accounts, cloud cards, worlds."""
        parsed = urllib.parse.urlsplit(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        action = parsed.path[len('/online/'):]
        token = query.get('token', '')
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length) if post and length else b''
        if action == 'card' and length > 16 << 20:
            self.send_json({'error': 'That upload is too large.'}, 413)
            return
        if action == 'capture' and length > 128 << 20:
            self.send_json({'error': 'That state is too large.'}, 413)
            return
        if action == 'story':
            # The authored campaign, re-read on every request so a story edit shows up on the next
            # page refresh instead of needing a rebuild.
            path_ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stories', 'online-adventure.json')
            try:
                with open(path_, 'rb') as f:
                    pack = json.loads(f.read())
            except OSError:
                self.send_json({'error': 'No story pack on this server.'}, 404)
                return
            except ValueError:
                self.send_json({'error': 'The story pack is not valid JSON.'}, 500)
                return
            name = ONLINE.account(token)
            if name:
                # This account's chapter and objective progress, proved from what the relay saw.
                ONLINE.advance_story(name)
                pack['state'] = ONLINE.story_state(name)
            body = json.dumps(pack).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if action == 'capture' and post:
            result = ONLINE.capture_start(token, body)
        elif action == 'leave' and post:
            result = ONLINE.leave(token)
        elif action in ('profile', 'mission') and post:
            try:
                fields = json.loads(body or b'{}')
            except ValueError:
                self.send_json({'error': 'Send the Online Adventure request as JSON.'}, 400)
                return
            if not isinstance(fields, dict):
                self.send_json({'error': 'Send the Online Adventure request as JSON.'}, 400)
                return
            if action == 'profile':
                result = ONLINE.profile_create(token, fields.get('country'), fields.get('motto', ''),
                                               fields.get('banner', '#e0a43b'))
            else:
                result = ONLINE.mission_action(token, fields.get('id'), fields.get('action'))
        elif action == 'profile':
            result = ONLINE.profile_get(token)
        elif action == 'mission':
            result = ONLINE.mission_state(token)
        elif action in ('signup', 'login', 'logout', 'me', 'card') and post:
            if action == 'card':
                result = ONLINE.card_put(token, body)
            else:
                try:
                    fields = json.loads(body or b'{}')
                except ValueError:
                    self.send_json({'error': 'Send a name and a password as JSON.'}, 400)
                    return
                if not isinstance(fields, dict):
                    self.send_json({'error': 'Send a name and a password as JSON.'}, 400)
                    return
                if action == 'signup':
                    result = ONLINE.signup(fields.get('name'), fields.get('password'))
                elif action == 'login':
                    result = ONLINE.login(fields.get('name'), fields.get('password'))
                else:
                    result = ONLINE.logout(token)
        elif action == 'card':
            card, status = ONLINE.card_get(token)
            if card is None:
                self.send_json({'error': 'No cloud save yet.' if status == 404 else 'Sign in again.'}, status)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(card)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(card)
            return
        elif action == 'pose' and post:
            try:
                fields = json.loads(body or b'{}')
                pose = fields.get('pose') if isinstance(fields, dict) else None
            except ValueError:
                pose = None
            if not isinstance(pose, dict) and pose is not None:
                self.send_json({'error': 'Send a pose as {x, y, z, h}.'}, 400)
                return
            result = ONLINE.report(token, query.get('world'), pose, query.get('label', ''))
        elif action == 'talk' and post:
            try:
                fields = json.loads(body or b'{}')
            except ValueError:
                fields = None
            if not isinstance(fields, dict):
                self.send_json({'error': 'Send the speaker you talked to as JSON.'}, 400)
                return
            result = ONLINE.note_talk(token, fields.get('world'), fields.get('npc', ''))
        elif action == 'world':
            result = ONLINE.world(token, query.get('world'), query.get('watch') == '1')
        elif action == 'race' and post:
            try:
                fields = json.loads(body or b'{}')
            except ValueError:
                fields = None
            if not isinstance(fields, dict):
                self.send_json({'error': 'Send a race action as JSON.'}, 400)
                return
            result = ONLINE.race(token, query.get('world'), fields.get('action', 'state'),
                                 fields.get('seconds'))
        elif action == 'race':
            result = ONLINE.race(token, query.get('world'))
        elif action in ('friends', 'crew') and post:
            try:
                fields = json.loads(body or b'{}')
            except ValueError:
                fields = None
            if not isinstance(fields, dict):
                self.send_json({'error': 'Send the social request as JSON.'}, 400)
                return
            if action == 'friends':
                result = ONLINE.friend_action(token, fields.get('action'), fields.get('name'))
            else:
                result = ONLINE.crew_action(token, fields.get('action'), fields.get('name', ''),
                                            fields.get('tag', ''), fields.get('motto', ''))
        elif action == 'friends':
            result = ONLINE.friends(token)
        elif action == 'crew':
            result = ONLINE.crew(token)
        elif action == 'worlds':
            result = ONLINE.world_list()
        elif action == 'me':
            result = ONLINE.me(token)
        else:
            self.send_json({'error': 'no such call'}, 404)
            return
        self.send_json(*result)

    def do_POST(self):
        if self.path.split('?', 1)[0].startswith('/online/'):
            self.online_request(True)
            return
        match = ROOM_PATH.match(self.path.split('?', 1)[0])
        length = int(self.headers.get('Content-Length', '0'))
        if not match or length > 65536:
            self.send_error(404 if not match else 413)
            return
        body = self.rfile.read(length)
        code, to = match.groups()
        with ROOMS_CHANGED:
            now = time.time()
            for old in [c for c, r in ROOMS.items() if now - r['seen'] > ROOM_IDLE]:
                del ROOMS[old]
            if not code:
                code = ''.join(secrets.choice(ROOM_LETTERS) for _ in range(6))
                while code in ROOMS:
                    code = ''.join(secrets.choice(ROOM_LETTERS) for _ in range(6))
                ROOMS[code] = {'inboxes': {}, 'seen': now}
                result = {'room': code}
            elif code in ROOMS:
                try:
                    message = json.loads(body)
                except ValueError:
                    message = None
                if not isinstance(message, dict):
                    self.send_json({'error': 'bad message'}, 400)
                    return
                ROOMS[code]['inboxes'].setdefault(to, []).append(message)
                ROOMS[code]['seen'] = now
                ROOMS_CHANGED.notify_all()
                result = {}
            else:
                self.send_json({'error': 'no such room'}, 404)
                return
        self.send_json(result)

    def do_HEAD(self):
        path = self.target()
        if not path:
            self.send_error(404)
            return
        self.send_response(200)
        self.headers_for(path, os.path.getsize(path))

    def online_socket(self):
        """One player's live view of the place their car is in (scripts/wsserver.py)."""
        parsed = urllib.parse.urlsplit(self.path)
        token = (urllib.parse.parse_qs(parsed.query).get('token') or [''])[0]
        try:
            HUB.serve(self, token)
        except Exception as error:  # one broken socket must not take the server with it
            print('online: socket closed (%s)' % error, flush=True)

    def online_start_file(self):
        """The state an Online Adventure boots from: one the page captured wins over the flag."""
        captured = os.path.join(ONLINE.data_dir, 'start.state')
        if os.path.isfile(captured):
            return captured
        return START_STATE if START_STATE and os.path.isfile(START_STATE) else None

    def online_start(self, head):
        """The state an Online Adventure boots from, or 404 when the server has none."""
        if not self.online_start_file():
            self.send_json({'error': 'No online start state is configured.'}, 404)
            return
        size = os.path.getsize(self.online_start_file())
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if head:
            return
        with open(self.online_start_file(), 'rb') as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def do_HEAD(self):
        # The page asks for the disc image with HEAD to learn its length (Module.discSize, which the
        # runtime needs before it can read a sector), so a HEAD has to answer like the GET would
        # without the body. Anything else here used to be 501, which made the disc look like zero
        # bytes and left the player at a black screen.
        path = self.path.split('?', 1)[0]
        if path == '/online/start':
            self.online_start(True)
            return
        if path.startswith('/online/'):
            self.send_error(405)
            return
        target = self.target()
        if not target or not os.path.isfile(target):
            # A disc path that does not exist has to answer 404: raising here used to drop the
            # connection, which the page saw as an empty response and a disc of zero bytes.
            self.send_error(404)
            return
        self.send_response(200)
        self.headers_for(target, os.path.getsize(target))
        self.end_headers()

    def do_GET(self):
        if self.path.split('?', 1)[0] == '/online/start':
            self.online_start(False)
            return
        if self.path.split('?', 1)[0] == '/online/socket':
            self.online_socket()
            return
        if self.path.split('?', 1)[0].startswith('/online/'):
            self.online_request(False)
            return
        match = ROOM_PATH.match(self.path.split('?', 1)[0])
        if match and match.group(1):
            self.room_get(*match.groups())
            return
        path = self.target()
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return
        packed = path + '.gz'
        if ('Range' not in self.headers and 'gzip' in self.headers.get('Accept-Encoding', '') and os.path.isfile(packed)
                and os.path.getmtime(packed) >= os.path.getmtime(path)):
            self.send_response(200)
            self.headers_for(path, os.path.getsize(packed), 'gzip')
            with open(packed, 'rb') as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            return
        size = os.path.getsize(path)
        match = re.match(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
        start, end = 0, size - 1
        if match:
            start = int(match.group(1))
            end = min(int(match.group(2)) if match.group(2) else size - 1, size - 1)
            self.send_response(206)
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        else:
            self.send_response(200)
        self.headers_for(path, end - start + 1)
        with open(path, 'rb') as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(left, 1 << 20))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_PUT(self):
        match = re.match(r'^/vu1/([0-9A-F]{16}_[0-9A-F]{4}\.bin)$', self.path.split('?', 1)[0])
        length = int(self.headers.get('Content-Length', '0'))
        if not RECORD or not match or length != 16384:
            self.send_error(501 if not RECORD else 400)
            return
        data = self.rfile.read(length)
        target = os.path.join(RECORD, match.group(1))
        if not os.path.exists(target):
            os.makedirs(RECORD, exist_ok=True)
            with open(target, 'wb') as f:
                f.write(data)
            print('recorded', match.group(1), flush=True)
        self.send_response(201)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


print('serving %s and %s on http://%s:%d/' % (BUILD, DISC, HOST, PORT))
print('online data in %s' % ONLINE.data_dir)
if START_STATE:
    print('online start state %s' % START_STATE)
http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
