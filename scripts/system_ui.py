"""Our own menu artwork, drawn in Road Trip's palette.

The game's panels are a flat olive body inside a three-pixel brown-to-orange frame, with a cream
label bar and a solid gold highlight on the row the cursor is on. These pieces are cut so they can
be nine-sliced at any size without resampling the frame's corners, and every colour is read from a
frame of the game's own parts menu (.verify/font/menuhook/menu.png), so our screens sit in the same
scheme as the ones the game draws:

    frame edge (32, 72, 31)   frame brown (115, 80, 23)   frame orange (179, 101, 16)
    ink line (14, 20, 6)      body (55, 75, 11)           body light (71, 86, 0)
    gold (244, 236, 32)       gold fill (226, 218, 15)    cream (246, 236, 203)
    dim cream (150, 158, 120) rust (196, 72, 32)

Pieces, and what draws them:

    panel.png       48x48, 16-pixel corners — a screen's body: frame, sheen, inner shadow
    panel-thin.png  48x48, 16-pixel corners — the same without the sheen, for a plate inside a screen
    bar.png         48x32, 12-pixel corners — the header ramp a title sits on
    strip.png       8x8                  — the full-width band at the very top of the screen
    tab.png / tab-on.png   32x28, 12-pixel corners — an unselected / selected section tab
    row.png / row-on.png   8x8           — a list row's body / the row under the cursor
    row-edge.png    3x8                  — the accent bar on the left of a row under the cursor
    cursor.png      12x14                — the pointing hand, our own drawing at the game's size
    key.png         24x24, 8-pixel corners — a key cap for the footer hints
    dot-on.png / dot-off.png        8x8  — a driver is on the road / away
    meter.png / meter-fill.png      8x8  — a crew's capacity, empty and filled
    divider.png     8x4                  — a hairline between blocks
    badge.png       24x18, 8-pixel corners — a pill behind a tag or a count
    icon-*.png      20x20                — crew, friends, options, road, flag, crown, car, coin,
                                           signal, plus, minus, exit, star, gear

Run: python scripts/system_ui.py      (writes web/ui/)
"""
import os

from PIL import Image

EDGE = (32, 72, 31)
BROWN = (115, 80, 23)
ORANGE = (179, 101, 16)
INK = (14, 20, 6)
BODY = (55, 75, 11)
BODY_LIGHT = (71, 86, 0)
BODY_DARK = (44, 60, 8)
GOLD = (244, 236, 32)
GOLD_FILL = (226, 218, 15)
CREAM = (246, 236, 203)
DIM = (150, 158, 120)
RUST = (196, 72, 32)
OUT = os.path.join(os.path.dirname(__file__), '..', 'web', 'ui')


def band(inside):
    """The game's frame, as the colour each distance-from-the-edge takes."""
    return {0: EDGE, 1: BROWN, 2: ORANGE, 3: INK}.get(inside)


def panel(size=48, corner=16, sheen=True):
    """A flat body inside the game's three-step frame, with a one-pixel sheen along the top."""
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            inside_x = min(x, size - 1 - x)
            inside_y = min(y, size - 1 - y)
            frame = band(min(inside_x, inside_y))
            if frame:
                pixels[x, y] = frame + (255,)
                continue
            if sheen and inside_y == 4:
                pixels[x, y] = BODY_LIGHT + (255,)
            elif inside_y == 4:
                pixels[x, y] = ORANGE + (90,)
            else:
                pixels[x, y] = BODY + (241,)
    return image


