"""Accounts, their cloud memory cards, and the shared worlds the page plays in.

The page still runs its own copy of the game; this only keeps who a player is, the memory
card that carries their progress between devices, and where each player's car is in each
world. Every function here returns (body, status) so serve.py can hand it straight out.

Serve it from scripts/serve.py, which mounts these under /online/.
"""

import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import zlib

NAME = re.compile(r'^[A-Za-z0-9_-]{3,16}$')
PASSWORD_MIN = 4
CARD_BYTES = 8650752
CARD_MAGIC = b'Sony PS2 Memory Card Format '
PLAYER_IDLE = 6.0     # a car that has not said where it is for this long leaves the world
POLL_SECONDS = 0.5    # how long a world poll waits for someone else to move
POSE_MIN_GAP = 0.02   # at most 50 updates a second from one player
MAX_SPEED = 400.0     # units a second: no car crosses a town between two updates
JUMP_GRACE = 1.0      # how long a jump the game itself made has to hold before it is believed
MAX_WORLDS = 64
WORLD_MAX = 32        # a world is one place: "town:3", "road:14", "race:7"
RACE_COUNTDOWN = 20.0  # how long a course waits after the first entrant before its synchronized start
RACE_LIFETIME = 900.0  # a session takes no more finishes this long after it started
RACE_MAX = 16         # entrants in one session
RACE_MIN_TIME = 1.0
RACE_TIME_MAX = 3600.0
LABEL_MAX = 40        # the place's name as the page knows it ("Peach Town")
COUNTRY_MAX = 24
MOTTO_MAX = 40
DELIVERY_GOAL = 120.0 # game-space units; tune after observing real town drives
DELIVERY_REWARD = 100
DELIVERY_MIN_SECONDS = 5.0
MISSION_POSE_FRESH = 2.0
COUNTRY_COLORS = {'#e0a43b', '#4c9d78', '#5485c3', '#b86b9f', '#d66c54', '#9a91cf'}
CREW_MAX = 20          # a crew rides twenty; the story treats it as one garage
CREW_TAG = re.compile(r'^[A-Z0-9]{2,5}$')
CREW_NAME_MAX = 28
FRIENDS_MAX = 60


def canonical_world(world):
    """Treat old chapter-qualified place keys as the same shared world cell."""
    return re.sub(r'@ch\d+$', '', clean(world, WORLD_MAX)) or 'town'


def clean(text, limit):
    """Printable ASCII, trimmed to `limit`: a world name or label is stored, not obeyed."""
    return ''.join(c for c in str(text or '') if ' ' <= c <= '~')[:limit].strip()


