#include "ps2/disc.hpp"
#include "ps2/memory.hpp"
#include "ps2/state_request.hpp"
#include "ps2/system.hpp"
#include "ps2/video.hpp"

#include <emscripten.h>
#include <emscripten/threading.h>
#include <pthread.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

void register_generated_code();
void register_generated_vu();
// Runs at the guest's car draw, so the borrowed town car slots are at the other players' poses when
// the game builds their transforms (see ps2web_roadtrip_remote).
void on_guest_hook(std::uint32_t pc, ps2::Cpu &c);
// Measures how far the town's traffic AI rebuilt the borrowed car slots away from the poses the
// draw puts them at (see ps2web_roadtrip_remote).
void measure_remote_drift();
namespace { void apply_online_dev_mode(); }

EM_JS_DEPS(ps2web, "$stringToNewUTF8,$UTF8ToString");

EM_JS(void, ps2web_fetch, (const char *url, double offset, int size, void *out, std::atomic<std::uint32_t> *state), {
    const target = UTF8ToString(url);
    const finish = (value) => {
        Atomics.store(HEAP32, state >> 2, value);
        Atomics.notify(HEAP32, state >> 2);
    };
    fetch(target, {headers: {Range: 'bytes=' + offset + '-' + (offset + size - 1)}})
        .then((response) => {
            if (response.status !== 206 && response.status !== 200) throw new Error('status ' + response.status);
            return response.arrayBuffer().then((buffer) => {
                let bytes = new Uint8Array(buffer);
                if (response.status === 200) bytes = bytes.subarray(offset, offset + size);
                HEAPU8.set(bytes.subarray(0, Math.min(bytes.length, size)), out);
                finish(3);
            });
        })
        .catch((e) => {
            console.warn('disc read failed', e);
            finish(4);
        });
});

EM_JS(int, ps2web_save_program, (const char *name, const std::uint8_t *code, int size), {
    try {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', 'vu1/' + UTF8ToString(name), false);
        xhr.send(HEAPU8.slice(code, code + size));
        return xhr.status;
    } catch (e) {
        return 0;
    }
});

EM_JS(double, ps2web_disc_size, (), { return Module.discSize || 0; });
EM_JS(char *, ps2web_state_url, (), { return Module.stateUrl ? stringToNewUTF8(new URL(Module.stateUrl, location.href).href) : 0; });
EM_JS(double, ps2web_url_size, (const char *url), {
    try {
        const xhr = new XMLHttpRequest();
        xhr.open('HEAD', UTF8ToString(url), false);
        xhr.send();
        return xhr.status === 200 ? Number(xhr.getResponseHeader('Content-Length') || 0) : 0;
    } catch (e) {
        return 0;
    }
});
EM_JS(double, ps2web_url_read, (const char *url, std::uint8_t *out, double size), {
    try {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', UTF8ToString(url), false);
        xhr.responseType = 'arraybuffer';
        xhr.send();
        if (xhr.status !== 200) return 0;
        const bytes = new Uint8Array(xhr.response);
        HEAPU8.set(bytes.subarray(0, size), out);
        return bytes.length;
    } catch (e) {
        return 0;
    }
});
EM_JS(char *, ps2web_disc_url, (), { return stringToNewUTF8(new URL(Module.discUrl, location.href).href); });
EM_JS(void, ps2web_frame_done, (), {
    if (!Module.shownChannel) {
        Module.shownChannel = new MessageChannel();
        Module.shownWaiters = [];
        Module.shownChannel.port1.onmessage = () => {
            Module.shownPending = false;
            const waiters = Module.shownWaiters;
            Module.shownWaiters = [];
            for (const resolve of waiters) resolve();
        };
    }
    Module.shownPending = true;
    requestAnimationFrame(() => Module.shownChannel.port2.postMessage(0));
    if (Module.onFrame) Module.onFrame();
});
EM_ASYNC_JS(void, ps2web_wait_shown, (), {
    if (Module.shownPending) await new Promise((resolve) => Module.shownWaiters.push(resolve));
});
EM_JS(void, ps2web_stream_frame, (const std::uint32_t *rgba, int width, int height), {
    if (Module.onStreamFrame) Module.onStreamFrame(HEAPU8.subarray(rgba, rgba + width * height * 4), width, height);
});

