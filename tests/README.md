# Recompiler regressions

These tests construct small ELF files from synthetic instructions and need no
game data. Build the recompiler and run them from the repository root:

```sh
cmake -S . -B build/tools -DCMAKE_BUILD_TYPE=Release
cmake --build build/tools --target eerecomp
python3 tests/analysis_tailcall.py
python3 tests/elf_bss.py
python3 tests/codegen_fallthrough.py
```

Each script accepts `--recompiler /path/to/eerecomp` to check another build.
The fallthrough test also uses `clang++` or `g++` from `PATH` to check that the
generated C++ compiles.

## Online relay

`python3 tests/online_race.py` checks the race sessions with no disc, build or browser: the
first entrant opens a session on a 20 s countdown, a second joins the same clock, a finish
before the start or a second finish is refused, a finish longer than the session has run is
refused, and a session that has had its time is replaced.

`python3 tests/online_worlds.py` checks `scripts/online.py` with no disc, build or
browser. The shard routes cars by loaded place (`town:3`, `road:14`, `race:7`), while
story progress never partitions players. Legacy chapter-qualified keys normalize to
the same place. A car moves with its player instead of lingering in the last town,
spectators are counted and never relayed as a car, place labels reach the world summary,
and a pose outside the bounds is refused.

`python3 tests/online_socket.py` starts `scripts/serve.py` on a temporary data dir and
speaks `/online/socket` the way the page does. Two cars in one place are pushed to each
other as they move, another place hears nothing, a jump no car could drive is refused
until it has held for a second, a spectator cannot post a pose, driving on leaves the last
place's roster at once, closing a socket takes the car out and says so, and a made-up
session token is refused with 401.
