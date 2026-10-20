#include "noctis_audio.h"

#include <string.h>

#include "cJSON.h"

/* ============================================================ кольцевой буфер */
void noctis_ring_init(noctis_ring_t* r, uint8_t* storage, size_t cap) {
    r->buf = storage;
    r->cap = cap;
    r->head = r->size = 0;
}

size_t noctis_ring_size(const noctis_ring_t* r) { return r->size; }
size_t noctis_ring_free(const noctis_ring_t* r) { return r->cap - r->size; }
void noctis_ring_clear(noctis_ring_t* r) { r->head = r->size = 0; }

size_t noctis_ring_write(noctis_ring_t* r, const uint8_t* data, size_t len) {
    if (r->cap == 0) return 0;
    const size_t n = len < noctis_ring_free(r) ? len : noctis_ring_free(r);
    size_t tail = (r->head + r->size) % r->cap;
    for (size_t i = 0; i < n;) {
        const size_t run = (r->cap - tail) < (n - i) ? (r->cap - tail) : (n - i);
        memcpy(r->buf + tail, data + i, run);
        i += run;
        tail = (tail + run) % r->cap;
    }
    r->size += n;
    return n;
}

size_t noctis_ring_read(noctis_ring_t* r, uint8_t* out, size_t max) {
    const size_t n = max < r->size ? max : r->size;
    for (size_t i = 0; i < n;) {
        const size_t run = (r->cap - r->head) < (n - i) ? (r->cap - r->head) : (n - i);
        memcpy(out + i, r->buf + r->head, run);
        i += run;
        r->head = (r->head + run) % r->cap;
    }
    r->size -= n;
    return n;
}

