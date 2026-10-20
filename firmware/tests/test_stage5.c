/* Хост-тесты этапа 5: время, расписание (таймеры/будильники), мелодии, буфер, base64, плеер. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "check.h"
#include "noctis_audio.h"
#include "noctis_core.h"
#include "noctis_sched.h"

#define T_NOON 1792152000LL        /* 2026-10-16 12:00:00 UTC, пятница */
#define T_FRI_0730 1792135800LL    /* 2026-10-16 07:30:00 UTC */
#define T_SAT_0730 1792222200LL    /* 2026-10-17 07:30:00 UTC */
#define T_TIMER1 1792152300LL      /* NOON + 5 мин */

/* ============================================================ время */
static void test_time_helpers(void) {
    CHECK(!noctis_time_valid(0) && !noctis_time_valid(1704067199LL) && noctis_time_valid(1704067200LL));
    noctis_civil_t c;
    noctis_civil(0, &c);
    CHECK(c.year == 1970 && c.month == 1 && c.day == 1 && c.hour == 0 && c.wday == 3);                 /* четверг */
    noctis_civil(T_NOON, &c);
    CHECK(c.year == 2026 && c.month == 10 && c.day == 16 && c.hour == 12 && c.minute == 0 && c.wday == 4);
    noctis_civil(1835481599LL, &c);                                                                    /* 29.02.2028 23:59:59 */
    CHECK(c.year == 2028 && c.month == 2 && c.day == 29 && c.hour == 23 && c.minute == 59 && c.second == 59 && c.wday == 1);
    noctis_civil(-1, &c);
    CHECK(c.year == 1969 && c.month == 12 && c.day == 31 && c.hour == 23 && c.wday == 2);

    char clk[8];
    CHECK(noctis_format_clock(T_NOON, 3 * 3600, clk, sizeof clk) == 5 && STREQ(clk, "15:00"));
    CHECK(noctis_format_clock(T_NOON, -(5 * 3600 + 1800), clk, sizeof clk) == 5 && STREQ(clk, "06:30"));
    CHECK(noctis_format_clock(T_NOON, 0, clk, 5) == -1);                                               /* не помещается */

    int64_t e = 0;
    int32_t off = 0;
    const char* ok = "{\"type\":\"time_sync\",\"epoch\":1792152000,\"utc_offset\":10800}";
    CHECK(noctis_parse_time_sync(ok, strlen(ok), &e, &off) && e == T_NOON && off == 10800);
    const char* old = "{\"type\":\"time_sync\",\"epoch\":1000,\"utc_offset\":0}";
    CHECK(!noctis_parse_time_sync(old, strlen(old), &e, &off));                                        /* 1970 — не время */
    const char* wild = "{\"type\":\"time_sync\",\"epoch\":1792152000,\"utc_offset\":99999}";
    CHECK(!noctis_parse_time_sync(wild, strlen(wild), &e, &off));
    const char* other = "{\"type\":\"config\",\"epoch\":1792152000,\"utc_offset\":0}";
    CHECK(!noctis_parse_time_sync(other, strlen(other), &e, &off) && !noctis_parse_time_sync("{bad", 4, &e, &off));
}

/* ============================================================ расписание */
static const char* SCHED_MSG =
    "{\"type\":\"schedule\",\"rev\":7,"
    "\"timers\":[{\"id\":1,\"end\":1792152300,\"label\":\"tea\"},{\"id\":2,\"end\":1792153000,\"label\":\"\"}],"
    "\"alarms\":[{\"id\":10,\"h\":7,\"m\":30,\"wd\":31,\"date\":0,\"label\":\"work\",\"melody\":\"soft\"},"
    "{\"id\":11,\"h\":6,\"m\":15,\"wd\":0,\"date\":20261231,\"label\":\"\",\"melody\":\"nope\"}]}";

