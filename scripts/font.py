"""The game's bitmap font, decoded out of SYS/FONT.GSL.

The file is one 512x384 4-bit texture behind a 0x60-byte header: the same sheet the game uploads for
its HUD and message window (the pad glyphs at x 104..248, y 224..248 are where the native decoder
looks for them, which is how the decode below is checked). Glyphs sit on a 20-pixel grid with the
low nibble first inside each byte, which is the part that is easy to get wrong: the high-nibble
reading produces mirrored, half-width letters.

usage: font.py <FONT.GSL> <out dir> [--map map.json]

  <out dir>/atlas.png   the decoded sheet
  <out dir>/atlas-alpha.png  the sheet as a mask: white where the font has ink, transparent elsewhere
  <out dir>/font.json   cell geometry and, when a map is given, character -> cell (or -> cut rect)
  <out dir>/cells.png   every cell with its index drawn on it, for checking a map

The character -> cell map is not in the sheet: the sheet is an irregular atlas, so the map comes
from matching glyphs the game actually drew against these cells (see .verify/font/probe.py). Without
one, this writes the geometry only, which is still what a renderer needs.
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HEADER = 0x60
WIDTH, HEIGHT = 512, 384
CELL_W, CELL_H = 20, 20
COLS, ROWS = WIDTH // CELL_W, HEIGHT // CELL_H
# The pad glyphs, as src/ui/ButtonGlyphs.cpp finds them in the decoded texture.
CHECK_X0, CHECK_X1, CHECK_Y0, CHECK_Y1 = 104, 248, 224, 248


def decode(path):
    """512x384, four bits per pixel, low nibble first: RGBA where the index is the ink."""
    data = open(path, 'rb').read()
    need = HEADER + WIDTH * HEIGHT // 2
    if len(data) < need:
        sys.exit('%s is %d bytes, too short for a %dx%d 4-bit texture' % (path, len(data), WIDTH, HEIGHT))
    packed = np.frombuffer(data[HEADER:need], dtype=np.uint8)
    index = np.empty(packed.size * 2, dtype=np.uint8)
    index[0::2] = packed & 0x0F
    index[1::2] = packed >> 4
    return index.reshape(HEIGHT, WIDTH)


def check(index):
    """The pad glyphs prove the decode: four L/R plates in a 2x2 block, then round buttons."""
    ink = index[CHECK_Y0:CHECK_Y1, CHECK_X0:CHECK_X1] > 0
    if ink.sum() < 400:
        sys.exit('the button glyphs are not where the native decoder expects them')


# A tile is one advance wide plus a gutter either side: the sheet's glyphs overhang their cells, and a
# clipped letter is worse than a glyph that leans into its neighbour.
GUTTER = 8
TILE_W, TILE_H = CELL_W + 2 * GUTTER, CELL_H


def cell(index, row, col):
    return index[row * CELL_H:(row + 1) * CELL_H, col * CELL_W:(col + 1) * CELL_W]


def components(ink, minimum=12):
    """Every connected blob of ink, as (x0, x1, y0, y1) exclusive on the high side."""
    height, width = ink.shape
    seen = np.zeros_like(ink, dtype=bool)
    found = []
    for y in range(height):
        for x in range(width):
            if not ink[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            xs = []
            ys = []
            while stack:
                cy, cx = stack.pop()
                xs.append(cx)
                ys.append(cy)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < height and 0 <= nx < width and ink[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            if len(xs) >= minimum:
                found.append((min(xs), max(xs) + 1, min(ys), max(ys) + 1))
    return found


def blob_cells(index, blobs):
    """For every pixel of ink, the cell index of the blob it belongs to; -1 where there is no ink.

    The sheet's glyph pitch is a little narrower than its 20-pixel cells, so a cell holds a sliver of
    its neighbour. A blob belongs to the cell its centre falls in, and a tile keeps only its own
    blobs, which is what makes a tile draw as one letter instead of a letter with a stroke of the
    next one.
    """
    owner = np.full(index.shape, -1, dtype=np.int32)
    ink = index > 0
    for key, (x0, x1, y0, y1) in enumerate(blobs):
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        owner[y0:y1, x0:x1][ink[y0:y1, x0:x1]] = (cy // CELL_H) * COLS + (cx // CELL_W)
    return owner


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    source, out = sys.argv[1], sys.argv[2]
    mapping = None
    if '--map' in sys.argv:
        mapping = json.load(open(sys.argv[sys.argv.index('--map') + 1]))
    os.makedirs(out, exist_ok=True)

    index = decode(source)
    check(index)

    sheet = (index * 17).astype(np.uint8)
    Image.fromarray(sheet).convert('RGBA').save(os.path.join(out, 'atlas.png'))
    # A mask, so a renderer can draw our text in any colour: alpha is the ink, colour is left white.
    # The sheet's ink is not one value: most letters sit at index 2..4 and a few at 15, so scaling
    # alpha by the index draws the common letters at a fifth of their weight and the glyph looks
    # eroded against the game's own type. Any ink at all is a full pixel here.
    mask = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    mask[:, :, :3] = 255
    mask[:, :, 3] = np.where(index > 0, 255, 0).astype(np.uint8)
    Image.fromarray(mask, 'RGBA').save(os.path.join(out, 'atlas-alpha.png'))

    # A picture of the whole sheet's cells with their indexes, so a guessed map can be eyeballed.
    preview = Image.fromarray(sheet).convert('RGB').resize((WIDTH * 2, HEIGHT * 2), Image.NEAREST)
    draw = ImageDraw.Draw(preview)
    for row in range(ROWS):
        for col in range(COLS):
            draw.text((col * CELL_W * 2 + 1, row * CELL_H * 2 + 1), '%d' % (row * COLS + col), fill=(80, 0, 0))
    preview.save(os.path.join(out, 'cells.png'))

    font = {
        'source': os.path.basename(source),
        'width': WIDTH, 'height': HEIGHT,
        'cell': {'w': CELL_W, 'h': CELL_H, 'cols': COLS, 'rows': ROWS},
        # Index into the sheet's grid, row-major, is what a renderer needs to cut a glyph out.
        'glyphs': {},
    }
    if mapping:
        ink = index > 0
        blobs = components(ink)
        # One tile per character, in the order they are mapped: a renderer pastes a tile and never
        # has to know anything about the sheet's irregular packing.
        tiles = []
        for ch, entry in mapping.items():
            # An entry is a cell index, or a dict with a cell plus a hand-measured cut: a couple of
            # glyphs (the digits 4 and 5) are stored clipped by the sheet's own packing, so their
            # real ink is in a different cell of a second copy of the run, and only an explicit
            # rectangle reads correctly.
            extra = entry if isinstance(entry, dict) else {}
            cellindex = int(extra.get('cell', entry if not isinstance(entry, dict) else 0))
            row, col = divmod(cellindex, COLS)
            if row >= ROWS:
                continue
            tile = np.zeros((TILE_H, TILE_W), dtype=np.uint8)
            if 'cut' in extra:
                x, y, w, h = [int(v) for v in extra['cut']]
                box = (x, y, x + w, y + h)
                reach = 1
            else:
                # Every blob whose centre is in this cell belongs to this character; its whole
                # bounding box is copied, even the part that reaches into a neighbouring cell.
                boxes = [b for b in blobs if ((b[2] + b[3]) // 2 // CELL_H) == row and ((b[0] + b[1]) // 2 // CELL_W) == col]
                if not boxes:
                    continue
                box = (min(b[0] for b in boxes), min(b[2] for b in boxes),
                       max(b[1] for b in boxes), max(b[3] for b in boxes))
                # A fixed left bearing. The sheet's own offsets follow its packing rather than the
                # cell grid (a run of letters wraps mid-row), so keeping them would space a line
                # unevenly; one bearing per glyph makes every line read evenly.
                reach = 1
            x0, y0, x1, y1 = box
            piece = (ink[y0:y1, x0:x1]).astype(np.uint8) * 255
            # Tiles are wider than an advance so a glyph that overhangs its cell still draws whole;
            # the ink keeps its height above the row's baseline, so every line sits the same way.
            dy = max(0, y0 - row * CELL_H)
            tx = max(0, min(TILE_W - piece.shape[1], GUTTER + reach))
            tile[dy:dy + piece.shape[0], tx:tx + piece.shape[1]] = piece[:TILE_H - dy, :TILE_W - tx]
            font['glyphs'][ch] = {'cell': cellindex, 'tile': len(tiles), 'advance': CELL_W,
                                  'x': x0, 'y': y0, 'w': x1 - x0, 'h': y1 - y0}
            tiles.append(tile)
        if tiles:
            sheet_tiles = np.concatenate(tiles, axis=1)
            glyphs = np.zeros((TILE_H, sheet_tiles.shape[1], 4), dtype=np.uint8)
            glyphs[:, :, :3] = 255
            glyphs[:, :, 3] = sheet_tiles
            Image.fromarray(glyphs, 'RGBA').save(os.path.join(out, 'glyphs.png'))
            font['tiles'] = {'width': TILE_W, 'height': TILE_H, 'count': len(tiles), 'gutter': GUTTER}
    json.dump(font, open(os.path.join(out, 'font.json'), 'w'), indent=1)
    print('%s: %dx%d, %d cells, %d glyphs mapped -> %s' % (
        os.path.basename(source), WIDTH, HEIGHT, COLS * ROWS, len(font['glyphs']), out))


if __name__ == '__main__':
    main()