EM_JS(void, ps2web_blit, (const std::uint32_t *rgba, int width, int height, int wide), {
    const canvas = Module.canvas;
    if (!Module.blitContext) Module.blitContext = canvas.getContext('2d');
    if (!Module.blitImage || Module.blitImage.width !== width || Module.blitImage.height !== height) {
        Module.blitImage = new ImageData(width, height);
        Module.blitSource = new OffscreenCanvas(width, height);
    }
    Module.blitImage.data.set(HEAPU8.subarray(rgba, rgba + width * height * 4));
    Module.blitSource.getContext('2d').putImageData(Module.blitImage, 0, 0);
    const ctx = Module.blitContext;
    const across = wide ? 16 : 4, down = wide ? 9 : 3;
    const scale = Math.min(canvas.width / across, canvas.height / down);
    const w = scale * across, h = scale * down;
    const sx = Math.max(1, Math.floor(w / width)), sy = Math.max(1, Math.floor(h / height));
    let source = Module.blitSource;
    if (sx > 1 || sy > 1) {
        if (!Module.blitSharp || Module.blitSharp.width !== width * sx || Module.blitSharp.height !== height * sy)
            Module.blitSharp = new OffscreenCanvas(width * sx, height * sy);
        const sharp = Module.blitSharp.getContext('2d');
        sharp.imageSmoothingEnabled = false;
        sharp.drawImage(Module.blitSource, 0, 0, width * sx, height * sy);
        source = Module.blitSharp;
    }
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(source, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
});

namespace {

constexpr std::size_t kChunk = 256u << 10;
constexpr std::uint32_t kSlots = 256;
constexpr std::uint64_t kReadAhead = 16;
constexpr std::uint32_t kAudioFrames = 1u << 14;

enum : std::uint32_t { kEmpty, kWanted, kLoading, kReady, kFailed };

struct Slot {
    std::atomic<std::uint32_t> state{kEmpty};
    std::uint64_t index = 0, used = 0;
    std::uint32_t size = 0;
    std::uint8_t *data = nullptr;
};

struct AudioRing {
    std::atomic<std::uint32_t> write{0}, read{0};
    std::uint32_t capacity = kAudioFrames;
    std::atomic<std::uint32_t> active{0};
    std::int16_t samples[kAudioFrames * 2];
};

AudioRing g_audio;
Slot g_slots[kSlots];
std::uint64_t g_disc_size = 0, g_use_clock = 0, g_last_chunk = ~0ull, g_run = 0;
char *g_disc_url = nullptr;
char *g_state_url = nullptr;

Slot *find_slot(std::uint64_t index) {
    for (Slot &s : g_slots)
        if (s.state.load(std::memory_order_acquire) != kEmpty && s.index == index) return &s;
    return nullptr;
}

Slot *want(std::uint64_t index) {
    if (index * kChunk >= g_disc_size) return nullptr;
    if (Slot *s = find_slot(index)) return s;
    Slot *victim = nullptr;
    for (Slot &s : g_slots) {
        const std::uint32_t state = s.state.load(std::memory_order_acquire);
        if (state == kEmpty) {
            victim = &s;
            break;
        }
        if ((state == kReady || state == kFailed) && (!victim || s.used < victim->used)) victim = &s;
    }
    if (!victim) return nullptr;
    if (!victim->data) victim->data = new std::uint8_t[kChunk];
    victim->index = index;
    victim->size = static_cast<std::uint32_t>(std::min<std::uint64_t>(kChunk, g_disc_size - index * kChunk));
    victim->used = ++g_use_clock;
    victim->state.store(kWanted, std::memory_order_release);
    ps2::wake_render_thread();
    return victim;
}

void advance(std::uint64_t index) {
    if (index <= g_last_chunk && g_last_chunk - index <= kReadAhead * 4) return;
    g_run = index == g_last_chunk + 1 ? g_run + 1 : 0;
    g_last_chunk = index;
}

void read_ahead(std::uint64_t index) {
    for (std::uint64_t k = 1; k <= std::min(g_run * 2, kReadAhead); ++k) want(index + k);
}

const Slot *chunk(std::uint64_t index) {
    Slot *s = want(index);
    advance(index);
    read_ahead(std::max(index, g_last_chunk));
    for (int attempt = 0; s && attempt < 4; ++attempt) {
        for (std::uint32_t state = s->state.load(); state != kReady && state != kFailed; state = s->state.load())
            emscripten_futex_wait(&s->state, state, INFINITY);
        if (s->state.load() == kReady) {
            s->used = ++g_use_clock;
            return s;
        }
        usleep(100000);
        s->state.store(kWanted, std::memory_order_release);
        ps2::wake_render_thread();
    }
    return nullptr;
}

bool disc_ready(std::uint64_t offset, std::size_t size) {
    if (!size) return true;
    const std::uint64_t last = (offset + size - 1) / kChunk;
    bool ready = true;
    for (std::uint64_t index = offset / kChunk; index <= last; ++index) {
        Slot *s = want(index);
        if (s && s->state.load(std::memory_order_acquire) == kFailed) {
            s->state.store(kWanted, std::memory_order_release);
            ps2::wake_render_thread();
        }
        ready = ready && s && s->state.load(std::memory_order_acquire) == kReady;
        advance(index);
    }
    read_ahead(std::max(last, g_last_chunk));
    return ready;
}

std::size_t read_disc(std::uint64_t offset, void *out, std::size_t size) {
    std::size_t done = 0;
    auto *to = static_cast<std::uint8_t *>(out);
    while (done < size && offset + done < g_disc_size) {
        const std::uint64_t at = offset + done;
        const Slot *c = chunk(at / kChunk);
        if (!c) break;
        const std::size_t within = static_cast<std::size_t>(at % kChunk);
        const std::size_t take = std::min<std::size_t>(size - done, c->size - within);
        std::memcpy(to + done, c->data + within, take);
        done += take;
    }
    return done;
}

void service_disc() {
    for (Slot &s : g_slots) {
        std::uint32_t expected = kWanted;
        if (s.state.compare_exchange_strong(expected, kLoading, std::memory_order_acq_rel))
            ps2web_fetch(g_disc_url, static_cast<double>(s.index * kChunk), static_cast<int>(s.size), s.data, &s.state);
    }
}

void push_audio(const std::int16_t *samples, std::size_t frames) {
    std::uint32_t write = g_audio.write.load(std::memory_order_relaxed);
    const std::uint32_t read = g_audio.read.load(std::memory_order_acquire);
    for (std::size_t i = 0; i < frames && write - read < kAudioFrames; ++i, ++write) {
        const std::uint32_t at = (write & (kAudioFrames - 1)) * 2;
        g_audio.samples[at] = samples[i * 2];
        g_audio.samples[at + 1] = samples[i * 2 + 1];
    }
    g_audio.write.store(write, std::memory_order_release);
}

double audio_queued_ms() { return static_cast<double>(g_audio.write.load() - g_audio.read.load()) / 48.0; }

double g_next_frame = 0.0;
std::atomic<double> g_emulation_idle{0.0}, g_render_idle{0.0};

void pace() {
    ps2::post_present();
    // The audio queue is the better clock while it is being drained, so waiting for it comes first.
    // It cannot be the only clock: a device that takes samples as fast as they are produced leaves
    // the queue near empty, and returning here on that basis let the guest free-run at several
    // hundred frames a second (seen at 560 fps on a fast GPU, which plays the game at nine times
    // speed). Every frame is still paced to the display rate below.
    if (g_audio.active.load()) while (audio_queued_ms() > 80.0) usleep(1000);
    const double period = ps2::g_options.pal ? 20.0 : 1000.0 / 59.94;
    const double now = emscripten_get_now();
    if (g_next_frame == 0.0 || now - g_next_frame > 200.0) g_next_frame = now;
    g_next_frame += period;
    if (g_next_frame > now) usleep(static_cast<useconds_t>((g_next_frame - now) * 1000.0));
}

void snapshot_camera();

void on_vblank() {
    const double start = emscripten_get_now();
    apply_online_dev_mode();
    snapshot_camera();
    measure_remote_drift();
    pace();
    g_emulation_idle.store(g_emulation_idle.load() + emscripten_get_now() - start);
}

struct Launch {
    std::vector<std::string> args;
};

void *emulate(void *data) {
    const Launch &launch = *static_cast<const Launch *>(data);
    if (g_disc_size && !ps2::disc::open("disc", g_disc_size, read_disc, disc_ready)) std::fprintf(stderr, "[web] cannot read the disc image\n");
    std::uint32_t entry = 0;
    ps2::load_patches("/game/patches.txt");
    if (!ps2::load_elf("/game/core.elf", entry)) {
        std::fprintf(stderr, "cannot load the game executable\n");
        return nullptr;
    }
    // The car draw asks the host for other players' cars as it goes (kCarDrawHook).
    ps2::g_options.on_hook = on_guest_hook;
    ps2::boot(entry, launch.args);
    if (g_state_url) {
        const double size = ps2web_url_size(g_state_url);
        std::vector<std::uint8_t> data(static_cast<std::size_t>(size));
        if (size <= 0 || ps2web_url_read(g_state_url, data.data(), size) != size || !ps2::load_state(data.data(), data.size())) {
            std::fprintf(stderr, "[web] cannot load the state %s\n", g_state_url);
            return nullptr;
        }
    }
    ps2::run();
    EM_ASM({
        if (typeof Module.onHalt === 'function')
            Module.onHalt(UTF8ToString($0));
    }, ps2::halt_reason().c_str());
    return nullptr;
}

}

extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_pad(int pad, int buttons, int lx, int ly, int rx, int ry) {
    ps2::set_pad_buttons(static_cast<std::uint16_t>(buttons), pad);
    ps2::set_pad_axis(0, static_cast<std::uint8_t>(rx), pad);
    ps2::set_pad_axis(1, static_cast<std::uint8_t>(ry), pad);
    ps2::set_pad_axis(2, static_cast<std::uint8_t>(lx), pad);
    ps2::set_pad_axis(3, static_cast<std::uint8_t>(ly), pad);
}

extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_pad_connected(int pad, int connected) { ps2::set_pad_connected(pad, connected != 0); }
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_take_online_adventure() { return ps2::take_online_adventure_request() ? 1 : 0; }
extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_activate_online_adventure() { ps2::activate_online_adventure(); }
extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_deactivate_online_adventure() { ps2::deactivate_online_adventure(); }

extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_pressure(int pad, int a, int b, int c, int d) {
    const int packed[4] = {a, b, c, d};
    for (int i = 0; i < 16; ++i) ps2::set_pad_pressure(i, static_cast<std::uint8_t>(static_cast<unsigned>(packed[i / 4]) >> (i % 4 * 8)), pad);
}

extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_rumble(int pad) { return ps2::pad_rumble(pad); }

// Read one field of a confirmed Road Trip town-car pose without exposing
// arbitrary EE RAM to page JavaScript. Fields 0..3 are x, y, z, heading.
// Slots 0..11 are the player and town traffic.
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_roadtrip_pose(int slot, int field) {
    if (slot < 0 || slot >= 12 || field < 0 || field >= 4) return std::nan("");
    constexpr std::uint32_t base = 0x0177ACE0u;
    constexpr std::uint32_t stride = 0x270u;
    const auto *car = ps2::g_ram + base + static_cast<std::uint32_t>(slot) * stride;
    float x, y, z, fx, fz, rx, rz;
    std::memcpy(&x, car, sizeof(float));
    std::memcpy(&y, car + 4, sizeof(float));
    std::memcpy(&z, car + 8, sizeof(float));
    std::memcpy(&fx, car + 0xB0, sizeof(float));
    std::memcpy(&fz, car + 0xB8, sizeof(float));
    std::memcpy(&rx, car + 0xD0, sizeof(float));
    std::memcpy(&rz, car + 0xD8, sizeof(float));
    switch (field) {
    case 0: return x;
    case 1: return y;
    case 2: return z;
    default: return std::atan2(fz - rz, fx - rx);
    }
}

// A bounded view of the Adventure save block for account progress and future title eligibility. Fields:
// 0 money, 1 scene, 2..5 story flag words, 6 earned stamp count, 7 WGP stages
// run, 8 towns-unvisited mask. It never sends the save block itself to the server.
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_roadtrip_progress(int field) {
    constexpr std::uint32_t progress = 0x0177F760u;
    std::uint32_t value = 0;
    if (field == 0 || field == 1 || (field >= 2 && field <= 5) || field == 7) {
        const std::uint32_t offsets[] = {0x654u, 0x538u, 0x538u, 0x53Cu, 0x540u, 0x544u, 0, 0x3310u};
        std::memcpy(&value, ps2::g_ram + progress + offsets[field], sizeof(value));
        return static_cast<double>(value);
    }
    if (field == 6) {
        for (int i = 0; i < 13; ++i) {
            std::uint8_t byte = ps2::g_ram[progress + 0x518u + static_cast<std::uint32_t>(i)];
            if (i == 12) byte &= 0x0Fu; // notebook has 100 stamps, not 104
            for (unsigned bit = 0; bit < 8; ++bit) value += (byte >> bit) & 1u;
        }
        return static_cast<double>(value);
    }
    if (field == 8) {
        std::uint16_t unvisited = 0;
        std::memcpy(&unvisited, ps2::g_ram + progress + 0x12DAu, sizeof(unvisited));
        return static_cast<double>(unvisited);
    }
    return std::nan("");
}

// Read-only title/entry probe for reverse-engineering the Online Adventure launch.
// Field IDs mirror the confirmed/likely globals in config/game_state.toml; no guest state is
// written. Values are only meaningful once the relevant native game task has initialized them.
extern "C" EMSCRIPTEN_KEEPALIVE std::uint32_t ps2web_roadtrip_entry_state(int field) {
    struct Field { std::uint32_t address; std::uint8_t width; };
    static constexpr Field fields[] = {
        {0x00335923u, 1}, // current location
        {0x0033590Au, 1}, // selected game mode
        {0x003356CCu, 4}, // current UI context (title/menu value is known)
        {0x003356C4u, 4}, // 3D scene active
        {0x003356D8u, 4}, // 3D scene handler
        {0x00335928u, 4}, // drive/race flags
        {0x0177F760u + 0x12DCu, 1}, // saved location (Adventure progress block)
        {0x0177F760u + 0x0538u, 4}, // low story/scene word (likely; stale outside Adventure)
    };
    if (field < 0 || field >= static_cast<int>(std::size(fields))) return 0xFFFFFFFFu;
    const Field selected = fields[field];
    if (selected.width == 1) return ps2::g_ram[selected.address];
    std::uint32_t value = 0;
    std::memcpy(&value, ps2::g_ram + selected.address, sizeof(value));
    return value;
}

// The game's state words that say what is being drawn: the 3D scene, which handler runs it, and
// the UI context. The addresses are the ones in config/game_state.toml ([game]) that the native
// second screen uses in src/game/GameStats.cpp.
namespace {
constexpr std::uint32_t kScene3D = 0x003356C4u, kHandler = 0x003356D8u, kUiContext = 0x003356CCu, kGameMode = 0x0033590Au;
constexpr std::uint32_t kRaceHandler = 0x0021CE48u, kTownHandler = 0x0021CF80u, kTitleContext = 0x0029AFA0u;
// Where the car is ([game] in config/game_state.toml), read as the native second screen reads it
// (src/game/GameStats.cpp).
constexpr std::uint32_t kRace = 0x00335910u, kLocationNow = 0x00335923u, kLocations = 0x002BE438u, kTile = 0x00335954u;
constexpr int kLastLocation = 21;

std::uint32_t word(std::uint32_t address) {
    std::uint32_t value;
    std::memcpy(&value, ps2::g_ram + address, sizeof value);
    return value;
}

bool in_world() {
    const std::uint32_t scene = word(kScene3D), handler = word(kHandler);
    // Towns run their scene as 2, the field areas between them as 1; a shop's menu is 1 with no handler.
    return (scene == 1u || scene == 2u) && (handler == kRaceHandler || handler == kTownHandler) &&
           word(kUiContext) != kTitleContext && ps2::g_ram[kGameMode] != 8;
}

// A town or a field area between towns: the world where the car array holds the player and street
// traffic instead of a race's entrants (where every slot is a racer).
bool in_street_world() { return in_world() && word(kHandler) == kTownHandler; }

// The location whose map tile the car is in (0x335923 only changes at a door), else that byte: the
// same rule the native second screen uses, so the two never disagree about where the car is.
int location_now() {
    const int tile = static_cast<int>(word(kTile) & 0xFFFFu);
    int location = std::min<int>(ps2::g_ram[kLocationNow], kLastLocation);
    for (int l = 1; l <= kLastLocation; ++l) {
        std::int16_t tile_of;
        std::memcpy(&tile_of, ps2::g_ram + kLocations + 8u * static_cast<std::uint32_t>(l) + 4, sizeof tile_of);
        if (tile_of == tile) {
            location = l;
            break;
        }
    }
    return location;
}
} // namespace

// Whether the game is drawing the 3D world this player is driving in (a race, a town or a field
// area between towns), so a menu, a shop or the title's attract demo never gets cars drawn over it.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_gameplay() { return in_world() ? 1 : 0; }

// Where the car is, so each shared world can be one place: a town, a field area between towns, or a
// race. Field 0 is the kind (0 not in the world, 1 town, 2 field area, 3 race), 1 the location
// (0 My Garage, 1..9 towns, 10..21 the field areas) and 2 the race's course byte (-1 outside a
// race). Two players only meet where this names the same place (the page keys its world by it),
// and a menu, a shop or the title names none.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_place(int field) {
    if (field < 0 || field > 2) return -1;
    if (!in_world()) return field == 2 ? -1 : 0;
    const bool racing = word(kHandler) == kRaceHandler;
    // Location 1..9 are towns and 10..21 the field areas between them, so the location names the
    // kind on its own. The 3D scene flag alone is not enough: a town can report scene 1 (that is
    // how Q's Factory and the other indoor town areas come up), which used to file a player
    // standing in a town under road:<town> and keep town-only features shut.
    if (field == 0) {
        if (racing) return 3;
        const int here = location_now();
        return here >= 1 && here <= 9 ? 1 : 2;
    }
    if (field == 1) return location_now();
    if (!racing) return -1;
    const std::uint32_t race = word(kRace) & 0x1FFFFFFu;
    if (race == 0 || race + 4u >= 32u * 1024u * 1024u) return -1;
    return ps2::g_ram[race];
}