static void test_sched_parse(void) {
    sched_state_t s;
    sched_init(&s);
    CHECK(sched_parse(SCHED_MSG, strlen(SCHED_MSG), NULL, &s));
    CHECK(s.rev == 7 && s.n_timers == 2 && s.n_alarms == 2);
    CHECK(s.timers[0].id == 1 && s.timers[0].end_epoch == T_TIMER1 && STREQ(s.timers[0].label, "tea") && !s.timers[0].fired);
    CHECK(s.alarms[0].id == 10 && s.alarms[0].hour == 7 && s.alarms[0].minute == 30 && s.alarms[0].weekdays == 31);
    CHECK(STREQ(s.alarms[0].melody, "soft") && STREQ(s.alarms[0].label, "work"));
    CHECK(s.alarms[1].date_ymd == 20261231 && s.alarms[1].weekdays == 0 && STREQ(s.alarms[1].melody, "default"));   /* неизвестная мелодия */

    sched_state_t keep = s;
    const char* bad[] = {"{\"type\":\"config\"}", "{broken", "[]", "{\"type\":\"schedule\"", NULL};
    for (int i = 0; bad[i]; i++) CHECK(!sched_parse(bad[i], strlen(bad[i]), NULL, &s));
    CHECK(memcmp(&keep, &s, sizeof s) == 0);                                                          /* при ошибке не изменяется */

    /* неверные элементы пропускаются, остальные принимаются */
    const char* mixed =
        "{\"type\":\"schedule\",\"rev\":1,\"timers\":[{\"id\":-1,\"end\":1792152300},{\"id\":3},{\"id\":4,\"end\":1792152400}],"
        "\"alarms\":[{\"id\":1,\"h\":24,\"m\":0,\"wd\":1},{\"id\":2,\"h\":7,\"m\":60,\"wd\":1},{\"id\":3,\"h\":7,\"m\":0,\"wd\":0},"
        "{\"id\":4,\"h\":7,\"m\":0,\"wd\":200},{\"id\":5,\"h\":7,\"m\":0,\"wd\":0,\"date\":20991301},{\"id\":6,\"h\":8,\"m\":5,\"wd\":3}]}";
    CHECK(sched_parse(mixed, strlen(mixed), NULL, &s));
    CHECK(s.n_timers == 1 && s.timers[0].id == 4);
    CHECK(s.n_alarms == 1 && s.alarms[0].id == 6 && s.alarms[0].weekdays == 3);   /* час 24, минута 60, однократный без даты, wd 200, месяц 13 — отброшены */

    /* лимиты */
    static char big[4096];
    int n = snprintf(big, sizeof big, "{\"type\":\"schedule\",\"timers\":[");
    for (int i = 0; i < 12; i++) n += snprintf(big + n, sizeof big - n, "%s{\"id\":%d,\"end\":1792152300}", i ? "," : "", i);
    n += snprintf(big + n, sizeof big - n, "],\"alarms\":[");
    for (int i = 0; i < 20; i++) n += snprintf(big + n, sizeof big - n, "%s{\"id\":%d,\"h\":7,\"m\":0,\"wd\":1}", i ? "," : "", i);
    snprintf(big + n, sizeof big - n, "]}");
    CHECK(sched_parse(big, strlen(big), NULL, &s) && s.n_timers == SCHED_MAX_TIMERS && s.n_alarms == SCHED_MAX_ALARMS);

    /* пустой снимок очищает всё */
    const char* empty = "{\"type\":\"schedule\",\"rev\":9,\"timers\":[],\"alarms\":[]}";
    sched_state_t prev = s;
    CHECK(sched_parse(empty, strlen(empty), &prev, &s) && s.n_timers == 0 && s.n_alarms == 0 && s.rev == 9);
}

