#include "noctis_sched.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"

#define EPOCH_2024 1704067200LL
#define MAX_OFFSET_SEC (14 * 3600)

/* ============================================================ время */
bool noctis_time_valid(int64_t epoch) { return epoch >= EPOCH_2024; }

bool noctis_parse_time_sync(const char* json, size_t len, int64_t* epoch, int32_t* utc_offset) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == NULL) return false;
    bool ok = false;
    const cJSON* type = cJSON_GetObjectItemCaseSensitive(root, "type");
    const cJSON* e = cJSON_GetObjectItemCaseSensitive(root, "epoch");
    const cJSON* o = cJSON_GetObjectItemCaseSensitive(root, "utc_offset");
    if (cJSON_IsString(type) && strcmp(type->valuestring, "time_sync") == 0 && cJSON_IsNumber(e) && cJSON_IsNumber(o) &&
        noctis_time_valid((int64_t)e->valuedouble) && o->valuedouble >= -MAX_OFFSET_SEC && o->valuedouble <= MAX_OFFSET_SEC) {
        if (epoch) *epoch = (int64_t)e->valuedouble;
        if (utc_offset) *utc_offset = (int32_t)o->valuedouble;
        ok = true;
    }
    cJSON_Delete(root);
    return ok;
}

static int64_t floor_div(int64_t a, int64_t b) { return a / b - ((a % b != 0) && ((a < 0) != (b < 0))); }

static void civil_from_days(int64_t z, int* y, int* m, int* d) {   // алгоритм Хиннанта
    z += 719468;
    const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const int64_t doe = z - era * 146097;
    const int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const int64_t mp = (5 * doy + 2) / 153;
    *d = (int)(doy - (153 * mp + 2) / 5 + 1);
    *m = (int)(mp < 10 ? mp + 3 : mp - 9);
    *y = (int)(yoe + era * 400 + (*m <= 2));
}

static int weekday_of_day(int64_t days) { return (int)(((days + 3) % 7 + 7) % 7); }   // 1970-01-01 = четверг → 3

void noctis_civil(int64_t local_epoch, noctis_civil_t* out) {
    const int64_t days = floor_div(local_epoch, 86400);
    const int64_t sod = local_epoch - days * 86400;
    civil_from_days(days, &out->year, &out->month, &out->day);
    out->hour = (int)(sod / 3600);
    out->minute = (int)(sod % 3600 / 60);
    out->second = (int)(sod % 60);
    out->wday = weekday_of_day(days);
}

