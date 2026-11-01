// The game's own state, read the way the native second screen reads it, for the pause pages.
//
// Everything here comes out of the running game: the Adventure save block in EE RAM
// (config/game_state.toml [progress]), the location table in the ELF's data, the stamp captions and
// the live radio/clock words. Nothing is invented, so a page that shows "no stamps" is showing a
// save with no stamps rather than a placeholder. The only write is the button map the game reads
// every frame, which is how our Settings page remaps controls; and the one action that leaves the
// page is a Warp, which is driven through the game's own pause menu (see travel()).
(function () {
  const PROGRESS = 0x0177F760;      // [progress] ram_address
  const LOCATIONS = 0x002BE438;     // [locations] table: 8 bytes per location
  const CAPTIONS = 0x0032E240;      // [stamps] captions_address
  const ACTION_MAP = 0x1780A48;     // [controls] action_map: u16 masks per action
  const CLOCK = 0x00335914;         // [clock] time_address: vblanks since midnight
  const DIALOGUE_OPEN = 0x175E89B;  // [dialogue] open_address

  // [progress.fields]
  const F = {
    nextTo: 0x00D, playerName: 0x62C, currency: 0x63F, licence: 0x651, money: 0x654, mileage: 0x658,
    photos: 0x508, stamps: 0x518, coinsMissing: 0x548, mailCount: 0x628, mailInbox: 0x5A8,
    results: 0xFF0, townsUnvisited: 0x12DA, location: 0x12DC, radio: 0x12DD,
  };
  const PARTS = ['TYRES', 'ENGINE', 'CHASSIS', 'TRANSMISSION', 'STEERING', 'BRAKE'];
  // [controls] action_map offsets to `allowed_buttons`: the game's own button order, DS2 bytes
  // swapped (config/game_state.toml).
  const ACTIONS = [
    { at: 0x00, name: 'GAS PEDAL' }, { at: 0x02, name: 'BRAKE PEDAL' }, { at: 0x04, name: 'REVERSE' },
    { at: 0x06, name: 'JET FIRE' }, { at: 0x08, name: 'WING UP' }, { at: 0x0A, name: 'WING DOWN' },
    { at: 0x0C, name: 'HORN & LIGHTS' }, { at: 0x0E, name: 'CHANGE VIEW' }, { at: 0x12, name: 'NAVIGATOR' },
  ];
  const BUTTONS = [
    [0x0040, 'CROSS'], [0x0080, 'SQUARE'], [0x0020, 'CIRCLE'], [0x0010, 'TRIANGLE'],
    [0x0008, 'R1'], [0x0004, 'L1'], [0x0002, 'R2'], [0x0001, 'L2'],
    [0x0100, 'SELECT'], [0x1000, 'UP'], [0x2000, 'RIGHT'], [0x4000, 'DOWN'], [0x8000, 'LEFT'],
  ];
  // [music]: the two stations' hour-long schedules, the same tables the native second screen uses.
  const STATIONS = [
    { id: 1, name: 'PEACH FM', file: 'SOUND/1CH', tracks: [
      [0.00, 'SUNDAY ON THE WEST SIDE', 'PUSH KINGS'], [221.95, 'THE WILD ONES', 'PUSH KINGS'],
      [431.75, 'THE MINUTE', 'PUSH KINGS'], [647.25, 'JADE', 'THE WAKING HOURS'],
      [828.45, 'SUNDAY ON THE WEST SIDE', 'PUSH KINGS'], [1049.65, 'THE WILD ONES', 'PUSH KINGS'],
      [1259.50, 'THE MINUTE', 'PUSH KINGS'], [1474.95, 'JADE', 'THE WAKING HOURS'],
      [1657.95, 'SUNDAY ON THE WEST SIDE', 'PUSH KINGS'], [1879.70, 'THE WILD ONES', 'PUSH KINGS'],
      [2089.50, 'THE MINUTE', 'PUSH KINGS'], [2304.95, 'JADE', 'THE WAKING HOURS'],
      [2486.20, 'SUNDAY ON THE WEST SIDE', 'PUSH KINGS'], [2707.40, 'THE WILD ONES', 'PUSH KINGS'],
      [2917.20, 'THE MINUTE', 'PUSH KINGS'], [3132.70, 'JADE', 'THE WAKING HOURS'],
      [3314.20, 'SUNDAY ON THE WEST SIDE', 'PUSH KINGS'], [3535.95, 'THE WILD ONES', 'PUSH KINGS'],
    ] },
    { id: 2, name: 'E-RADIO', file: 'SOUND/3CH', tracks: [
      [0.85, '', 'MICHAEL WALTHIUS'], [220.00, '', 'MICHAEL WALTHIUS'], [356.20, "JAMMIN' ON SUNSET", 'MICHAEL WALTHIUS'],
      [583.10, 'CYBERBEAT', 'MICHAEL WALTHIUS'], [745.50, '', 'MICHAEL WALTHIUS'], [915.00, '', 'MICHAEL WALTHIUS'],
      [1089.55, 'SONG FOR MY CHILDREN', 'MICHAEL WALTHIUS'], [1429.25, 'ECHOES', 'MICHAEL WALTHIUS'],
      [1767.80, '', 'MICHAEL WALTHIUS'], [1917.55, '', 'MICHAEL WALTHIUS'], [2112.10, '', 'MICHAEL WALTHIUS'],
      [2306.00, 'DREAMING IN STEREO', 'MICHAEL WALTHIUS'], [2580.00, 'FUNKENGRUVEN', 'MICHAEL WALTHIUS'],
      [2773.95, 'NITEFUNK', 'MICHAEL WALTHIUS'], [2975.50, 'INCIDENT AT DARK SHORES', 'MICHAEL WALTHIUS'],
      [3214.70, 'PACIFIC COAST HIGHWAY', 'MICHAEL WALTHIUS'], [3539.10, '', 'MICHAEL WALTHIUS'],
    ] },
  ];
  // The towns' names as the disc has them, for a save whose location table has not loaded (the
  // title, or a boot that failed): [towns] names in config/game_state.toml.
  const NAMES = ['MY GARAGE', 'PEACH TOWN', 'FUJI CITY', 'SANDPOLIS', 'CHESTNUT CANYON', 'MUSHROOM ROAD',
                 'WHITE MOUNTAIN', 'PAPAYA ISLAND', 'CLOUD HILL', 'MY CITY'];

  let seenBuffer = null, stampCache = null, nameCache = null;

  function base() {
    if (!window.Module || typeof Module._ps2web_roadtrip_ram !== 'function') return 0;
    return Module._ps2web_roadtrip_ram();
  }

  // A view over guest RAM, rebuilt when the wasm heap grows (which replaces its buffer).
  function view(address, length) {
    const at = base();
    if (!at || address < 0 || address + length > 0x2000000) return null;
    const buffer = Module.HEAPU8.buffer;
    if (buffer !== seenBuffer) { seenBuffer = buffer; stampCache = null; nameCache = null; }
    return new DataView(buffer, at + address, length);
  }

  function u8(a) { const v = view(a, 1); return v ? v.getUint8(0) : 0; }
  function u16(a) { const v = view(a, 2); return v ? v.getUint16(0, true) : 0; }
  function u32(a) { const v = view(a, 4); return v ? v.getUint32(0, true) : 0; }

  function text(address, max) {
    const at = address & 0x1FFFFFF;
    const v = view(at, Math.max(1, max));
    if (!v) return '';
    let out = '';
    for (let i = 0; i < max; ++i) {
      const c = v.getUint8(i);
      if (!c) break;
      out += c >= 0x20 && c < 0x7F ? String.fromCharCode(c) : ' ';
    }
    return out.trim();
  }

  function bits(address, count) {
    const v = view(address, Math.ceil(count / 8));
    const out = [];
    for (let i = 0; i < count; ++i) out.push(v ? (v.getUint8(i >> 3) >> (i & 7)) & 1 : 0);
    return out;
  }

  function popcount(address, bytes) {
    const v = view(address, bytes);
    let n = 0;
    for (let i = 0; v && i < bytes; ++i) {
      let b = v.getUint8(i);
      while (b) { n += b & 1; b >>= 1; }
    }
    return n;
  }

  // The stamp captions: after "mail %d/%d" in the ELF's data, 100 captions with stamp 100 first,
  // NUL-separated, '#' where the game breaks a line.
  function stampCaptions() {
    if (stampCache) return stampCache;
    const v = view(CAPTIONS, 0x2000);
    if (!v) return [];
    const raw = [];
    for (let i = 0; i < 0x2000; ++i) raw.push(v.getUint8(i));
    const marker = String.fromCharCode.apply(null, raw.slice(0, 10));
    if (marker !== 'mail %d/%d') return [];
    const captions = [];
    let at = 10;
    for (let entry = 0; entry <= 100; ++entry) {
      while (at < raw.length && raw[at] === 0) ++at;
      let line = '';
      for (; at < raw.length && raw[at]; ++at) line += raw[at] === 35 ? ' ' : String.fromCharCode(raw[at]);
      line = line.replace(/\s+/g, ' ').trim();
      if (entry > 0) captions[100 - entry] = line;
    }
    stampCache = captions;
    return captions;
  }

  // The location table: name pointer, u16 map tile, doors, drivers — 0 My Garage, 1..9 towns,
  // 10..21 the field areas between them ([locations]).
  function towns() {
    if (nameCache) return nameCache;
    const list = [];
    for (let index = 0; index <= 21; ++index) {
      const entry = LOCATIONS + 8 * index;
      const pointer = u32(entry);
      const name = pointer ? text(pointer, 40).toUpperCase() : '';
      const tile = u16(entry + 4);
      list.push({ index, name: name || NAMES[index] || ('AREA ' + index), tile, kind: index >= 10 ? 2 : 1 });
    }
    nameCache = list;
    return list;
  }

  // A town's world position from its map tile: FLD tiles are a checkerboard of 1600-unit tiles
  // (0x218424 / 0x218B10), which is what the game's own map page plots.
  function tileOrigin(tile) {
    return { x: (tile & 0xF) * 800, z: ((tile >> 4) * 2 + (tile & 1)) * 1600 };
  }

  function nowPlaying(snapshot) {
    const station = STATIONS.find((s) => s.id === snapshot.radio);
    if (!station) return null;
    const seconds = (snapshot.clockVblanks || 0) / 150 * 60;
    let track = station.tracks[0];
    let end = 3600;
    for (let i = 0; i < station.tracks.length; ++i) {
      if (station.tracks[i][0] <= seconds) { track = station.tracks[i]; end = station.tracks[i + 1] ? station.tracks[i + 1][0] : 3600; }
      else break;
    }
    return { station, title: track[1], artist: track[2], elapsed: Math.max(0, seconds - track[0]),
             length: Math.max(1, end - track[0]) };
  }

  function clock() {
    const vblanks = u32(CLOCK);
    const minutes = Math.floor(vblanks / 150);
    return { hour: Math.floor(minutes / 60) % 24, minute: minutes % 60, vblanks };
  }

  function place() {
    if (!window.Module || typeof Module._ps2web_roadtrip_place !== 'function') return { kind: 0, at: 0, course: -1 };
    return { kind: Module._ps2web_roadtrip_place(0), at: Module._ps2web_roadtrip_place(1),
             course: Module._ps2web_roadtrip_place(2) };
  }

  // Everything a page needs, read once: the pages then draw from this.
  function snapshot() {
    if (!base()) return { ok: false };
    const stamps = bits(PROGRESS + F.stamps, 104);
    const photos = bits(PROGRESS + F.photos, 100);
    const results = view(PROGRESS + F.results, 35);
    const parts = [];
    for (let i = 0; i < PARTS.length; ++i) parts.push({ name: PARTS[i], id: u8(PROGRESS + F.nextTo + i) });
    const townMask = u16(PROGRESS + F.townsUnvisited);
    const shot = {
      ok: true,
      player: text(PROGRESS + F.playerName, 19),
      currency: text(PROGRESS + F.currency, 21),
      money: u32(PROGRESS + F.money),
      mileage: u32(PROGRESS + F.mileage),
      licence: u8(PROGRESS + F.licence),
      location: u8(PROGRESS + F.location),
      coins: 100 - popcount(PROGRESS + F.coinsMissing, 16),
      stampCount: stamps.slice(0, 100).reduce((a, b) => a + b, 0),
      stamps,
      photoCount: photos.reduce((a, b) => a + b, 0),
      photos,
      results: Array.from({ length: 35 }, (_, i) => (results ? results.getUint8(i) : 0xFF)),
      mail: Math.min(128, u32(PROGRESS + F.mailCount)),
      unread: 0,
      parts,
      radio: u8(PROGRESS + F.radio),
      townsUnvisited: townMask,
      visited: towns().filter((town) => town.index >= 1 && town.index <= 9 && !(townMask & (1 << town.index))).map((t) => t.index),
      clock: clock(),
      clockVblanks: u32(CLOCK),
      place: place(),
    };
    for (let i = 0; i < shot.mail; ++i) if (!(u8(PROGRESS + F.mailInbox + i) & 0x80)) shot.unread += 1;
    return shot;
  }

  // The buttons the game listens for, and the one write our Settings page makes: the game reads
  // this map every frame, so a new mask takes effect at once (and, like the game's own page, is not
  // saved: a fresh boot restores the defaults).
  function actions() {
    return ACTIONS.map((action) => ({
      name: action.name, mask: u16(ACTION_MAP + action.at),
      button: (BUTTONS.find(([mask]) => mask === u16(ACTION_MAP + action.at)) || [0, 'NONE'])[1],
    }));
  }

  function setAction(index, mask) {
    const action = ACTIONS[index];
    const v = view(ACTION_MAP + action.at, 2);
    if (!v) return false;
    v.setUint16(0, mask, true);
    return u16(ACTION_MAP + action.at) === mask;
  }

  function buttonFor(mask) {
    const found = BUTTONS.find(([value]) => value === mask);
    return found ? found[1] : mask ? '#' + mask.toString(16) : 'NONE';
  }

  function maskFor(name) {
    switch (name) {
      case 'Enter': case 'NumpadEnter': return 0x0040;    // Cross
      case 'KeyX': return 0x0020;                          // Circle
      case 'Space': return 0x0080;                         // Square
      case 'KeyS': return 0x0010;                          // Triangle
      case 'KeyW': return 0x0008;                          // R1
      case 'KeyQ': return 0x0004;                          // L1
      case 'KeyE': return 0x0002;                          // R2
      case 'KeyA': return 0x0001;                          // L2
      case 'Digit1': return 0x0100;                        // Select
      case 'ArrowUp': return 0x1000;
      case 'ArrowRight': return 0x2000;
      case 'ArrowDown': return 0x4000;
      case 'ArrowLeft': return 0x8000;
      default: return 0;
    }
  }

  // ---- pad pulses and travel -------------------------------------------------------------
  function pulse(bit, hold) {
    if (!window.Module || typeof Module._ps2web_pad !== 'function') return;
    Module._ps2web_pad(0, 1 << bit, 128, 128, 128, 128);
    return new Promise((resolve) => setTimeout(() => {
      try { Module._ps2web_pad(0, 0, 128, 128, 128, 128); } catch (e) {}
      setTimeout(resolve, hold || 140);
    }, 110));
  }
  const PAD = { start: 3, cross: 14, circle: 13, up: 4, down: 6 };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const talking = () => u8(DIALOGUE_OPEN) !== 0;

  // A Warp is the game's own: the pause list holds the towns you have reached, in town order, and
  // picking row k loads that town. So our page closes, the game's list is opened and walked to the
  // chosen town's row, and the greeting that lands you in the town's Q's Factory is stepped through
  // to "Drive around town". Returns the town it travelled to, or 0 when the row was not reachable.
  async function travel(town) {
    const visited = snapshot().visited;
    const row = visited.indexOf(town);
    if (row < 0) return 0;
    await pulse(PAD.start);          // the world, not our overlay's pause
    await wait(420);
    await pulse(PAD.start);          // the game's own pause list
    await wait(700);
    await pulse(PAD.cross);          // Warp (the first row)
    await wait(900);
    for (let i = 0; i < row; ++i) { await pulse(PAD.down, 90); await wait(60); }
    await wait(180);
    await pulse(PAD.cross);          // travel
    await wait(9000);
    for (let i = 0; i < 10 && talking(); ++i) { await pulse(PAD.cross); await wait(650); }
    for (let i = 0; i < 5; ++i) { await pulse(PAD.down, 80); await wait(60); }
    await pulse(PAD.cross);          // Drive around town
    await wait(1800);
    await pulse(PAD.cross);          // the shop's farewell
    await wait(1200);
    return town;
  }

  window.gameData = {
    snapshot, towns, stampCaptions, actions, setAction, buttonFor, maskFor,
    nowPlaying, clock, place, tileOrigin, travel, stations: STATIONS, buttons: BUTTONS,
    get ready() { return !!base(); },
  };
})();