def bar(size=48, height=32):
    """The plate's header: cream lettering sits on this brown ramp, as the game's own plates do."""
    image = Image.new('RGBA', (size, height), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(height):
        for x in range(size):
            inside_x = min(x, size - 1 - x)
            inside_y = min(y, height - 1 - y)
            frame = band(min(inside_x, inside_y))
            if frame:
                pixels[x, y] = frame + (255,)
                continue
            if inside_y > 1:
                shade = (139, 92, 26)
            elif inside_y == 0:
                shade = (164, 112, 30)
            else:
                shade = (152, 100, 28)
            pixels[x, y] = shade + (232,)
    return image


def strip():
    """The band across the top of the screen: dark, with the game's brown and orange over it."""
    image = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(8):
        colour = {0: ORANGE, 1: BROWN, 2: INK}.get(y, (26, 38, 10) if y < 6 else BODY_DARK)
        for x in range(8):
            pixels[x, y] = colour + (255,)
    return image


def row(on=False):
    image = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(8):
        for x in range(8):
            if on:
                pixels[x, y] = (GOLD if y == 0 else GOLD_FILL) + (255,)
            else:
                pixels[x, y] = (BODY_LIGHT if y == 0 else BODY) + ((150,) if y == 0 else (105,))
    return image


def row_edge():
    """The accent bar on the left of the selected row: cream over gold, in the game's own frame."""
    image = Image.new('RGBA', (3, 8), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(8):
        for x in range(3):
            pixels[x, y] = (CREAM if y == 0 else (255, 250, 120)) + (255,)
    return image


def tab(on=False):
    size, height = 32, 28
    image = Image.new('RGBA', (size, height), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(height):
        for x in range(size):
            inside_x = min(x, size - 1 - x)
            inside_y = min(y, height - 1 - y)
            if on:
                colour = {0: EDGE, 1: ORANGE, 2: GOLD}.get(min(inside_x, inside_y),
                                                           GOLD_FILL if inside_y > 2 else (250, 244, 90))
            else:
                colour = {0: EDGE, 1: BROWN, 2: ORANGE}.get(min(inside_x, inside_y), BODY_DARK)
            pixels[x, y] = colour + (255,)
    return image


def cursor():
    """The pointing hand beside the game's own option rows, our own drawing at the same size."""
    image = Image.new('RGBA', (12, 14), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(14):
        for x in range(12):
            if x < 6:
                reach = 6 - abs(y - 6)
                if x <= reach - 1 and y < 12:
                    pixels[x, y] = GOLD + (255,)
            elif 5 <= x <= 9 and 3 <= y <= 12:
                pixels[x, y] = GOLD + (255,)
    outlined = image.copy()
    ink = outlined.load()
    for y in range(14):
        for x in range(12):
            if pixels[x, y][3]:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < 12 and 0 <= ny < 14 and pixels[nx, ny][3]:
                    ink[x, y] = EDGE + (255,)
                    break
    return outlined


def key(cap=24):
    """A key cap: a raised face for a footer hint, the shape the game's own plates use."""
    image = Image.new('RGBA', (cap, cap), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(cap):
        for x in range(cap):
            inside_x = min(x, cap - 1 - x)
            inside_y = min(y, cap - 1 - y)
            frame = band(min(inside_x, inside_y))
            if frame:
                pixels[x, y] = frame + (255,)
            elif inside_y == 4:
                pixels[x, y] = (164, 112, 30) + (255,)
            elif inside_y >= cap - 6:
                pixels[x, y] = (96, 66, 20) + (255,)
            else:
                pixels[x, y] = (139, 92, 26) + (255,)
    return image


def dot(on=True, size=8):
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pixels = image.load()
    centre = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            dx, dy = x - centre, y - centre
            if dx * dx + dy * dy > (centre + 0.4) ** 2:
                continue
            if on:
                pixels[x, y] = ((CREAM if (dx + dy) < -1.2 else GOLD)) + (255,)
            else:
                pixels[x, y] = ((70, 80, 40) if (dx + dy) < -1.2 else (48, 58, 26)) + (255,)
    return image


def meter(fill=False):
    image = Image.new('RGBA', (8, 8), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(8):
        for x in range(8):
            if y == 0:
                pixels[x, y] = (INK) + (255,)
            elif y == 7:
                pixels[x, y] = (BODY_LIGHT) + (255,)
            else:
                pixels[x, y] = (GOLD if fill else BODY_DARK) + (255,)
    return image


def divider():
    image = Image.new('RGBA', (8, 4), (0, 0, 0, 0))
    pixels = image.load()
    for x in range(8):
        pixels[x, 0] = INK + (255,)
        pixels[x, 1] = BODY_DARK + (255,)
        pixels[x, 2] = BODY_LIGHT + (200,)
        pixels[x, 3] = (0, 0, 0, 0)
    return image


def badge(size=24, height=18):
    image = Image.new('RGBA', (size, height), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(height):
        for x in range(size):
            inside_x = min(x, size - 1 - x)
            inside_y = min(y, height - 1 - y)
            frame = band(min(inside_x, inside_y))
            if frame:
                pixels[x, y] = frame + (255,)
            else:
                pixels[x, y] = BROWN + (232,)
    return image


def icon(kind, size=20):
    """Small pixel icons in the game's cream, gold and rust."""
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    pixels = image.load()

    def dot(x, y, colour=CREAM):
        if 0 <= x < size and 0 <= y < size:
            pixels[x, y] = colour + (255,)

    def block(x0, y0, x1, y1, colour=CREAM):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                dot(x, y, colour)

    if kind == 'crew':
        # Three figures abreast, the middle one a step forward, the outer two dimmed.
        for cx, top, colour in ((4, 7, DIM), (10, 4, CREAM), (15, 7, DIM)):
            block(cx - 1, top, cx + 1, top + 2, colour)
            block(cx - 2, top + 4, cx + 2, top + 7, colour)
    elif kind == 'friends':
        # Two figures: the one at the front in cream, the one behind it dimmed.
        block(4, 5, 7, 8, CREAM)
        block(3, 10, 8, 15, CREAM)
        block(12, 6, 14, 9, DIM)
        block(11, 11, 16, 15, DIM)
    elif kind == 'options':
        # A gear: a ring with four teeth and a hole.
        block(8, 2, 11, 3)
        block(8, 16, 11, 17)
        block(2, 8, 3, 11)
        block(16, 8, 17, 11)
        for y in range(4, 16):
            for x in range(4, 16):
                dx, dy = x - 9.5, y - 9.5
                reach = dx * dx + dy * dy
                if 26 <= reach <= 46:
                    dot(x, y)
    elif kind == 'road':
        # A road in perspective: a widening surface with a gold line down its middle.
        for y in range(3, 18):
            half = 1 + (y - 3) // 3
            block(10 - half, y, 10 + half, y, BODY_LIGHT if y > 4 else DIM)
        for y in range(5, 17, 4):
            block(9, y, 10, y + 1, GOLD)
    elif kind == 'flag':
        block(3, 2, 4, 17)
        block(5, 3, 15, 9, ORANGE)
        block(7, 5, 12, 7)
    elif kind == 'crown':
        block(3, 11, 16, 14, GOLD)
        block(3, 8, 4, 10, GOLD)
        block(9, 6, 10, 10, GOLD)
        block(15, 8, 16, 10, GOLD)
        block(5, 15, 14, 16, (196, 172, 26))
    elif kind == 'car':
        block(2, 10, 17, 14)
        block(5, 6, 14, 9, GOLD)
        block(3, 15, 5, 16, INK)
        block(14, 15, 16, 16, INK)
        block(6, 7, 8, 8, BODY)
        block(11, 7, 13, 8, BODY)
    elif kind == 'coin':
        for y in range(4, 16):
            for x in range(4, 16):
                dx, dy = x - 9.5, y - 9.5
                if dx * dx + dy * dy <= 30:
                    dot(x, y, GOLD)
        block(9, 6, 10, 13, BROWN)
        block(8, 8, 11, 9, BROWN)
    elif kind == 'signal':
        block(3, 12, 5, 17)
        block(8, 8, 10, 17)
        block(14, 4, 16, 17)
    elif kind == 'plus':
        block(8, 4, 11, 15)
        block(4, 8, 15, 11)
    elif kind == 'minus':
        block(4, 8, 15, 11)
    elif kind == 'exit':
        block(3, 3, 4, 16)
        for y in range(5, 15):
            width = 14 - abs(y - 9)
            block(6, y, 6 + width, y)
        block(3, 3, 16, 4, DIM)
    elif kind == 'book':
        # A notebook: a cover with a spine, a label band and three lines of ruled text.
        block(3, 2, 16, 17, ORANGE)
        block(3, 2, 4, 17, BROWN)
        block(5, 4, 13, 8, CREAM)
        for y in (11, 13, 15):
            block(6, y, 12, y, CREAM if y == 11 else DIM)
    elif kind == 'radio':
        # A set with a dial on the left and three tuning marks to its right.
        block(2, 6, 17, 15, CREAM)
        block(3, 7, 17, 14, BODY)
        for y in range(8, 14):
            for x in range(4, 10):
                dx, dy = x - 6.5, y - 10.5
                if dx * dx + dy * dy <= 6:
                    dot(x, y, GOLD)
        block(11, 9, 16, 9, CREAM)
        block(11, 11, 14, 11, DIM)
        block(2, 16, 17, 17, BROWN)
    elif kind == 'items':
        # A crate: a lid, slats and a gold clasp.
        block(2, 5, 17, 7, ORANGE)
        block(3, 8, 16, 17, BROWN)
        for y in range(9, 18, 3):
            block(3, y, 16, y, BODY_DARK)
        block(9, 10, 10, 13, GOLD)
    elif kind == 'map':
        # A folded map: three panels, a route and a cross marking a town.
        block(2, 4, 17, 15, CREAM)
        block(6, 4, 7, 15, DIM)
        block(12, 4, 13, 15, DIM)
        for x in range(4, 16, 2):
            dot(x, 7 + ((x // 2) % 3), GOLD)
        block(8, 11, 10, 12, RUST)
        block(9, 10, 9, 13, RUST)
    elif kind == 'star':
        # A five-point star, wide enough to read at 20 pixels.
        for y in range(2, 10):
            half = 1 + (y - 2) // 2
            block(10 - half, y, 9 + half, y)
        for y in range(10, 18):
            half = max(0, 5 - (y - 10))
            block(10 - half, y, 9 + half, y, GOLD if y < 16 else DIM)
        block(2, 8, 17, 8, GOLD)
    return image


def main():
    os.makedirs(OUT, exist_ok=True)
    pieces = {
        'panel.png': panel(),
        'panel-thin.png': panel(sheen=False),
        'bar.png': bar(),
        'strip.png': strip(),
        'tab.png': tab(False),
        'tab-on.png': tab(True),
        'row.png': row(False),
        'row-on.png': row(True),
        'row-edge.png': row_edge(),
        'cursor.png': cursor(),
        'key.png': key(),
        'dot-on.png': dot(True),
        'dot-off.png': dot(False),
        'meter.png': meter(False),
        'meter-fill.png': meter(True),
        'divider.png': divider(),
        'badge.png': badge(),
        'icon-crew.png': icon('crew'),
        'icon-friends.png': icon('friends'),
        'icon-options.png': icon('options'),
        'icon-road.png': icon('road'),
        'icon-flag.png': icon('flag'),
        'icon-crown.png': icon('crown'),
        'icon-car.png': icon('car'),
        'icon-coin.png': icon('coin'),
        'icon-signal.png': icon('signal'),
        'icon-plus.png': icon('plus'),
        'icon-minus.png': icon('minus'),
        'icon-exit.png': icon('exit'),
        'icon-star.png': icon('star'),
        'icon-book.png': icon('book'),
        'icon-radio.png': icon('radio'),
        'icon-items.png': icon('items'),
        'icon-map.png': icon('map'),
    }
    for name, image in pieces.items():
        image.save(os.path.join(OUT, name))
        print('wrote web/ui/%s %dx%d' % (name, image.width, image.height))


if __name__ == '__main__':
    main()