// The quest-giver hook: the game's own cast, re-pointed at a speaker of ours.
//
// Each location has a roster ([people] in config/game_state.toml): a NULL-terminated list of
// pointers to entries, and an entry is a pointer to the speaker's name followed by pointers to
// its script lines. One roster word is all a conversation needs, so pointing it at an entry we
// built in guest RAM makes one of that town's street NPCs into our contact, in the game's own
// window with the game's own font. The president test proved the mechanism by pointing Dust's
// entry at the Secretary's.
//
// The page fills the scratch area (it can write guest RAM through ps2web_roadtrip_ram) and then
// commits: strings and entry first, the roster word last, so the emulation thread never reads a
// half-built entry. Restoring puts the original word back, so an offline save never notices.
namespace {
constexpr std::uint32_t kRosters = 0x2A7ED4u; // [people] rosters: location 1 at +0
constexpr int kRosterLocations = 21, kRosterLimit = 256;
constexpr std::uint32_t kWindow = 0x175E898u, kWindowEntry = 0x1Cu; // [dialogue]
constexpr std::uint32_t kSlotLow = 0x1770000u, kSlotHigh = 0x1780000u;
// Guest RAM for our own name and lines: above every address the traced game data uses (the save
// block ends at 0x1780000) and below the thread stacks at the top of RAM. It is only written
// after a check that it is still all zero, and emptied again when the chapter ends.
constexpr std::uint32_t kScratch = 0x01A00000u, kScratchSize = 0x1000u;
// Up to three speakers share the scratch: a town's street NPCs are split into groups, so the whole
// cast of a chapter can be standing in one town at once, each group saying its own lines. One
// speaker's block is its name and three messages; the entry arrays that point at them, one per
// replaced roster slot, follow the last block. 3 * 0x440 + 24 * 0x20 = 0xFC0, inside 0x1000.
constexpr int kCastMax = 3, kCastBlock = 0x440, kQuestName = 0x40, kQuestLines = 3, kQuestLine = 0x140;
constexpr int kQuestSlots = 24, kQuestEntryWords = 8;
constexpr std::uint32_t kScratchEntry = kScratch + static_cast<std::uint32_t>(kCastBlock * kCastMax);
constexpr std::uint32_t kCastBase(int member) {
    return kScratch + static_cast<std::uint32_t>(kCastBlock) * static_cast<std::uint32_t>(member - 1);
}
constexpr std::uint32_t kQuestLineAt(int member, int i) {
    return kCastBase(member) + static_cast<std::uint32_t>(kQuestName) + static_cast<std::uint32_t>(kQuestLine) * static_cast<std::uint32_t>(i);
}
constexpr std::uint32_t kQuestEntryAt(int i) { return kScratchEntry + 0x20u * static_cast<std::uint32_t>(i); }

// A town's street NPCs are the tail of its roster, and which car belongs to which entry is the
// game's business, not ours: each cast member replaces a whole range of entries, so whichever car
// the player meets in that range is that character, and every one of them goes back when the
// chapter moves on. Several members share one reservation, which is what makes a town's whole
// street cast ours instead of one contact among the game's own people.
struct CastMember {
    bool used = false, committed = false;
    int first = 0, count = 0, lines = 0, entry = 0;
    std::uint32_t original[kQuestSlots] = {};
};
struct QuestGiver {
    bool reserved = false;
    int location = 0, members = 0, slots = 0;
    std::uint32_t roster = 0;
    CastMember cast[kCastMax];
} g_quest;
// The scratch keeps its strings after a restore: a conversation that is still on screen reads them.
// Only the next reservation empties it, once nothing can be pointing at it any more.
bool g_scratch_dirty = false;

std::uint32_t ram_word(std::uint32_t address) {
    std::uint32_t value = 0;
    std::memcpy(&value, ps2::g_ram + address, sizeof value);
    return value;
}

void ram_word(std::uint32_t address, std::uint32_t value) { std::memcpy(ps2::g_ram + address, &value, sizeof value); }

bool scratch_clear() { return std::all_of(ps2::g_ram + kScratch, ps2::g_ram + kScratch + kScratchSize,
                                          [](std::uint8_t byte) { return byte == 0; }); }

void restore_quest_giver() {
    if (g_quest.reserved && g_quest.roster)
        for (int m = 0; m < g_quest.members; ++m) {
            const CastMember &member = g_quest.cast[m];
            if (!member.used) continue;
            for (int i = 0; i < member.count; ++i)
                ram_word(g_quest.roster + 4u * static_cast<std::uint32_t>(member.first + i), member.original[i]);
        }
    g_scratch_dirty = g_scratch_dirty || g_quest.reserved;
    g_quest = QuestGiver{};
}

// True when an entry pointer is one of ours, whichever cast member built it.
bool quest_own_entry(std::uint32_t entry) {
    for (int m = 0; m < g_quest.members; ++m) {
        const CastMember &member = g_quest.cast[m];
        if (!member.used || !member.committed) continue;
        for (int i = 0; i < member.count; ++i)
            if (entry == kQuestEntryAt(member.entry + i)) return true;
    }
    return false;
}

bool quest_roster_word(std::uint32_t word) {
    for (int m = 0; m < g_quest.members; ++m)
        for (int i = 0; i < g_quest.cast[m].count; ++i)
            if (word == kQuestEntryAt(g_quest.cast[m].entry + i)) return true;
    return false;
}
} // namespace