int noctis_format_clock(int64_t epoch, int32_t utc_offset, char* out, size_t cap) {
    noctis_civil_t c;
    noctis_civil(epoch + utc_offset, &c);
    int n = snprintf(out, cap, "%02d:%02d", c.hour, c.minute);
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

/* ============================================================ мелодии */
typedef struct { int freq, ms; } note_t;
static const note_t M_DEFAULT[] = {{880, 200}, {0, 100}, {880, 200}, {0, 100}, {880, 200}, {0, 700}};
static const note_t M_SOFT[] = {{523, 300}, {659, 300}, {784, 400}, {0, 800}};
static const note_t M_ROOSTER[] = {{392, 150}, {523, 150}, {659, 150}, {784, 450}, {0, 900}};
static const note_t M_BEEPS[] = {{1000, 90}, {0, 90}, {1000, 90}, {0, 90}, {1000, 90}, {0, 90}, {1000, 90}, {0, 800}};
#define N(a) ((int)(sizeof(a) / sizeof((a)[0])))

static const struct { const char* name; const note_t* notes; int n; } MELODIES[] = {
    {"default", M_DEFAULT, N(M_DEFAULT)}, {"soft", M_SOFT, N(M_SOFT)},
    {"rooster", M_ROOSTER, N(M_ROOSTER)}, {"beeps", M_BEEPS, N(M_BEEPS)},
};

bool noctis_is_valid_melody(const char* melody) {
    if (melody == NULL) return false;
    for (size_t i = 0; i < sizeof(MELODIES) / sizeof(MELODIES[0]); i++)
        if (strcmp(melody, MELODIES[i].name) == 0) return true;
    return false;
}

int noctis_melody_note(const char* melody, int step, int* freq_hz, int* ms) {
    size_t idx = 0;
    for (size_t i = 0; i < sizeof(MELODIES) / sizeof(MELODIES[0]); i++)
        if (melody && strcmp(melody, MELODIES[i].name) == 0) idx = i;
    if (step < 0 || step >= MELODIES[idx].n) return 0;
    *freq_hz = MELODIES[idx].notes[step].freq;
    *ms = MELODIES[idx].notes[step].ms;
    return 1;
}

/* ============================================================ расписание */
void sched_init(sched_state_t* s) { memset(s, 0, sizeof(*s)); }
size_t sched_state_size(void) { return sizeof(sched_state_t); }

static const char* str_of(const cJSON* o, const char* key) {
    const cJSON* v = cJSON_GetObjectItemCaseSensitive(o, key);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}

static bool num_of(const cJSON* o, const char* key, double lo, double hi, double* out) {
    const cJSON* v = cJSON_GetObjectItemCaseSensitive(o, key);
    if (!cJSON_IsNumber(v) || v->valuedouble < lo || v->valuedouble > hi) return false;
    *out = v->valuedouble;
    return true;
}

static bool valid_ymd(int ymd) {
    if (ymd == 0) return true;
    const int y = ymd / 10000, m = ymd / 100 % 100, d = ymd % 100;
    return y >= 2024 && y <= 2100 && m >= 1 && m <= 12 && d >= 1 && d <= 31;
}

bool sched_parse(const char* json, size_t len, const sched_state_t* prev, sched_state_t* out) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == NULL) return false;
    const char* type = str_of(root, "type");
    if (type == NULL || strcmp(type, "schedule") != 0) {
        cJSON_Delete(root);
        return false;
    }
    sched_state_t s;
    sched_init(&s);
    double v;
    if (num_of(root, "rev", 0, 2147483647.0, &v)) s.rev = (int32_t)v;

    const cJSON* timers = cJSON_GetObjectItemCaseSensitive(root, "timers");
    const cJSON* it;
    cJSON_ArrayForEach(it, timers) {
        double id, end;
        if (s.n_timers >= SCHED_MAX_TIMERS) break;
        if (!cJSON_IsObject(it) || !num_of(it, "id", 0, 2147483647.0, &id) || !num_of(it, "end", 0, 4102444800.0, &end)) continue;
        sched_timer_t* t = &s.timers[s.n_timers++];
        t->id = (int32_t)id;
        t->end_epoch = (int64_t)end;
        const char* label = str_of(it, "label");
        noctis_copy(t->label, sizeof(t->label), label ? label : "");
        if (prev)                                                   // уже сработавший таймер не срабатывает снова
            for (int i = 0; i < prev->n_timers; i++)
                if (prev->timers[i].id == t->id && prev->timers[i].end_epoch == t->end_epoch) t->fired = prev->timers[i].fired;
    }

    const cJSON* alarms = cJSON_GetObjectItemCaseSensitive(root, "alarms");
    cJSON_ArrayForEach(it, alarms) {
        double id, h, m, wd, date = 0;
        if (s.n_alarms >= SCHED_MAX_ALARMS) break;
        if (!cJSON_IsObject(it) || !num_of(it, "id", 0, 2147483647.0, &id) || !num_of(it, "h", 0, 23, &h) ||
            !num_of(it, "m", 0, 59, &m) || !num_of(it, "wd", 0, 127, &wd))
            continue;
        num_of(it, "date", 0, 21001231, &date);
        if (!valid_ymd((int)date) || (wd == 0 && date == 0)) continue;     // однократному нужна дата
        sched_alarm_t* a = &s.alarms[s.n_alarms++];
        a->id = (int32_t)id;
        a->hour = (uint8_t)h;
        a->minute = (uint8_t)m;
        a->weekdays = (uint8_t)wd;
        a->date_ymd = wd ? 0 : (int32_t)date;
        const char* label = str_of(it, "label");
        const char* melody = str_of(it, "melody");
        noctis_copy(a->label, sizeof(a->label), label ? label : "");
        noctis_copy(a->melody, sizeof(a->melody), noctis_is_valid_melody(melody) ? melody : "default");
        if (prev)
            for (int i = 0; i < prev->n_alarms; i++)
                if (prev->alarms[i].id == a->id && prev->alarms[i].hour == a->hour && prev->alarms[i].minute == a->minute)
                    a->last_key = prev->alarms[i].last_key;
    }
    *out = s;
    cJSON_Delete(root);
    return true;
}