static void test_sched_poll(void) {
    sched_state_t s;
    sched_event_t ev[8];
    sched_init(&s);
    CHECK(sched_parse(SCHED_MSG, strlen(SCHED_MSG), NULL, &s));
    CHECK(sched_poll(&s, 100, 0, ev, 8) == 0);                                                        /* время не синхронизировано */
    CHECK(sched_poll(&s, T_TIMER1 - 1, 0, ev, 8) == 0);
    CHECK(sched_poll(&s, T_TIMER1, 0, ev, 8) == 1 && ev[0].kind == SCHED_EVT_TIMER && ev[0].id == 1 && STREQ(ev[0].label, "tea"));
    CHECK(sched_poll(&s, T_TIMER1 + 1, 0, ev, 8) == 0);                                               /* повторно — нет */
    CHECK(sched_poll(&s, 1792153000LL, 0, ev, 8) == 1 && ev[0].id == 2);

    /* будильник пн–пт 7:30 (UTC) */
    CHECK(sched_poll(&s, T_FRI_0730 - 1, 0, ev, 8) == 0);
    CHECK(sched_poll(&s, T_FRI_0730, 0, ev, 8) == 1 && ev[0].kind == SCHED_EVT_ALARM && ev[0].id == 10 && STREQ(ev[0].melody, "soft"));
    CHECK(sched_poll(&s, T_FRI_0730 + 30, 0, ev, 8) == 0);                                            /* та же минута */
    CHECK(sched_poll(&s, T_SAT_0730, 0, ev, 8) == 0);                                                 /* суббота */
    CHECK(sched_poll(&s, T_FRI_0730 + 3 * 86400LL, 0, ev, 8) == 1);                                   /* понедельник */

    /* часовой пояс: 7:30 в UTC+3 — это 04:30 UTC */
    CHECK(sched_parse(SCHED_MSG, strlen(SCHED_MSG), NULL, &s));
    CHECK(sched_poll(&s, T_FRI_0730 - 3 * 3600, 3 * 3600, ev, 8) == 1 && ev[0].id == 10);
    CHECK(sched_poll(&s, T_FRI_0730, 3 * 3600, ev, 8) == 0);                                          /* в Москве это уже 10:30 */

    /* однократный: только в свою дату */
    sched_state_t one;
    const char* o = "{\"type\":\"schedule\",\"timers\":[],\"alarms\":[{\"id\":1,\"h\":7,\"m\":30,\"wd\":0,\"date\":20261017}]}";
    CHECK(sched_parse(o, strlen(o), NULL, &one));
    CHECK(sched_poll(&one, T_FRI_0730, 0, ev, 8) == 0);
    CHECK(sched_poll(&one, T_SAT_0730, 0, ev, 8) == 1);
    CHECK(sched_poll(&one, T_SAT_0730 + 10, 0, ev, 8) == 0);

    /* через полночь по местному времени: UTC 22:00 + 3 ч = 01:00 следующих суток */
    sched_state_t late;
    const char* l = "{\"type\":\"schedule\",\"timers\":[],\"alarms\":[{\"id\":1,\"h\":1,\"m\":0,\"wd\":0,\"date\":20261017}]}";
    CHECK(sched_parse(l, strlen(l), NULL, &late));
    CHECK(sched_poll(&late, T_NOON + 10 * 3600LL, 3 * 3600, ev, 8) == 1);

    /* ограничение числа событий за вызов и порядок */
    sched_state_t many;
    const char* mm = "{\"type\":\"schedule\",\"timers\":[{\"id\":1,\"end\":1792152300},{\"id\":2,\"end\":1792152300},{\"id\":3,\"end\":1792152300}],\"alarms\":[]}";
    CHECK(sched_parse(mm, strlen(mm), NULL, &many));
    CHECK(sched_poll(&many, 1792152400LL, 0, ev, 2) == 2 && ev[0].id == 1 && ev[1].id == 2);
    CHECK(sched_poll(&many, 1792152400LL, 0, ev, 2) == 1 && ev[0].id == 3);                           /* остаток — в следующий раз */
    CHECK(sched_poll(&many, 1792152400LL, 0, ev, 0) == 0);
}

static void test_sched_previous_state_survives_new_snapshot(void) {
    sched_state_t s, next;
    sched_event_t ev[4];
    CHECK(sched_parse(SCHED_MSG, strlen(SCHED_MSG), NULL, &s));
    CHECK(sched_poll(&s, T_TIMER1, 0, ev, 4) == 1);                                                   /* таймер 1 сработал */
    CHECK(sched_poll(&s, T_FRI_0730, 0, ev, 4) == 1);                                                 /* будильник 10 сработал */
    CHECK(sched_parse(SCHED_MSG, strlen(SCHED_MSG), &s, &next));                                      /* сервер прислал тот же снимок */
    CHECK(sched_poll(&next, T_TIMER1, 0, ev, 4) == 0);                                                /* таймер не повторяется */
    CHECK(sched_poll(&next, T_FRI_0730 + 20, 0, ev, 4) == 0);                                         /* будильник — тоже */
    /* перенесённый будильник — новый звонок разрешён */
    const char* moved = "{\"type\":\"schedule\",\"timers\":[],\"alarms\":[{\"id\":10,\"h\":7,\"m\":31,\"wd\":31}]}";
    CHECK(sched_parse(moved, strlen(moved), &s, &next));
    CHECK(sched_poll(&next, T_FRI_0730 + 60, 0, ev, 4) == 1);
}

