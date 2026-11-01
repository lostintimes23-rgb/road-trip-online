// Road Trip's system menu: our own art, our own screens, drawn at the game's resolution over the
// paused game, opened where the game's own pause menu used to be. It replaces every page of that
// menu — Warp, Notebook, Radio, Items, Settings and Map — and adds a crew and a friends list, so
// the game's own six pages never open.
//
// The game's pause menu is six pieces of artwork with no text in the ELF (docs/system-menu.md), and
// its pages read a save block and a location table that are documented in config/game_state.toml.
// Our pages read the same state through web/game-data.js, so a page shows the save that is actually
// loaded: stamps earned, coins found, the town you are in, the radio station, the buttons the game
// listens for. The two actions that are not ours — Warp and a control remap — go through the game's
// own machinery: a Warp walks the game's pause list (the only thing that loads a town), and a remap
// writes the map the game reads every frame.
//
// The art is ours (web/ui/*.png, from scripts/system_ui.py) and the type is the game's own HUD font
// (web/font/font.json + glyphs.png): uppercase, digits and punctuation, verified against the game's
// own frames (docs/online-ui-font.md). The HUD font advances 20 pixels a cell, so a line inside the
// panel holds about twenty-eight characters: labels are names, and the second line of a row is a
// short note, never a sentence.
(function () {
  const WIDTH = 640, HEIGHT = 448;
  const CELL = 20;                      // the HUD font's advance
  const PALETTE = {
    cream: 'rgb(246,236,203)', ink: 'rgb(20,26,8)', gold: 'rgb(244,236,32)',
    dim: 'rgb(150,158,120)', rust: 'rgb(196,72,32)',
  };

  // ---- layout, in the game's own pixels ------------------------------------------------
  const PANEL = { x: 16, y: 12, w: 608, h: 424 };
  const HEAD = { x: 26, y: 22, w: 588, h: 34 };
  // Six section tabs, one per page of the game's own pause menu, icon-only: a label would need
  // twenty pixels a letter and the strip holds 594.
  const TAB = { x: 26, y: 62, w: 94, h: 26, gap: 6 };
  // A row is two lines of the game's 20-pixel type on a 42-pixel plate, five rows to a page.
  const LIST = { x: 34, y: 100, w: 572, row: 46, rows: 5, plate: 42 };
  const NOTE = { y: 336 };
  const FOOT = { y: 372 };
  const CAP = 22;                       // a key cap's height

  const TABS = [
    { id: 'warps', name: 'WARP', icon: 'icon-road' },
    { id: 'notebook', name: 'NOTEBOOK', icon: 'icon-book' },
    { id: 'radio', name: 'RADIO', icon: 'icon-radio' },
    { id: 'items', name: 'ITEMS', icon: 'icon-items' },
    { id: 'settings', name: 'SETTINGS', icon: 'icon-options' },
    { id: 'map', name: 'MAP', icon: 'icon-map' },
  ];
  const LICENCES = ['NONE', 'B', 'A', 'SUPER A'];

  let board = null, ctx = null;
  let sheet = null, glyphs = null;
  const art = {};
  let open = false;
  let screen = 'root';                  // root | warps | notebook | stamps | pictures | results |
                                        // radio | items | settings | map | crew | friends
  let tab = -1;                         // which tab the strip lights, -1 on the root list
  let cursor = 0, offset = 0;
  let fade = 0;                         // the screen eases in
  let entry = null;                     // {kind, label, value, max, submit} while prompting
  let busy = false, status = '', statusAt = 0, statusLevel = 0;
  let crew = null, crews = [], friends = [], me = '', crewLimit = 20, friendLimit = 50;
  let startHeld = false, lastPad = null, frame = 0;

  // Everything the game's own pages show, read from the running game (web/game-data.js).
  function data() { return window.gameData && window.gameData.ready ? window.gameData : null; }
  let save = null;

  function readSave() {
    const game = data();
    try { save = game ? game.snapshot() : null; } catch (error) { save = null; }
    return save;
  }

  // ---- drawing helpers -----------------------------------------------------------------
  function slice(image, x, y, w, h, corner) {
    if (!image) return;
    const c = Math.min(corner, Math.floor(Math.min(image.width, image.height) / 2));
    const midW = image.width - c * 2, midH = image.height - c * 2;
    const drawW = w - c * 2, drawH = h - c * 2;
    if (drawW < 0 || drawH < 0) return;
    const rows = [[0, c, y, c], [c, midH, y + c, drawH], [image.height - c, c, y + h - c, c]];
    const cols = [[0, c, x, c], [c, midW, x + c, drawW], [image.width - c, c, x + w - c, c]];
    for (const [sy, sh, dy, dh] of rows) {
      for (const [sx, sw, dx, dw] of cols) {
        if (dw <= 0 || dh <= 0) continue;
        ctx.drawImage(image, sx, sy, sw, sh, dx, dy, dw, dh);
      }
    }
  }

  function tile(image, x, y, w, h) {
    if (!image || w <= 0 || h <= 0) return;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    for (let dy = 0; dy < h; dy += image.height) {
      for (let dx = 0; dx < w; dx += image.width) ctx.drawImage(image, x + dx, y + dy);
    }
    ctx.restore();
  }

  function textWidth(value) {
    return String(value == null ? '' : value).length * CELL;
  }

  // The HUD font is uppercase only: anything else is skipped rather than approximated, so a missing
  // glyph can never pass for the game's own type.
  function draw(value, x, y, colour, shadow) {
    if (!glyphs) return x;
    let cursorX = x;
    for (const ch of String(value == null ? '' : value).toUpperCase()) {
      if (ch === ' ') { cursorX += CELL; continue; }
      const glyph = glyphs[ch];
      if (!glyph) continue;
      if (shadow) ctx.drawImage(glyph[shadow], cursorX + 2, y + 2);
      ctx.drawImage(glyph[colour] || glyph.cream, cursorX, y);
      cursorX += glyph.advance || CELL;
    }
    return cursorX;
  }

  // Cut to the box, with a pair of full stops so a cut name reads as cut.
  function fit(value, width) {
    let line = String(value == null ? '' : value).toUpperCase();
    const room = Math.max(1, Math.floor(width / CELL));
    if (line.length > room && room > 3) line = line.slice(0, room - 2) + '..';
    return line;
  }

  function drawFitted(value, x, y, width, colour, shadow) {
    return draw(fit(value, width), x, y, colour, shadow);
  }

  function drawRight(value, right, y, colour, shadow) {
    const line = String(value == null ? '' : value).toUpperCase();
    return draw(line, right - textWidth(line), y, colour, shadow);
  }

  function drawCentre(value, centre, y, colour, shadow) {
    const line = String(value == null ? '' : value).toUpperCase();
    return draw(line, Math.round(centre - textWidth(line) / 2), y, colour, shadow);
  }

  function icon(name, x, y) {
    if (art[name]) ctx.drawImage(art[name], x, y);
  }

  function dot(x, y, on) {
    const piece = on ? art['dot-on'] : art['dot-off'];
    if (piece) ctx.drawImage(piece, x, y);
  }

  // A key cap: one label (or two, for R2) on our own cap artwork, as wide as its label.
  function keycap(label, x, y) {
    const width = Math.max(CAP + 4, textWidth(label) + 10);
    slice(art.key, x, y, width, CAP, 8);
    drawCentre(label, x + width / 2, y, 'cream', 'ink');
    return x + width;
  }

  // parts: [['Z', 'OPEN'], ['X', 'BACK']] — caps above their labels, laid out in one row.
  function hints(parts, x, y, colour) {
    let at = x;
    for (const [cap, label] of parts) {
      const start = at;
      at = keycap(cap, at, y);
      at += 6;
      draw(label, start, y + CAP + 1, colour || 'dim', 'ink');
      at = Math.max(at, start + textWidth(label)) + 20;
    }
    return at;
  }

  // A capacity meter: the track and the filled part, one eight-pixel tile each.
  function meter(x, y, width, value) {
    tile(art.meter, x, y, width, 8);
    tile(art['meter-fill'], x, y, Math.round(width * Math.max(0, Math.min(1, value))), 8);
  }

  // A pill behind a tag or a count. The pill is brown, so its text is gold on any row.
  function badge(value, x, y) {
    const width = textWidth(value) + 16;
    slice(art.badge, x, y, width, 20, 8);
    draw(value, x + 8, y - 1, 'gold', 'ink');
    return x + width;
  }

  // An on/off switch: the game's selected tab for on, its idle tab for off.
  function switchPlate(on, x, y) {
    const w = 36, h = 18;
    slice(on ? art['tab-on'] : art.tab, x, y, w, h, 8);
    ctx.fillStyle = on ? PALETTE.ink : PALETTE.cream;
    if (on) ctx.fillRect(x + w - 14, y + 4, 9, 10);
    else ctx.fillRect(x + 5, y + 4, 9, 10);
    return x + w;
  }

  function divider(y) {
    tile(art.divider, PANEL.x + 22, y, PANEL.w - 44, 3);
  }

  // ---- the pages, as rows ---------------------------------------------------------------
  function townName(index) {
    const game = data();
    const list = game ? game.towns() : [];
    const town = list.find((entry) => entry.index === index);
    return town ? town.name : 'A TOWN';
  }

  function money(value) {
    return String(Math.max(0, Math.round(value))).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  function miles(metres) {
    const value = metres / 1609;
    return (value >= 100 ? Math.round(value) : value.toFixed(1)) + ' MI';
  }

  function clockLine() {
    if (!save) return '';
    return String(save.clock.hour).padStart(2, '0') + ':' + String(save.clock.minute).padStart(2, '0');
  }

  function rowsFor() {
    if (screen === 'root') {
      const online = friends.filter((f) => f.online).length;
      return [
        { id: 'warps', label: 'WARP', icon: 'icon-road',
          note: save ? (save.visited.length + ' TOWNS ON THE MAP') : 'TOWNS YOU HAVE REACHED' },
        { id: 'notebook', label: 'NOTEBOOK', icon: 'icon-book',
          note: save ? (save.stampCount + ' OF 100 STAMPS, ' + save.coins + ' COINS') : 'STAMPS, PICTURES, RESULTS' },
        { id: 'radio', label: 'RADIO', icon: 'icon-radio',
          note: save && save.radio ? (data().nowPlaying(save) || {}).station.name + ' ON AIR' : 'THE SET IS OFF' },
        { id: 'items', label: 'ITEMS', icon: 'icon-items',
          note: save ? (money(save.money) + ' ' + (save.currency || 'COINS')) : 'WHAT YOU CARRY' },
        { id: 'settings', label: 'SETTINGS', icon: 'icon-options', note: 'CONTROLS AND THIS PAGE' },
        { id: 'map', label: 'MAP', icon: 'icon-map',
          note: save ? ('YOU ARE IN ' + townName(save.place.at || save.location)) : 'WHERE THE ROAD GOES' },
        { id: 'crew', label: 'CREW', icon: 'icon-crew',
          note: crew ? crew.name : 'RIDE WITH A CREW',
          badge: crew ? (crew.size + '/' + crew.limit) : '' },
        { id: 'friends', label: 'FRIENDS', icon: 'icon-friends',
          note: friends.length ? (online + ' OF ' + friends.length + ' ON THE ROAD') : 'NOBODY ON YOUR LIST YET',
          badge: friends.length ? String(friends.length) : '' },
        { id: 'resume', label: 'BACK TO THE ROAD', icon: 'icon-road', note: 'UNPAUSES THE GAME' },
      ];
    }
    if (screen === 'warps') {
      const rows = [];
      for (let index = 1; index <= 9; ++index) {
        const reached = save ? save.visited.indexOf(index) >= 0 : false;
        const here = save && (save.place.at || save.location) === index;
        rows.push({
          id: 'warp-to', town: index, label: townName(index), icon: 'icon-road',
          note: here ? 'YOU ARE HERE' : reached ? 'WARP HERE' : 'NOT REACHED YET',
          online: here, disabled: !reached && !here,
        });
      }
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
      return rows;
    }
    if (screen === 'notebook') {
      if (!save) return [{ id: 'back', label: 'BACK', icon: 'icon-road', note: '' }];
      return [
        { id: 'book-stamps', label: 'STAMPS', icon: 'icon-star',
          note: save.stampCount + ' OF 100 EARNED', badge: String(save.stampCount) },
        { id: 'book-pictures', label: 'PICTURES', icon: 'icon-car',
          note: save.photoCount + ' OF 100 TAKEN', badge: String(save.photoCount) },
        { id: 'book-results', label: 'RESULTS', icon: 'icon-flag',
          note: save.results.filter((place) => place !== 0xFF).length + ' RACES FINISHED' },
        { id: 'back', label: 'BACK', icon: 'icon-road', note: '' },
      ];
    }
    if (screen === 'stamps') {
      const captions = (data() && data().stampCaptions()) || [];
      const rows = [];
      for (let n = 1; n <= 100; ++n) {
        const earned = !!(save && save.stamps[n - 1]);
        rows.push({ id: 'stamp', label: 'STAMP ' + n, note: captions[n - 1] || 'NOT NAMED',
                    online: earned, disabled: !earned });
      }
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
      return rows;
    }
    if (screen === 'pictures') {
      const rows = [];
      for (let n = 1; n <= 100; ++n) {
        const taken = !!(save && save.photos[n - 1]);
        rows.push({ id: 'picture', label: 'PICTURE ' + n, note: taken ? 'TAKEN' : 'NOT TAKEN',
                    online: taken, disabled: !taken });
      }
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
      return rows;
    }
    if (screen === 'results') {
      const rows = [];
      for (let n = 1; n <= 35; ++n) {
        const place = save ? save.results[n - 1] : 0xFF;
        const raced = place !== 0xFF;
        rows.push({ id: 'result', label: 'RACE ' + n, note: raced ? ('FINISHED ' + ordinal(place + 1)) : 'NOT RACED',
                    online: raced, disabled: !raced });
      }
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
      return rows;
    }
    if (screen === 'radio') {
      const rows = [{ id: 'radio-off', label: 'OFF', icon: 'icon-minus', note: 'NO STATION' }];
      const stations = (data() && data().stations) || [];
      for (const station of stations) {
        const on = !!save && save.radio === station.id;
        rows.push({ id: 'radio-station', station, label: station.name, icon: 'icon-radio',
                    note: station.file + (on ? '  ON AIR' : ''), online: on });
      }
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
      return rows;
    }
    if (screen === 'items') {
      if (!save) return [{ id: 'back', label: 'BACK', icon: 'icon-road', note: '' }];
      const rows = [
        { id: 'item-coins', label: 'CHORO Q COINS', icon: 'icon-coin',
          note: save.coins + ' OF 100 FOUND', badge: save.coins + '/100' },
        { id: 'item-money', label: 'POCKET MONEY', icon: 'icon-coin',
          note: (save.currency || 'COINS') + ' FOR PARTS AND PAINT', badge: money(save.money) },
        { id: 'item-mileage', label: 'MILEAGE', icon: 'icon-road', note: 'THE ROAD SO FAR', badge: miles(save.mileage) },
        { id: 'item-licence', label: 'LICENCE', icon: 'icon-flag',
          note: save.licence >= 3 ? 'YOU MAY RACE ANYTHING' : 'WIN RACES TO STEP UP',
          badge: LICENCES[Math.min(3, save.licence)] },
        { id: 'item-mail', label: 'MAIL', icon: 'icon-flag',
          note: save.unread ? (save.unread + ' UNREAD') : 'NOTHING NEW', badge: String(save.mail) },
      ];
      for (const part of save.parts) {
        rows.push({ id: 'item-part', label: part.name, icon: 'icon-star',
                    note: part.id ? 'A PART IS FITTED' : 'STANDARD', badge: '#' + part.id });
      }
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
      return rows;
    }
    if (screen === 'settings') {
      const game = data();
      const rows = [];
      const actions = game ? game.actions() : [];
      for (let index = 0; index < actions.length; ++index) {
        rows.push({ id: 'setting-button', action: index, label: actions[index].name,
                    icon: 'icon-options', note: 'PRESS Z, THEN A BUTTON', badge: actions[index].button });
      }
      rows.push({ id: 'setting-gap', label: 'THIS PAGE', spacer: true });
      const stats = pageCheckbox('stats-toggle'), smooth = pageCheckbox('aa');
      rows.push({ id: 'options-stats', label: 'SHOW STATS', icon: 'icon-signal', toggle: stats,
                  note: 'FRAME RATE AND MEMORY' });
      rows.push({ id: 'options-smooth', label: 'SMOOTH EDGES', icon: 'icon-star', toggle: smooth,
                  note: smooth === null ? 'NOT ON THE GPU YET' : 'SMOOTHS EDGES AT ONCE',
                  disabled: smooth === null });
      rows.push({ id: 'options-fullscreen', label: 'FULLSCREEN', icon: 'icon-exit', toggle: !!document.fullscreenElement,
                  note: 'FILLS THE WHOLE SCREEN' });
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: 'CLOSES THIS PAGE' });
      return rows;
    }
    if (screen === 'map') return [{ id: 'back', label: 'BACK TO THE ROAD', icon: 'icon-road', note: 'CLOSES THIS PAGE' }];
    if (screen === 'crew') {
      if (!crew) {
        const rows = [{ id: 'crew-create', label: 'FOUND A CREW', icon: 'icon-flag',
                        note: 'NAME IT, THEN TAG IT' }];
        if (crews.length) rows.push({ id: 'crew-gap', label: 'CREWS ON THE ROAD', spacer: true });
        for (const other of crews) {
          rows.push({ id: 'crew-join', crew: other, label: other.name, icon: 'icon-crew',
                      note: (other.size + ' OF ' + other.limit + ' DRIVERS'), badge: other.tag });
        }
        rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
        return rows;
      }
      const rows = crew.members.map((member) => ({
        id: 'member', member, label: member.name,
        icon: member.leader ? 'icon-crown' : 'icon-crew',
        note: member.online ? (member.place || 'ON THE ROAD') : 'AWAY',
        badge: member.leader ? 'LEADER' : '',
        online: member.online,
      }));
      rows.push({ id: 'crew-gap', label: 'CREW', spacer: true });
      rows.push({ id: 'crew-motto', label: 'CREW MOTTO', icon: 'icon-star',
                  note: crew.motto ? crew.motto : 'SET WHAT YOU RIDE UNDER' });
      rows.push({ id: 'crew-leave', label: 'LEAVE THIS CREW', icon: 'icon-exit',
                  note: crew.leader ? 'HANDS THE CREW ON' : 'YOU CAN JOIN AGAIN LATER' });
      rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
      return rows;
    }
    // friends
    const rows = friends.map((friend) => ({
      id: 'friend', friend, label: friend.name, icon: 'icon-friends',
      note: friend.online ? (friend.place || 'ON THE ROAD')
                          : (friend.crew ? (friend.crew + '  AWAY') : 'AWAY'),
      badge: friend.crew || '', online: friend.online,
    }));
    if (!friends.length) {
      rows.push({ id: 'friends-empty', label: 'NOBODY YET', icon: 'icon-friends', note: 'ADD A DRIVER BELOW' });
    }
    rows.push({ id: 'friend-add', label: 'ADD A DRIVER', icon: 'icon-plus', note: 'BY ACCOUNT NAME' });
    rows.push({ id: 'back', label: 'BACK', icon: 'icon-road', note: '' });
    return rows;
  }

  function ordinal(n) {
    const suffix = (n % 10 === 1 && n % 100 !== 11) ? 'ST' : (n % 10 === 2 && n % 100 !== 12) ? 'ND'
                 : (n % 10 === 3 && n % 100 !== 13) ? 'RD' : 'TH';
    return n + suffix;
  }

  function titleFor() {
    if (screen === 'warps') return 'WARP';
    if (screen === 'notebook' || screen === 'stamps' || screen === 'pictures' || screen === 'results') return 'NOTEBOOK';
    if (screen === 'radio') return 'RADIO';
    if (screen === 'items') return 'ITEMS';
    if (screen === 'settings') return 'SETTINGS';
    if (screen === 'map') return 'MAP';
    if (screen === 'crew') return crew ? crew.name : 'CREWS';
    if (screen === 'friends') return 'FRIENDS';
    return 'ROAD TRIP';
  }

  function subtitleFor() {
    if (screen === 'warps') return save ? (save.visited.length + ' OF 9 TOWNS') : 'TOWNS YOU HAVE REACHED';
    if (screen === 'notebook') return save ? (save.stampCount + ' OF 100 STAMPS') : 'YOUR NOTEBOOK';
    if (screen === 'stamps') return (save ? save.stampCount : 0) + ' OF 100';
    if (screen === 'pictures') return (save ? save.photoCount : 0) + ' OF 100';
    if (screen === 'results') return 'RACE RESULTS';
    if (screen === 'radio') return (clockLine() + (save && save.radio ? '  ' + ((data().nowPlaying(save) || {}).station.name || '') : '')).trim();
    if (screen === 'items') return save ? money(save.money) + ' ' + (save.currency || 'COINS') : 'WHAT YOU CARRY';
    if (screen === 'settings') return 'CONTROLS AND THIS PAGE';
    if (screen === 'map') return save ? townName(save.place.at || save.location) : 'THE WORLD';
    if (screen === 'crew') return crew ? ('TAG ' + crew.tag) : 'NO CREW YET';
    if (screen === 'friends') return friends.length + ' OF ' + friendLimit;
    return me || 'NOT SIGNED IN';
  }

  function footerFor() {
    if (entry) return entry.kind === 'button' ? [['X', 'CANCEL']] : [['ENTER', 'ACCEPT'], ['X', 'CANCEL']];
    if (screen === 'map') return [['X', 'BACK'], ['ENTER', 'CLOSE']];
    if (screen === 'settings') return [['Z', 'CHANGE'], ['X', 'BACK'], ['ENTER', 'CLOSE']];
    if (screen === 'friends') return [['Z', 'OPEN'], ['R2', 'REMOVE'], ['X', 'BACK']];
    if (screen === 'root') return [['Z', 'OPEN'], ['L1', 'PAGES'], ['ENTER', 'CLOSE']];
    return [['Z', 'OPEN'], ['X', 'BACK'], ['ENTER', 'CLOSE']];
  }

  // ---- render --------------------------------------------------------------------------
  function render() {
    if (!board) return;
    place();
    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    if (!open) { board.style.display = 'none'; return; }
    board.style.display = 'block';
    if (!art.panel || !glyphs) return;

    ctx.save();
    ctx.globalAlpha = fade;

    // The frame behind: darkened, with a soft glow so our plate reads as sitting on the world.
    ctx.fillStyle = 'rgba(6,12,6,0.62)';
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    const glow = ctx.createRadialGradient(WIDTH / 2, HEIGHT / 2, 60, WIDTH / 2, HEIGHT / 2, 430);
    glow.addColorStop(0, 'rgba(74,94,22,0.18)');
    glow.addColorStop(1, 'rgba(0,0,0,0.44)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);

    slice(art.panel, PANEL.x, PANEL.y, PANEL.w, PANEL.h, 16);

    slice(art.bar, HEAD.x, HEAD.y, HEAD.w, HEAD.h, 12);
    const subtitle = subtitleFor();
    drawRight(subtitle, HEAD.x + HEAD.w - 14, HEAD.y + 6, 'dim', 'ink');
    drawFitted(titleFor(), HEAD.x + 14, HEAD.y + 6, HEAD.w - textWidth(subtitle) - 48, 'cream', 'ink');

    drawTabs();
    divider(94);

    if (entry) { drawEntry(); ctx.restore(); return; }

    if (screen === 'map') drawMap();
    const rows = rowsFor();
    clamp(rows);
    if (screen !== 'map') {
      let y = LIST.y;
      for (let index = offset; index < Math.min(rows.length, offset + LIST.rows); ++index) {
        drawRow(rows[index], index, y);
        y += LIST.row;
      }
      if (rows.length > LIST.rows) scrollbar(rows.length);
    }
    drawNote(rows);
    divider(FOOT.y - 12);
    hints(footerFor(), LIST.x + 2, FOOT.y, 'dim');
    if (screen !== 'map' && rows.length > LIST.rows) {
      drawRight((offset + 1) + '-' + Math.min(rows.length, offset + LIST.rows) + ' OF ' + rows.length,
                LIST.x + LIST.w - 4, FOOT.y + CAP + 1, 'dim', 'ink');
    }
    ctx.restore();
  }

  // The line between the list and the footer: what the screen is about, or what just happened.
  function drawNote(rows) {
    if (status && (busy || performance.now() - statusAt < 9000)) {
      const age = performance.now() - statusAt;
      ctx.save();
      ctx.globalAlpha = fade * (busy || age < 7500 ? 1 : Math.max(0, (9000 - age) / 1500));
      drawFitted((busy ? '> ' : '') + status, LIST.x + 2, NOTE.y,
                 LIST.w - 240, busy ? 'gold' : (statusLevel ? 'rust' : 'cream'), 'ink');
      ctx.restore();
      return;
    }
    if (screen === 'radio' && save) {
      const playing = data().nowPlaying(save);
      if (!playing) { draw('THE RADIO IS OFF', LIST.x + 2, NOTE.y, 'dim', 'ink'); return; }
      const title = playing.title || playing.station.name;
      drawFitted('NOW PLAYING ' + title, LIST.x + 2, NOTE.y, LIST.w - 200, 'cream', 'ink');
      drawRight(title && playing.elapsed ? Math.round(playing.elapsed) + 'S' : '', LIST.x + LIST.w - 2, NOTE.y, 'dim', 'ink');
      return;
    }
    if (screen === 'items' && save) {
      drawFitted('LICENCE ' + LICENCES[Math.min(3, save.licence)] + '  ' + save.mail + ' MAIL', LIST.x + 2, NOTE.y, 'cream', 'ink');
      meter(LIST.x + LIST.w - 152, NOTE.y + 6, 116, save.coins / 100);
      drawRight(save.coins + '/100', LIST.x + LIST.w - 2, NOTE.y, 'cream', 'ink');
      return;
    }
    if ((screen === 'notebook' || screen === 'stamps') && save) {
      meter(LIST.x + LIST.w - 232, NOTE.y + 6, 196, save.stampCount / 100);
      drawRight(save.stampCount + '/100', LIST.x + LIST.w - 2, NOTE.y, 'cream', 'ink');
      draw('STAMPS EARNED', LIST.x + 2, NOTE.y, 'dim', 'ink');
      return;
    }
    if (screen === 'warps') {
      draw('A WARP IS THE GAME\'S OWN: PICK A TOWN YOU HAVE REACHED', LIST.x + 2, NOTE.y, 'dim', 'ink');
      return;
    }
    if (screen === 'settings') { draw('CHANGES APPLY AT ONCE', LIST.x + 2, NOTE.y, 'dim', 'ink'); return; }
    if (screen === 'map' && save) {
      drawFitted('YOU ARE IN ' + townName(save.place.at || save.location), LIST.x + 2, NOTE.y, 'cream', 'ink');
      return;
    }
    if (screen === 'crew' && crew) {
      const count = crew.size + '/' + crew.limit;
      const countWidth = textWidth(count);
      drawRight(count, LIST.x + LIST.w - 2, NOTE.y, 'cream', 'ink');
      const meterWidth = 80;
      const meterX = LIST.x + LIST.w - 2 - countWidth - 14 - meterWidth;
      meter(meterX, NOTE.y + 6, meterWidth, crew.size / Math.max(1, crew.limit));
      drawFitted(crew.motto || 'INVITE YOUR FRIENDS', LIST.x + 2, NOTE.y,
                 meterX - LIST.x - 18, crew.motto ? 'cream' : 'dim', 'ink');
      return;
    }
    if (screen === 'friends') {
      const online = friends.filter((f) => f.online).length;
      const line = online ? (online + ' ON THE ROAD NOW') : 'NOBODY ON THE ROAD RIGHT NOW';
      draw(line, LIST.x + 2, NOTE.y, online ? 'gold' : 'dim', 'ink');
    }
  }

  // The map page: every town and field area the location table names, plotted from its map tile
  // (the same tile origins the game's own map uses), with the place the car is in marked.
  function drawMap() {
    const game = data();
    const box = { x: LIST.x, y: LIST.y + 2, w: LIST.w, h: 224 };
    slice(art['panel-thin'], box.x, box.y, box.w, box.h, 16);
    if (!game || !save) { drawCentre('NO SAVE LOADED', box.x + box.w / 2, box.y + 100, 'dim', 'ink'); return; }
    const places = (game.towns() || []).filter((town) => town.tile);
    if (!places.length) { drawCentre('NO MAP THIS SAVE KNOWS', box.x + box.w / 2, box.y + 100, 'dim', 'ink'); return; }
    const points = places.map((town) => {
      const origin = game.tileOrigin(town.tile);
      return { town, x: origin.x, z: origin.z };
    });
    const minX = Math.min(...points.map((p) => p.x)), maxX = Math.max(...points.map((p) => p.x));
    const minZ = Math.min(...points.map((p) => p.z)), maxZ = Math.max(...points.map((p) => p.z));
    const spanX = Math.max(1, maxX - minX), spanZ = Math.max(1, maxZ - minZ);
    const inner = { x: box.x + 26, y: box.y + 22, w: box.w - 52, h: box.h - 44 };
    const scale = Math.min(inner.w / spanX, inner.h / spanZ);
    const at = (p) => ({ x: inner.x + (p.x - minX) * scale + (inner.w - spanX * scale) / 2,
                         y: inner.y + (p.z - minZ) * scale * 0.62 + (inner.h - spanZ * scale * 0.62) / 2 });
    const here = save.place.at || save.location;

    for (const point of points) {
      const spot = at(point);
      const reached = point.town.kind === 2 || save.visited.indexOf(point.town.index) >= 0;
      const isHere = point.town.index === here;
      ctx.fillStyle = isHere ? PALETTE.gold : reached ? PALETTE.cream : 'rgba(120,130,96,0.55)';
      const size = isHere ? 6 : point.town.kind === 2 ? 2 : 4;
      ctx.fillRect(Math.round(spot.x - size / 2), Math.round(spot.y - size / 2), size, size);
      if (point.town.kind === 1) {
        drawFitted(point.town.name, Math.round(spot.x + 8), Math.round(spot.y - 8), 180,
                   isHere ? 'gold' : reached ? 'cream' : 'dim', 'ink');
      }
    }
    draw('WORLD MAP', box.x + 14, box.y + box.h - 30, 'dim', 'ink');
    drawRight(save.visited.length + ' OF 9 TOWNS REACHED', box.x + box.w - 14, box.y + box.h - 30, 'dim', 'ink');
  }

  function clamp(rows) {
    if (cursor >= rows.length) cursor = rows.length - 1;
    if (cursor < 0) cursor = 0;
    if (rows[cursor] && rows[cursor].spacer) cursor = Math.max(0, cursor - 1);
    if (cursor < offset) offset = cursor;
    if (cursor >= offset + LIST.rows) offset = cursor - LIST.rows + 1;
    if (offset > Math.max(0, rows.length - LIST.rows)) offset = Math.max(0, rows.length - LIST.rows);
  }

  function drawTabs() {
    for (let index = 0; index < TABS.length; ++index) {
      const x = TAB.x + index * (TAB.w + TAB.gap);
      const selected = tab === index;
      slice(selected ? art['tab-on'] : art.tab, x, TAB.y, TAB.w, TAB.h, 12);
      const piece = art[TABS[index].icon];
      if (piece) {
        ctx.save();
        ctx.globalAlpha = selected ? 1 : 0.55;
        ctx.drawImage(piece, Math.round(x + (TAB.w - piece.width) / 2), Math.round(TAB.y + (TAB.h - piece.height) / 2));
        ctx.restore();
      }
      if (selected) {
        ctx.fillStyle = PALETTE.gold;
        ctx.fillRect(x + 10, TAB.y + TAB.h - 4, TAB.w - 20, 2);
      }
    }
  }

  function drawRow(row, index, y) {
    const selected = index === cursor;
    const height = LIST.plate;   // 42: the label's 20 pixels, a gap, then the note's 20
    if (row.spacer) {
      draw(row.label, LIST.x + 4, y + 2, 'dim', 'ink');
      const lineX = LIST.x + 4 + textWidth(row.label) + 12;
      tile(art.divider, lineX, y + 26, LIST.w - (lineX - LIST.x) - 8, 3);
      return;
    }
    if (selected) {
      tile(art['row-on'], LIST.x, y, LIST.w, height);
      tile(art['row-edge'], LIST.x, y, 3, height);
      // The hand bobs a pixel either way, the way a cursor does on an arcade menu.
      const bob = typeof performance !== 'undefined' ? Math.round(Math.sin(performance.now() / 190)) : 0;
      ctx.drawImage(art.cursor, LIST.x - 12 + bob, y + Math.round((height - 14) / 2));
    } else {
      tile(art.row, LIST.x, y, LIST.w, height);
    }
    let labelRight = LIST.x + LIST.w - 14;
    if (row.toggle !== undefined && row.toggle !== null) {
      switchPlate(row.toggle, labelRight - 36, y + 12);
      labelRight -= 46;
    } else if (row.online !== undefined) {
      dot(labelRight - 8, y + 7, row.online);
      labelRight -= 24;
    }
    const badgeAt = row.badge ? (labelRight - textWidth(row.badge) - 16) : 0;
    if (row.badge) { badge(row.badge, badgeAt, y + 1); labelRight = badgeAt - 12; }
    if (row.icon) icon(row.icon, LIST.x + 8, y + 10);
    const left = LIST.x + 36;
    const labelColour = selected ? 'ink' : (row.disabled ? 'dim' : 'cream');

    drawFitted(row.label, left, y + 1, labelRight - left, labelColour, selected ? null : 'ink');
    if (row.note) {
      const noteColour = selected ? 'ink' : (row.online ? 'gold' : 'dim');
      drawFitted(row.note, left, y + 21, labelRight - left + 8, noteColour, selected ? null : 'ink');
    }
  }

  function scrollbar(total) {
    const x = PANEL.x + PANEL.w - 12, y = LIST.y, h = LIST.rows * LIST.row - 4;
    ctx.fillStyle = 'rgba(14,20,6,0.55)';
    ctx.fillRect(x, y, 4, h);
    const span = Math.max(24, Math.round(h * LIST.rows / total));
    const at = Math.round((h - span) * (offset / Math.max(1, total - LIST.rows)));
    ctx.fillStyle = 'rgba(244,236,32,0.85)';
    ctx.fillRect(x, y + at, 4, span);
  }

  function drawEntry() {
    const box = { x: 64, y: 134, w: 512, h: 172 };
    slice(art.panel, box.x, box.y, box.w, box.h, 16);
    slice(art.bar, box.x + 10, box.y + 10, box.w - 20, 30, 12);
    drawFitted(entry.label, box.x + 24, box.y + 17, box.w - 48, 'cream', 'ink');
    if (entry.kind === 'confirm') {
      drawFitted('ENTER CONFIRMS', box.x + 24, box.y + 68, box.w - 48, 'gold', 'ink');
    } else if (entry.kind === 'button') {
      drawFitted('PRESS THE BUTTON TO USE', box.x + 24, box.y + 66, box.w - 48, 'gold', 'ink');
    } else {
      tile(art['row-on'], box.x + 20, box.y + 58, box.w - 40, 38);
      drawFitted((entry.value || '') + '_', box.x + 30, box.y + 64, box.w - 60, 'ink', null);
    }
    hints(entry.caps || (entry.kind === 'button' ? [['X', 'CANCEL']] : [['ENTER', 'ACCEPT'], ['X', 'CANCEL']]),
          box.x + 24, box.y + 118, 'dim');
  }

  // ---- placement -----------------------------------------------------------------------
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

  function ensure() {
    if (board) return board;
    const stage = document.getElementById('stage');
    if (!stage) return null;
    board = document.createElement('canvas');
    board.id = 'system-menu';
    board.width = WIDTH;
    board.height = HEIGHT;
    board.style.cssText = 'position:absolute;pointer-events:none;display:none;image-rendering:pixelated;z-index:3';
    stage.appendChild(board);
    ctx = board.getContext('2d');
    window.addEventListener('resize', place);
    return board;
  }

  // ---- assets --------------------------------------------------------------------------
  function makeGlyphs(json) {
    const made = {};
    const tileW = (json.tiles && json.tiles.width) || CELL;
    const tileH = (json.tiles && json.tiles.height) || CELL;
    for (const [ch, glyph] of Object.entries(json.glyphs || {})) {
      if (ch.length !== 1) continue;
      const sx = (glyph.tile || 0) * tileW;
      const cut = (colour) => {
        const piece = document.createElement('canvas');
        piece.width = tileW;
        piece.height = tileH;
        const paint = piece.getContext('2d');
        paint.drawImage(sheet, sx, 0, tileW, tileH, 0, 0, tileW, tileH);
        paint.globalCompositeOperation = 'source-in';
        paint.fillStyle = colour;
        paint.fillRect(0, 0, tileW, tileH);
        return piece;
      };
      const tinted = {};
      for (const [name, colour] of Object.entries(PALETTE)) tinted[name] = cut(colour);
      made[ch] = Object.assign({ advance: glyph.advance || CELL }, tinted);
    }
    return made;
  }

  function loadImage(src) {
    return fetch(src, { cache: 'no-store' })
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => (blob ? createImageBitmap(blob) : null))
      .catch(() => null);
  }

  const PIECES = ['panel', 'panel-thin', 'bar', 'tab', 'tab-on', 'row', 'row-on', 'row-edge', 'cursor', 'key',
                  'dot-on', 'dot-off', 'meter', 'meter-fill', 'divider', 'badge',
                  'icon-crew', 'icon-friends', 'icon-options', 'icon-road', 'icon-flag',
                  'icon-crown', 'icon-signal', 'icon-plus', 'icon-exit', 'icon-star', 'icon-car',
                  'icon-coin', 'icon-book', 'icon-radio', 'icon-items', 'icon-map'];

  let loading = null;
  function loadAssets() {
    if (loading) return loading;
    ensure();
    loading = Promise.all([
      fetch('font/font.json', { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      loadImage('font/glyphs.png'),
      Promise.all(PIECES.map((name) => loadImage('ui/' + name + '.png').then((image) => [name, image]))),
    ]).then(([json, image, loaded]) => {
      if (json && image) {
        sheet = image;
        glyphs = makeGlyphs(json);
      }
      for (const [name, piece] of loaded) if (piece) art[name] = piece;
      return !!(glyphs && art.panel);
    }).catch(() => false);
    return loading;
  }

  // ---- pad handshake -------------------------------------------------------------------
  // A one-shot Start press: the game pauses on the rising edge, and the shell's own poll sends its
  // real state on the next change, which is the release. Without the pulse our menu would sit over a
  // world that is still being simulated.
  function pulseStart() {
    if (startHeld) return;
    if (!window.Module || typeof Module._ps2web_pad !== 'function') return;
    startHeld = true;
    try { Module._ps2web_pad(0, 1 << 3, 128, 128, 128, 128); } catch (e) {}
    setTimeout(() => {
      try { Module._ps2web_pad(0, 0, 128, 128, 128, 128); } catch (e) {}
      startHeld = false;
    }, 120);
  }

  // ---- the page's own settings, which the settings page drives -------------------------
  function pageCheckbox(id) {
    const input = document.getElementById(id);
    return input ? !!input.checked : null;
  }

  function togglePage(id) {
    const input = document.getElementById(id);
    if (!input || input.disabled) return false;
    input.click();
    return true;
  }

  // ---- data ----------------------------------------------------------------------------
  function session() {
    const bridge = window.roadtripBridge;
    return bridge && typeof bridge.session === 'function' ? bridge.session() : { token: '', name: '' };
  }

  function ask(path, body) {
    const bridge = window.roadtripBridge;
    if (!bridge) return Promise.reject(new Error('The online side is not running.'));
    return bridge.json(bridge.query(path), body);
  }

  async function refresh() {
    readSave();
    const who = session();
    me = (who.name || '').toUpperCase();
    if (!who.token) {
      crew = null; crews = []; friends = [];
      setStatus('SIGN IN FROM THE BAR TO USE CREWS AND FRIENDS', 0);
      render();
      return;
    }
    try {
      const [crewData, friendData] = await Promise.all([ask('online/crew'), ask('online/friends')]);
      crew = crewData.crew || null;
      crews = crewData.crews || [];
      if (crewData.limit) crewLimit = crewData.limit;
      friends = friendData.friends || [];
      if (friendData.limit) friendLimit = friendData.limit;
    } catch (error) {
      setStatus(String(error.message || error).toUpperCase(), 1);
    }
    render();
  }

  function setStatus(message, level) {
    status = message || '';
    statusAt = performance.now();
    statusLevel = level || 0;
  }

  // ---- actions -------------------------------------------------------------------------
  async function act() {
    const rows = rowsFor();
    const row = rows[cursor];
    if (!row || busy || row.spacer) return;
    switch (row.id) {
      case 'resume': close(); return;
      case 'back':
        if (screen === 'root') { close(); return; }
        if (screen === 'stamps' || screen === 'pictures' || screen === 'results') { go('notebook'); return; }
        back();
        return;
      case 'warps': go('warps'); return;
      case 'notebook': go('notebook'); return;
      case 'radio': go('radio'); return;
      case 'items': go('items'); return;
      case 'settings': go('settings'); return;
      case 'map': go('map'); return;
      case 'crew': go('crew'); return;
      case 'friends': go('friends'); return;
      case 'book-stamps': go('stamps'); return;
      case 'book-pictures': go('pictures'); return;
      case 'book-results': go('results'); return;
      case 'warp-to': traveller(row.town); return;
      case 'options-stats':
        if (!togglePage('stats-toggle')) setStatus('NO STATS CHECKBOX IN THIS PAGE', 1);
        render();
        return;
      case 'options-smooth':
        if (row.disabled) { setStatus('THIS BUILD IS NOT ON THE GPU YET', 1); render(); return; }
        togglePage('aa');
        render();
        return;
      case 'options-fullscreen': {
        const button = document.getElementById('fullscreen');
        if (button) { button.click(); setTimeout(render, 400); }
        return;
      }
      case 'setting-button': {
        const game = data();
        if (!game) { setStatus('THE GAME HAS NOT STARTED', 1); render(); return; }
        entry = { kind: 'button', label: row.label, action: row.action, submit: null };
        render();
        return;
      }
      case 'crew-create':
        prompt('CREW NAME', 28, (name) => prompt('CREW TAG, 2 TO 5 LETTERS', 5, (tag) => {
          run('Founding ' + name + '...', () => ask('online/crew', { action: 'create', name, tag }));
        }));
        return;
      case 'crew-join':
        run('Joining ' + row.crew.name + '...',
            () => ask('online/crew', { action: 'join', name: row.crew.name }));
        return;
      case 'crew-motto':
        prompt('CREW MOTTO', 40, (motto) => {
          run('Setting the motto...', () => ask('online/crew', { action: 'motto', motto }));
        });
        return;
      case 'crew-leave':
        confirm('LEAVE ' + crew.name, () => {
          run('Leaving your crew...', () => ask('online/crew', { action: 'leave' }));
        });
        return;
      case 'friend-add':
        prompt('DRIVER NAME', 16, (name) => {
          run('Adding ' + name + '...', () => ask('online/friends', { action: 'add', name }));
        });
        return;
      default: return;
    }
  }

  // A Warp is the game's own pause list walking to the town's row, so it happens with our screen
  // out of the way: the overlay hides, the game travels, and the page comes back on the far side.
  async function traveller(town) {
    const game = data();
    if (!game || !save) { setStatus('THE GAME HAS NOT STARTED', 1); render(); return; }
    if (save.visited.indexOf(town) < 0) {
      setStatus('YOU HAVE NOT REACHED ' + townName(town) + ' YET', 1);
      render();
      return;
    }
    busy = true;
    setStatus('WARPING TO ' + townName(town) + '...', 0);
    render();
    board.style.display = 'none';
    open = false;
    await game.travel(town);
    busy = false;
    setStatus('');
    readSave();
    render();
  }

  function go(next) {
    screen = next;
    tab = TABS.findIndex((item) => item.id === next);
    cursor = 0;
    offset = 0;
    setStatus('');
    readSave();
    render();
    if (next === 'crew' || next === 'friends') refresh();
  }

  async function run(label, action) {
    busy = true;
    setStatus(label, 0);
    render();
    try {
      await action();
      setStatus('', 0);
    } catch (error) {
      setStatus(String(error.message || error).toUpperCase(), 1);
    }
    busy = false;
    await refresh();
  }

  function prompt(label, max, submit) {
    entry = { kind: 'text', label, value: '', max, submit };
    render();
  }

  function confirm(label, action) {
    entry = { kind: 'confirm', label, value: '', submit: () => action() };
    render();
  }

  async function removeFriend() {
    if (screen !== 'friends' || busy) return;
    const row = rowsFor()[cursor];
    if (!row || row.id !== 'friend') return;
    confirm('REMOVE ' + row.friend.name, () => run('Removing ' + row.friend.name + '...',
           () => ask('online/friends', { action: 'remove', name: row.friend.name })));
  }

  // ---- open and close ------------------------------------------------------------------
  function show() {
    ensure();
    if (!open) {
      fade = 0;
      open = true;
      screen = 'root';
      tab = -1;
      cursor = 0;
      offset = 0;
      entry = null;
      setStatus('');
    }
    board.style.display = 'block';
    readSave();
    render();
    loadAssets().then((ok) => {
      if (!ok) console.warn('[system-menu] our art or the game font is not available');
      render();
    });
    refresh();
    pulseStart();
  }

  function close() {
    if (!open) return;
    open = false;
    entry = null;
    board.style.display = 'none';
    pulseStart();
  }

  // Circle goes one screen back and only leaves the menu from the root list.
  function back() {
    if (screen === 'stamps' || screen === 'pictures' || screen === 'results') { screen = 'notebook'; tab = 1; cursor = 0; offset = 0; render(); return; }
    if (screen !== 'root') { screen = 'root'; tab = -1; cursor = 0; offset = 0; setStatus(''); render(); return; }
    close();
  }

  function move(delta) {
    const rows = screen === 'map' ? [] : rowsFor();
    if (!rows.length) return;
    let next = cursor;
    for (let step = 0; step < rows.length; ++step) {
      next += delta;
      if (next < 0) next = rows.length - 1;
      if (next >= rows.length) next = 0;
      if (!rows[next].spacer) break;
    }
    cursor = next;
    render();
  }

  function sideways(delta) {
    const current = TABS.findIndex((item) => item.id === screen);
    const base = current >= 0 ? current : (delta > 0 ? -1 : 0);
    const next = (base + delta + TABS.length) % TABS.length;
    go(TABS[next].id);
  }

  // ---- animation -----------------------------------------------------------------------
  function tick() {
    requestAnimationFrame(tick);
    if (!open) return;
    let dirty = false;
    if (fade < 1) { fade = Math.min(1, fade + 0.14); dirty = true; }
    if (status && !busy && performance.now() - statusAt > 9000) { status = ''; dirty = true; }
    // The hand bobs while the menu is open, and the game's own state (the clock, the station, a
    // race that finished) moves under a still page, so everything redraws on a slow clock.
    if (++frame % 12 === 0) { readSave(); dirty = true; }
    if (dirty) render();
  }

  // ---- input ---------------------------------------------------------------------------
  // Capture phase on window: the shell's own listeners are bubble-phase, so consuming the event here
  // keeps our keys out of the game's pad state entirely.
  function consume(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  window.addEventListener('keydown', (event) => {
    if (event.repeat) {
      if (open) consume(event);
      return;
    }
    if (entry) {
      consume(event);
      // A control remap: the next button pressed becomes the action's, written into the map the
      // game reads every frame, and the game's own default can be restored by resetting it there.
      if (entry.kind === 'button') {
        if (event.code === 'Escape' || event.code === 'KeyX') { entry = null; render(); return; }
        const game = data();
        const mask = game ? game.maskFor(event.code) : 0;
        if (!mask) { setStatus('THAT KEY IS NOT A PAD BUTTON', 1); entry = null; render(); return; }
        const action = entry.action;
        entry = null;
        const ok = game.setAction(action, mask);
        setStatus(ok ? 'SET TO ' + game.buttonFor(mask) : 'THE GAME DID NOT TAKE IT', ok ? 0 : 1);
        render();
        return;
      }
      const typing = entry.kind === 'text';
      if (event.key === 'Enter') {
        const typed = (entry.value || '').trim();
        const submit = entry.submit;
        entry = null;
        if (typed || !typing) submit(typed); else render();
      } else if (event.key === 'Escape' || event.code === 'KeyX') {
        entry = null;
        render();
      } else if (!typing) {
        return;
      } else if (event.key === 'Backspace') {
        entry.value = entry.value.slice(0, -1);
        render();
      } else if (event.key.length === 1 && event.key >= ' ' && entry.value.length < entry.max) {
        entry.value += event.key;
        render();
      }
      return;
    }
    if (!open) {
      if (event.code === 'Enter') {
        // The game would open its own pause menu here; open ours instead, and pulse Start so the
        // world is genuinely paused behind it.
        consume(event);
        show();
      }
      return;
    }
    consume(event);
    if (event.code === 'Enter') { close(); return; }
    if (event.code === 'ArrowUp') { move(-1); return; }
    if (event.code === 'ArrowDown') { move(1); return; }
    if (event.code === 'ArrowLeft') { sideways(-1); return; }
    if (event.code === 'ArrowRight') { sideways(1); return; }
    if (event.code === 'KeyZ' || event.code === 'Space') { act(); return; }
    if (event.code === 'KeyX' || event.code === 'Escape') { back(); return; }
    if (event.code === 'Digit2') { removeFriend(); return; }
  }, true);

  // The pad's Start opens the menu too, polled here because the shell only forwards buttons to the
  // game and a pad sends no keydown.
  function pollPad() {
    requestAnimationFrame(pollPad);
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    const pad = pads && Array.from(pads).find((candidate) => candidate && candidate.connected);
    if (!pad) return;
    const pressed = (index) => !!(pad.buttons[index] && pad.buttons[index].pressed);
    const now = { start: pressed(9), up: pressed(12), down: pressed(13), cross: pressed(14),
                  circle: pressed(15), select: pressed(8), l1: pressed(4), r1: pressed(5),
                  l2: pressed(6), r2: pressed(7) };
    const was = lastPad || {};
    lastPad = now;
    if (!open) {
      if (now.start && !was.start) show();
      return;
    }
    if (now.start && !was.start) { close(); return; }
    if (now.up && !was.up) { move(-1); return; }
    if (now.down && !was.down) { move(1); return; }
    if (now.cross && !was.cross) { act(); return; }
    if (now.circle && !was.circle) {
      if (entry) { entry = null; render(); } else back();
      return;
    }
    if (now.select && !was.select) removeFriend();
    if ((now.r1 && !was.r1) || (now.r2 && !was.r2)) sideways(1);
    if (now.l1 && !was.l1 || now.l2 && !was.l2) sideways(-1);
  }

  tick();
  pollPad();

  window.systemMenu = {
    show, close,
    get open() { return open; },
    get screen() { return screen; },
    get cursor() { return cursor; },
    get status() { return status; },
    get rows() { return rowsFor().map((row) => ({ id: row.id, label: row.label, note: row.note || '' })); },
    get save() { return save; },
    navigate: (next) => go(next),
    refresh,
  };
})();
