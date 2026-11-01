"""Words of ours, in the pause sheet's own letterforms.

The pause menu's rows are cells of a 2 x 11 sheet of word signs (`SYS/POSE.GSL`, the 256x256 4bpp
texture dumped as `251df9b6635a0a8b_256x256_psm14.png`), and the cell at the bottom of the left
column is empty. A row of ours goes there — and the only letters available are the sheet's own, which
is the point: the word comes out in the game's own typeface, at its own scale, in its own two colours,
instead of a different typeface pasted next to it.

This script cuts the letters it needs out of words already on the sheet and lays them into the empty
cell, then writes the result as data the runtime applies to the sheet after the game uploads it (see
`runtime/src/gs/art-words.hpp` and `art_words` in `runtime/src/gs/gs.cpp`). Because the patch copies
pixel *indices* within the same texture, no palette is involved and the two colours stay exactly the
game's.

Letters are found by the columns their fill occupies, because one letter's dark outline touches the
next and column ink would never separate them. Each letter is then grown by a flood fill from its
fill through its outline, bounded by its own row, so a neighbour's ink is left behind.

    python scripts/art_words.py --sheet <dump.png>

writes `runtime/src/gs/art-words.hpp` and a review image beside the sheet dump.
"""
import argparse
import json
import os
import sys

from PIL import Image

PITCH = 22.0          # the sheet's 11 rows over 256 px
CELL = (128, 22)      # one cell of the 2 x 11 grid
COLUMNS = 2
EMPTY_CELL = (0, 10)  # the one unused cell: left column, bottom row

# The sheet's own words, in cell order, for the letters picked below.
WORDS = ["Pause", "Warp", "Notebook", "Radio", "Items", "Settings", "Map", "Stamps", "Memo",
         "Pictures", None, "Results", "My Garage", "Peach Town", "Fuji City", "Sand polis",
         "Chestnut Canyon", "Mushroom Road", "White Mountain", "Papaya Island", "Cloud Hill",
         "My City"]


def is_fill(c):
    return c[3] > 8 and c[0] > 180 and c[1] > 180 and c[2] < 120


def is_ink(c):
    return c[3] > 8


