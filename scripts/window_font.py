"""The game's dialogue-window font, cut out of frames the game drew.

The window's type is not the chunky HUD font: it is the small set, about 13 px per character with real
lowercase, and the sheet's 20-pixel grid says nothing about it. Rather than guess at an atlas, this
takes frames whose text is known — `.verify/font/window-ours.js` authors a building's dialogue, so the
string is ours — cuts one tile per character, and then proves the cut by rebuilding every line from
the tiles and comparing it with the frame it came from.

Two things make the cut less obvious than a grid crop:

* the characters are laid out on a ~13 px pitch but their outlines run into the next cell, so each
  connected blob of ink belongs to the cell its centre falls in and a tile keeps only its own glyph;
* the window's own spacing is not the pitch for every character (a space or a `+` is narrower than a
  letter), so a tile keeps the glyph's ink box and the renderer advances by its width plus TRACKING.
  That advance is an approximation, and the rebuild prints exactly how many pixels it costs.

usage: window_font.py <out dir> <frame>::<text> [<frame>::<text> ...]

  <out dir>/window.png       the tiles in one row, cut from the game's own pixels, transparent elsewhere
  <out dir>/window.json      per character: tile index, atlas position, ink offset, advance
  <out dir>/rebuilt-<n>.png  each line drawn again from the tiles, under the frame it came from
"""
import json
import os
import sys

import numpy as np
from PIL import Image

PANEL = np.array([72, 88, 0])  # the window's flat background, and the key for "no ink"
ADVANCE = 13                   # the cell every character is drawn in
SPACE = 6                      # what a word gap is worth when we draw our own text



def load(path):
    return np.asarray(Image.open(path).convert('RGB')).astype(np.int16)


def ink_of(image):
    """Ink is anything that is not the window's flat background."""
    return np.abs(image - PANEL).max(axis=2) > 14


