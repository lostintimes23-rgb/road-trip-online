// Our online text, drawn in the game's own font over the running game.
//
// web/font/atlas-alpha.png is SYS/FONT.GSL decoded into a mask (see docs/online-ui-font.md) and
// web/font/font.json says which cell each character is. The board is a 640x448 canvas stacked on the
// game canvas and scaled with it, so a glyph drawn here lands on the same pixel grid the game draws
// on: no resampling, no web font, the game's own letterforms in whatever colour a panel wants.
//
// Only characters the map knows are drawn; anything else is skipped rather than approximated, so a
// missing glyph can never pass for the game's own type.
(function () {
  const CELL = 20;
  const WIDTH = 640, HEIGHT = 448;
  const INK = 'rgb(246,236,203)';
  const SHADOW = 'rgba(0,0,0,0.85)';

  let font = null, ink = null, shadow = null, sprites = null;
  let board = null, ctx = null, loading = null, lines = null;

  // A mask drawn in one colour: the sheet on its own, then keep only where it has ink.
  function tint(source, colour) {
    const tinted = document.createElement('canvas');
    tinted.width = source.width;
    tinted.height = source.height;
    const paint = tinted.getContext('2d');
    paint.drawImage(source, 0, 0);
    paint.globalCompositeOperation = 'source-in';
    paint.fillStyle = colour;
    paint.fillRect(0, 0, tinted.width, tinted.height);
    return tinted;
  }

  function ensure() {
    if (board) return board;
    const stage = document.getElementById('stage');
    if (!stage) return null;
    board = document.createElement('canvas');
    board.id = 'font-board';
    board.width = WIDTH;
    board.height = HEIGHT;
    board.style.cssText = 'position:absolute;pointer-events:none;display:none;image-rendering:pixelated';
    stage.appendChild(board);
    ctx = board.getContext('2d');
    window.addEventListener('resize', place);
    return board;
  }

  // Match the game canvas: the board is 640x448 in the game's own space, scaled by CSS only.
  function place() {
    if (!board) return;
    const canvas = document.getElementById('canvas'), stage = document.getElementById('stage');
    if (!canvas || !canvas.clientWidth || !stage) return;
    const a = canvas.getBoundingClientRect(), b = stage.getBoundingClientRect();
    board.style.left = (a.left - b.left) + 'px';
    board.style.top = (a.top - b.top) + 'px';
    board.style.width = a.width + 'px';
    board.style.height = a.height + 'px';
  }

  function load() {
    if (loading) return loading;
    loading = Promise.all([
      fetch('font/font.json', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      // Fetched rather than loaded as an image so a rebuilt sheet is never served from the cache: it
      // changes whenever the decoder does, and a stale one draws the old, wrong glyphs.
      fetch('font/glyphs.png', { cache: 'no-store' })
        .then((r) => (r.ok ? r.blob() : null))
        .then((blob) => (blob ? createImageBitmap(blob) : null))
        .catch(() => null),
    ]).then(([data, image]) => {
      if (data && image) {
        font = data;
        ink = tint(image, INK);
        shadow = tint(image, SHADOW);
        sprites = cut(image);
      }
      return !!(font && ink && sprites);
    });
    return loading;
  }

  // Each glyph becomes its own tiny canvas, once, out of the tile sheet the decoder writes: one tile
  // per character, already positioned inside its advance, so drawing is a plain paste with no source
  // rectangle. (The nine-argument form of drawImage was rejected for some cells of the atlas while
  // accepting others, and pasting a tile avoids the question entirely.)
  function cut(image) {
    const tileW = (font.tiles && font.tiles.width) || CELL;
    const tileH = (font.tiles && font.tiles.height) || CELL;
    const made = {};
    for (const [ch, glyph] of Object.entries(font.glyphs)) {
      const sx = (glyph.tile || 0) * tileW;
      const sprite = (colour) => {
        const piece = document.createElement('canvas');
        piece.width = tileW;
        piece.height = tileH;
        const paint = piece.getContext('2d');
        paint.drawImage(image, sx, 0, tileW, tileH, 0, 0, tileW, tileH);
        paint.globalCompositeOperation = 'source-in';
        paint.fillStyle = colour;
        paint.fillRect(0, 0, tileW, tileH);
        return piece;
      };
      made[ch] = {
        ink: sprite(INK),
        shadow: sprite(SHADOW),
        advance: glyph.advance || CELL,
      };
    }
    return made;
  }

  function plate(width, height) {
    ctx.fillStyle = 'rgba(12,20,14,0.86)';
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = 'rgba(240,226,168,0.20)';
    ctx.fillRect(0, 0, width, 1);
    ctx.fillRect(0, height - 1, width, 1);
    ctx.fillRect(0, 0, 1, height);
    ctx.fillRect(width - 1, 0, 1, height);
  }

  function drawLine(line, x, y) {
    let cursor = x;
    for (const raw of String(line).toUpperCase()) {
      if (raw === ' ') { cursor += CELL; continue; }
      const glyph = font.glyphs[raw];
      if (!glyph) continue;
      const sprite = sprites[raw];
      if (sprite) {
        ctx.drawImage(sprite.shadow, cursor, y + 1);
        ctx.drawImage(sprite.ink, cursor, y);
      }
      cursor += (sprite ? sprite.advance : glyph.advance) || CELL;
    }
    return cursor - x;
  }

  function render() {
    if (!board || !font) return;
    place();
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    if (!lines || !lines.length) { board.style.display = 'none'; return; }
    let widest = 0;
    for (const line of lines) widest = Math.max(widest, String(line).length * CELL);
    const height = lines.length * CELL + 12;
    plate(widest + 20, height);
    lines.forEach((line, i) => drawLine(line, 10, 8 + i * CELL));
    board.style.display = 'block';
  }

  // Show lines of our own text (uppercase; unsupported characters are left out). Nothing hides the board.
  function show(next) {
    ensure();
    load().then((ok) => {
      if (!ok) return console.warn('[font-board] the game font is not available');
      lines = Array.isArray(next) ? next : [next];
      render();
    });
  }

  function hide() {
    lines = null;
    if (board) board.style.display = 'none';
  }

  window.addEventListener('keydown', (event) => {
    if (event.code !== 'F8') return;
    event.preventDefault();
    if (lines) hide();
    else show(window.boardFont.last || []);
  });

  window.boardFont = {
    show: (next) => { window.boardFont.last = next; show(next); },
    hide,
    ready: load,
    get atlas() { return { font, atlas: ink }; },
  };
})();