static void test_sched_summary(void) {
    sched_state_t s;
    int left, h, m;
    CHECK(sched_parse(SCHED_MSG, strlen(SCHED_MSG), NULL, &s));
    sched_summary(&s, T_NOON, 0, &left, &h, &m);
    CHECK(left == 300 && h == 7 && m == 30);                      /* пятница 12:00: 7:30 уже было → ближайший пн 7:30 (или 6:15 31 декабря — позже) */
    sched_summary(&s, T_FRI_0730 - 600, 0, &left, &h, &m);
    CHECK(h == 7 && m == 30);                                                                         /* сегодня ещё не звонил */
    sched_event_t ev[4];
    sched_poll(&s, T_FRI_0730, 0, ev, 4);
    sched_summary(&s, T_FRI_0730 + 1, 0, &left, &h, &m);
    CHECK(h == 7 && m == 30);                                                                         /* следующий — в понедельник */
    sched_summary(&s, 50, 0, &left, &h, &m);
    CHECK(left == -1 && h == -1 && m == -1);                                                          /* время не задано */
    sched_state_t none;
    sched_init(&none);
    sched_summary(&none, T_NOON, 0, &left, &h, &m);
    CHECK(left == -1 && h == -1);
}

static void test_sched_storage(void) {
    sched_state_t s, back;
    sched_event_t ev[4];
    uint8_t blob[SCHED_BLOB_MAX], bad[SCHED_BLOB_MAX];
    CHECK(sched_parse(SCHED_MSG, strlen(SCHED_MSG), NULL, &s));
    sched_poll(&s, T_TIMER1, 0, ev, 4);
    sched_poll(&s, T_FRI_0730, 0, ev, 4);
    size_t n = sched_serialize(&s, blob, sizeof blob);
    CHECK(n > 0 && n <= SCHED_BLOB_MAX);
    CHECK(sched_deserialize(blob, n, &back) && back.rev == s.rev && back.n_timers == s.n_timers && back.n_alarms == s.n_alarms);
    CHECK(back.timers[0].fired && !back.timers[1].fired && back.alarms[0].last_key == s.alarms[0].last_key && back.alarms[0].last_key != 0);
    CHECK(STREQ(back.alarms[0].melody, "soft") && STREQ(back.timers[0].label, "tea") && back.timers[1].end_epoch == 1792153000LL);

    /* порча любого байта обнаруживается (CRC32), как и обрезка/хвост/NULL */
    int rejected = 0;
    for (size_t i = 0; i < n; i++) {
        memcpy(bad, blob, n);
        bad[i] ^= 0x5A;
        if (!sched_deserialize(bad, n, &back)) rejected++;
    }
    CHECK(rejected == (int)n);
    CHECK(!sched_deserialize(blob, n - 1, &back) && !sched_deserialize(blob, 0, &back) && !sched_deserialize(NULL, 10, &back));
    memcpy(bad, blob, n);
    bad[n] = 0;
    CHECK(!sched_deserialize(bad, n + 1, &back));
    CHECK(sched_serialize(&s, blob, 20) == 0);                                                        /* не помещается */

    sched_state_t empty;
    sched_init(&empty);
    n = sched_serialize(&empty, blob, sizeof blob);
    CHECK(n > 0 && sched_deserialize(blob, n, &back) && back.n_timers == 0 && back.n_alarms == 0);

    /* полностью заполненное состояние помещается в SCHED_BLOB_MAX */
    sched_state_t full;
    sched_init(&full);
    full.n_timers = SCHED_MAX_TIMERS;
    full.n_alarms = SCHED_MAX_ALARMS;
    for (int i = 0; i < SCHED_MAX_TIMERS; i++) {
        full.timers[i].id = i;
        full.timers[i].end_epoch = T_TIMER1;
        memset(full.timers[i].label, 'x', SCHED_LABEL_MAX - 1);
    }
    for (int i = 0; i < SCHED_MAX_ALARMS; i++) {
        full.alarms[i].id = i;
        full.alarms[i].weekdays = 1;
        strcpy(full.alarms[i].melody, "rooster");
        memset(full.alarms[i].label, 'y', SCHED_LABEL_MAX - 1);
    }
    n = sched_serialize(&full, blob, sizeof blob);
    CHECK(n > 0 && n <= SCHED_BLOB_MAX && sched_deserialize(blob, n, &back) && memcmp(&full, &back, sizeof full) == 0);
}