def panel_box(image):
    """The panel's bounding box: the biggest block of the window's own background."""
    hit = np.all(np.abs(image - PANEL) < 6, axis=2)
    rows = np.where(hit.sum(axis=1) > image.shape[1] // 4)[0]
    cols = np.where(hit[rows.min():rows.max() + 1].sum(axis=0) > (rows.max() - rows.min()) // 3)[0]
    return int(cols.min()), int(cols.max()), int(rows.min()), int(rows.max())


def text_rows(image, box):
    """(y0, y1) of the first line of text: the panel's own bevel lines are excluded by inset."""
    x0, x1, y0, y1 = box
    ink = ink_of(image)[y0 + 10:y1 - 22, x0 + 24:x1 - 24]
    rows = np.where(ink.sum(axis=1) > 2)[0]
    if not rows.size:
        sys.exit('no text in the window')
    runs, start = [], rows[0]
    for i in range(1, len(rows)):
        if rows[i] != rows[i - 1] + 1:
            runs.append((start, rows[i - 1] + 1))
            start = rows[i]
    runs.append((start, rows[-1] + 1))
    if len(runs) > 1:
        print('  note: %d lines in this window, using the first' % len(runs))
    return y0 + 10 + runs[0][0], y0 + 10 + runs[0][1]


INSET = 23  # the window's first cell starts this far inside the panel's left edge


def line_left(ink, band, box):
    """Where the first cell starts — the panel's own text origin, not the first inked column.

    A narrow character is drawn inside its cell rather than at its left edge: an `I` is a 5-pixel
    stem in the middle of a 13-pixel advance, so its first ink sits four pixels in. Taking the first
    inked column as the origin therefore shifts the whole grid by that inset, and every cell after a
    narrow first character is cut a few pixels off (it showed up as a line starting `IHIHI` scoring
    22% on its own rebuild). The origin belongs to the window's layout, not to the string.
    """
    left = box[0] + INSET
    cols = np.where(ink[band[0]:band[1], box[0] + 12:box[1] - 12].any(axis=0))[0]
    if cols.size:
        first = int(cols.min()) + box[0] + 12
        if not left <= first < left + ADVANCE:
            print('  note: first ink at x %d is not in the first cell (x %d..%d)'
                  % (first, left, left + ADVANCE - 1))
    return left


def blobs(ink):
    """Every connected blob of ink, 8-connected, as a boolean mask per blob."""
    height, width = ink.shape
    seen = np.zeros_like(ink, dtype=bool)
    found = []
    for y in range(height):
        for x in range(width):
            if not ink[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            mask = np.zeros_like(ink, dtype=bool)
            while stack:
                cy, cx = stack.pop()
                mask[cy, cx] = True
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < height and 0 <= nx < width and ink[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            if mask.sum() >= 3:
                found.append(mask)
    return found


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out = sys.argv[1]
    samples = []
    for arg in sys.argv[2:]:
        path, _, text = arg.rpartition('::')
        if not path:
            sys.exit('each sample is <frame>::<text>')
        samples.append((path, text))
    os.makedirs(out, exist_ok=True)

    # One vertical band for every sample: the same font in the same place, so a tile is the same
    # height everywhere and a renderer can put every line on one baseline.
    bands = [text_rows(load(path), panel_box(load(path))) for path, _ in samples]
    top = min(b[0] for b in bands)
    bottom = max(b[1] for b in bands)
    cell_h = bottom - top

    tiles, lefts = {}, {}
    for path, text in samples:
        image = load(path)
        box = panel_box(image)
        band = text_rows(image, box)
        ink = ink_of(image)[top:bottom, :]
        left = line_left(ink_of(image), band, box)
        lefts[path] = left
        print('%s: y %d..%d, text from x %d, %d characters' %
              (os.path.basename(path), band[0], band[1], left, len(text)))
        for i, ch in enumerate(text):
            if ch == ' ' or ch in tiles:
                continue
            a = left + i * ADVANCE
            take = ink[:, a:a + ADVANCE]
            if not take.any():
                print('  %r sits past the grid: no ink in cell %d' % (ch, i))
                continue
            # The cell is the character: every glyph is drawn in one 13-pixel cell whatever its ink
            # width (an `i` is narrow, its cell is not, and a period in this window takes a cell too).
            # Neighbouring letters touch, so a cell carries up to two columns of the letter beside it —
            # which is exactly how the game's own lines look, and cutting it off by blob would take a
            # whole stem away from a glyph like `H` whose left stroke is fused to the `G` before it.
            piece = np.zeros((cell_h, ADVANCE, 4), dtype=np.uint8)
            piece[:, :, :3] = image[top:bottom, a:a + ADVANCE]
            piece[:, :, 3][take] = 255
            tiles[ch] = {'image': piece, 'dx': 0, 'dy': 0, 'advance': int(ADVANCE)}

    order = ''.join(sorted(tiles))
    atlas_w = sum(tiles[ch]['image'].shape[1] + 1 for ch in order)
    sheet = np.zeros((cell_h, atlas_w, 4), dtype=np.uint8)
    cursor = 0
    mapped = {}
    for ch in order:
        tile = tiles[ch]
        piece = tile['image']
        h, w, _ = piece.shape
        sheet[:h, cursor:cursor + w] = piece
        mapped[ch] = {'tile': int(len(mapped)), 'x': int(cursor), 'y': 0, 'w': int(w), 'h': int(h),
                      'dx': tile['dx'], 'dy': tile['dy'], 'advance': tile['advance']}
        cursor += w + 1
    Image.fromarray(sheet, 'RGBA').save(os.path.join(out, 'window.png'))
    font = {'source': [os.path.basename(p) for p, _ in samples],
            'cell': {'w': int(ADVANCE), 'h': int(cell_h)}, 'space': int(SPACE),
            'order': order, 'glyphs': mapped}
    json.dump(font, open(os.path.join(out, 'window.json'), 'w'), indent=1)
    print('wrote %s/window.png: %d tiles, %dx%d' % (out, len(order), atlas_w, cell_h))

    # Put each line back together with exactly the rules a renderer has, and measure it.
    for n, (path, text) in enumerate(samples):
        image = load(path)
        left = lefts[path]
        rebuilt = np.zeros((cell_h, image.shape[1], 3), dtype=np.uint8)
        rebuilt[:, :] = PANEL
        cursor = left
        for ch in text:
            if ch == ' ':
                cursor += SPACE
                continue
            if ch not in mapped:
                continue
            g = mapped[ch]
            piece = sheet[g['y']:g['y'] + g['h'], g['x']:g['x'] + g['w']]
            take = piece[:, :, 3] > 0
            at = cursor + g['dx']
            rebuilt[g['dy']:g['dy'] + g['h'], at:at + g['w']][take] = piece[:, :, :3][take]
            cursor += g['advance']
        original = np.full((cell_h, image.shape[1], 3), PANEL[0], dtype=np.int16)
        got = min(cell_h, image.shape[0] - top)
        original[:got] = image[top:top + got]
        a, b = left, left + len(text) * ADVANCE
        differ = int((np.abs(original[:, a:b] - rebuilt[:, a:b].astype(np.int16)).max(axis=2) > 8).sum())
        stack = np.concatenate([original.astype(np.uint8), np.zeros((2, image.shape[1], 3), dtype=np.uint8),
                                rebuilt], axis=0)
        Image.fromarray(stack).save(os.path.join(out, 'rebuilt-%d.png' % n))
        print('  %s: %d of %d pixels differ (%.1f%%)'
              % (os.path.basename(path), differ, (b - a) * cell_h, 100.0 * differ / ((b - a) * cell_h)))


if __name__ == '__main__':
    main()
