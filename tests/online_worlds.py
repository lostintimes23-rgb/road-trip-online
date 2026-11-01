#!/usr/bin/env python3
"""Regression: a shared world is one place, and only its players are relayed.

The page keys each world by the place the car is in ("town:3", "road:14", "race:7"), so a
player in Peach Town must never receive a car standing in Fuji City. Accounts, labels,
spectators and the world summary are checked against scripts/online.py with no disc or build.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import online as online_module

POSE = {'x': 504.8, 'y': 30.2, 'z': 469.9, 'h': 0.3}


def names(body):
    return sorted(player['name'] for player in body['players'])


def pose_at(x):
    return dict(POSE, x=x)


with tempfile.TemporaryDirectory(prefix='ps2-online-test-') as temp:
    online = online_module.Online(temp)
    alice = online.signup('alice', 'passw0rd')[0]['token']
    bob = online.signup('bob', 'passw0rd')[0]['token']
    cara = online.signup('cara', 'passw0rd')[0]['token']

    # Two towns are two worlds: the car in Peach Town is not in Fuji City.
    online.report(alice, 'town:1', POSE, 'Peach Town')
    online.report(bob, 'town:2', POSE, 'Fuji City')
    body, status = online.world(bob, 'town:2', False)
    assert status == 200 and body['world'] == 'town:2', body
    assert names(body) == ['bob'], names(body)
    assert body['count'] == 1 and body['label'] == 'Fuji City', body

    # A player who arrives in the same town is relayed to everyone already there.
    online.report(cara, 'town:2', pose_at(520.0), 'Fuji City')
    body, _ = online.world(bob, 'town:2', False)
    assert names(body) == ['bob', 'cara'], names(body)

    # Driving on to the next place moves the car with it, not a copy in the old world.
    online.report(cara, 'town:3', pose_at(700.0), 'Sandpolis')
    body, _ = online.world(bob, 'town:2', False)
    assert names(body) == ['bob'], names(body)
    body, _ = online.world(cara, 'town:3', False)
    assert names(body) == ['cara'], names(body)

    # Spectators join a place without a car: counted, never relayed as a player.
    body, _ = online.world(alice, 'town:2', True)
    assert body['spectators'] == 1 and names(body) == ['bob'], body

    # A race is its own world, even in the town whose Q's Factory runs it.
    online.report(alice, 'race:7', POSE, 'Race: Fuji City')
    body, _ = online.world(bob, 'town:2', False)
    assert names(body) == ['bob'], names(body)

    # The world summary carries each place's name, for a world picker.
    summary, status = online.world_list()
    assert status == 200
    worlds = summary['worlds']
    assert worlds['town:2']['label'] == 'Fuji City', worlds
    assert worlds['race:7']['label'] == 'Race: Fuji City', worlds
    assert 'town:3' in worlds, sorted(worlds)

    # Story chapter never partitions the shared world, including legacy chapter-qualified keys.
    online.report(alice, 'town:2@ch5', POSE, 'Fuji City - Chapter 5')
    online.report(cara, 'town:2@ch4', pose_at(520.0), 'Fuji City - Chapter 4')
    body, _ = online.world(alice, 'town:2@ch5', False)
    assert names(body) == ['alice', 'bob', 'cara'], names(body)
    assert body['world'] == 'town:2', body

    # The world summary exposes the shared place, not progress-specific partitions.
    summary, status = online.world_list()
    assert status == 200
    worlds = summary['worlds']
    assert worlds['town:2']['names'] == ['alice', 'bob', 'cara'], worlds
    assert 'town:2@ch5' not in worlds and 'town:2@ch4' not in worlds, worlds

    # A pose outside the world's bounds is refused instead of stored.
    assert online.report(bob, 'town:2', pose_at(1e9))[1] == 400

print('Place-keyed worlds regression passed')