// Where guest RAM begins in the wasm heap, so the page can fill the scratch area itself. Handing
// out the base is also how the dev probes read the game's state without a second export per field.
extern "C" EMSCRIPTEN_KEEPALIVE std::uint32_t ps2web_roadtrip_ram() {
    return static_cast<std::uint32_t>(reinterpret_cast<std::uintptr_t>(ps2::g_ram));
}

extern "C" {
std::uint32_t ps2web_roadtrip_cast(int action, int location, int first, int last);
int ps2web_roadtrip_cast_commit(int member, int lines);
}

// action 1: reserve the scratch area for roster entries first..last, returning its guest address
// (0 when the location, the range or the scratch area is not usable); action 0: put every original
// entry back. No roster word changes until commit, so a page that reserves and never commits
// changes nothing. This is the one-speaker case, kept because it is the whole of a chapter's need
// when a town has a single contact.
extern "C" EMSCRIPTEN_KEEPALIVE std::uint32_t ps2web_roadtrip_npc(int action, int location, int first, int last) {
    if (action == 0) {
        restore_quest_giver();
        return 1;
    }
    ps2web_roadtrip_cast(1, location, 0, 0);
    const std::uint32_t base = ps2web_roadtrip_cast(2, location, first, last);
    if (!base) restore_quest_giver();
    return base;
}

// Commits the reserved speaker: `lines` messages are at its block + 0x40 + 0x140 * n, each ended by
// a NUL. Returns 1 once the roster points at it and the write reads back the same.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_npc_commit(int lines) {
    return ps2web_roadtrip_cast_commit(1, lines);
}

// The cast of a chapter: several speakers in one town, each replacing its own range of the town's
// roster, so the whole street cast is ours rather than one contact among the game's own people.
//
//  action 1 (location, 0, 0): start a cast in that location, putting any previous one back first.
//            Returns 1 when the scratch area is ours and the location has a roster.
//  action 2 (location, first, last): add a member replacing roster entries first..last and return
//            the guest address of its name (0x40 name, then 0x140 per message), or 0 when the town,
//            the range or the budget is not usable. Names and lines are written there by the page.
//  action 0: put every original entry back, which is what leaving the chapter does.
// Nothing in the roster changes until a member is committed, so a cast that is built and dropped
// leaves the game exactly as it was.
extern "C" EMSCRIPTEN_KEEPALIVE std::uint32_t ps2web_roadtrip_cast(int action, int location, int first, int last) {
    if (action == 0) {
        restore_quest_giver();
        return 1;
    }
    if (action == 1) {
        if (location < 1 || location > kRosterLocations) return 0;
        const std::uint32_t roster = ram_word(kRosters + 4u * static_cast<std::uint32_t>(location - 1));
        if (roster == 0) return 0;
        if (g_quest.reserved) restore_quest_giver();
        if (g_scratch_dirty) {
            std::memset(ps2::g_ram + kScratch, 0, kScratchSize); // ours from the last cast
            g_scratch_dirty = false;
        } else if (!scratch_clear()) {
            return 0; // something of the game's is living there; do not touch it
        }
        g_quest = QuestGiver{};
        g_quest.reserved = true;
        g_quest.location = location;
        g_quest.roster = roster;
        g_scratch_dirty = true; // the page writes the strings next; they stay until the next cast
        return 1;
    }
    if (action != 2 || !g_quest.reserved) return 0;
    if (location != g_quest.location) return 0; // a cast belongs to one town's roster
    if (first < 0 || last < first || last >= kRosterLimit) return 0;
    if (g_quest.members >= kCastMax) return 0;
    const int count = last - first + 1;
    if (g_quest.slots + count > kQuestSlots) return 0;
    std::uint32_t original[kQuestSlots] = {};
    for (int i = 0; i < count; ++i) {
        original[i] = ram_word(g_quest.roster + 4u * static_cast<std::uint32_t>(first + i));
        if (original[i] == 0) return 0; // past the end of that location's roster
    }
    CastMember &member = g_quest.cast[g_quest.members];
    member.used = true;
    member.committed = false;
    member.first = first;
    member.count = count;
    member.lines = 0;
    member.entry = g_quest.slots; // first entry array of this member
    std::memcpy(member.original, original, sizeof original);
    ++g_quest.members;
    g_quest.slots += count;
    return kCastBase(g_quest.members);
}