static void test_sched_builders_melodies_and_command(void) {
    char out[96];
    CHECK(sched_build_event(out, sizeof out, SCHED_EVT_TIMER, 5) > 0 && STREQ(out, "{\"type\":\"sched_event\",\"kind\":\"timer\",\"id\":5}"));
    CHECK(sched_build_event(out, sizeof out, SCHED_EVT_ALARM, 12) > 0 && strstr(out, "\"kind\":\"alarm\"") && strstr(out, "\"id\":12"));
    CHECK(sched_build_event(out, 10, SCHED_EVT_ALARM, 12) == -1);
    CHECK(sched_build_ring_stopped(out, sizeof out) > 0 && STREQ(out, "{\"type\":\"ring_stopped\"}"));

    const char* names[] = {"default", "soft", "rooster", "beeps", "no-such-melody", NULL};
    for (int i = 0; names[i]; i++) {
        int f, ms, steps = 0, total_ms = 0;
        while (noctis_melody_note(names[i], steps, &f, &ms) && steps < 50) {
            CHECK(f >= 0 && f <= 4000 && ms > 0 && ms <= 1000);
            total_ms += ms;
            steps++;
        }
        CHECK(steps >= 4 && steps < 50 && total_ms >= 1000 && total_ms <= 4000);                      /* паттерн конечный, 1–4 с */
    }
    int f, ms;
    CHECK(noctis_melody_note("soft", -1, &f, &ms) == 0 && noctis_melody_note(NULL, 0, &f, &ms) == 1 && f == 880);
    CHECK(noctis_is_valid_melody("rooster") && !noctis_is_valid_melody("../x") && !noctis_is_valid_melody(NULL));

    noctis_command_t c;
    const char* sr = "{\"type\":\"command\",\"name\":\"stop_ring\"}";
    CHECK(noctis_parse_server_message(sr, strlen(sr), NULL, &c) == NOCTIS_MSG_COMMAND && c.cmd == NOCTIS_CMD_STOP_RING);
    CHECK(STREQ(NOCTIS_FIRMWARE_VERSION, "0.4.0"));
}

/* ============================================================ буфер и base64 */
static void test_ring(void) {
    uint8_t store[8], out[16];
    noctis_ring_t r;
    noctis_ring_init(&r, store, sizeof store);
    CHECK(noctis_ring_write(&r, (const uint8_t*)"abcdef", 6) == 6 && noctis_ring_size(&r) == 6 && noctis_ring_free(&r) == 2);
    CHECK(noctis_ring_read(&r, out, 4) == 4 && memcmp(out, "abcd", 4) == 0);
    CHECK(noctis_ring_write(&r, (const uint8_t*)"ghijk", 5) == 5);                                      /* через конец буфера */
    CHECK(noctis_ring_size(&r) == 7 && noctis_ring_write(&r, (const uint8_t*)"zz", 2) == 1);           /* помещается 1 */
    CHECK(noctis_ring_read(&r, out, 16) == 8 && memcmp(out, "efghijkz", 8) == 0);
    CHECK(noctis_ring_read(&r, out, 4) == 0 && noctis_ring_size(&r) == 0);
    noctis_ring_write(&r, (const uint8_t*)"xy", 2);
    noctis_ring_clear(&r);
    CHECK(noctis_ring_size(&r) == 0 && noctis_ring_free(&r) == 8);
    noctis_ring_t zero;
    noctis_ring_init(&zero, store, 0);
    CHECK(noctis_ring_write(&zero, (const uint8_t*)"a", 1) == 0);
}