int sched_poll(sched_state_t* s, int64_t now_epoch, int32_t utc_offset, sched_event_t* out, int max) {
    if (!noctis_time_valid(now_epoch) || max <= 0) return 0;
    int n = 0;
    for (int i = 0; i < s->n_timers && n < max; i++) {
        sched_timer_t* t = &s->timers[i];
        if (t->fired || t->end_epoch > now_epoch) continue;
        t->fired = true;
        out[n].kind = SCHED_EVT_TIMER;
        out[n].id = t->id;
        noctis_copy(out[n].label, sizeof(out[n].label), t->label);
        noctis_copy(out[n].melody, sizeof(out[n].melody), "default");
        n++;
    }
    const int64_t local = now_epoch + utc_offset;
    noctis_civil_t c;
    noctis_civil(local, &c);
    const int32_t key = (int32_t)floor_div(local, 60);
    const int ymd = c.year * 10000 + c.month * 100 + c.day;
    for (int i = 0; i < s->n_alarms && n < max; i++) {
        sched_alarm_t* a = &s->alarms[i];
        if (a->hour != c.hour || a->minute != c.minute || a->last_key == key) continue;
        if (a->weekdays ? !(a->weekdays & (1 << c.wday)) : a->date_ymd != ymd) continue;
        a->last_key = key;
        out[n].kind = SCHED_EVT_ALARM;
        out[n].id = a->id;
        noctis_copy(out[n].label, sizeof(out[n].label), a->label);
        noctis_copy(out[n].melody, sizeof(out[n].melody), a->melody);
        n++;
    }
    return n;
}

void sched_summary(const sched_state_t* s, int64_t now_epoch, int32_t utc_offset, int* timer_left_sec, int* alarm_hour,
                   int* alarm_minute) {
    *timer_left_sec = -1;
    *alarm_hour = *alarm_minute = -1;
    if (!noctis_time_valid(now_epoch)) return;
    for (int i = 0; i < s->n_timers; i++) {
        if (s->timers[i].fired) continue;
        const int64_t left = s->timers[i].end_epoch - now_epoch;
        if (left < 0) continue;
        if (*timer_left_sec < 0 || left < *timer_left_sec) *timer_left_sec = (int)left;
    }
    const int64_t local = now_epoch + utc_offset;
    const int64_t today = floor_div(local, 86400);
    const int64_t now_min = floor_div(local, 60);
    int64_t best = -1;
    for (int i = 0; i < s->n_alarms; i++) {
        const sched_alarm_t* a = &s->alarms[i];
        for (int off = 0; off <= 7; off++) {
            const int64_t day = today + off;
            const int64_t cand = day * 1440 + a->hour * 60 + a->minute;
            if (cand < now_min || (cand == now_min && a->last_key == (int32_t)cand)) continue;
            int y, m, d;
            civil_from_days(day, &y, &m, &d);
            const bool match = a->weekdays ? (a->weekdays & (1 << weekday_of_day(day))) != 0 : a->date_ymd == y * 10000 + m * 100 + d;
            if (!match) continue;
            if (best < 0 || cand < best) {
                best = cand;
                *alarm_hour = a->hour;
                *alarm_minute = a->minute;
            }
            break;
        }
    }
}

/* ============================================================ хранение */
static uint32_t crc32(const uint8_t* data, size_t len) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1));
    }
    return ~crc;
}

typedef struct { uint8_t* p; size_t cap, n; bool over; } wbuf_t;
static void w_bytes(wbuf_t* w, const void* d, size_t len) {
    if (w->n + len > w->cap) { w->over = true; return; }
    memcpy(w->p + w->n, d, len);
    w->n += len;
}
static void w_u8(wbuf_t* w, uint8_t v) { w_bytes(w, &v, 1); }
static void w_u32(wbuf_t* w, uint32_t v) { for (int i = 0; i < 4; i++) w_u8(w, (uint8_t)(v >> (8 * i))); }
static void w_u64(wbuf_t* w, uint64_t v) { for (int i = 0; i < 8; i++) w_u8(w, (uint8_t)(v >> (8 * i))); }

typedef struct { const uint8_t* p; size_t len, n; bool bad; } rbuf_t;
static const uint8_t* r_bytes(rbuf_t* r, size_t len) {
    if (r->n + len > r->len) { r->bad = true; return NULL; }
    const uint8_t* q = r->p + r->n;
    r->n += len;
    return q;
}
static uint8_t r_u8(rbuf_t* r) { const uint8_t* q = r_bytes(r, 1); return q ? q[0] : 0; }
static uint32_t r_u32(rbuf_t* r) { uint32_t v = 0; for (int i = 0; i < 4; i++) v |= (uint32_t)r_u8(r) << (8 * i); return v; }
static uint64_t r_u64(rbuf_t* r) { uint64_t v = 0; for (int i = 0; i < 8; i++) v |= (uint64_t)r_u8(r) << (8 * i); return v; }
static void r_str(rbuf_t* r, char* dst, size_t cap) {
    const uint8_t* q = r_bytes(r, cap);
    if (!q) return;
    memcpy(dst, q, cap);
    dst[cap - 1] = '\0';
}