class Sheet:
    def __init__(self, path):
        self.image = Image.open(path).convert('RGBA')
        if self.image.size != (256, 256):
            sys.exit('the sheet is 256x256; this is %dx%d' % self.image.size)
        self.px = self.image.load()

    def band(self, row):
        top = int(round(row * PITCH))
        return top, top + CELL[1]

    @staticmethod
    def cell_of(index):
        """(column, row) of a cell index in the sheet's 2 x 11 grid."""
        return (0, index) if index < 11 else (1, index - 11)

    def letters(self, cell):
        """(x, y, w, h) of each letter of a cell, left to right, found by its fill's columns."""
        column, row = self.cell_of(cell)
        x0, (y0, y1) = column * CELL[0], self.band(row)
        fill = [any(is_fill(self.px[x, y]) for y in range(y0, min(y1, 256)))
                for x in range(x0, min(x0 + CELL[0], 256))]
        runs, start = [], None
        for i, v in enumerate(fill):
            if v and start is None:
                start = i
            elif not v and start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, len(fill)))
        out = []
        for a, b in runs:
            ys = [y for y in range(y0, min(y1, 256))
                  if any(is_ink(self.px[x, y]) for x in range(x0 + a, x0 + b))]
            if ys:
                out.append((x0 + a, ys[0], b - a, ys[-1] - ys[0] + 1))
        return out

    def cut(self, letter, row, grow=2):
        """A letter's pixels with its own outline, as a box and absolute points, inside its row."""
        x, y, w, h = letter
        y0, y1 = self.band(row)
        bx0, bx1 = max(0, x - grow), min(256, x + w + grow)
        by0, by1 = max(y0, y - grow), min(y1, y + h + grow)
        inside, queue = set(), []
        for sy in range(y, y + h):
            for sx in range(x, x + w):
                if is_fill(self.px[sx, sy]):
                    inside.add((sx, sy))
                    queue.append((sx, sy))
        while queue:
            cx, cy = queue.pop()
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if (nx, ny) in inside or not (bx0 <= nx < bx1 and by0 <= ny < by1):
                    continue
                if is_ink(self.px[nx, ny]):
                    inside.add((nx, ny))
                    queue.append((nx, ny))
        xs = [p[0] for p in inside]
        ys = [p[1] for p in inside]
        box = (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        return box, sorted(inside)


def compose(sheet, letters, name, cell=(0, 10), left=20, baseline=20, gap=2):
    """Lay `letters` — (glyph, sheet cell, letter index) — into `cell`, in the sheet's own style."""
    dst_x0, dst_row = cell[0] * CELL[0], cell[1]
    dst_y0 = int(round(dst_row * PITCH))
    glyphs, x = [], left
    for glyph_name, source_cell, index in letters:
        row = sheet.cell_of(source_cell)[1]
        found = sheet.letters(source_cell)
        if index >= len(found):
            sys.exit('%s: cell %d (%s) has %d letters, not %d' %
                     (name, source_cell, WORDS[source_cell], len(found), index + 1))
        letter = found[index]
        (sx, sy, sw, sh), points = sheet.cut(letter, row)
        dst_x, dst_y = x, baseline - sh
        inside_cell = [(px_ - sx, py_ - sy) for px_, py_ in points
                       if 0 <= dst_x + px_ - sx < CELL[0] and 0 <= dst_y + py_ - sy < CELL[1]]
        glyphs.append({'name': glyph_name, 'srcX': sx, 'srcY': sy, 'w': sw, 'h': sh,
                       'dstX': dst_x, 'dstY': dst_y, 'points': inside_cell})
        x += sw + gap
    return {'name': name, 'cellX': dst_x0, 'cellY': dst_y0, 'glyphs': glyphs}


def header(sheet, word):
    """The C++ the runtime compiles: the word's pixels, and how to recognise the sheet."""
    # The pixels that tell the sheet from any other 4bpp texture: ink from the first word's letters,
    # and a corner of the empty cell that must carry none. A texture is uploaded in strips, so this is
    # what the patch recognises the sheet by — not the size of any one upload.
    # Ink probes come from several of the sheet's words, not one, so a different texture full of
    # 4bpp ink at one corner cannot pass. The gaps it must have are just as telling: the blank column
    # each column of words starts with, the empty cell, and the strip below the last row.
    ink, blank = [], []
    for cell_index, letter_index in ((0, 0), (3, 2), (5, 3), (12, 1), (16, 6), (19, 4), (21, 0)):
        letter = sheet.letters(cell_index)[letter_index]
        row = sheet.cell_of(cell_index)[1]
        _, points = sheet.cut(letter, row)
        ink.extend(points[::max(1, len(points) // 4)][:4])
    # A blank column per row, found in the sheet itself: the widest words start at the very edge of
    # their cell, so an assumed margin would be wrong for those rows.
    for row in range(11):
        y0, y1 = sheet.band(row)
        for column in (0, 1):
            x0 = column * CELL[0]
            gaps = [x for x in range(x0, x0 + CELL[0])
                    if not any(is_ink(sheet.px[x, y]) for y in range(y0, min(y1, 256)))]
            if gaps:
                blank.append((gaps[-1], y0 + 2))
    empty_x, empty_y = (EMPTY_CELL[0] * CELL[0] + CELL[0] - 8,
                        int(round(EMPTY_CELL[1] * PITCH)) + 8)
    blank.append((empty_x, empty_y))
    total = sum(len(g['points']) for g in word['glyphs'])

    lines = [
        '// Words of ours in the pause sheet\'s own letterforms.',
        '//',
        '// Generated by scripts/art_words.py: the word "%s" laid into the empty cell (%d, %d)'
        % (word['name'], word['cellX'] // CELL[0], int(round(word['cellY'] / PITCH))),
        '// from letters already on the sheet. The runtime patches this into the sheet after the game',
        '// uploads it, copying pixel indices inside the same texture, so the two colours and the',
        '// typeface are the game\'s own. Do not edit: change the letters in the script and run it again.',
        '',
        '#pragma once',
        '',
        '#include <cstdint>',
        '',
        'namespace gs::art {',
        '',
        '// One glyph of a word: the box it was cut from, where its corner goes, and its pixels as',
        '// offsets from that corner (only the letters\' own ink, never a neighbour\'s).',
        'struct Glyph {',
        '    std::uint8_t srcX, srcY;    // the sheet box the letters were cut from',
        '    std::uint8_t dstX, dstY;    // where that box\'s corner goes, in the sheet\'s own pixels',
        '    std::uint16_t first, count; // this glyph\'s run of points',
        '};',
        '',
        'struct Word {',
        '    const char *name;',
        '    std::uint16_t dstY;         // the empty cell\'s top, in sheet pixels',
        '    std::uint16_t first, count; // this word\'s glyphs',
        '};',
        '',
        '// PSMT4, the sheet\'s format. The sheet reaches the GS in strips, so the patch is keyed on the',
        '// texture\'s content (the probes below) rather than on the size of any one upload.',
        'inline constexpr std::uint32_t kSheetPsm = 20;',
        'inline constexpr std::uint8_t kEmpty[] = {%d, %d};  // a corner of the empty cell: no ink'
        % (empty_x, empty_y),
        '',
        'inline constexpr std::uint8_t kPoints[][2] = {',
    ]
    for g in word['glyphs']:
        lines.append('    // %s' % g['name'])
        row = []
        for x, y in g['points']:
            row.append('{%d, %d},' % (x, y))
            if len(row) == 6:
                lines.append('    ' + ' '.join(row))
                row = []
        if row:
            lines.append('    ' + ' '.join(row))
    lines += [
        '};',
        '',
        '// The sheet\'s signature: these pixels must carry ink (strokes across seven of its words) and',
        '// these must carry none (the blank column each column of words starts with). Together they say',
        '// "this 4bpp texture is the pause sheet" without depending on its address or its upload.',
        'inline constexpr std::uint8_t kProbes[][2] = {' + ', '.join(
            '{%d, %d}' % p for p in sorted(set(ink))) + '};',
        '',
        'inline constexpr std::uint8_t kBlanks[][2] = {' + ', '.join(
            '{%d, %d}' % p for p in sorted(set(blank))) + '};',
        '',
        'inline constexpr Glyph kGlyphs[] = {',
    ]
    first = 0
    for g in word['glyphs']:
        lines.append('    {%d, %d, %d, %d, %d, %d}, // %s' %
                     (g['srcX'], g['srcY'], g['dstX'], g['dstY'], first, len(g['points']), g['name']))
        first += len(g['points'])
    lines += [
        '};',
        '',
        'inline constexpr Word kWords[] = {',
        '    {"%s", %d, 0, %d},' % (word['name'], word['cellY'], len(word['glyphs'])),
        '};',
        '',
        '} // namespace gs::art',
        '',
    ]
    return '\n'.join(lines), total


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--sheet', required=True, help='the dumped 256x256 pause sheet (PNG)')
    parser.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      '..', 'runtime', 'src', 'gs', 'art-words.hpp'),
                        help='the header to write')
    parser.add_argument('--preview', help='where to write a review image of the empty cell')
    args = parser.parse_args()

    sheet = Sheet(args.sheet)
    # "Crew" in the sheet's own Title Case: the capital and the x-height letters come from words the
    # sheet already draws at this size, so the word needs no scaling.
    word = compose(sheet, [('C', 21, 2), ('r', 12, 4), ('e', 12, 7), ('w', 13, 7)], 'Crew')
    text, total = header(sheet, word)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)

    if args.preview:
        cell = Image.new('RGBA', CELL, (0, 112, 32, 255))
        for g in word['glyphs']:
            for dx, dy in g['points']:
                cell.putpixel((g['dstX'] + dx, g['dstY'] + dy),
                              sheet.px[g['srcX'] + dx, g['srcY'] + dy])
        os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
        cell.resize((CELL[0] * 3, CELL[1] * 3), Image.NEAREST).save(args.preview)

    span = word['glyphs'][-1]['dstX'] + word['glyphs'][-1]['w']
    print('%s: %d glyphs, %d pixels, x %d..%d, cell %d,%d' %
          (word['name'], len(word['glyphs']), total, word['glyphs'][0]['dstX'], span,
           word['cellX'], word['cellY']))
    print('wrote %s' % args.out)


if __name__ == '__main__':
    main()