static void test_base64(void) {
    uint8_t out[32];
    size_t n;
    const struct { const char* enc; const char* dec; } v[] = {{"", ""}, {"Zg==", "f"}, {"Zm8=", "fo"}, {"Zm9v", "foo"},
                                                               {"Zm9vYg==", "foob"}, {"Zm9vYmE=", "fooba"}, {"Zm9vYmFy", "foobar"}};
    for (size_t i = 0; i < sizeof v / sizeof v[0]; i++)
        CHECK(noctis_base64_decode(v[i].enc, strlen(v[i].enc), out, sizeof out, &n) && n == strlen(v[i].dec) && memcmp(out, v[i].dec, n) == 0);
    const char* bad[] = {"Zm9", "Zm9v!", "Zm=v", "=Zm9", "Zg==Zg==", "Zm9vYmFy====", "Z m9v", "Zm9v\n", NULL};
    for (int i = 0; bad[i]; i++) CHECK(!noctis_base64_decode(bad[i], strlen(bad[i]), out, sizeof out, &n));
    CHECK(!noctis_base64_decode("Zm9vYmFy", 8, out, 5, &n));                                         /* не помещается */
    CHECK(noctis_base64_decode("/+8=", 4, out, sizeof out, &n) && n == 2 && out[0] == 0xFF && out[1] == 0xEF);
}

/* ============================================================ плеер */
static size_t chunk_msg(char* out, size_t cap, const uint8_t* pcm, size_t len) {
    static const char T[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t n = (size_t)snprintf(out, cap, "{\"type\":\"music_chunk\",\"index\":0,\"data\":\"");
    for (size_t i = 0; i < len; i += 3) {
        uint32_t v = (uint32_t)pcm[i] << 16 | (i + 1 < len ? (uint32_t)pcm[i + 1] << 8 : 0) | (i + 2 < len ? pcm[i + 2] : 0);
        out[n++] = T[v >> 18 & 63];
        out[n++] = T[v >> 12 & 63];
        out[n++] = i + 1 < len ? T[v >> 6 & 63] : '=';
        out[n++] = i + 2 < len ? T[v & 63] : '=';
    }
    n += (size_t)snprintf(out + n, cap - n, "\"}");
    return n;
}

static player_msg_t feed(noctis_player_t* p, size_t bytes, uint8_t fill) {
    static uint8_t pcm[3200];
    static char msg[5000];
    memset(pcm, fill, sizeof pcm);
    size_t n = chunk_msg(msg, sizeof msg, pcm, bytes);
    return noctis_player_message(p, msg, n);
}

#define MSG(p, s) noctis_player_message((p), (s), strlen(s))
#define FEED3(p, fill) do { feed((p), 3200, (fill)); feed((p), 3200, (fill)); feed((p), 3200, (fill)); } while (0)

static void test_player_flow(void) {
    static uint8_t store[32768];
    static uint8_t out[4096];
    noctis_player_t p;
    noctis_player_init(&p, store, sizeof store);
    CHECK(!noctis_player_active(&p) && noctis_player_pull(&p, out, sizeof out) == 0);
    CHECK(feed(&p, 3200, 1) == PLAYER_MSG_NONE);                                                     /* чанк без старта игнорируется */
    CHECK(MSG(&p, "{\"type\":\"music_start\",\"title\":\"Song\",\"artist\":\"Band\",\"source\":\"local\"}") == PLAYER_MSG_START);
    CHECK(p.state == PLAYER_BUFFERING && STREQ(p.title, "Song") && STREQ(p.artist, "Band") && noctis_player_active(&p));
    CHECK(noctis_player_pull(&p, out, sizeof out) == 0);                                             /* предзапас не набран */
    CHECK(feed(&p, 3200, 1) == PLAYER_MSG_CHUNK && feed(&p, 3200, 2) == PLAYER_MSG_CHUNK && p.state == PLAYER_BUFFERING);
    CHECK(feed(&p, 3200, 3) == PLAYER_MSG_CHUNK && p.state == PLAYER_PLAYING);                       /* 9600 ≥ 8000 */
    CHECK(noctis_player_pull(&p, out, 3200) == 3200 && out[0] == 1 && out[3199] == 1);
    CHECK(noctis_player_pull(&p, out, 3201) == 3200 && out[0] == 2);                                 /* чётное число байт */

    /* пауза/продолжение: данные не теряются */
    CHECK(MSG(&p, "{\"type\":\"music_pause\"}") == PLAYER_MSG_PAUSE && p.state == PLAYER_PAUSED);
    CHECK(noctis_player_pull(&p, out, sizeof out) == 0 && feed(&p, 3200, 4) == PLAYER_MSG_CHUNK);
    CHECK(MSG(&p, "{\"type\":\"music_resume\"}") == PLAYER_MSG_RESUME && p.state == PLAYER_PLAYING);
    CHECK(noctis_player_pull(&p, out, 3200) == 3200 && out[0] == 3);

    /* конец трека: доиграть остаток, затем покой */
    CHECK(MSG(&p, "{\"type\":\"music_end\",\"reason\":\"end\"}") == PLAYER_MSG_END && feed(&p, 3200, 9) == PLAYER_MSG_NONE);
    CHECK(noctis_player_pull(&p, out, 3200) == 3200 && out[0] == 4);
    CHECK(p.state == PLAYER_IDLE && !noctis_player_active(&p));

    /* стоп сбрасывает буфер; новый старт — тоже */
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"A\"}");
    FEED3(&p, 1);
    CHECK(MSG(&p, "{\"type\":\"music_stop\"}") == PLAYER_MSG_STOP && p.state == PLAYER_IDLE && noctis_ring_size(&p.ring) == 0);
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"B\"}");
    feed(&p, 3200, 1);
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"C\"}");
    CHECK(noctis_ring_size(&p.ring) == 0 && STREQ(p.title, "C") && p.state == PLAYER_BUFFERING);

    /* принудительная остановка прошивкой (началась команда голосом) */
    FEED3(&p, 1);
    noctis_player_stop(&p);
    CHECK(p.state == PLAYER_IDLE && noctis_player_pull(&p, out, sizeof out) == 0);
}