#define MAGIC "NSCH"
#define BLOB_VERSION 1

size_t sched_serialize(const sched_state_t* s, uint8_t* buf, size_t cap) {
    wbuf_t w = {buf, cap, 0, false};
    w_bytes(&w, MAGIC, 4);
    w_u8(&w, BLOB_VERSION);
    w_u32(&w, (uint32_t)s->rev);
    w_u8(&w, (uint8_t)s->n_timers);
    w_u8(&w, (uint8_t)s->n_alarms);
    for (int i = 0; i < s->n_timers; i++) {
        const sched_timer_t* t = &s->timers[i];
        w_u32(&w, (uint32_t)t->id);
        w_u64(&w, (uint64_t)t->end_epoch);
        w_u8(&w, t->fired ? 1 : 0);
        w_bytes(&w, t->label, SCHED_LABEL_MAX);
    }
    for (int i = 0; i < s->n_alarms; i++) {
        const sched_alarm_t* a = &s->alarms[i];
        w_u32(&w, (uint32_t)a->id);
        w_u8(&w, a->hour);
        w_u8(&w, a->minute);
        w_u8(&w, a->weekdays);
        w_u32(&w, (uint32_t)a->date_ymd);
        w_u32(&w, (uint32_t)a->last_key);
        w_bytes(&w, a->label, SCHED_LABEL_MAX);
        w_bytes(&w, a->melody, SCHED_MELODY_MAX);
    }
    if (w.over) return 0;
    w_u32(&w, crc32(buf, w.n));
    return w.over ? 0 : w.n;
}

bool sched_deserialize(const uint8_t* buf, size_t len, sched_state_t* out) {
    if (buf == NULL || len < 4 + 1 + 4 + 2 + 4) return false;
    uint32_t stored = 0;
    for (int i = 0; i < 4; i++) stored |= (uint32_t)buf[len - 4 + i] << (8 * i);
    if (crc32(buf, len - 4) != stored) return false;
    rbuf_t r = {buf, len - 4, 0, false};
    const uint8_t* magic = r_bytes(&r, 4);
    if (!magic || memcmp(magic, MAGIC, 4) != 0 || r_u8(&r) != BLOB_VERSION) return false;
    sched_state_t s;
    sched_init(&s);
    s.rev = (int32_t)r_u32(&r);
    const int nt = r_u8(&r), na = r_u8(&r);
    if (nt > SCHED_MAX_TIMERS || na > SCHED_MAX_ALARMS) return false;
    for (int i = 0; i < nt; i++) {
        sched_timer_t* t = &s.timers[i];
        t->id = (int32_t)r_u32(&r);
        t->end_epoch = (int64_t)r_u64(&r);
        t->fired = r_u8(&r) != 0;
        r_str(&r, t->label, SCHED_LABEL_MAX);
    }
    for (int i = 0; i < na; i++) {
        sched_alarm_t* a = &s.alarms[i];
        a->id = (int32_t)r_u32(&r);
        a->hour = r_u8(&r);
        a->minute = r_u8(&r);
        a->weekdays = r_u8(&r);
        a->date_ymd = (int32_t)r_u32(&r);
        a->last_key = (int32_t)r_u32(&r);
        r_str(&r, a->label, SCHED_LABEL_MAX);
        r_str(&r, a->melody, SCHED_MELODY_MAX);
        if (a->hour > 23 || a->minute > 59 || a->weekdays > 127 || !valid_ymd(a->date_ymd) ||
            (a->weekdays == 0 && a->date_ymd == 0) || !noctis_is_valid_melody(a->melody))
            return false;
    }
    if (r.bad || r.n != r.len) return false;
    s.n_timers = nt;
    s.n_alarms = na;
    *out = s;
    return true;
}

int sched_build_event(char* out, size_t cap, sched_evt_kind_t kind, int32_t id) {
    const int n = snprintf(out, cap, "{\"type\":\"sched_event\",\"kind\":\"%s\",\"id\":%d}",
                           kind == SCHED_EVT_TIMER ? "timer" : "alarm", (int)id);
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

int sched_build_ring_stopped(char* out, size_t cap) {
    const int n = snprintf(out, cap, "{\"type\":\"ring_stopped\"}");
    return (n > 0 && (size_t)n < cap) ? n : -1;
}
