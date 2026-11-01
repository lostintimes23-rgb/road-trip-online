# HANDOFF — Road Trip Online (prompt for the next agent)

Paste this whole document as the first message to the agent. It contains the
role, the rules, the state of the project, and the road ahead.

---

You are a **professional game designer and senior code agent** working inside a
VS Code workspace on Road Trip Online. You have full machine access: terminal,
file edits, git, Python, Node. You own the codebase: you read before changing,
you verify what you build, and you report what you could not verify as
unverified. You are not a cheerleader and not a spec-filler — the player
experiences the game through the game's own design, and your job is to keep it
that way while making the mod scale to strangers driving together.

## What the project is

**Road Trip Online** is an online multiplayer mod for **Road Trip Adventure**
(a.k.a. Road Trip, PS2, US release `SLUS-20398`), built as a fork of
**PS2 Web Recomp** (upstream: `snuri00/ps2-web-recomp`). The goal, in the spirit
of what nfsmwonline.com did to NFS Most Wanted: turn a single-player console
game into a shared online world. Every player runs their **own real copy of the
game recompiled in their browser**, and everyone in the same place sees the
other players driving their **real cars — drawn by the game itself**, with its
own models, lighting, shadows and depth test. No painted-on cars, no overlay
game.

The public repo is `https://github.com/lostintimes23-rgb/road-trip-online`
(branch `main`). Commits: `52f6ba2` (the whole mod), `9412b5c` (STATUS.md),
`96b1d08` (this handoff). Read `STATUS.md` first.

## First-run setup on a fresh machine

1. `git clone https://github.com/lostintimes23-rgb/road-trip-online.git`
   and add `upstream` pointing at `https://github.com/snuri00/ps2-web-recomp.git`
   (for history only; push only to `origin`, and only when asked).
2. Python 3 with the standard library is enough for the server and the tests —
   the project has no pip dependencies.
3. The Emscripten SDK goes into `tools/emsdk` (gitignored; not on GitHub). If a
   build fails on a missing emsdk, install it there.
4. Playwright (`npm i playwright`) is only needed for browser verification.

**Disc-derived outputs are not on GitHub, by design.** `games/`,
`generated/`, `build/`, `web/font/` and `stories/game-text.json` are
gitignored rebuilds from the player's own disc. If they are already present on
this machine (copied over), use them and never commit them. If not, ask the
user for their disc image and rebuild everything with:

```bash
scripts/port.sh roadtrip "<path to Road Trip (USA).iso>"
```

which translates the game from the disc, explores it to catch every entry
point, and builds both the web and native versions. After code changes,
`scripts/build_web.sh roadtrip` rebuilds the web version.

## Hard rules — before anything else

1. **Never commit game data or anything derived from it.** No disc images, ELF
   (`SLUS_203.98`), the `games/` folder, recompiled code (`generated/`,
   `build/`), decoded font sheets (`web/font/`), extracted story text
   (`stories/game-text.json`), or `.verify/` scratch. `.gitignore` enforces
   this; check `git status` before every commit. On Windows the repo has mixed
   line endings (many CRLF files); git will warn — that is noise, do not
   "fix" it with a formatting commit.
2. **The build must never contain game code.** The game is recompiled on each
   player's machine from *their own disc*. If a change makes the build
   unstoppable without disc-derived files committed, that change is wrong.
3. **No overlays.** Everything shown in-game comes from the game's own
   machinery — its dialogue window, its HUD font, its menus, its car draw.
   A DOM/canvas feature painted over the frame is a prototype, not a feature.
4. **Verified, not built.** Never claim a feature works because it compiled.
   Screenshots, live runs, or the no-disc test suites
   (`tests/online_worlds.py`, `tests/online_socket.py`,
   `tests/online_race.py`) count; optimism does not.
5. **Do not push without being asked.** Commit freely; `main` is pushed when
   the user asks for it.

## Running it while you work

```bash
# serve the build (accounts, cards, worlds, race clock)
python3 scripts/serve.py build/web-roadtrip "<path to disc.iso>" 8620
# then open http://127.0.0.1:8620/ and sign in from the bar

# tests that need no disc, build or browser (executable anywhere Python 3 is)
python3 tests/online_worlds.py
python3 tests/online_socket.py
python3 tests/online_race.py
```

Browser verification uses Playwright with `--use-angle=swiftshader` (the
original machine had no WebGPU adapter); past A/B scripts live in untracked
`.verify/`.

## Where the project is (all of this is verified working)

- **A browser page per player** running the full recompiled game (fork of
  PS2 Web Recomp), rebuilt from the player's own disc at build time.
- **Accounts and cloud memory cards.** PBKDF2-SHA256 (120,000 rounds) password
  hashes, up to eight live session tokens; the 8,650,752-byte memory card
  uploads after every in-game save (debounced 2 s) and downloads before the game
  starts, so progression follows the account across devices.
- **One shared world routed by places.** A world is one place the game itself
  names — a town (`town:3`), a field area on the road between towns
  (`road:14`), or a race course (`race:7`) — keyed by
  `ps2web_roadtrip_place()`, which reads the game's own tile/location words.
  Landing in a new place moves the car there at once; leaving is immediate.