static void test_player_edge_cases(void) {
    static uint8_t store[16000];
    static uint8_t out[4096];
    noctis_player_t p;
    noctis_player_init(&p, store, sizeof store);
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"T\"}");

    /* нечётный размер: хвостовой байт отбрасывается, чтобы не ломать отсчёты */
    CHECK(feed(&p, 3001, 5) == PLAYER_MSG_CHUNK && noctis_ring_size(&p.ring) == 3000);
    /* мусор */
    CHECK(MSG(&p, "{\"type\":\"music_chunk\",\"data\":\"@@@@\"}") == PLAYER_MSG_NONE);
    CHECK(MSG(&p, "{\"type\":\"music_chunk\"}") == PLAYER_MSG_NONE && MSG(&p, "{broken") == PLAYER_MSG_NONE);
    CHECK(MSG(&p, "{\"type\":\"config\"}") == PLAYER_MSG_NONE && MSG(&p, "{\"type\":\"music_dance\"}") == PLAYER_MSG_NONE);

    /* переполнение буфера считается, лишнее отбрасывается */
    for (int i = 0; i < 6; i++) feed(&p, 3200, 1);
    CHECK(p.overruns > 0 && noctis_ring_size(&p.ring) == sizeof store);

    /* опустошение посреди трека: накопить предзапас заново */
    noctis_player_stop(&p);
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"U\"}");
    FEED3(&p, 1);                                                                                    /* 9600 байт, играем */
    CHECK(p.state == PLAYER_PLAYING);
    CHECK(noctis_player_pull(&p, out, 4096) == 4096 && noctis_player_pull(&p, out, 4096) == 4096);
    CHECK(p.state == PLAYER_PLAYING && p.underruns == 0);
    CHECK(noctis_player_pull(&p, out, 4096) == 1408);                                                /* остаток, данные кончились */
    CHECK(p.state == PLAYER_BUFFERING && p.underruns == 1 && noctis_player_pull(&p, out, 4096) == 0);
    FEED3(&p, 1);
    CHECK(p.state == PLAYER_PLAYING);

    /* пауза, поставленная во время набора предзапаса, сохраняется после набора */
    noctis_player_stop(&p);
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"V\"}");
    MSG(&p, "{\"type\":\"music_pause\"}");
    FEED3(&p, 1);
    CHECK(p.state == PLAYER_PAUSED && noctis_player_pull(&p, out, 100) == 0);
    MSG(&p, "{\"type\":\"music_resume\"}");
    CHECK(p.state == PLAYER_PLAYING && noctis_player_pull(&p, out, 100) == 100);

    /* короткий трек (меньше предзапаса) проигрывается целиком после music_end */
    noctis_player_stop(&p);
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"W\"}");
    feed(&p, 1000, 7);
    CHECK(p.state == PLAYER_BUFFERING);
    MSG(&p, "{\"type\":\"music_end\"}");
    CHECK(p.state == PLAYER_PLAYING && noctis_player_pull(&p, out, 4096) == 1000 && p.state == PLAYER_IDLE);
}