// Commits one member: `lines` messages at its block + 0x40 + 0x140 * n, each ended by a NUL.
// Returns 1 once every roster slot of that member points at it and the write reads back the same.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_cast_commit(int member, int lines) {
    if (!g_quest.reserved || member < 1 || member > g_quest.members) return 0;
    if (lines < 1 || lines > kQuestLines) return 0;
    CastMember &cast = g_quest.cast[member - 1];
    if (!cast.used) return 0;
    const std::uint32_t base = kCastBase(member);
    if (ps2::g_ram[base] == 0) return 0;
    for (int i = 0; i < lines; ++i) {
        const std::uint32_t at = kQuestLineAt(member, i);
        if (ps2::g_ram[at] == 0) return 0;
        const void *end = std::memchr(ps2::g_ram + at, 0, kQuestLine);
        if (!end) return 0;
        const auto length = static_cast<std::size_t>(static_cast<const std::uint8_t *>(end) - (ps2::g_ram + at));
        if (length + 4 > kQuestLine) return 0;
        // The window reads a script line from wherever the cursor points, one opcode with its one
        // argument at a time, and the game's own lines are wrapped in that: an opener, the text, and
        // 0x03 to end the message ("\x04\x04We'll buy parts...\x01\x02\x03"). Its arguments are the
        // bytes the opener eats, so a plain space goes there and the text starts on its first letter.
        // Without the wrapper the first letter is eaten and the window walks off the end of our line.
        if (ps2::g_ram[at] >= 0x20) {
            std::memmove(ps2::g_ram + at + 2, ps2::g_ram + at, length + 1);
            ps2::g_ram[at] = 0x04;
            ps2::g_ram[at + 1] = 0x20;
            ps2::g_ram[at + 2 + length] = 0x03;
            ps2::g_ram[at + 3 + length] = 0;
        }
    }
    // Each entry: the name, then each line, then a NULL (the shape the game's own entries have).
    for (int slot = 0; slot < cast.count; ++slot) {
        const std::uint32_t entry = kQuestEntryAt(cast.entry + slot);
        ram_word(entry, base);
        for (int i = 0; i < lines; ++i) ram_word(entry + 4u * static_cast<std::uint32_t>(i + 1), kQuestLineAt(member, i));
        ram_word(entry + 4u * static_cast<std::uint32_t>(lines + 1), 0u);
    }
    std::atomic_thread_fence(std::memory_order_release);
    cast.lines = lines;
    cast.committed = true;
    for (int slot = 0; slot < cast.count; ++slot) {
        const std::uint32_t at = g_quest.roster + 4u * static_cast<std::uint32_t>(cast.first + slot);
        ram_word(at, kQuestEntryAt(cast.entry + slot));
        if (!quest_roster_word(ram_word(at))) return 0;
    }
    return 1;
}

// 1 while the message window belongs to any of our speakers, 0 for the game's own or for no window:
// the page asks this to tell whether the player actually got to talk to one of the cast.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_npc_talking() {
    if (g_quest.members == 0) return 0;
    const std::uint32_t slot = ram_word(kWindow);
    if (slot < kSlotLow || slot >= kSlotHigh) return 0;
    return quest_own_entry(ram_word(slot + kWindowEntry)) ? 1 : 0;
}

// Which cast member the open window belongs to: 1..members, or 0 for the game's own speaker or no
// window. The page needs this to know which of our characters the player actually met, so an
// objective can name one of them and a chapter can have more than one talk in it.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_npc_talking_member() {
    const std::uint32_t slot = ram_word(kWindow);
    if (slot < kSlotLow || slot >= kSlotHigh) return 0;
    const std::uint32_t entry = ram_word(slot + kWindowEntry);
    for (int m = 0; m < g_quest.members; ++m) {
        const CastMember &member = g_quest.cast[m];
        if (!member.used || !member.committed) continue;
        for (int i = 0; i < member.count; ++i)
            if (entry == kQuestEntryAt(member.entry + i)) return m + 1;
    }
    return 0;
}

// The hook's state, for the page and for the tests: 0 location, 1 first entry replaced by the first
// member, 2 how many, 3 the entry the game had at the first one, 4 the roster word there now,
// 5 whether a window is open, 6 the speaker's entry, 7 how many lines the first member has,
// 8 whether that speaker is ours, 9 how many members the cast has, 10 how many slots it replaced.
extern "C" EMSCRIPTEN_KEEPALIVE std::uint32_t ps2web_roadtrip_npc_state(int field) {
    if (field == 9) return static_cast<std::uint32_t>(g_quest.members);
    if (field == 10) return static_cast<std::uint32_t>(g_quest.slots);
    switch (field) {
    case 0: return static_cast<std::uint32_t>(g_quest.location);
    case 1: return static_cast<std::uint32_t>(g_quest.cast[0].first);
    case 2: return static_cast<std::uint32_t>(g_quest.cast[0].count);
    case 3: return g_quest.cast[0].original[0];
    case 4: return g_quest.reserved ? ram_word(g_quest.roster + 4u * static_cast<std::uint32_t>(g_quest.cast[0].first)) : 0u;
    case 7: return static_cast<std::uint32_t>(g_quest.cast[0].lines);
    case 8: {
        const std::uint32_t slot = ram_word(kWindow);
        return slot >= kSlotLow && slot < kSlotHigh && quest_own_entry(ram_word(slot + kWindowEntry)) ? 1u : 0u;
    }
    case 5: return ram_word(kWindow) >= kSlotLow && ram_word(kWindow) < kSlotHigh ? 1u : 0u;
    case 6: {
        const std::uint32_t slot = ram_word(kWindow);
        return slot >= kSlotLow && slot < kSlotHigh ? ram_word(slot + kWindowEntry) : 0u;
    }
    default: return 0xFFFFFFFFu;
    }
}

// Our own rows in the game's own option menu.
//
// The parts menu (Q's Factory, door 0 of Peach Town) draws six rows whose labels the game reads from
// one pointer table in its loaded data: 0x29F370 holds Tires, Engine, Chassis, Transmission,
// Steering, Brake. Pointing those six words at our own strings makes the game draw our rows with its
// own panel, cursor, font and description box, so a question can be asked in the game's own UI. Six
// is the table's length and therefore the limit per page. The row the player has highlighted is the
// byte at 0x177A637 (0-based; Up/Down move it, and it is what the confirm action reads).
namespace {
constexpr std::uint32_t kMenuTable = 0x29F370u;
constexpr int kMenuRows = 6;
constexpr std::uint32_t kMenuIndex = 0x177A637u;

struct MenuPatch {
    std::uint32_t original[kMenuRows] = {};
    bool installed = false;
} g_menu;
} // namespace

// 1 installs the page's labels: with action 1, row `index` is pointed at the guest string at
// `address` (the page writes the text into RAM first and passes its address); with action 0 every
// original pointer goes back. Returns 1 on success, 0 for a bad row.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_menu(int action, int index, std::uint32_t address) {
    if (action == 0) {
        if (g_menu.installed)
            for (int i = 0; i < kMenuRows; ++i)
                ram_word(kMenuTable + 4u * static_cast<std::uint32_t>(i), g_menu.original[i]);
        g_menu.installed = false;
        return 1;
    }
    if (index < 0 || index >= kMenuRows || address == 0) return 0;
    if (!g_menu.installed) {
        for (int i = 0; i < kMenuRows; ++i)
            g_menu.original[i] = ram_word(kMenuTable + 4u * static_cast<std::uint32_t>(i));
        g_menu.installed = true;
    }
    ram_word(kMenuTable + 4u * static_cast<std::uint32_t>(index), address);
    std::atomic_thread_fence(std::memory_order_release);
    return 1;
}

// The row the player has highlighted, 0..5, or -1 when no menu is up. The page polls this to turn
// the game's cursor into an answer (and its description box already shows the highlighted row).
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_menu_index() {
    if (!g_menu.installed) return -1;
    const std::uint32_t index = ram_word(kMenuIndex) & 0xFFu;
    return index < static_cast<std::uint32_t>(kMenuRows) ? static_cast<int>(index) : -1;
}

// Our rows in the game's own pause list.
//
// The list is data, but not in a place we can append to: the town pause page (0x217148) builds it
// on every open from one array in the loaded image (0x29CA30) into the page's own state at
// 0x01727B90 — the row signs at +0x803C, the last cursor index at +0x807C. That array is exactly
// eight words (title, six rows, terminator) and the word after it, 0x29CA50, is the first row sign
// of the notebook page's list, so appending to the array takes a row away from the notebook. The
// row goes into the page state instead, once the game has built it: from there the page draws it,
// scrolls it, and moves its own cursor onto it like any of its six, and the sign it draws is the
// sheet's empty cell with our word painted into it (scripts/art_words.py).
//
// This is a check, not a blind write: it does nothing unless the state holds the game's own six
// rows, so a rebuilt list is added to once and another page's list is never touched. Running it
// every frame is what keeps the row there, because every opening rebuilds the state.
namespace {
constexpr std::uint32_t kPausePage = 0x01727B90u;
constexpr std::uint32_t kPauseSigns = kPausePage + 0x803Cu, kPauseLast = kPausePage + 0x807Cu;
constexpr std::uint32_t kPauseCell = 10u; // the sheet's empty cell
// The builder copies the array as it stands — the title sign first, then a sign per row — so the
// state is `[title, row 0 .. row 5]` and its last cursor index is 6, one past the last row sign.
constexpr int kPauseGameRows = 6; // Warp .. Map, the six rows the array names
}