class Online:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.cards_dir = os.path.join(self.data_dir, 'cards')
        self.path = os.path.join(self.data_dir, 'accounts.json')
        os.makedirs(self.cards_dir, exist_ok=True)
        self.accounts = {}
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.worlds = {}
        self.races = {}
        self.listeners = set()
        self.profile_path = os.path.join(self.data_dir, 'profiles.json')
        self.social_path = os.path.join(self.data_dir, 'social.json')
        self.social = {'friends': {}, 'crews': {}}
        self.profiles = {}
        self.active_missions = {}
        self.story = self.load_story()
        self.evidence = {}
        self.load()
        self.load_profiles()
        self.load_social()

    # ---- storage -------------------------------------------------------------------------
    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('accounts'), dict):
                self.accounts = data['accounts']
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print('online: cannot read %s (%s), starting empty' % (self.path, e), flush=True)

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'accounts': self.accounts}, f, indent=1)
        os.replace(tmp, self.path)

    def load_profiles(self):
        try:
            with open(self.profile_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('profiles'), dict):
                self.profiles = data['profiles']
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print('online: cannot read %s (%s), starting with no adventure profiles' %
                  (self.profile_path, e), flush=True)

    def save_profiles(self):
        tmp = self.profile_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'profiles': self.profiles}, f, indent=1)
        os.replace(tmp, self.profile_path)

    @staticmethod
    def digest(salt, password):
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 120000).hex()

    def card_path(self, name):
        return os.path.join(self.cards_dir, name.lower() + '.ps2.z')

    def has_card(self, name):
        return os.path.isfile(self.card_path(name))

    # ---- accounts ------------------------------------------------------------------------
    @staticmethod
    def issue(account):
        """A new session token; an account keeps the last few, so two devices can sign in."""
        token = secrets.token_urlsafe(24)
        tokens = account.setdefault('tokens', [])
        tokens.append(token)
        del tokens[:-8]
        return token

    def account(self, token):
        """The name a token belongs to, or None."""
        if not isinstance(token, str) or not token:
            return None
        with self.lock:
            for name, account in self.accounts.items():
                for issued in account.get('tokens', []):
                    if secrets.compare_digest(issued, token):
                        return name
        return None

    def signup(self, name, password):
        name = (name or '').strip()
        password = password or ''
        if not NAME.match(name):
            return {'error': 'A name is 3 to 16 letters, digits, - or _.'}, 400
        if len(password) < PASSWORD_MIN:
            return {'error': 'A password needs at least %d characters.' % PASSWORD_MIN}, 400
        with self.lock:
            if any(existing.lower() == name.lower() for existing in self.accounts):
                return {'error': 'That name is taken.'}, 409
            salt = secrets.token_hex(16)
            account = self.accounts[name] = {
                'salt': salt,
                'hash': self.digest(salt, password),
                'created': int(time.time()),
            }
            token = self.issue(account)
            self.save()
        print('online: %s signed up' % name, flush=True)
        return {'name': name, 'token': token, 'has_card': False}, 200

    def login(self, name, password):
        name = (name or '').strip()
        requested = password or ''
        with self.lock:
            match = next((key for key in self.accounts if key.lower() == name.lower()), None)
            if not match:
                return {'error': 'No account called %s. Create it first.' % name}, 404
            account = self.accounts[match]
            if not secrets.compare_digest(self.digest(account['salt'], requested), account['hash']):
                return {'error': 'That password is not right.'}, 403
            token = self.issue(account)
            account['seen'] = int(time.time())
            self.save()
        return {'name': match, 'token': token, 'has_card': self.has_card(match)}, 200

    def logout(self, token):
        name = self.account(token)
        if name:
            with self.lock:
                tokens = self.accounts[name].get('tokens', [])
                self.accounts[name]['tokens'] = [issued for issued in tokens if issued != token]
                self.save()
        return {}, 200

    def me(self, token):
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        return {'name': name, 'has_card': self.has_card(name)}, 200

    # ---- separate Online Adventure profile -----------------------------------------------
    @staticmethod
    def _public_profile(profile):
        if profile is None:
            return None
        # Return only gameplay fields; account hashes, tokens and internal timestamps stay private.
        return {key: (list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value)
                for key, value in profile.items()}

    def _mission_state(self, name):
        profile = self.profiles.get(name)
        if not profile:
            return None
        mission_id = 'first_delivery'
        base = {'id': mission_id, 'title': "Pip's First Delivery", 'goal': DELIVERY_GOAL,
                'reward': DELIVERY_REWARD}
        if mission_id in profile.get('completed_missions', []):
            return dict(base, status='complete', distance=DELIVERY_GOAL)
        active = self.active_missions.get(name)
        if active:
            return dict(base, status='active', distance=round(active['distance'], 1))
        return dict(base, status='available', distance=0.0)

    def profile_get(self, token):
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        with self.lock:
            profile = self._public_profile(self.profiles.get(name))
            return {'profile': profile, 'mission': self._mission_state(name)}, 200

    def profile_create(self, token, country, motto='', banner='#e0a43b'):
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        country = clean(country, COUNTRY_MAX)
        motto = clean(motto, MOTTO_MAX)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '\-]{1,23}", country):
            return {'error': 'A country name must be 2 to 24 letters, numbers, spaces, apostrophes or hyphens.'}, 400
        if banner not in COUNTRY_COLORS:
            return {'error': 'Choose one of the available country colors.'}, 400
        now = int(time.time())
        with self.lock:
            if name in self.profiles:
                return {'error': 'This account already has a country.'}, 409
            profile = {
                'version': 1,
                'country': country,
                'motto': motto,
                'banner': banner,
                'coins': 0,
                'chapter': 1,
                'completed_missions': [],
                'owned_parts': [],
                'equipped_parts': {},
                'season': {'id': None, 'points': 0},
                'created': now,
                'updated': now,
            }
            self.profiles[name] = profile
            self.save_profiles()
            return {'profile': self._public_profile(profile), 'mission': self._mission_state(name)}, 201

    def mission_action(self, token, mission_id, action):
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        if mission_id != 'first_delivery' or action not in ('start', 'complete'):
            return {'error': 'That mission action is not available.'}, 400
        now = time.time()
        with self.changed:
            profile = self.profiles.get(name)
            if not profile:
                return {'error': 'Create your country before starting a mission.'}, 409
            if mission_id in profile.get('completed_missions', []):
                return {'profile': self._public_profile(profile), 'mission': self._mission_state(name)}, 200
            if action == 'start':
                world = self.accounts[name].get('world', '')
                entry = self.worlds.get(world)
                player = entry['players'].get(name) if entry else None
                if not world.startswith('town:') or not player or now - player['at'] > MISSION_POSE_FRESH:
                    return {'error': 'Drive into a town and join the online world before starting this delivery.'}, 409
                self.active_missions[name] = {
                    'id': mission_id, 'world': world, 'started': now, 'distance': 0.0,
                    'last': (player['x'], player['y'], player['z']), 'last_at': player['at'],
                }
                return {'profile': self._public_profile(profile), 'mission': self._mission_state(name)}, 200
            active = self.active_missions.get(name)
            if not active or active['id'] != mission_id:
                return {'error': 'Start Pipâ€™s delivery in a town first.'}, 409
            if now - active['started'] < DELIVERY_MIN_SECONDS:
                return {'error': 'Keep driving the delivery route for a few more seconds.'}, 409
            if active['distance'] < DELIVERY_GOAL:
                return {'error': 'The delivery route is not complete yet.'}, 409
            profile['coins'] = int(profile.get('coins', 0)) + DELIVERY_REWARD
            profile.setdefault('completed_missions', []).append(mission_id)
            profile['updated'] = int(now)
            self.active_missions.pop(name, None)
            self.save_profiles()
            return {'profile': self._public_profile(profile), 'mission': self._mission_state(name)}, 200

    def capture_start(self, token, data):
        """Store a state the page captured as the place Online Adventure boots into."""
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        if not data or len(data) < 1 << 20 or len(data) > 128 << 20:
            return {'error': 'That is not a saved state of a sensible size.'}, 400
        path = os.path.join(self.data_dir, 'start.state')
        staged = path + '.tmp'
        with open(staged, 'wb') as f:
            f.write(data)
        os.replace(staged, path)
        return {'start': os.path.getsize(path), 'captured_by': name}, 200

    def mission_state(self, token):
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        with self.lock:
            profile = self.profiles.get(name)
            if not profile:
                return {'profile': None, 'mission': None}, 200
            return {'profile': self._public_profile(profile), 'mission': self._mission_state(name)}, 200

    # ---- cloud memory cards --------------------------------------------------------------
    # ---- story ---------------------------------------------------------------------------
    @staticmethod
    def load_story():
        """The authored campaign (stories/online-adventure.json); an empty pack if it is missing."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'stories', 'online-adventure.json')
        try:
            with open(path, encoding='utf-8') as f:
                pack = json.load(f)
            return pack if isinstance(pack, dict) else {'chapters': []}
        except (OSError, ValueError):
            return {'chapters': []}

    def chapter(self, number):
        for chapter in self.story.get('chapters', []):
            if chapter.get('number') == number:
                return chapter
        return None

    def note_pose(self, name, world, driven=0.0, now=None):
        """Evidence from accepted pose updates: where the car is, and how far it has driven there.

        Only accepted poses reach this, so a claim the relay refused never becomes story progress.
        """
        now = time.time() if now is None else now
        evidence = self.evidence.setdefault(name, {'place': '', 'place_at': 0.0, 'distance': {}, 'race': None,
                                                    'visits': [], 'visit_events': [], 'talks': []})
        previous_place = evidence.get('place')
        evidence['place'] = world
        evidence['place_at'] = now
        visits = evidence.setdefault('visits', [])
        if world not in visits:
            visits.append(world)
            del visits[:-64]
        if world != previous_place:
            events = evidence.setdefault('visit_events', [])
            events.append({'world': world, 'at': now})
            del events[:-128]
        if driven > 0.0:
            evidence['distance'][world] = evidence['distance'].get(world, 0.0) + driven
        self.advance_story(name)

    def note_talk(self, token, world, npc=''):
        """A player got to a quest giver and talked to them.

        The page can only report this after the host saw the game's message window open on the
        speaker we installed (ps2web_roadtrip_npc_talking), but a page can say anything, so it is
        only believed where the car provably is: an accepted pose from the same world, just now.

        Takes the session token (the caller has nothing else) and resolves the account itself, so a
        talk can never be recorded against a key that is not a player's own evidence.
        """
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        world = canonical_world(world)
        now = time.time()
        evidence = self.evidence.setdefault(name, {'place': '', 'place_at': 0.0, 'distance': {}, 'race': None,
                                                    'visits': [], 'visit_events': [], 'talks': []})
        if evidence.get('place') != world or abs(now - evidence.get('place_at', 0.0)) > MISSION_POSE_FRESH:
            return {'error': 'Drive into that town before you talk to anyone.'}, 400
        evidence['talk'] = {'world': world, 'npc': clean(npc, 64), 'at': now}
        talks = evidence.setdefault('talks', [])
        talks.append({'world': world, 'npc': clean(npc, 64), 'at': now})
        del talks[:-32]
        self.advance_story(name)
        return {'ok': True}, 200

    def _race_finish(self, name, expected_world=None, after=0.0):
        """A finish the relay itself accepted, read back from the session it was recorded in."""
        for world, session in list(self.races.items()):
            if expected_world is not None and world != expected_world:
                continue
            entry = session['entries'].get(name)
            if entry and entry.get('time') is not None and entry.get('finished_at', 0.0) > after:
                return {'world': world, 'seconds': entry['time']}
        return None

    def _objective_proved(self, name, objective, default_place, evidence, now, after=0.0):
        """Check one authored objective against relay evidence, including cross-town quests."""
        kind = objective.get('kind')
        place = canonical_world(objective.get('place') or default_place)
        if kind in ('visit', 'return'):
            events = evidence.get('visit_events') or []
            return (any(event.get('world') == place and event.get('at', 0.0) > after for event in events)
                    or (after <= 0.0 and (place in evidence.get('visits', [])
                        or (evidence.get('place') == place
                            and abs(now - evidence.get('place_at', 0.0)) <= MISSION_POSE_FRESH))))
        if kind == 'drive':
            return evidence.get('distance', {}).get(place, 0.0) >= float(objective.get('goal') or 0)
        if kind == 'talk':
            npc = objective.get('npc')
            talks = evidence.get('talks') or [evidence.get('talk') or {}]
            return any(talk.get('world') == place and talk.get('at', 0.0) > after
                       and (not npc or talk.get('npc') == npc) for talk in talks)
        if kind == 'race':
            return bool(self._race_finish(name, place if objective.get('place') else None, after))
        return False

    def advance_story(self, name):
        """Complete objectives their own evidence proves, then pay the chapter once."""
        profile = self.profiles.get(name)
        if not profile:
            return False
        story = profile.setdefault('story', {'chapter': 1, 'objectives': {}})
        chapter = self.chapter(story.get('chapter', 1)) or {'id': '', 'objectives': []}
        done = story.setdefault('objectives', {})
        evidence = self.evidence.setdefault(name, {'place': '', 'place_at': 0.0, 'distance': {}, 'race': None,
                                                    'visits': [], 'visit_events': [], 'talks': []})
        now = time.time()
        changed = False
        for objective in chapter.get('objectives', []):
            key = str(chapter.get('id', '')) + '.' + str(objective.get('id'))
            if key in done:
                continue
            proved = self._objective_proved(name, objective, chapter.get('place', ''), evidence, now)
            if proved:
                done[key] = int(now)
                changed = True
        objectives = chapter.get('objectives', [])
        if objectives and all(str(chapter.get('id', '')) + '.' + str(o.get('id')) in done for o in objectives):
            if not story.setdefault('rewards', {}).get(chapter.get('id')):
                story['rewards'][chapter.get('id')] = int(now)
                # A finish answers *this* chapter's race objective only: the next chapter that asks
                # for a race needs its own, or an old result would complete it for free.
                evidence.pop('race', None)
                profile['coins'] = int(profile.get('coins', 0)) + int((chapter.get('reward') or {}).get('coins') or 0)
                story['chapter'] = int(chapter.get('number', 1)) + 1
                profile['chapter'] = story['chapter']
                profile['updated'] = int(now)
                changed = True
        current_chapter = int(story.get('chapter', 1))
        side_done = story.setdefault('side_objectives', {})
        side_rewards = story.setdefault('side_rewards', {})
        for quest in self.story.get('side_quests', []):
            quest_id = str(quest.get('id') or '')
            if not quest_id or current_chapter < int(quest.get('unlock_chapter') or 1):
                continue
            quest_done = side_done.setdefault(quest_id, {})
            objectives = quest.get('objectives', [])
            for objective in objectives:
                key = str(objective.get('id') or '')
                if not key or key in quest_done:
                    continue
                required = [str(value) for value in objective.get('requires', [])]
                if not all(value in quest_done for value in required):
                    continue
                after = max((float(quest_done[value]) for value in required), default=0.0)
                if self._objective_proved(name, objective, quest.get('place', ''), evidence, now, after):
                    quest_done[key] = now
                    changed = True
            if objectives and all(str(objective.get('id') or '') in quest_done for objective in objectives):
                if quest_id not in side_rewards:
                    side_rewards[quest_id] = int(now)
                    reward = quest.get('reward') or {}
                    profile['coins'] = int(profile.get('coins', 0)) + int(reward.get('coins') or 0)
                    recruit = quest.get('recruit')
                    if recruit:
                        roster = profile.setdefault('recruited_teammates', [])
                        if recruit not in roster:
                            roster.append(recruit)
                    advisor = quest.get('advisor')
                    if advisor:
                        roster = profile.setdefault('advisors', [])
                        if advisor not in roster:
                            roster.append(advisor)
                    profile['updated'] = int(now)
                    changed = True
        if changed:
            self.save_profiles()
        return changed

    def story_state(self, name):
        """The chapter the account is on and how far each of its objectives has got."""
        profile = self.profiles.get(name) or {}
        story = profile.get('story') or {'chapter': 1, 'objectives': {}}
        chapter = self.chapter(story.get('chapter', 1)) or {}
        done = story.get('objectives') or {}
        evidence = self.evidence.get(name) or {}
        distance = evidence.get('distance', {}).get(chapter.get('place'), 0.0)
        objectives = []
        for objective in chapter.get('objectives', []):
            place = canonical_world(objective.get('place') or chapter.get('place', ''))
            objectives.append({
                'id': objective.get('id'), 'kind': objective.get('kind'), 'text': objective.get('text'),
                'goal': objective.get('goal'), 'npc': objective.get('npc'),
                'place': place,
                'progress': round(evidence.get('distance', {}).get(place, 0.0), 1) if objective.get('kind') == 'drive' else None,
                'done': str(chapter.get('id', '')) + '.' + str(objective.get('id')) in done,
            })
        side_quests = []
        side_done = story.get('side_objectives') or {}
        side_rewards = story.get('side_rewards') or {}
        for quest in self.story.get('side_quests', []):
            quest_id = str(quest.get('id') or '')
            unlocked = int(story.get('chapter', 1)) >= int(quest.get('unlock_chapter') or 1)
            quest_done = side_done.get(quest_id, {})
            quest_objectives = []
            for objective in quest.get('objectives', []):
                place = canonical_world(objective.get('place') or quest.get('place', ''))
                quest_objectives.append({
                    'id': objective.get('id'), 'kind': objective.get('kind'), 'text': objective.get('text'),
                    'goal': objective.get('goal'), 'npc': objective.get('npc'), 'place': place,
                    'progress': round(evidence.get('distance', {}).get(place, 0.0), 1)
                    if objective.get('kind') == 'drive' else None,
                    'done': str(objective.get('id') or '') in quest_done,
                })
            side_quests.append({
                'id': quest_id, 'title': quest.get('title', ''), 'place': quest.get('place', ''),
                'unlocked': unlocked, 'complete': quest_id in side_rewards,
                'objectives': quest_objectives,
            })
        return {'chapter': story.get('chapter', 1), 'title': chapter.get('title', ''),
                'contact': chapter.get('contact', ''), 'place': chapter.get('place', ''),
                'objectives': objectives, 'side_quests': side_quests,
                'complete': bool(objectives) and all(o['done'] for o in objectives)}

    def card_get(self, token):
        name = self.account(token)
        if not name:
            return None, 401
        try:
            with open(self.card_path(name), 'rb') as f:
                data = zlib.decompress(f.read())
        except FileNotFoundError:
            return None, 404
        except (OSError, zlib.error):
            return None, 500
        return data, 200

    def card_put(self, token, data):
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        if len(data) != CARD_BYTES or not data.startswith(CARD_MAGIC):
            return {'error': 'That is not an 8 MB PS2 memory card.'}, 400
        tmp = self.card_path(name) + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(zlib.compress(bytes(data), 6))
        os.replace(tmp, self.card_path(name))
        with self.lock:
            self.accounts[name]['card_saved'] = int(time.time())
            self.save()
        print('online: saved the card of %s (%d bytes on the card)' % (name, len(data)), flush=True)
        return {'saved': len(data)}, 200

    # ---- worlds --------------------------------------------------------------------------
    def listen(self, listener):
        """Hear every pose and departure as it happens (scripts/wsserver.py pushes them out).

        A listener is called with the lock held and only queues what it is told, so it must
        not call back into this object.
        """
        self.listeners.add(listener)

    def announce(self, world, event):
        for listener in tuple(self.listeners):
            try:
                listener(world, event)
            except Exception as error:  # a listener that breaks must not cost a player their car
                print('online: a listener failed (%s)' % error, flush=True)

    def _world(self, world, label=''):
        """One place's record, made on first use and bounded."""
        entry = self.worlds.get(world)
        if entry is None:
            while len(self.worlds) >= MAX_WORLDS:
                self.worlds.pop(next(iter(self.worlds)))
            entry = self.worlds[world] = {'players': {}, 'watchers': {}, 'version': 0, 'label': ''}
        if label:
            entry['label'] = label
        return entry

    @staticmethod
    def _counts(entry):
        return len(entry['players']), len(entry['watchers'])

    def _leave(self, name, keep=None):
        """Take a player out of the place they were in, and tell the people still there."""
        left = self.accounts[name].pop('world', None)
        mission = self.active_missions.get(name)
        if mission and mission.get('world') != keep:
            self.active_missions.pop(name, None)
        entry = self.worlds.get(left) if left and left != keep else None
        if entry is None:
            return
        gone = entry['players'].pop(name, None) is not None
        gone = entry['watchers'].pop(name, None) is not None or gone
        if gone:
            entry['version'] += 1
            self.changed.notify_all()
            self.announce(left, {'t': 'left', 'name': name,
                                 'count': len(entry['players']), 'spectators': len(entry['watchers'])})

    def report(self, token, world, pose, label='', watch=False):
        """Record a player's car, or arrive as a spectator who shares no car.

        A world is one place in the game (a town, a field area between towns, a race), so a
        player's car is only ever relayed to the people standing in the same place. A pose of
        None without `watch` takes the car out of the place it was in.
        """
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        world = canonical_world(world)
        now = time.time()
        driven = 0.0
        with self.changed:
            if pose is None and not watch:
                self._leave(name)
                return {'ok': True}, 200
            entry = self._world(world, clean(label, LABEL_MAX))
            if self.accounts[name].get('world') != world:
                # A car is in one place: driving on to the next town takes it out of the last
                # one at once, instead of leaving a ghost there until the idle timeout.
                self._leave(name)
            if watch:
                entry['players'].pop(name, None)
                entry['watchers'][name] = now
                self.accounts[name]['world'] = world
                entry['version'] += 1
                self.changed.notify_all()
                count, spectators = self._counts(entry)
                self.announce(world, {'t': 'counts', 'count': count, 'spectators': spectators})
                return {'ok': True}, 200
            if name in entry['watchers']:
                # A page that is watching shares no car; it stops watching before it drives.
                return {'error': 'A spectator shares no car.'}, 400
            try:
                x, y, z, h = (float(pose[key]) for key in ('x', 'y', 'z', 'h'))
            except (KeyError, TypeError, ValueError):
                return {'error': 'A pose is x, y, z and h.'}, 400
            if not all(math.isfinite(v) and abs(v) <= 100000.0 for v in (x, y, z, h)):
                return {'error': 'A pose is out of range.'}, 400
            previous = entry['players'].get(name)
            previous_position = (previous['x'], previous['y'], previous['z']) if previous else None
            if previous:
                elapsed = now - previous['at']
                if elapsed < POSE_MIN_GAP:
                    return {'ok': True}, 200
                moved = math.sqrt((x - previous['x']) ** 2 + (y - previous['y']) ** 2
                                  + (z - previous['z']) ** 2)
                if moved > MAX_SPEED * elapsed:
                    # Nothing drives across a town between two updates, so a page cannot put its
                    # car anywhere it likes. The game itself does move a car instantly (the end
                    # of a race, a story beat), so a jump is believed once it has held still for
                    # JUMP_GRACE rather than refused for ever.
                    held = previous.get('jump_at')
                    if held is None or now - held < JUMP_GRACE:
                        previous['jump_at'] = now if held is None else held
                        return {'error': 'That is further than a car can drive in %.2f s.' % elapsed}, 400
                previous.pop('jump_at', None)
                driven = moved
            active = self.active_missions.get(name)
            if active and active['world'] == world:
                elapsed = now - active['last_at']
                moved = math.sqrt((x - active['last'][0]) ** 2 + (y - active['last'][1]) ** 2
                                  + (z - active['last'][2]) ** 2)
                # Only count fresh, plausible movement. The world relay may forgive a genuine
                # scene transition after a held jump, but a mission never pays for that jump.
                if 0.0 < elapsed <= MISSION_POSE_FRESH and moved <= MAX_SPEED * elapsed + 1.0:
                    active['distance'] += moved
                active['last'] = (x, y, z)
                active['last_at'] = now
            self.accounts[name]['world'] = world
            entry['players'][name] = {'x': x, 'y': y, 'z': z, 'h': h, 'at': now}
            entry['watchers'].pop(name, None)
            entry['version'] += 1
            self.changed.notify_all()
            count, spectators = self._counts(entry)
            self.announce(world, {'t': 'pose', 'name': name, 'x': x, 'y': y, 'z': z, 'h': h,
                                  'count': count, 'spectators': spectators})
        # Outside the relay's lock: the story reads evidence, never the other way round.
        self.note_pose(name, world, driven, now)
        return {'ok': True}, 200

    def watch(self, token, world, on=True, label=''):
        """Arrive as, or stop being, a spectator in a place; a watcher shares no car."""
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        world = canonical_world(world)
        with self.changed:
            entry = self._world(world, clean(label, LABEL_MAX))
            if self.accounts[name].get('world') != world:
                self._leave(name)
            if on:
                entry['players'].pop(name, None)
                entry['watchers'][name] = time.time()
            else:
                entry['watchers'].pop(name, None)
            self.accounts[name]['world'] = world
            entry['version'] += 1
            self.changed.notify_all()
            count, spectators = self._counts(entry)
            self.announce(world, {'t': 'counts', 'count': count, 'spectators': spectators})
        return {'ok': True}, 200

    def leave(self, token):
        """Take this player out of the world at once (a page closing, a socket closing)."""
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        with self.changed:
            self._leave(name)
        return {'ok': True}, 200

    def _roster(self, world, now):
        """Who is in a place, dropping cars that have gone quiet. Called with the lock held."""
        entry = self.worlds.get(world)
        if entry is None:
            return {'world': world, 'label': '', 'players': [], 'count': 0, 'spectators': 0}
        quiet = [k for k, p in entry['players'].items() if now - p['at'] > PLAYER_IDLE]
        for key in quiet:
            del entry['players'][key]
            entry['version'] += 1
        for key in [k for k, at in entry['watchers'].items() if now - at > PLAYER_IDLE]:
            del entry['watchers'][key]
        players = [
            {'name': key, 'x': p['x'], 'y': p['y'], 'z': p['z'], 'h': p['h'],
             'age': round(now - p['at'], 3)}
            for key, p in sorted(entry['players'].items())
        ]
        return {'world': world, 'label': entry.get('label', ''), 'players': players,
                'count': len(entry['players']), 'spectators': len(entry['watchers'])}

    def snapshot(self, world):
        """A place right now, without waiting for anyone to move (a socket's first answer)."""
        world = canonical_world(world)
        with self.changed:
            return self._roster(world, time.time())

    def world(self, token, world, watching):
        """Wait until another car moves (or half a second passes), then return the roster."""
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        world = canonical_world(world)
        with self.changed:
            entry = self._world(world)
            if watching:
                entry['watchers'][name] = time.time()
                # A spectator is in the place too, so leaving can take it out of this one.
                self.accounts[name]['world'] = world
            start, deadline = entry['version'], time.time() + POLL_SECONDS
            while entry['version'] == start:
                left = deadline - time.time()
                if left <= 0:
                    break
                self.changed.wait(left)
            return self._roster(world, time.time()), 200

    # ---- races ---------------------------------------------------------------------------
    # A race world ("race:7") shares poses like any other place, but a race also needs everyone to
    # start *together*: the game itself has no lobby, so the server holds the clock. A session opens
    # with its first entrant, starts RACE_COUNTDOWN seconds later whatever anybody else does, and
    # then takes finishes for as long as it lives. The page counts down to that start and the player
    # starts the course in their own copy of the game at zero.
    def _session(self, world, now):
        """The session on a course now, opening the next one when the last has had its time."""
        session = self.races.get(world)
        if session is None or now - session['start'] > RACE_LIFETIME:
            while len(self.races) >= MAX_WORLDS:
                self.races.pop(next(iter(self.races)))
            session = self.races[world] = {'start': now + RACE_COUNTDOWN, 'entries': {}}
        return session

    @staticmethod
    def _session_body(world, session, now):
        """A session as the page reads it: who is in it, when it starts, who has finished."""
        entries = [{'name': name, 'time': entry.get('time')}
                   for name, entry in session['entries'].items()]
        finished = sorted((e for e in entries if e['time'] is not None), key=lambda e: e['time'])
        return {'world': world, 'start': round(session['start'], 3),
                'starts_in': round(max(-1.0, session['start'] - now), 2),
                'started': now >= session['start'],
                'entrants': finished + [e for e in entries if e['time'] is None],
                'count': len(entries)}

    def race(self, token, world, action='state', seconds=None):
        """Join, leave, finish or look at the session on a course.

        Times are what the page measured from the session's start, so they are only as good as the
        page that sent them; the server keeps them bounded (not before the start, not longer than
        the session has run, not twice) rather than believed. Course checkpoints are what would make
        a time provable, and those need the game's own race state.
        """
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        world = canonical_world(world)
        if not world.startswith('race:'):
            return {'error': 'A race session belongs to a race world.'}, 400
        if action not in ('state', 'join', 'leave', 'finish'):
            return {'error': 'A race action is state, join, leave or finish.'}, 400
        now = time.time()
        with self.lock:
            session = self._session(world, now)
            entries = session['entries']
            if action == 'leave':
                entries.pop(name, None)
            elif action in ('join', 'finish'):
                if name not in entries:
                    if len(entries) >= RACE_MAX:
                        return {'error': 'That race is full.'}, 409
                    entries[name] = {'time': None, 'joined': now}
                if action == 'finish':
                    if now < session['start']:
                        return {'error': 'That race has not started yet.'}, 400
                    if entries[name].get('time') is not None:
                        return {'error': 'You have already finished this race.'}, 409
                    try:
                        seconds = float(seconds)
                    except (TypeError, ValueError):
                        return {'error': 'A finish is a time in seconds.'}, 400
                    if not math.isfinite(seconds) or not (RACE_MIN_TIME <= seconds <= RACE_TIME_MAX):
                        return {'error': 'A finish is between %d and %d seconds.' % (RACE_MIN_TIME, RACE_TIME_MAX)}, 400
                    if seconds > now - session['start'] + JUMP_GRACE:
                        # A race cannot have taken longer than the session has been running, which
                        # is what a made-up time looks like from here.
                        return {'error': 'That is longer than this race has been running.'}, 400
                    entries[name]['time'] = round(seconds, 3)
                    entries[name]['finished_at'] = now
            return self._session_body(world, session, now), 200

    def world_list(self):
        """How many players and spectators each world has, for the page's world picker."""
        now = time.time()
        with self.lock:
            summary = {}
            for name, entry in self.worlds.items():
                players = sum(1 for p in entry['players'].values() if now - p['at'] <= PLAYER_IDLE)
                watchers = sum(1 for at in entry['watchers'].values() if now - at <= PLAYER_IDLE)
                if players or watchers:
                    names = sorted(k for k, p in entry['players'].items() if now - p['at'] <= PLAYER_IDLE)
                    summary[name] = {'players': players, 'spectators': watchers,
                                     'names': names, 'label': entry.get('label', '')}
        return {'worlds': summary, 'accounts': len(self.accounts)}, 200
    # ---- crews and friends ---------------------------------------------------------------
    # A crew is a standing group of accounts that rides together: it outlives a session, it is shown
    # in the game's own system menu, and it is the unit the story will treat as one garage. Friends
    # are mutual by construction (adding adds you to the other account as well), so a list is one
    # list rather than two halves that never meet. Both read presence from the world relay, so
    # "online" means a car the relay has heard from in the last few seconds.
    def _social(self):
        social = self.social
        if not isinstance(social.get('friends'), dict):
            social['friends'] = {}
        if not isinstance(social.get('crews'), dict):
            social['crews'] = {}
        return social

    def load_social(self):
        try:
            with open(self.social_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.social = {'friends': data.get('friends') or {}, 'crews': data.get('crews') or {}}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print('online: cannot read %s (%s), starting with no crews or friends' % (self.social_path, e), flush=True)

    def save_social(self):
        tmp = self.social_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self._social(), f, indent=1)
        os.replace(tmp, self.social_path)

    def _match_account(self, name):
        """The stored spelling of an account name, or None. Called with the lock held."""
        if not isinstance(name, str) or not NAME.match(name.strip()):
            return None
        wanted = name.strip().lower()
        return next((key for key in self.accounts if key.lower() == wanted), None)

    def _present(self, name, now):
        """Where an account's car is right now, or None. Called with the lock held."""
        world = self.accounts.get(name, {}).get('world')
        entry = self.worlds.get(world) if world else None
        if not entry:
            return None
        player = entry['players'].get(name)
        at = player['at'] if player else entry['watchers'].get(name)
        if at is None or now - at > PLAYER_IDLE:
            return None
        return {'world': world, 'label': entry.get('label', '')}

    def _crew_of(self, name):
        """The crew an account rides with: (key, crew) or (None, None). Called with the lock held."""
        social = self._social()
        for key, crew in social['crews'].items():
            if name in crew.get('members', []):
                return key, crew
        return None, None

    @staticmethod
    def _crew_view(key, crew, owners):
        members = []
        for member in crew.get('members', []):
            here = owners.get(member)
            members.append({'name': member, 'online': here is not None,
                            'place': (here or {}).get('label', ''),
                            'leader': member == crew.get('leader')})
        return {'key': key, 'name': crew.get('name', key), 'tag': crew.get('tag', ''),
                'motto': crew.get('motto', ''), 'colour': crew.get('colour', '#e0a43b'),
                'leader': crew.get('leader', ''), 'members': members,
                'size': len(members), 'limit': CREW_MAX, 'created': crew.get('created', 0)}

    def _presence(self, now):
        """Every account whose car the relay has heard from lately. Called with the lock held."""
        owners = {}
        for other in list(self.accounts):
            here = self._present(other, now)
            if here:
                owners[other] = here
        return owners

    def friends(self, token):
        """This account's friends, each with where they are right now and the crew they ride with."""
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        now = time.time()
        with self.lock:
            social = self._social()
            names = list(social['friends'].get(name, []))
            owners = self._presence(now)
            rows = []
            for other in names:
                _, crew = self._crew_of(other)
                rows.append({'name': other, 'online': other in owners,
                             'place': owners.get(other, {}).get('label', ''),
                             'crew': crew.get('tag', '') if crew else ''})
            rows.sort(key=lambda one: (not one['online'], one['name'].lower()))
        return {'friends': rows, 'limit': FRIENDS_MAX, 'me': name}, 200

    def friend_action(self, token, action, other):
        """Add or remove a friend; adding is mutual, so the other account sees you as well."""
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        with self.lock:
            social = self._social()
            target = self._match_account(other)
            if not target:
                return {'error': 'No account called %s.' % clean(other, 16)}, 404
            if target == name:
                return {'error': 'That is your own name.'}, 400
            mine = social['friends'].setdefault(name, [])
            theirs = social['friends'].setdefault(target, [])
            if action == 'add':
                if target in mine:
                    return {'error': '%s is already on your list.' % target}, 409
                if len(mine) >= FRIENDS_MAX:
                    return {'error': 'A friends list holds %d drivers.' % FRIENDS_MAX}, 400
                mine.append(target)
                if name not in theirs:
                    theirs.append(name)
            elif action == 'remove':
                if target not in mine:
                    return {'error': '%s is not on your list.' % target}, 404
                mine.remove(target)
                if name in theirs:
                    theirs.remove(name)
            else:
                return {'error': 'Friends take "add" or "remove".'}, 400
            social['friends'][name] = mine
            social['friends'][target] = theirs
            self.save_social()
        return {'ok': True, 'name': target, 'action': action}, 200

    def crew(self, token):
        """This account's crew, and the other crews worth showing in the menu."""
        name = self.account(token)
        if not name:
            return {'error': 'Sign in again.'}, 401
        now = time.time()
        with self.lock:
            social = self._social()
            owners = self._presence(now)
            key, mine = self._crew_of(name)
            view = self._crew_view(key, mine, owners) if mine else None
            others = []
            for crew_key, crew in social['crews'].items():
                if crew_key == key:
                    continue
                item = self._crew_view(crew_key, crew, owners)
                others.append({'key': item['key'], 'name': item['name'], 'tag': item['tag'],
                               'size': item['size'], 'limit': item['limit']})
            others.sort(key=lambda one: (-one['size'], one['name'].lower()))
        return {'crew': view, 'crews': others[:12], 'limit': CREW_MAX, 'me': name}, 200

    def crew_action(self, token, action, name='', tag='', motto=''):
        """Found a crew, join one by name or tag, or leave the one you ride with."""
        me = self.account(token)
        if not me:
            return {'error': 'Sign in again.'}, 401
        with self.lock:
            social = self._social()
            current_key, current = self._crew_of(me)
            owners = self._presence(time.time())
            if action == 'create':
                title = clean(name, CREW_NAME_MAX)
                letters = clean(tag, 5).upper()
                if len(title) < 3:
                    return {'error': 'A crew name is at least 3 letters.'}, 400
                if not CREW_TAG.match(letters):
                    return {'error': 'A crew tag is 2 to 5 letters or digits.'}, 400
                if current:
                    return {'error': 'Leave %s before founding another crew.' % current.get('name')}, 409
                wanted = title.lower()
                if wanted in social['crews']:
                    return {'error': 'A crew called %s already rides.' % title}, 409
                if any(crew.get('tag') == letters for crew in social['crews'].values()):
                    return {'error': 'The tag %s is taken.' % letters}, 409
                crew = social['crews'][wanted] = {
                    'name': title, 'tag': letters, 'motto': clean(motto, MOTTO_MAX),
                    'colour': '#e0a43b', 'leader': me, 'members': [me],
                    'created': int(time.time()),
                }
                self.save_social()
                return {'ok': True, 'crew': self._crew_view(wanted, crew, owners)}, 200
            if action == 'join':
                key = self._match_crew(social, name)
                if not key:
                    return {'error': 'No crew called %s.' % clean(name, CREW_NAME_MAX)}, 404
                if current:
                    return {'error': 'You already ride with %s.' % current.get('name')}, 409
                crew = social['crews'][key]
                if len(crew.get('members', [])) >= CREW_MAX:
                    return {'error': '%s is full (%d drivers).' % (crew.get('name'), CREW_MAX)}, 400
                crew.setdefault('members', []).append(me)
                self.save_social()
                return {'ok': True, 'crew': self._crew_view(key, crew, owners)}, 200
            if action == 'leave':
                if not current:
                    return {'error': 'You are not in a crew.'}, 400
                members = current.get('members', [])
                if me in members:
                    members.remove(me)
                if not members:
                    social['crews'].pop(current_key, None)
                elif current.get('leader') == me:
                    current['leader'] = members[0]
                self.save_social()
                return {'ok': True, 'crew': None}, 200
            if action == 'motto':
                # The motto is the crew's line, so it is the leader's to set: every member sees it
                # under the roster, and it is the one string a crew writes about itself.
                if not current:
                    return {'error': 'You are not in a crew.'}, 400
                if current.get('leader') != me:
                    return {'error': 'Only %s can set the motto.' % current.get('leader')}, 403
                current['motto'] = clean(motto, MOTTO_MAX)
                self.save_social()
                return {'ok': True, 'crew': self._crew_view(current_key, current, owners)}, 200
        return {'error': 'A crew action is create, join, leave or motto.'}, 400

    @staticmethod
    def _match_crew(social, name):
        """A crew by name or tag, as the key it is stored under, or None."""
        wanted = clean(name, CREW_NAME_MAX).lower()
        if not wanted:
            return None
        if wanted in social['crews']:
            return wanted
        for key, crew in social['crews'].items():
            if str(crew.get('tag', '')).lower() == wanted:
                return key
        return None