static void test_gain(void) {
    CHECK(noctis_music_gain_q8(100, 100) == 256 && noctis_music_gain_q8(0, 100) == 0);
    CHECK(noctis_music_gain_q8(70, 50) == 256 * 70 * 70 / 10000 * 50 / 100);
    CHECK(noctis_music_gain_q8(300, 100) == 256 && noctis_music_gain_q8(50, -5) == 0);
    int16_t s[4] = {1000, -1000, 32767, -32768};
    noctis_apply_gain(s, 4, 128);
    CHECK(s[0] == 500 && s[1] == -500 && s[2] == 16383 && s[3] == -16384);
    int16_t loud[2] = {30000, -30000};
    noctis_apply_gain(loud, 2, 512);
    CHECK(loud[0] == 32767 && loud[1] == -32768);                                                    /* ограничение без переполнения */
}

static void test_player_prebuffer(void) {
    static uint8_t store[65536];
    static uint8_t out[4096];
    noctis_player_t p;
    noctis_player_init(&p, store, sizeof store);
    CHECK(p.prebuffer == PLAYER_PREBUFFER_BYTES);
    noctis_player_set_prebuffer(&p, 32001);                                                          /* нечётное округляется вниз */
    CHECK(p.prebuffer == 32000);
    noctis_player_set_prebuffer(&p, 1u << 20);                                                       /* больше ёмкости: ограничивается ею */
    CHECK(p.prebuffer == sizeof store);
    noctis_player_set_prebuffer(&p, 32000);
    MSG(&p, "{\"type\":\"music_start\",\"title\":\"P\"}");
    for (int i = 0; i < 9; i++) CHECK(feed(&p, 3200, 1) == PLAYER_MSG_CHUNK && p.state == PLAYER_BUFFERING);   /* 28800 < 32000 */
    CHECK(noctis_player_pull(&p, out, sizeof out) == 0);
    CHECK(feed(&p, 3200, 1) == PLAYER_MSG_CHUNK && p.state == PLAYER_PLAYING);                       /* 32000: играем */
    /* после опустошения предзапас набирается заново — те же 32000 */
    while (noctis_player_pull(&p, out, sizeof out) > 0) {}
    CHECK(p.state == PLAYER_BUFFERING && p.underruns == 1);
    for (int i = 0; i < 9; i++) feed(&p, 3200, 1);
    CHECK(p.state == PLAYER_BUFFERING);
    feed(&p, 3200, 1);
    CHECK(p.state == PLAYER_PLAYING);
}

void run_stage5_tests(void) {
    test_time_helpers();
    test_sched_parse();
    test_sched_poll();
    test_sched_previous_state_survives_new_snapshot();
    test_sched_summary();
    test_sched_storage();
    test_sched_builders_melodies_and_command();
    test_ring();
    test_base64();
    test_player_flow();
    test_player_edge_cases();
    test_player_prebuffer();
    test_gain();
}