// How many rows the pause list holds, 0 when it is not up: 6 until our cell is added, 7 after. The
// page polls it (and so does a test), which is also what makes the append idempotent.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_pause_rows() {
    if (ram_word(kPauseSigns) != 0u) return 0; // the title sign is the pause plate
    for (int i = 0; i < kPauseGameRows; ++i)
        if (ram_word(kPauseSigns + 4u * static_cast<std::uint32_t>(i + 1)) != 1u + static_cast<std::uint32_t>(i)) return 0;
    const std::uint32_t last = ram_word(kPauseLast);
    if (last < static_cast<std::uint32_t>(kPauseGameRows)) return 0;
    if (last > static_cast<std::uint32_t>(kPauseGameRows)) return static_cast<int>(last); // already added
    ram_word(kPauseSigns + 4u * static_cast<std::uint32_t>(kPauseGameRows + 1), kPauseCell);
    ram_word(kPauseLast, static_cast<std::uint32_t>(kPauseGameRows + 1));
    std::atomic_thread_fence(std::memory_order_release);
    return kPauseGameRows + 1;
}

// Read-only race instrumentation for designing safe remote racer actors. Fields 0..3 are
// x, y, z and heading; 4 is completed laps; 5 is the game's place (one-based); 7 is entrant
// count (read with any valid slot). Only initialized entrants in a live race are exposed.
// This deliberately writes no guest state, so it cannot affect an offline race.
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_roadtrip_race_car(int slot, int field) {
    if (!in_world() || word(kHandler) != kRaceHandler || slot < 0 || slot >= 24 ||
        field < 0 || field > 7 || field == 6) return std::nan("");
    constexpr std::uint32_t cars = 0x0177ACE0u, stride = 0x270u, entrantsAddress = 0x015B008Cu;
    const std::uint32_t entrants = word(entrantsAddress);
    if (entrants == 0 || entrants > 24 || slot >= static_cast<int>(entrants)) return std::nan("");
    if (field == 7) return static_cast<double>(entrants);
    const std::uint32_t car = cars + static_cast<std::uint32_t>(slot) * stride;
    float x, y, z, fx, fz, rx, rz;
    std::memcpy(&x, ps2::g_ram + car, sizeof(float));
    std::memcpy(&y, ps2::g_ram + car + 4, sizeof(float));
    std::memcpy(&z, ps2::g_ram + car + 8, sizeof(float));
    std::memcpy(&fx, ps2::g_ram + car + 0xB0, sizeof(float));
    std::memcpy(&fz, ps2::g_ram + car + 0xB8, sizeof(float));
    std::memcpy(&rx, ps2::g_ram + car + 0xD0, sizeof(float));
    std::memcpy(&rz, ps2::g_ram + car + 0xD8, sizeof(float));
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) return std::nan("");
    switch (field) {
    case 0: return x;
    case 1: return y;
    case 2: return z;
    case 3:
        if (!std::isfinite(fx) || !std::isfinite(fz) || !std::isfinite(rx) || !std::isfinite(rz))
            return std::nan("");
        return std::atan2(fz - rz, fx - rx);
    case 4: return static_cast<double>(ps2::g_ram[car + 0x10B]);
    case 5: return static_cast<double>(ps2::g_ram[car + 0x1B7] + 1);
    default: return std::nan("");
    }
}

// Other players' cars, drawn by the game itself. Road Trip keeps the player's car and the street's
// traffic in twelve slots at 0x0177ACE0, stride 0x270, and the town draws every slot with its own
// car model, lighting and shadow (see config/game_state.toml, [race] and [traffic]). The page
// publishes the poses of the players it sees and the last few traffic slots are moved onto them, so
// the game draws a real car with its own model, lighting and depth test instead of a picture over
// the screen.
//
// The pose has to be written at the draw, not between two frames: the town's traffic AI rebuilds
// each traffic car's transform from its own route state every frame, so a pose written at vblank is
// gone before the car is drawn and the NPC appears at its route position (measured 2026-11-01,
// `ps2web_roadtrip_remote_drift` reads ~500 units; a car is ~4). The town's car draw therefore
// calls back into the host through the recompiled hook at 0x00243190 (`games/roadtrip/hooks.txt`),
// at the top of its loop over the car structs and before it builds each car's transform: the pose
// written there is the one the frame draws. The borrowed slots keep their AI, which is why they
// still walk back to their routes between frames (that is what the drift number reads) and why they
// have no collision of their own with the other players' cars.
namespace {
constexpr std::uint32_t kCarsBase = 0x0177ACE0u, kCarStride = 0x270u;
// Inside the town's car draw, 0x00242FE8: r23 is the base of the car array and r21 the offset of
// the iteration about to be drawn, so r21 + r23 is that car (see games/roadtrip/hooks.txt).
constexpr std::uint32_t kCarDrawHook = 0x00243190u;
constexpr int kRemoteMax = 4;
// Borrowed from the end of the street's traffic, so the first NPC cars keep their routes.
constexpr int kRemoteSlots[kRemoteMax] = {11, 10, 9, 8};
// World-space front and rear wheel positions; their difference is the car's facing.
constexpr std::uint32_t kCarFront = 0xB0, kCarRear = 0xD0;

struct RemotePose {
    float x = 0.0f, y = 0.0f, z = 0.0f, heading = 0.0f;
    std::uint32_t active = 0;
};

struct RemoteTable {
    std::atomic<std::uint32_t> sequence{0};
    RemotePose pose[kRemoteMax];
    // Written by the emulation thread: how far (world units) the town's traffic walked each borrowed
    // car away from the pose the draw put it at, as of the last vblank, and how many borrowed cars
    // the last frame's draw actually placed.
    std::atomic<float> drifted[kRemoteMax]{};
    std::atomic<int> placed{0}, drawn{0};
} g_remote;
std::atomic_bool g_online_dev_mode{false};

void apply_online_dev_mode() {
    if (!g_online_dev_mode.load(std::memory_order_relaxed) || !ps2::online_adventure_active()) return;
    constexpr std::uint32_t progress = 0x0177F760u;
    std::uint32_t scene = 0;
    std::memcpy(&scene, ps2::g_ram + progress + 0x538u, sizeof scene);
    if (scene != 2u && scene != 3u && scene != 6u && scene != 10u) return;
    // Dev-only unlocks in the Online Adventure progress block. Leave the low scene word intact.
    const std::uint32_t cash = 1000000000u;
    std::memcpy(ps2::g_ram + progress + 0x654u, &cash, sizeof cash);
    ps2::g_ram[progress + 0x651u] = 3u; // Super A licence
    ps2::g_ram[progress + 0x12DAu] = 0u; // all towns visited (low byte)
    ps2::g_ram[progress + 0x12DBu] = 0u;
    // Unlock story and championship checks while preserving the scene selector at +0x538.
    std::memset(ps2::g_ram + progress + 0x53Cu, 0xFF, 12);
    // All 100 notebook stamps are available in dev mode.
    std::memset(ps2::g_ram + progress + 0x518u, 0xFF, 12);
    ps2::g_ram[progress + 0x524u] |= 0x0Fu;
    // Two team members satisfy the WGP team-size gate.
    ps2::g_ram[progress + 0x674u] = 1u;
    ps2::g_ram[progress + 0x676u] = 2u;
}

float ram_f32(std::uint32_t address) {
    float value;
    std::memcpy(&value, ps2::g_ram + address, sizeof value);
    return value;
}

void ram_f32(std::uint32_t address, float value) { std::memcpy(ps2::g_ram + address, &value, sizeof value); }

bool sane(float v) { return std::isfinite(v) && std::abs(v) < 100000.0f; }

// Moves a car rigidly so its origin lands on (x, y, z) facing `heading`, carrying the front and
// rear wheel points along (the game draws the body from them). Their offsets are taken from the
// car as it stands, so each model keeps its own wheelbase and wheel heights.
void place_car(std::uint32_t base, float x, float y, float z, float heading) {
    const float ox = ram_f32(base), oy = ram_f32(base + 4), oz = ram_f32(base + 8);
    const float fx = ram_f32(base + kCarFront), fy = ram_f32(base + kCarFront + 4), fz = ram_f32(base + kCarFront + 8);
    const float rx = ram_f32(base + kCarRear), ry = ram_f32(base + kCarRear + 4), rz = ram_f32(base + kCarRear + 8);
    if (!sane(ox) || !sane(oz) || !sane(fx) || !sane(fz) || !sane(rx) || !sane(rz)) return;
    const float from = std::atan2(fz - rz, fx - rx);
    if (!sane(from)) return;
    const float c0 = std::cos(from), s0 = std::sin(from), c1 = std::cos(heading), s1 = std::sin(heading);
    // Turn each wheel point around the car's origin, then place it on the new pose.
    const auto turn = [&](float px, float pz, float &out_x, float &out_z) {
        const float dx = px - ox, dz = pz - oz;
        const float lx = dx * c0 + dz * s0, lz = -dx * s0 + dz * c0;
        out_x = x + lx * c1 - lz * s1;
        out_z = z + lx * s1 + lz * c1;
    };
    float nfx, nfz, nrx, nrz;
    turn(fx, fz, nfx, nfz);
    turn(rx, rz, nrx, nrz);
    ram_f32(base, x);
    ram_f32(base + 4, y);
    ram_f32(base + 8, z);
    ram_f32(base + kCarFront, nfx);
    ram_f32(base + kCarFront + 4, y + (fy - oy));
    ram_f32(base + kCarFront + 8, nfz);
    ram_f32(base + kCarRear, nrx);
    ram_f32(base + kCarRear + 4, y + (ry - oy));
    ram_f32(base + kCarRear + 8, nrz);
}
} // namespace

extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_online_dev_mode(int enabled) {
    g_online_dev_mode.store(enabled != 0, std::memory_order_release);
}

// Copies one player's last complete pose out of the table the page writes; false while the page is
// between two stores.
bool read_remote(int index, RemotePose &out) {
    for (int attempt = 0; attempt < 4; ++attempt) {
        const std::uint32_t before = g_remote.sequence.load(std::memory_order_acquire);
        if (before & 1u) continue;
        out = g_remote.pose[index];
        if (g_remote.sequence.load(std::memory_order_relaxed) == before) return true;
    }
    return false;
}

// Puts the borrowed slot that owns `base` on its player's pose, if `base` is one of them. Called
// from the car draw, so this is what the frame draws.
void place_remote_car(std::uint32_t base) {
    if (!in_street_world()) return;
    for (int i = 0; i < kRemoteMax; ++i) {
        if (base != kCarsBase + static_cast<std::uint32_t>(kRemoteSlots[i]) * kCarStride) continue;
        RemotePose pose;
        if (!read_remote(i, pose) || !pose.active) return;
        if (!sane(pose.x) || !sane(pose.y) || !sane(pose.z) || !sane(pose.heading)) return;
        place_car(base, pose.x, pose.y, pose.z, pose.heading);
        g_remote.placed.fetch_add(1, std::memory_order_relaxed);
        return;
    }
}

// How far the traffic AI rebuilt each borrowed car away from the pose it was drawn at, read at
// vblank between two frames (the draw itself has already put it back there). A large number is the
// AI's pull on a borrowed slot, not a pose that failed to take.
void measure_remote_drift() {
    g_remote.drawn.store(g_remote.placed.exchange(0, std::memory_order_relaxed), std::memory_order_relaxed);
    if (!in_street_world()) return;
    for (int i = 0; i < kRemoteMax; ++i) {
        RemotePose pose;
        if (!read_remote(i, pose) || !pose.active) {
            g_remote.drifted[i].store(0.0f, std::memory_order_relaxed);
            continue;
        }
        const std::uint32_t base = kCarsBase + static_cast<std::uint32_t>(kRemoteSlots[i]) * kCarStride;
        const float gap = std::hypot(ram_f32(base) - pose.x, ram_f32(base + 8) - pose.z);
        g_remote.drifted[i].store(sane(gap) ? gap : 0.0f, std::memory_order_relaxed);
    }
}

void on_guest_hook(std::uint32_t pc, ps2::Cpu &c) {
    if (pc == kCarDrawHook) place_remote_car(c.r[21].w[0] + c.r[23].w[0]);
}

// Publishes one remote player's car, or clears it with active = 0. Called from the page, which is
// not the emulation thread: the sequence number keeps the draw from reading a half-written table.
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_remote(int index, double x, double y, double z, double heading, int active) {
    if (index < 0 || index >= kRemoteMax) return 0;
    const std::uint32_t start = g_remote.sequence.load(std::memory_order_relaxed);
    g_remote.sequence.store(start + 1, std::memory_order_relaxed);
    std::atomic_thread_fence(std::memory_order_release);
    RemotePose &pose = g_remote.pose[index];
    pose.x = static_cast<float>(x);
    pose.y = static_cast<float>(y);
    pose.z = static_cast<float>(z);
    pose.heading = static_cast<float>(heading);
    pose.active = active ? 1u : 0u;
    std::atomic_thread_fence(std::memory_order_release);
    g_remote.sequence.store(start + 2, std::memory_order_relaxed);
    return 1;
}

// How far the town's traffic AI walked a borrowed car away from its player's pose between two
// frames, in world units (0 when the slot is not borrowed or the game is not drawing a street).
// The draw puts the car back, so this is how much the AI fights the pose, not whether it takes.
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_roadtrip_remote_drift(int index) {
    if (index < 0 || index >= kRemoteMax) return std::nan("");
    return g_remote.drifted[index].load(std::memory_order_relaxed);
}

// How many borrowed cars the last frame's draw placed on a player's pose: 0 while the game is in a
// menu, and fewer than the borrowed slots while a car is off screen (the draw skips those).
extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_roadtrip_remote_drawn() {
    return g_remote.drawn.load(std::memory_order_relaxed);
}

// The gameplay camera's screen matrix, copied at vblank on the emulation thread: the guest
// rebuilds it every frame, so reading it from the page could catch it halfway through an update.
namespace {
constexpr std::uint32_t kPlayerCamera = 0x0177E6D0u;

struct CameraSnapshot {
    std::atomic<std::uint32_t> sequence{0};
    float matrix[16] = {};
    float centre_x = 2048.0f, centre_y = 2048.0f;
} g_camera;

void snapshot_camera() {
    const std::uint32_t before = g_camera.sequence.load(std::memory_order_relaxed);
    g_camera.sequence.store(before + 1, std::memory_order_relaxed);
    std::atomic_thread_fence(std::memory_order_release);
    std::memcpy(g_camera.matrix, ps2::g_ram + kPlayerCamera, sizeof g_camera.matrix);
    std::memcpy(&g_camera.centre_x, ps2::g_ram + kPlayerCamera + 0xB0, sizeof(float));
    std::memcpy(&g_camera.centre_y, ps2::g_ram + kPlayerCamera + 0xB4, sizeof(float));
    std::atomic_thread_fence(std::memory_order_release);
    g_camera.sequence.store(before + 2, std::memory_order_relaxed);
}

// Copies the last complete snapshot out; false while the writer is between stores.
bool read_camera(float (&matrix)[16], float &centre_x, float &centre_y) {
    for (int attempt = 0; attempt < 4; ++attempt) {
        const std::uint32_t before = g_camera.sequence.load(std::memory_order_relaxed);
        if (before & 1u) continue;
        std::atomic_thread_fence(std::memory_order_acquire);
        std::memcpy(matrix, g_camera.matrix, sizeof g_camera.matrix);
        centre_x = g_camera.centre_x;
        centre_y = g_camera.centre_y;
        std::atomic_thread_fence(std::memory_order_acquire);
        if (g_camera.sequence.load(std::memory_order_relaxed) == before) return true;
    }
    return false;
}
} // namespace

