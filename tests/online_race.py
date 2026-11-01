#!/usr/bin/env python3
"""Regression: a race session starts on the server's clock and takes bounded finishes.

The game has no lobby, so the relay holds one: the first entrant opens a session on a course, it
starts a fixed countdown later whatever anyone else does, and finishes are only accepted after
that start, only once, and only as long as the session has actually been running. No disc, build or
browser is needed: this exercises scripts/online.py directly.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import online as online_module

COURSE = 'race:7'


def entrants(body):
    return [(e['name'], e['time']) for e in body['entrants']]


with tempfile.TemporaryDirectory(prefix='ps2-race-test-') as temp:
    online = online_module.Online(temp)
    alice = online.signup('alice', 'passw0rd')[0]['token']
    bob = online.signup('bob', 'passw0rd')[0]['token']

    # The first entrant opens the session; it does not start until the countdown has passed.
    body, status = online.race(alice, COURSE, 'join')
    assert status == 200, body
    assert body['count'] == 1 and entrants(body) == [('alice', None)], body
    assert 0 < body['starts_in'] <= online_module.RACE_COUNTDOWN + 0.5, body
    assert body['started'] is False, body
    start = body['start']

    # A second player joins the same session instead of opening another one.
    body, status = online.race(bob, COURSE, 'join')
    assert status == 200 and body['count'] == 2 and body['start'] == start, body

    # Nobody finishes before the start.
    assert online.race(alice, COURSE, 'finish', 61.0)[1] == 400

    # Once the clock has passed the start, a finish is taken and leads the board.
    online.races[COURSE]['start'] = time.time() - 90
    body, status = online.race(alice, COURSE, 'finish', 61.5)
    assert status == 200, body
    assert entrants(body)[0] == ('alice', 61.5), body

    # A second finish for the same race is refused, and so is one for longer than it has run.
    assert online.race(alice, COURSE, 'finish', 55.0)[1] == 409
    assert online.race(bob, COURSE, 'finish', 3600.0)[1] == 400
    body, status = online.race(bob, COURSE, 'finish', 63.25)
    assert status == 200 and [e[0] for e in entrants(body)] == ['alice', 'bob'], body

    # Leaving takes an entrant out; looking at the session does not add one.
    body, _ = online.race(bob, COURSE, 'leave')
    assert body['count'] == 1, body
    body, _ = online.race(alice, COURSE, 'state')
    assert body['count'] == 1, body

    # The session is per course: another course is another race.
    body, status = online.race(alice, 'race:9', 'join')
    assert status == 200 and body['count'] == 1 and body['starts_in'] > 0, body

    # A session that has had its time is replaced by a fresh one on the next call.
    online.races[COURSE]['start'] = time.time() - online_module.RACE_LIFETIME - 1
    body, _ = online.race(alice, COURSE, 'state')
    assert body['count'] == 0 and body['starts_in'] > 0 and body['started'] is False, body

    # Only a race world carries a session, only real actions are accepted, and a token is needed.
    assert online.race(alice, 'town:3', 'join')[1] == 400
    assert online.race(alice, COURSE, 'teleport')[1] == 400
    assert online.race('not-a-token', COURSE, 'join')[1] == 401
    assert online.race(alice, COURSE, 'finish', 'soon')[1] == 400

print('Race session regression passed')
