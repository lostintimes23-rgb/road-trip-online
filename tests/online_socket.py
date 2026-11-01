#!/usr/bin/env python3
"""Regression: the world is pushed over a socket, and the server decides what is possible.

Starts scripts/serve.py on a temporary data dir, signs two accounts up, and talks to
/online/socket the way the page does: a car in a place is relayed to the other drivers there as
it moves (no polling), another place hears nothing, a spectating page is counted and never
relayed, a jump no car can drive is refused until it has held, and closing a socket takes the
car out of the world at once. Needs no disc, build or browser.
"""

import base64
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import wsproto

POSE = {'x': 504.8, 'y': 30.2, 'z': 469.9, 'h': 0.3}


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def post_json(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def signup(port, name):
    return post_json('http://127.0.0.1:%d/online/signup' % port,
                     {'name': name, 'password': 'passw0rd'})['token']


class Page:
    """One page's socket: the frames a browser sends, and a queue of what it hears."""

    def __init__(self, port, token):
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        self.sock.sendall(
            ('GET /online/socket?token=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n'
             'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
             'Sec-WebSocket-Version: 13\r\n\r\n' % (urllib.parse.quote(token), port, key)).encode())
        self.stream = self.sock.makefile('rb')
        status = self.stream.readline().decode('latin-1')
        headers = {}
        while True:
            line = self.stream.readline()
            if line in (b'\r\n', b''):
                break
            name, _, value = line.decode('latin-1').partition(':')
            headers[name.strip().lower()] = value.strip()
        assert '101' in status, (status, headers)
        assert headers.get('sec-websocket-accept') == wsproto.accept_key(key), headers
        self.inbox = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for text in wsproto.messages(self.stream, reply=self.sock.sendall, client=False):
                self.inbox.put(json.loads(text))
        except (wsproto.Closed, OSError):
            pass

    def send(self, message):
        self.sock.sendall(wsproto.frame(json.dumps(message), mask=True))

    def next(self, timeout=3.0):
        return self.inbox.get(timeout=timeout)

    def wait(self, predicate, what, timeout=3.0):
        """The next message that matches, quietly dropping the ones that do not."""
        end, seen = time.time() + timeout, []
        while True:
            left = end - time.time()
            if left <= 0:
                raise AssertionError('no %s; last messages %r' % (what, seen[-6:]))
            try:
                message = self.inbox.get(timeout=left)
            except queue.Empty:
                raise AssertionError('no %s; last messages %r' % (what, seen[-6:]))
            seen.append(message)
            if predicate(message):
                return message

    def quiet(self, seconds, forbid, what):
        """Nothing matching `forbid` arrives in this time."""
        end = time.time() + seconds
        while True:
            left = end - time.time()
            if left <= 0:
                return
            try:
                message = self.inbox.get(timeout=left)
            except queue.Empty:
                return
            assert not forbid(message), 'unexpected %s: %r' % (what, message)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


def names(body):
    return sorted(player['name'] for player in body['players'])


def pose(**changes):
    return dict(POSE, **changes)


def main():
    port = free_port()
    with tempfile.TemporaryDirectory(prefix='ps2-socket-test-') as temp:
        build = Path(temp, 'build')
        build.mkdir()
        Path(build, 'ps2web.html').write_text('<!doctype html><title>stub</title>')
        disc = Path(temp, 'disc.iso')
        disc.write_bytes(b'\0' * 16)
        log = open(Path(temp, 'server.log'), 'w+', encoding='utf-8')
        server = subprocess.Popen(
            [sys.executable, str(ROOT / 'scripts' / 'serve.py'), str(build), str(disc), str(port),
             '--data', str(Path(temp, 'data'))],
            stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%d/online/worlds' % port, timeout=1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError('the server did not start')
            run(port)
        except Exception:
            log.flush()
            log.seek(0)
            print(log.read()[-2000:])
            raise
        finally:
            server.terminate()
            server.wait(timeout=5)
            log.close()


def run(port):
    alice = Page(port, signup(port, 'alice'))
    bob = Page(port, signup(port, 'bob'))
    cara = Page(port, signup(port, 'cara'))
    for page in (alice, bob, cara):
        assert page.wait(lambda m: m['t'] == 'hello', 'hello')['name'] in ('alice', 'bob', 'cara')

    # A car joins a place and gets that place's roster back.
    alice.send({'t': 'join', 'world': 'town:1', 'label': 'Peach Town', 'pose': POSE})
    roster = alice.wait(lambda m: m['t'] == 'world' and m['world'] == 'town:1', 'the roster')
    assert names(roster) == ['alice'] and roster['label'] == 'Peach Town', roster

    # A second car in the same place is relayed at once, not on the next poll.
    bob.send({'t': 'join', 'world': 'town:1', 'label': 'Peach Town', 'pose': pose(x=520.0)})
    roster = bob.wait(lambda m: m['t'] == 'world' and m['world'] == 'town:1', 'the roster')
    assert names(roster) == ['alice', 'bob'], roster
    seen = alice.wait(lambda m: m['t'] == 'pose' and m['name'] == 'bob', "bob's car")
    assert seen['world'] == 'town:1' and seen['x'] == 520.0, seen

    # Driving is pushed as it happens: ten updates a second reach the other car.
    started = time.time()
    for step in range(5):
        bob.send({'t': 'pose', 'x': 520.0 + 4 * (step + 1), 'y': POSE['y'], 'z': POSE['z'], 'h': 0.3})
        time.sleep(0.05)
    moved = alice.wait(lambda m: m['t'] == 'pose' and m['name'] == 'bob' and m['x'] == 540.0,
                       "bob's last pose")
    assert time.time() - started < 2.0, 'poses arrived slowly'
    assert moved['count'] == 2, moved

    # A jump nothing could drive in one update is refused, and the old car is what others see.
    bob.send({'t': 'pose', 'x': 9000.0, 'y': POSE['y'], 'z': POSE['z'], 'h': 0.3})
    refused = bob.wait(lambda m: m['t'] == 'error', 'the refusal')
    assert 'further than a car can drive' in refused['error'], refused
    alice.quiet(0.4, lambda m: m['t'] == 'pose' and m['name'] == 'bob' and m['x'] > 1000.0,
                'a teleported car')

    # The game itself does move a car instantly, so a jump that holds for a second is believed.
    time.sleep(1.1)
    bob.send({'t': 'pose', 'x': 9000.0, 'y': POSE['y'], 'z': POSE['z'], 'h': 0.3})
    jumped = alice.wait(lambda m: m['t'] == 'pose' and m['name'] == 'bob' and m['x'] == 9000.0,
                        'the jump that held')
    assert jumped['world'] == 'town:1', jumped

    # A spectator is counted in the place and never relayed as a car.
    cara.send({'t': 'join', 'world': 'town:1', 'label': 'Peach Town', 'watch': True, 'pose': None})
    roster = cara.wait(lambda m: m['t'] == 'world' and m['world'] == 'town:1', 'the roster')
    assert names(roster) == ['alice', 'bob'] and roster['spectators'] == 1, roster
    counted = bob.wait(lambda m: m['t'] == 'counts' and m['spectators'] == 1, 'the spectator count')
    assert counted['count'] == 2, counted
    # A spectator cannot put a car in the world by reporting one.
    cara.send({'t': 'pose', 'x': 530.0, 'y': POSE['y'], 'z': POSE['z'], 'h': 0.3})
    refused = cara.wait(lambda m: m['t'] == 'error', 'the refused pose')
    assert 'spectator' in refused['error'], refused
    alice.quiet(0.4, lambda m: m['t'] == 'pose' and m['name'] == 'cara', "a spectator's car")

    # Driving on to the next town leaves the last place, and its cars stop being relayed.
    alice.send({'t': 'join', 'world': 'town:2', 'label': 'Fuji City', 'pose': pose(x=700.0)})
    left = bob.wait(lambda m: m['t'] == 'left' and m['name'] == 'alice', 'alice leaving town:1')
    assert left['count'] == 1, left
    alice.wait(lambda m: m['t'] == 'world' and m['world'] == 'town:2', 'the roster of town:2')
    for step in range(3):
        bob.send({'t': 'pose', 'x': 540.0 + step, 'y': POSE['y'], 'z': POSE['z'], 'h': 0.3})
        time.sleep(0.05)
    alice.quiet(0.4, lambda m: m['t'] == 'pose' and m['name'] == 'bob', "a car from another place")

    # The summary a page can show says where the people are.
    bob.send({'t': 'worlds'})
    summary = bob.wait(lambda m: m['t'] == 'worlds' and 'town:1' in m['worlds'], 'the summary')
    assert summary['worlds']['town:1']['label'] == 'Peach Town', summary
    assert summary['worlds']['town:1']['players'] == 1, summary
    assert summary['worlds']['town:1']['spectators'] == 1, summary

    # Older pages that still send a chapter-qualified world meet the same drivers.
    cara.send({'t': 'join', 'world': 'town:1@ch4', 'label': 'Peach Town', 'pose': pose(x=530.0)})
    legacy_roster = cara.wait(lambda m: m['t'] == 'world' and m['world'] == 'town:1',
                              'the shared roster for a legacy chapter key')
    assert names(legacy_roster) == ['bob', 'cara'], legacy_roster
    legacy_pose = bob.wait(lambda m: m['t'] == 'pose' and m['name'] == 'cara',
                           "the legacy client's pose")
    assert legacy_pose['world'] == 'town:1', legacy_pose

    # Closing the page takes the car out of the place at once instead of after the timeout, and
    # the people still there hear about it.
    bob.send({'t': 'join', 'world': 'town:2', 'label': 'Fuji City', 'pose': pose(x=710.0)})
    roster = bob.wait(lambda m: m['t'] == 'world' and m['world'] == 'town:2', 'the roster of town:2')
    assert names(roster) == ['alice', 'bob'], roster
    alice.close()
    gone = bob.wait(lambda m: m['t'] == 'left' and m['name'] == 'alice', 'alice leaving town:2', 5.0)
    assert gone['count'] == 1, gone

    # A socket is for a signed-in account.
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/online/socket?token=nope' % port, timeout=5):
            raise AssertionError('a socket opened for a made-up token')
    except urllib.error.HTTPError as error:
        assert error.code == 401, error

    for page in (bob, cara):
        page.close()
    print('Socket world regression passed')


if __name__ == '__main__':
    main()
