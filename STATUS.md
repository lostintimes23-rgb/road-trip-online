# Road Trip Online — where we are

Writes dated 2026-11-01. This document tracks how far the mod is, what was
actually verified (not just built), and what remains open. It is committed so it
travels with the code; [docs/media/] holds the screenshots taken along the way.

## What works

- **The game in the browser, built from your own disc.** Each page runs its own
  game (PS2 Web Recomp recompiles at first launch). The repository and the
  shipped build contain no game code or data: `generated/`, `games/`, `build/`,
  the decoded font sheets (`web/font/`) and the extracted story text
  (`stories/game-text.json`) are all per-machine outputs that `scripts/font.py`,
  `scripts/window_font.py` and `scripts/text.py` recreate from the player's own
  disc.
- **Accounts and cloud memory cards.** Sign-up and login by name and password
  (salted PBKDF2-SHA256, 120,000 rounds), up to eight live session tokens. The
  memory card (8,650,752 bytes as on the device) uploads to `/online/card` after
  every in-game save (debounced 2 s) and downloads before the game starts, so
  progression follows the account between devices.
- **One shared world, routed by places.** A world is one place in the game —
  a town (`town:3`), a field area on the road between towns (`road:14`), or a
  race course (`race:7`) — keyed by `ps2web_roadtrip_place()`, which reads the
  same tile and location words the native second screen reads. Story chapter
  never partitions players; progression stays on each card. Driving to another
  town moves the car there at once; a page that closes or stops playing leaves
  the place immediately.
- **Transport.** Poses are pushed over a WebSocket (`/online/socket`, hand-rolled
  RFC 6455 in the standard library so the server has no dependencies) at 10 poses
  a second; every accepted pose reaches the other drivers at once instead of on a
  poll. A server that cannot upgrade leaves the page on the polling path, which
  still works. The server bounds what a pose may claim: finite values inside
  100,000 units, at most 50 a second, no motion faster than 400 units a second —
  a cheater can ratchet a teleport forward about once a second, no more.
- **Other players drawn by the game itself.** The town's last four traffic slots
  are borrowed, and the recompiled car draw calls back into the host through a
  hook at the top of its loop (`0x00243190`, see `games/roadtrip/hooks.txt` at
  build time), so the pose written there is the one the frame draws: the car is
  the game's own model, lighting, shadow and depth test, not a picture over the
  screen. Borrowed slots keep their traffic AI and walk back to their routes
  between frames; the online panel shows how hard the AI fights each frame
  (`ps2web_roadtrip_remote_drift`).
- **Spectating.** Joining a place without sharing a car counts you as a spectator
  and keeps you off the road.
- **Races share a clock.** A race session per course: the first entrant opens it
  and it starts a fixed 20 s later whoever else does, so entering late joins the
  same start instead of postponing it. Finishes are reported to a leaderboard
  (entrants capped at 16, session lives 15 minutes).
- **Story of our own.** `stories/online-adventure.json` is our story content,
  delivered through the game's own dialogue window, doors and rewards machinery
  rather than an overlay.

## What was verified

- Two real Chromium clients (software GL renderer — this machine has no WebGPU
  adapter) signed up from the panel, joined the same place, and each drew the
  other's car driving in front of it in a live town.
- A/B through the car-draw hook: before the hook the borrowed car drew at its
  traffic route position (~500 units away; measured 2026-11-01, a car is about 4
  units long); with the hook it drew at the published pose. Far placements in a
  live town are verified. **Near-field placement — a car close to the camera —
  is not fully verified yet** and is noted as a caveat in the code comments.
- The gameplay gate keeps cars off menus, shops, the title and the attract demo:
  poses are only sent while `ps2web_roadtrip_gameplay()` says the page is drawing
  the world the player drives in.
- A cloud card uploaded from one page and downloaded by a second page under the
  same account carried identical bytes.
- The relay, socket hub and race clock are covered without a disc or browser by
  `tests/online_worlds.py`, `tests/online_socket.py` and `tests/online_race.py`
  (two towns are two worlds; a car moves worlds with its player; spectators are
  counted and never relayed as cars; jumping is refused until it has held; leave
  is immediate; a bad token is refused; race bounds as listed above).

## What is still open

- **No authoritative server.** Poses and memory cards are client-claimed —
  bounded, not proved. Race times are measured by the page; trusted times need
  course checkpoints read from the game's own race state.
- **Races draw nobody.** The borrowed slots are the town's traffic, which exists
  in towns and field areas only, so racers share a grid and a clock, not a
  screen.
- **Four cars per place, and borrowed.** Borrowing traffic slots caps a place at
  four other cars. Reserved actors of our own (AI off, never despawned) lift the
  cap and are also what races need.
- **Hosting is a test convenience.** `scripts/share.py` wraps a Cloudflare quick
  tunnel whose name changes every run and acts as a bearer secret — fine for
  testing with friends, not a service. A service wants a named tunnel or a real
  host, `--data` on durable disk (accounts and cards live there) and moderation.
- **A docs pass is owed.** `docs/multiplayer-spike.md` and `docs/quick-index.md`
  in the local project still describe the pre-hook state; the hook is documented
  where it lives (`host/web.cpp` comments) and here.

## Where things live

| Thing | File |
| --- | --- |
| Browser host and car-draw hook | `host/web.cpp` |
| Page, panel, rooms and radar | `web/shell.html` |
| Online server (accounts, cards, worlds, races) | `scripts/online.py`, `scripts/serve.py` |
| WebSocket push | `scripts/wsserver.py`, `scripts/wsproto.py` |
| Online adventure story | `stories/online-adventure.json` |
| Per-machine asset builders | `scripts/font.py`, `scripts/window_font.py`, `scripts/text.py` |
| Hosting tunnel | `scripts/share.py` |
