#!/usr/bin/env python3
"""Checks the online campaign against the game it plays in.

The pack (stories/online-adventure.json) is content, so a typo there is a broken quest rather
than a crash: an objective naming somebody the chapter never installs, a cast range past the end
of that town's roster, a line too long for the game's message window. This reads the same disc
the browser build runs and refuses those mistakes before a player finds them.

    python scripts/check_story.py
    python scripts/check_story.py --story other.json --elf path/to/SLUS_203.98

Rules it enforces (each one is something that has actually gone wrong while authoring):
  * every chapter has a place that is a real town, and a cast in that town's roster
  * a cast member's range is inside that roster and sized for the host's slot budget
  * the chapter's contact covers the whole street tail, because a town opens exactly one street
    entry (measured) and a contact that misses it can never be talked to
  * every `talk` objective names a member of its own chapter's cast
  * a name fits 64 bytes and a message fits 320, the host's slot sizes
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import text  # noqa: E402  the roster reader (same disc, same addresses)

DEFAULT_STORY = ROOT / 'stories/online-adventure.json'
KINDS = {'visit', 'return', 'drive', 'race', 'talk'}
CAST_MAX = 3      # host/web.cpp kCastMax
SLOTS = 24        # host/web.cpp kQuestSlots
NAME_BYTES = 64   # QUEST_NAME
LINE_BYTES = 320  # QUEST_LINE
# The street NPCs of a town, as a range of roster entries: the people the game can put behind a
# conversation when the player bumps into them. Everything outside the range is a shop, the
# bartender or a Quick-Pic counter, and replacing one of those breaks that building, so a cast
# may never cover them (scripts/text.py --names <location> prints the roster).
STREET = {1: (28, 38), 2: (29, 39), 3: (33, 41), 4: (17, 27), 5: (3, 4), 6: (27, 36),
          7: (28, 37), 8: (15, 24)}
TOWNS = ['', 'Peach Town', 'Fuji City', 'Sandpolis', 'Chestnut Canyon', 'Mushroom Road',
         'White Mountain', 'Papaya Island', 'Cloud Hill']


def names(elf, location):
    """Every speaker in a location's roster, in index order."""
    base, entries = text.roster(elf, location)
    bounds = entries + ([base] if base else [])
    return [text.entry(elf, address, bounds)['name'] for address in entries]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--story', default=str(DEFAULT_STORY))
    parser.add_argument('--elf', default=str(text.DEFAULT_ELF))
    parser.add_argument('--quiet', action='store_true')
    args = parser.parse_args()

    pack = json.loads(Path(args.story).read_text(encoding='utf-8'))
    elf = text.Elf(args.elf)
    problems, numbers = [], set()

    for chapter in pack.get('chapters', []):
        where = 'chapter %s' % chapter.get('number')
        number = chapter.get('number')
        if not isinstance(number, int) or number < 1:
            problems.append('%s: no usable number' % where)
            continue
        if number in numbers:
            problems.append('%s: a previous chapter already uses number %d' % (where, number))
        numbers.add(number)
        for key in ('id', 'title', 'place', 'place_name', 'contact', 'intro', 'outro'):
            if not str(chapter.get(key) or '').strip():
                problems.append('%s: empty %s' % (where, key))

        kind, _, index = str(chapter.get('place') or '').partition(':')
        location = int(index) if index.isdigit() else 0
        roster = names(elf, location) if location else []
        if kind != 'town' or not roster:
            problems.append('%s: place %r is not a town with a roster' % (where, chapter.get('place')))
            continue
        if TOWNS[location] != chapter.get('place_name'):
            problems.append('%s: place_name %r, but town %d is %r'
                            % (where, chapter.get('place_name'), location, TOWNS[location]))

        cast = chapter.get('cast') or []
        if not cast:
            problems.append('%s: no cast, so the town keeps the offline story\'s people' % where)
        if len(cast) > CAST_MAX:
            problems.append('%s: %d cast members, the host installs at most %d' % (where, len(cast), CAST_MAX))
        slots, covered, speakers = 0, set(), {}
        for member in cast:
            npc = str(member.get('npc') or '')
            marker = member.get('marker') or {}
            first, last = marker.get('first'), marker.get('last')
            if len(npc.encode('utf-8')) >= NAME_BYTES:
                problems.append('%s: name %r is longer than the window has room for' % (where, npc))
            if not isinstance(first, int) or not isinstance(last, int) or first < 0 or last < first:
                problems.append('%s: %r has no usable marker range' % (where, npc))
                continue
            if last >= len(roster):
                problems.append('%s: %r covers entries %d..%d, but town %d has %d'
                                % (where, npc, first, last, location, len(roster)))
                continue
            street = range(*[STREET[location][0], STREET[location][1] + 1]) if location in STREET else range(0)
            outside = [i for i in range(first, last + 1) if i not in street]
            if outside:
                problems.append('%s: %r covers %s, which is not a street NPC (shops live there)'
                                % (where, npc, outside))
            slots += last - first + 1
            for i in range(first, last + 1):
                if i in covered:
                    problems.append('%s: entry %d is claimed by two cast members' % (where, i))
                covered.add(i)
                speakers[i] = npc
            lines = [line for line in (member.get('say') or []) if str(line).strip()]
            if not lines:
                problems.append('%s: %r has nothing to say' % (where, npc))
            for line in lines:
                if len(line.encode('utf-8')) >= LINE_BYTES:
                    problems.append('%s: %r has a message longer than the window holds' % (where, npc))
            if len(lines) > 3:
                problems.append('%s: %r has %d messages; the page sends three' % (where, npc, len(lines)))
        if slots > SLOTS:
            problems.append('%s: cast needs %d slots, the host has %d' % (where, slots, SLOTS))

        # A town resolves any street conversation to one entry; the contact has to be able to be
        # on it, so its range must cover the whole tail.
        contact = chapter.get('contact')
        tail = STREET.get(location)
        if not tail:
            problems.append('%s: no street range known for town %d; add it to STREET' % (where, location))
        else:
            missed = [i for i in range(tail[0], tail[1] + 1) if speakers.get(i) != contact]
            if missed:
                problems.append('%s: %r does not cover street entries %s, so the town cannot reach them'
                                % (where, contact, missed))

        objectives = chapter.get('objectives') or []
        if not objectives:
            problems.append('%s: no objectives' % where)
        for objective in objectives:
            oid = str(objective.get('id') or '')
            if not oid:
                problems.append('%s: an objective has no id' % where)
            if objective.get('kind') not in KINDS:
                problems.append('%s.%s: unknown kind %r' % (where, oid, objective.get('kind')))
            if not str(objective.get('text') or '').strip():
                problems.append('%s.%s: no text' % (where, oid))
            if objective.get('kind') == 'drive' and not float(objective.get('goal') or 0) > 0:
                problems.append('%s.%s: a drive objective needs a positive goal' % (where, oid))
            if objective.get('kind') == 'talk' and objective.get('npc') not in set(speakers.values()):
                problems.append('%s.%s: talks to %r, who this chapter never installs'
                                % (where, oid, objective.get('npc')))
        if (chapter.get('reward') or {}).get('coins') is None:
            problems.append('%s: no reward' % where)
        if not args.quiet:
            print('chapter %d %-28s %-14s %d..%d %s'
                  % (number, chapter.get('title'), chapter.get('place_name'),
                     min(covered), max(covered), contact))

    side_ids = set()
    for quest in pack.get('side_quests', []):
        where = 'side quest %s' % (quest.get('id') or '?')
        quest_id = str(quest.get('id') or '')
        if not quest_id or quest_id in side_ids:
            problems.append('%s: missing or duplicate id' % where)
        side_ids.add(quest_id)
        kind, _, index = str(quest.get('place') or '').partition(':')
        location = int(index) if index.isdigit() else 0
        roster = names(elf, location) if location else []
        if kind != 'town' or not roster:
            problems.append('%s: place %r is not a town with a roster' % (where, quest.get('place')))
            continue
        if location >= len(TOWNS) or TOWNS[location] != quest.get('place_name'):
            problems.append('%s: place_name does not match town %d' % (where, location))
        cast = quest.get('cast') or []
        speakers, covered, slots = set(), set(), 0
        for member in cast:
            npc = str(member.get('npc') or '')
            marker = member.get('marker') or {}
            first, last = marker.get('first'), marker.get('last')
            if not isinstance(first, int) or not isinstance(last, int) or first < 0 or last < first:
                problems.append('%s: %r has no usable marker range' % (where, npc))
                continue
            if last >= len(roster):
                problems.append('%s: %r covers beyond the %d-entry roster' % (where, npc, len(roster)))
                continue
            street = STREET.get(location)
            if not street or any(i < street[0] or i > street[1] for i in range(first, last + 1)):
                problems.append('%s: %r covers entries outside the town street cast' % (where, npc))
            if any(i in covered for i in range(first, last + 1)):
                problems.append('%s: cast member ranges overlap' % where)
            covered.update(range(first, last + 1))
            slots += last - first + 1
            speakers.add(npc)
            if len(npc.encode('utf-8')) >= NAME_BYTES:
                problems.append('%s: NPC name is too long for the game window' % where)
            lines = [line for line in (member.get('say') or []) if str(line).strip()]
            if not lines or len(lines) > 3:
                problems.append('%s: %r needs one to three dialogue lines' % (where, npc))
            if any(len(line.encode('utf-8')) >= LINE_BYTES for line in lines):
                problems.append('%s: %r has dialogue too long for the game window' % (where, npc))
        if slots > SLOTS:
            problems.append('%s: cast exceeds the %d-slot host limit' % (where, SLOTS))
        street = STREET.get(location)
        if street and any(i not in covered for i in range(street[0], street[1] + 1)):
            problems.append('%s: contact must cover the whole street range to be reachable' % where)
        ids = set()
        for objective in quest.get('objectives') or []:
            oid = str(objective.get('id') or '')
            if not oid or oid in ids:
                problems.append('%s: objective id is missing or duplicated' % where)
            if any(str(required) not in ids for required in objective.get('requires', [])):
                problems.append('%s.%s: prerequisites must refer to earlier objectives' % (where, oid))
            ids.add(oid)
            if objective.get('kind') not in KINDS:
                problems.append('%s.%s: unknown objective kind' % (where, oid))
            target = str(objective.get('place') or quest.get('place') or '')
            if not re.fullmatch(r'(town|road|race):\d+', target):
                problems.append('%s.%s: place %r must be a town, road, or race key' % (where, oid, target))
            if objective.get('kind') == 'talk' and objective.get('npc') not in speakers:
                problems.append('%s.%s: talk target is not installed by this quest' % (where, oid))
        if not quest.get('objectives'):
            problems.append('%s: no objectives' % where)
        if (quest.get('reward') or {}).get('coins') is None:
            problems.append('%s: no Coins reward' % where)
        if not args.quiet:
            print('%s  %s  %d..%d' % (where, quest.get('place_name'),
                                       min(covered) if covered else -1, max(covered) if covered else -1))

    if not pack.get('chapters'):
        problems.append('the pack has no chapters')
    if problems:
        print('\n%d problem(s):' % len(problems), file=sys.stderr)
        for problem in problems:
            print('  ' + problem, file=sys.stderr)
        return 1
    print('%d chapter(s) OK' % len(pack['chapters']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