// Project one world point through the gameplay camera, in the 640x448 frame the game draws.
// The screen matrix is the camera's clip matrix with X and Y widened by the 512-unit focal
// length: screen X = focal * x / z + centre. Its Y is in field lines (224), so the frame's
// 448 lines double it. Fields 0..3 return screen X, screen Y, view depth, and 1 if in front.
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_roadtrip_project(double x, double y, double z, int field) {
    if (field < 0 || field > 3 || !std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z) ||
        std::abs(x) > 100000.0 || std::abs(y) > 100000.0 || std::abs(z) > 100000.0) return std::nan("");
    float m[16], centre_x, centre_y;
    if (!read_camera(m, centre_x, centre_y)) return std::nan("");
    const double sx = m[0] * x + m[4] * y + m[8] * z + m[12];
    const double sy = m[1] * x + m[5] * y + m[9] * z + m[13];
    const double w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (!std::isfinite(sx) || !std::isfinite(sy) || !std::isfinite(w) || !std::isfinite(centre_x) ||
        !std::isfinite(centre_y) || std::abs(centre_x) > 100000.0 || std::abs(centre_y) > 100000.0) return std::nan("");
    if (field == 3) return w > 0.05 ? 1.0 : 0.0;
    if (w <= 0.05) return std::nan(""); // behind the camera
    if (field == 2) return w;
    if (field == 0) return 320.0 + (sx / w - centre_x);
    return 224.0 + 2.0 * (sy / w - centre_y);
}

extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_resize(int width, int height) {
    ps2::resize_window(static_cast<std::uint32_t>(width), static_cast<std::uint32_t>(height));
}

extern "C" EMSCRIPTEN_KEEPALIVE int ps2web_quick_state(int loading) {
    return ps2::request_state_operation(loading != 0, "/quicksave.state", [](bool load, bool success) {
        MAIN_THREAD_ASYNC_EM_ASM({
            if (Module.onQuickState) Module.onQuickState(!!$0, !!$1);
        }, load, success);
    }) ? 1 : 0;
}

std::atomic<bool> g_streaming{false};

extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_stream(int on) { g_streaming.store(on != 0); }

extern "C" EMSCRIPTEN_KEEPALIVE void ps2web_video_options(int flags) { ps2::set_video_options(static_cast<std::uint32_t>(flags)); }

extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_gpu_ms() { return ps2::gpu_busy_ms(); }
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_frames() { return static_cast<double>(ps2::frames_run()); }
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_primitives() { return static_cast<double>(ps2::primitives_drawn()); }
extern "C" EMSCRIPTEN_KEEPALIVE void *ps2web_audio_ring() { return &g_audio; }
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_audio_queued_ms() { return audio_queued_ms(); }
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_vu_interpreted() { return static_cast<double>(ps2::vu1_interpreted_calls()); }
extern "C" EMSCRIPTEN_KEEPALIVE double ps2web_idle_ms(int which) {
    if (which == 2) return ps2::vu_thread_idle_ms();
    return which ? g_render_idle.load() : g_emulation_idle.load();
}

int main(int argc, char **argv) {
    static Launch launch;
    for (int i = 2; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--arg" && i + 1 < argc) launch.args.push_back(argv[++i]);
        else if (a == "--frames" && i + 1 < argc) ps2::g_options.max_frames = std::strtoull(argv[++i], nullptr, 10);
        else if (a == "--widescreen") ps2::g_options.widescreen = true;
        else if (a == "--press" && i + 1 < argc) {
            ps2::Press press;
            if (ps2::parse_press(argv[++i], press)) ps2::g_options.presses.push_back(press);
        }
    }
    if (launch.args.empty()) {
        std::string boot = "/game/core.elf";
        if (std::FILE *f = std::fopen("/game/boot.txt", "rb")) {
            char text[256] = {};
            const std::size_t n = std::fread(text, 1, sizeof text - 1, f);
            std::fclose(f);
            std::string line(text, n);
            line.erase(line.find_last_not_of(" \r\n\t") + 1);
            if (!line.empty()) boot = line;
        }
        launch.args.push_back(boot);
    }
    g_disc_size = static_cast<std::uint64_t>(ps2web_disc_size());
    g_disc_url = ps2web_disc_url();
    g_state_url = ps2web_state_url();
    ps2::g_options.on_vblank = on_vblank;
    ps2::g_options.on_vu_program = [](const std::string &name, const std::uint8_t *code, std::size_t size) {
        static bool unsupported = false;
        if (unsupported) return;
        const int status = ps2web_save_program(name.c_str(), code, static_cast<int>(size));
        if (status == 200 || status == 201) std::fprintf(stderr, "[web] saved VU1 program %s\n", name.c_str());
        else unsupported = true;
    };
    if (!EM_ASM_INT({ return Module.noCard ? 1 : 0; })) {
        ps2::g_options.memcard_path = "/memcard/Mcd001.ps2";
        ps2::g_options.on_memcard_saved = [] { MAIN_THREAD_ASYNC_EM_ASM({ if (Module.ps2CardSaved) Module.ps2CardSaved(); }); };
    }
    ps2::set_audio_sink(push_audio);
    register_generated_code();
    register_generated_vu();

    ps2::init_graphics();
    const bool no_surface = EM_ASM_INT({ return Module.noSurface ? 1 : 0; });
    bool copy = EM_ASM_INT({ return Module.presentCopy ? 1 : 0; });
    bool surface = false;
    if (!copy && !no_surface) {
        ps2::WindowTarget target;
        target.kind = ps2::WindowTarget::Canvas;
        target.selector = "#canvas";
        target.width = static_cast<std::uint32_t>(EM_ASM_INT({ return Module.canvas.width; }));
        target.height = static_cast<std::uint32_t>(EM_ASM_INT({ return Module.canvas.height; }));
        surface = ps2::attach_window(target);
    }
    copy = copy || (!surface && !no_surface);
    EM_ASM({
        Module.videoEffectsAvailable = !!$0;
        if (Module.refreshVideoControls) Module.refreshVideoControls();
    }, surface);
    std::fprintf(stderr, "[web] %s\n", surface ? "presenting through WebGPU" : copy ? "presenting through a 2D canvas copy" : "not presenting");

    ps2::start_render_thread();
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, 16u << 20);
    if (!std::getenv("PS2_NO_VU_THREAD")) {
        ps2::start_vu_thread();
        pthread_t vu_thread;
        if (pthread_create(&vu_thread, &attr, [](void *) -> void * {
                ps2::run_vu_thread();
                return nullptr;
            }, nullptr) != 0) {
            std::fprintf(stderr, "[web] cannot start the VU thread\n");
            return 1;
        }
    }
    pthread_t thread;
    if (pthread_create(&thread, &attr, emulate, &launch) != 0) {
        std::fprintf(stderr, "[web] cannot start the emulation thread\n");
        return 1;
    }
    const std::function<void(const std::uint32_t *, std::uint32_t, std::uint32_t)> blit = [](const std::uint32_t *rgba, std::uint32_t width, std::uint32_t height) {
        ps2web_blit(rgba, static_cast<int>(width), static_cast<int>(height), ps2::g_options.widescreen ? 1 : 0);
    };
    const std::function<void(const std::uint32_t *, std::uint32_t, std::uint32_t)> streamed = [copy, &blit](const std::uint32_t *rgba, std::uint32_t width, std::uint32_t height) {
        if (copy) blit(rgba, width, height);
        if (g_streaming.load()) ps2web_stream_frame(rgba, static_cast<int>(width), static_cast<int>(height));
    };
    const std::function<void(const std::uint32_t *, std::uint32_t, std::uint32_t)> none;
    const std::function<void()> wait_shown = [] {
        static int since_skip = 0;
        static double window_start = 0.0, window_gpu = 0.0, gpu_load = 1.0;
        const double start = emscripten_get_now();
        if (start - window_start > 500.0) {
            const double gpu = ps2::gpu_busy_ms();
            gpu_load = window_start > 0.0 ? (gpu - window_gpu) / (start - window_start) : 1.0;
            window_start = start;
            window_gpu = gpu;
        }
        if (++since_skip > 8 && gpu_load < 0.8 && g_audio.active.load() && audio_queued_ms() < 40.0) {
            since_skip = 0;
            return;
        }
        ps2web_wait_shown();
        g_render_idle.store(g_render_idle.load() + emscripten_get_now() - start);
    };
    for (;;) {
        service_disc();
        const ps2::Rendered rendered = ps2::render_pending(surface, g_streaming.load() ? streamed : copy ? blit : none, wait_shown, 6.0);
        if (rendered == ps2::Rendered::presented) ps2web_frame_done();
        if (rendered != ps2::Rendered::idle) {
            ps2::browser_yield();
        } else {
            const double start = emscripten_get_now();
            ps2::wait_render_work(50);
            g_render_idle.store(g_render_idle.load() + emscripten_get_now() - start);
        }
    }
}