/* ============================================================ base64 */
static int b64_val(unsigned char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

bool noctis_base64_decode(const char* in, size_t in_len, uint8_t* out, size_t cap, size_t* out_len) {
    if (in_len % 4 != 0) return false;
    size_t n = 0;
    for (size_t i = 0; i < in_len; i += 4) {
        int v[4], pad = 0;
        for (int k = 0; k < 4; k++) {
            const unsigned char c = (unsigned char)in[i + k];
            if (c == '=') {
                if (i + 4 != in_len || k < 2) return false;     // «=» только в конце (последние 1–2 символа)
                v[k] = 0;
                pad++;
            } else if (pad || (v[k] = b64_val(c)) < 0) {
                return false;
            }
        }
        const uint32_t triple = ((uint32_t)v[0] << 18) | ((uint32_t)v[1] << 12) | ((uint32_t)v[2] << 6) | (uint32_t)v[3];
        const int bytes = 3 - pad;
        if (n + (size_t)bytes > cap) return false;
        for (int b = 0; b < bytes; b++) out[n++] = (uint8_t)(triple >> (16 - 8 * b));
    }
    if (out_len) *out_len = n;
    return true;
}

/* ============================================================ плеер */
void noctis_player_init(noctis_player_t* p, uint8_t* storage, size_t cap) {
    memset(p, 0, sizeof(*p));
    noctis_ring_init(&p->ring, storage, cap);
    p->prebuffer = PLAYER_PREBUFFER_BYTES;
}

void noctis_player_set_prebuffer(noctis_player_t* p, size_t bytes) {
    if (bytes > p->ring.cap) bytes = p->ring.cap;   /* больше ёмкости — воспроизведение никогда не началось бы */
    p->prebuffer = bytes & ~(size_t)1;
}

size_t noctis_player_size(void) { return sizeof(noctis_player_t); }

static void player_reset(noctis_player_t* p) {
    noctis_ring_clear(&p->ring);
    p->state = PLAYER_IDLE;
    p->ended = p->paused = false;
}

void noctis_player_stop(noctis_player_t* p) { player_reset(p); }
bool noctis_player_active(const noctis_player_t* p) { return p->state != PLAYER_IDLE; }

static const char* jstr(const cJSON* o, const char* key) {
    const cJSON* v = cJSON_GetObjectItemCaseSensitive(o, key);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}

player_msg_t noctis_player_message(noctis_player_t* p, const char* json, size_t len) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == NULL) return PLAYER_MSG_NONE;
    const char* type = jstr(root, "type");
    player_msg_t kind = PLAYER_MSG_NONE;
    if (type == NULL || strncmp(type, "music_", 6) != 0) {
        cJSON_Delete(root);
        return PLAYER_MSG_NONE;
    }
    if (strcmp(type, "music_start") == 0) {
        player_reset(p);
        const char* t = jstr(root, "title");
        const char* a = jstr(root, "artist");
        const char* s = jstr(root, "source");
        noctis_copy(p->title, sizeof(p->title), t ? t : "");
        noctis_copy(p->artist, sizeof(p->artist), a ? a : "");
        noctis_copy(p->source, sizeof(p->source), s ? s : "");
        p->state = PLAYER_BUFFERING;
        kind = PLAYER_MSG_START;
    } else if (strcmp(type, "music_chunk") == 0) {
        const char* data = jstr(root, "data");
        if (data && p->state != PLAYER_IDLE && !p->ended) {
            uint8_t tmp[4096];
            size_t n = 0, dlen = strlen(data);
            if (dlen <= (sizeof(tmp) / 3) * 4 && noctis_base64_decode(data, dlen, tmp, sizeof(tmp), &n)) {
                const size_t put = noctis_ring_write(&p->ring, tmp, n & ~(size_t)1);   // только целые 16-битные отсчёты
                p->received_bytes += (uint32_t)put;
                if (put < (n & ~(size_t)1)) p->overruns += (uint32_t)((n & ~(size_t)1) - put);
                if (p->state == PLAYER_BUFFERING && noctis_ring_size(&p->ring) >= p->prebuffer)
                    p->state = p->paused ? PLAYER_PAUSED : PLAYER_PLAYING;
                kind = PLAYER_MSG_CHUNK;
            }
        }
    } else if (strcmp(type, "music_end") == 0) {
        if (p->state != PLAYER_IDLE) {
            p->ended = true;
            if (p->state == PLAYER_BUFFERING && !p->paused) p->state = PLAYER_PLAYING;    // короткий трек: доиграть то, что есть
            kind = PLAYER_MSG_END;
        }
    } else if (strcmp(type, "music_pause") == 0) {
        if (p->state != PLAYER_IDLE) {
            p->paused = true;
            if (p->state == PLAYER_PLAYING) p->state = PLAYER_PAUSED;
            kind = PLAYER_MSG_PAUSE;
        }
    } else if (strcmp(type, "music_resume") == 0) {
        if (p->state != PLAYER_IDLE) {
            p->paused = false;
            if (p->state == PLAYER_PAUSED) p->state = PLAYER_PLAYING;
            kind = PLAYER_MSG_RESUME;
        }
    } else if (strcmp(type, "music_stop") == 0) {
        player_reset(p);
        kind = PLAYER_MSG_STOP;
    }
    cJSON_Delete(root);
    return kind;
}

size_t noctis_player_pull(noctis_player_t* p, uint8_t* out, size_t max) {
    if (p->state != PLAYER_PLAYING) return 0;
    max &= ~(size_t)1;
    if (max == 0) return 0;
    const size_t n = noctis_ring_read(&p->ring, out, max);
    if (noctis_ring_size(&p->ring) == 0) {
        if (p->ended) {
            player_reset(p);                       // всё доиграно
        } else if (n < max) {                      // данные кончились раньше, чем пришли новые: накопить заново
            p->state = PLAYER_BUFFERING;
            p->underruns++;
        }
    }
    return n;
}

int noctis_music_gain_q8(int volume_percent, int music_percent) {
    if (volume_percent < 0) volume_percent = 0;
    if (volume_percent > 100) volume_percent = 100;
    if (music_percent < 0) music_percent = 0;
    if (music_percent > 100) music_percent = 100;
    return 256 * volume_percent * volume_percent / 10000 * music_percent / 100;   // та же квадратичная кривая, что у речи
}

void noctis_apply_gain(int16_t* samples, size_t count, int gain_q8) {
    for (size_t i = 0; i < count; i++) {
        int32_t v = ((int32_t)samples[i] * gain_q8) >> 8;
        if (v > 32767) v = 32767;
        if (v < -32768) v = -32768;
        samples[i] = (int16_t)v;
    }
}
