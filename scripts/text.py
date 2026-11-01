#!/usr/bin/env python3
"""Reads the game's own NPC rosters and dialogue out of the executable.

Every location (01..21) has a roster at [people] rosters in config/game_state.toml: a
NULL-terminated array of pointers to entries. An entry starts with a pointer to the
speaker's name, followed by pointers to its script lines; the entry ends where the next
entry in the same roster begins (they are packed back to back, so a long entry's last
line pointer is followed by the next entry's name).

    python scripts/text.py                    # write stories/game-text.json
    python scripts/text.py --names 1          # Peach Town's roster: index, name
    python scripts/text.py --name Dust --lines

The lines are the message window's script text: '\\n' breaks a line, '\\t' separates the
choices of a question, and the other control bytes are the engine's opcodes (portraits,
expressions, which answer leads where). They are reported as `controls`, never guessed at.
"""
import argparse
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_ELF = ROOT / 'games/roadtrip/disc/SLUS_203.98'
DEFAULT_OUT = ROOT / 'stories/game-text.json'
ROSTERS = 0x2A7ED4  # [people] rosters: one pointer per location, location 1 first
LOCATIONS = 21
POINTER = range(0x200000, 0x400000)


class Elf:
    """vaddr reads for the executable the browser build runs (EE RAM addresses)."""

    def __init__(self, path):
        self.data = Path(path).read_bytes()
        phoff, = struct.unpack_from('<I', self.data, 28)
        phentsize, phnum = struct.unpack_from('<HH', self.data, 42)
        self.loads = []
        for i in range(phnum):
            kind, offset, vaddr, _, filesz, _ = struct.unpack_from('<6I', self.data, phoff + i * phentsize)
            if kind == 1:
                self.loads.append((vaddr & 0x1FFFFFFF, offset, filesz))

    def read(self, address, size):
        for vaddr, offset, filesz in self.loads:
            if vaddr <= address < vaddr + filesz:
                at = offset + address - vaddr
                return self.data[at:at + size]
        return None

    def u32(self, address):
        raw = self.read(address, 4)
        return None if raw is None else struct.unpack('<I', raw)[0]

    def string(self, address, limit=2000):
        raw = self.read(address, limit)
        if raw is None:
            return None
        return raw.split(b'\0', 1)[0]


def strip_prefix(raw):
    """The message text, without the opcodes in front of it.

    A string that starts with a control byte starts with the speech's setup: portrait and
    expression opcodes, whose argument bytes can be letters ('\\x15V\\x07Hi. You drive
    fast!'). The text begins after the last control byte near the start; a 'Y' or 'N' left
    in front of it is the answer branch the line belongs to (which choice leads here), not
    something the window prints.
    """
    if not raw or raw[0] >= 0x20:
        return raw
    cut = max(i for i, byte in enumerate(raw[:12]) if byte < 0x20)
    rest = raw[cut + 1:]
    if len(rest) > 1 and rest[0:1] in (b'Y', b'N') and (rest[1:2].isupper() or rest[1:2] == b' '):
        rest = rest[1:]
    return rest.lstrip(b' ')


def decode(raw):
    """One script string -> the text the window shows, its choices and its control bytes."""
    raw = strip_prefix(raw)
    text, controls = [], []
    for byte in raw:
        if byte in (0x0A, 0x09) or 32 <= byte < 127:
            text.append(chr(byte))
        elif byte >= 0x80:
            text.append(bytes([byte]).decode('latin-1'))
        else:
            controls.append(byte)
    shown = ''.join(text)
    choices = [part.strip() for part in shown.split('\t') if part.strip()]
    return {'text': shown.strip('\n'), 'choices': choices[1:] or [], 'controls': ['%02X' % c for c in controls]}


def roster(elf, location):
    """(address, [entry address]) for a location: its pointers up to the terminating NULL."""
    base = elf.u32(ROSTERS + 4 * (location - 1))
    if base is None or base == 0:
        return None, []
    out = []
    for index in range(256):
        entry = elf.u32(base + 4 * index)
        if not entry:
            break
        out.append(entry)
    return base, out


def entry(elf, address, bounds):
    """An NPC entry: its name and its script lines, up to the next entry's start."""
    words = sorted(b for b in bounds if b > address)
    end = words[0] if words else address + 0x400
    name_at = elf.u32(address)
    name = elf.string(name_at, 64) if name_at in POINTER else None
    lines = []
    for at in range(address + 4, end, 4):
        pointer = elf.u32(at)
        if pointer is None or pointer not in POINTER:
            break
        raw = elf.string(pointer)
        if raw is None or not raw:
            break
        lines.append(decode(raw))
    return {
        'address': '0x%08X' % address,
        'name': name.decode('latin-1') if name else '',
        'lines': lines,
    }


def everything(elf):
    out = []
    for location in range(1, LOCATIONS + 1):
        base, entries = roster(elf, location)
        bounds = entries + ([base] if base else [])
        out.append({
            'location': location,
            'address': '0x%08X' % base if base else None,
            'entries': [entry(elf, e, bounds) for e in entries],
        })
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--elf', type=Path, default=DEFAULT_ELF)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--names', type=int, metavar='LOCATION', help="print one location's roster")
    parser.add_argument('--name', help='print the lines of the NPCs whose name contains this')
    parser.add_argument('--lines', action='store_true', help='print script lines, not just names')
    parser.add_argument('--write', action='store_true', help='write the JSON (default when nothing is printed)')
    args = parser.parse_args()
    elf = Elf(args.elf)
    printed = False

    if args.names is not None:
        base, entries = roster(elf, args.names)
        print('location %d roster at %s, %d entries' % (args.names, '0x%08X' % base if base else 'none', len(entries)))
        bounds = entries + ([base] if base else [])
        for index, address in enumerate(entries):
            one = entry(elf, address, bounds)
            print('%3d  %-24s %s' % (index, one['name'], one['address']))
        printed = True

    if args.name:
        wanted = args.name.lower()
        found = 0
        for location in everything(elf):
            for one in location['entries']:
                if wanted not in one['name'].lower():
                    continue
                found += 1
                print('%s  location %d  %s  %s' % (one['name'], location['location'], one['address'], '#'))
                if args.lines:
                    for line in one['lines']:
                        choices = ('  choices: ' + ' | '.join(line['choices'])) if line['choices'] else ''
                        controls = ('  [' + ' '.join(line['controls']) + ']') if line['controls'] else ''
                        print('    %r%s%s' % (line['text'], choices, controls))
        if not found:
            raise SystemExit('no NPC named %r' % args.name)
        printed = True

    if args.write or not printed:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'elf': args.elf.name,
            'rosters': {'address': '0x%08X' % ROSTERS, 'locations': LOCATIONS},
            'note': "The game's own rosters: location 1..21, each a list of entries whose first "
                    "pointer is the speaker's name and whose rest are script lines.",
            'locations': everything(elf),
        }
        args.out.write_text(json.dumps(data, indent=1) + '\n', encoding='utf-8')
        counts = sum(len(one['entries']) for one in data['locations'])
        print('%d entries across %d locations -> %s' % (counts, LOCATIONS, args.out))


if __name__ == '__main__':
    main()