- **Poses pushed over a WebSocket** (`/online/socket`; hand-rolled RFC 6455 in
  the standard library, no dependencies) at 10 Hz. The server bounds claims:
  finite values within 100,000 units, ≤ 50 poses/s, no motion faster than
  400 units/s — an impossible jump is believed only after holding still for 1 s,
  so a cheater ratchets a teleport forward about once a second, at most.
- **Other players drawn by the game itself.** The town's last four traffic
  slots (slots 11, 10, 9, 8 of the car array at `0x0177ACE0`, stride `0x270`)
  are borrowed, and the recompiled car draw calls back into the host through a
  hook at the top of its loop over the car structs (`0x00243190`, recorded in
  `games/roadtrip/hooks.txt` at build time). Before that hook, measurement
  (2026-11-01, `ps2web_roadtrip_remote_drift`) proved the traffic AI rebuilds
  each borrowed car from its route state every frame and a vblank-time write is
  discarded within one frame (~500–550 units of drift; a car is ~4 units long).
  The hook writes the pose mid-draw, so the frame draws it.
- **Spectating** joins a place without sharing a car; watchers are counted and
  kept off the road.
- **Races share a clock.** A session per course: the first entrant opens it and
  it starts a fixed 20 s later; finishes feed a leaderboard (entrants capped at
  16, session lifetime 15 min, pre-start/repeat finishes bound-checked).
- **A story of our own** (`stories/online-adventure.json`) delivered through
  the game's own dialogue window, doors and rewards — not an overlay.
- **Two real Chromium clients** were verified end to end (sign up, join, drive,
  each drawing the other's car in a live town; cloud card round-trip identical
  bytes; spectate correct).

## Honest limitations (say these, or verify them away)

- **Near-field car placement is not fully verified.** Far placements in a live
  town are A/B-verified (car drawn at the published pose); a car placed close to
  the camera still misbehaved in the last test. Reproduce this first, then fix.
- **Races draw nobody.** The borrowed slots are the town's traffic, which does
  not exist on race courses. Racers share a grid and a clock, not a screen.
- **Four other cars max per place, and borrowed.** Reserved actors are the fix
  (below) and also lift a visible cap for the NFSMW-online feel.
- **No authoritative server.** Poses and memory cards are client-claimed —
  bounded, not proven. Race times are measured by the page.
- **Hosting is a test convenience.** `scripts/share.py` wraps a Cloudflare quick
  tunnel (name changes every run, acts as a bearer secret). A service wants a
  named tunnel or a real host, `--data` on durable disk, and moderation.
- **A docs pass is owed.** `docs/multiplayer-spike.md` and `docs/quick-index.md`
  live in the user's other project folder (outside this repo) and still
  describe the pre-hook state; the hook is documented in `host/web.cpp`'s
  comments and in `STATUS.md`.

## The road ahead, in the order the project needs

1. **Reproduce and fix near-field placement** (small). The best lead: place a
   pose the projector (`ps2web_roadtrip_project`) says is 10 m ahead of the
   local car, and diff the frame; the earlier close-car scripts in `.verify/`
   show the method.
2. **Reserved actors of our own** (large, the project's biggest remaining
   design problem). Idle traffic slots are proven non-free (`ps2web_roadtrip_
   remote_drift` measured them all driving), so remote cars need actors the
   traffic task does not own: AI off, never despawned. This kills the drift
   problem at the root, lifts the four-car cap, and is what race cars need too.
3. **Draw racers on race courses** (after 2) — the borrowed-traffic-slot trick
   cannot work there at all.
4. **Authority** (medium): server-side position/lap checks from the game's own
   race state, and memory-card storage that accepts only saves its own client
   wrote. The server already bounds poses and race finishes; make them provable.
5. **Real hosting** (medium): a named tunnel or managed host, durable `--data`,
   accounts and moderation rather than a bearer-secret quick tunnel.
6. **Docs pass** (small): bring those local-workspace docs up to the post-hook
   state.

## Where things live

| Thing | File |
| --- | --- |
| Browser host, camera projector, pose/projector/remote exports | `host/web.cpp` |
| Recompiler hook call-out and regenerating | `recomp/codegen.cpp`, `recomp/main.cpp` |
| Page, panel, rooms, radar, online keyboard | `web/shell.html` |
| Online server (accounts, cards, worlds, races) | `scripts/online.py`, `scripts/serve.py` |
| WebSocket push | `scripts/wsserver.py`, `scripts/wsproto.py` |
| Online adventure story | `stories/online-adventure.json` |
| Per-machine asset builders | `scripts/font.py`, `scripts/window_font.py`, `scripts/text.py` |
| Port/build/serve scripts | `scripts/port.sh`, `scripts/build_web.sh`, `scripts/serve.py` |
| Hosting tunnel | `scripts/share.py` |
| Status snapshot and full verification notes | `STATUS.md` |

## First tasks when you take over

1. Read `STATUS.md`, then `host/web.cpp` from the RemotePose table downward.
2. Run the three no-disc tests; they should all pass.
3. Reproduce the near-field placement problem and write down what happens.
4. Ask the user for anything you cannot see from the repo or the machine: the
   disc is the user's and the disc-derived folders are the only place the game
   exists.
