#include "noctis_core.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"

static const char* const LANGS[] = {"ru", "en", "es", "de", "fr"};
static const char* const VOICES[] = {"male", "female"};
static const char* const CHARACTERS[] = {"friendly", "formal",  "humorous", "sarcastic", "motivating",
                                         "calm",     "energetic", "wise",    "caring",    "neutral"};

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))

static bool in_list(const char* v, const char* const* list, size_t n) {
    if (v == NULL) return false;
    for (size_t i = 0; i < n; i++)
        if (strcmp(v, list[i]) == 0) return true;
    return false;
}

static const char* const WAKE_PRESETS[] = {"nils", "privet_esp", "hi_esp", "hallo_esp", "hola_esp",
                                           "bonjour_esp", "alexa", "computer", "jarvis"};

bool noctis_is_valid_wake_word(const char* v) {
    if (v == NULL) return false;
    if (in_list(v, WAKE_PRESETS, COUNT(WAKE_PRESETS))) return true;
    if (strncmp(v, "custom:", 7) != 0) return false;
    const unsigned char* p = (const unsigned char*)v + 7;
    int chars = 0;
    unsigned char first = *p, last = 0;
    for (; *p; p++) {
        if ((*p & 0xC0) == 0x80) continue;  // продолжение UTF-8: тот же символ
        chars++;
        last = *p;
        bool letter = isalpha(*p) || *p >= 0x80;
        bool inner = *p == ' ' || *p == '-';
        if (!letter && !inner) return false;
    }
    bool edge_ok = first && (isalpha(first) || first >= 0x80) && (isalpha(last) || last >= 0x80);
    return chars >= 2 && chars <= 32 && edge_ok;
}

bool noctis_is_valid_language(const char* v) { return in_list(v, LANGS, COUNT(LANGS)); }
bool noctis_is_valid_voice(const char* v) { return in_list(v, VOICES, COUNT(VOICES)); }
bool noctis_is_valid_character(const char* v) { return in_list(v, CHARACTERS, COUNT(CHARACTERS)); }

bool noctis_copy(char* dst, size_t cap, const char* src) {
    if (cap == 0) return false;
    if (src == NULL) {
        dst[0] = '\0';
        return true;
    }
    size_t n = strlen(src);
    bool fit = n < cap;
    if (!fit) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
    return fit;
}

void noctis_settings_defaults(noctis_settings_t* s) {
    memset(s, 0, sizeof(*s));
    noctis_copy(s->wake_word, sizeof(s->wake_word), "nils");
    noctis_copy(s->voice_gender, sizeof(s->voice_gender), "male");
    noctis_copy(s->character_id, sizeof(s->character_id), "friendly");
    noctis_copy(s->language, sizeof(s->language), "ru");
    noctis_copy(s->timezone, sizeof(s->timezone), "Europe/Moscow");
    s->volume = 70;
    s->revision = 0;
}

int noctis_settings_sanitize(noctis_settings_t* s) {
    noctis_settings_t d;
    noctis_settings_defaults(&d);
    int fixed = 0;
    if (!noctis_is_valid_wake_word(s->wake_word)) {
        noctis_copy(s->wake_word, sizeof(s->wake_word), d.wake_word);
        fixed++;
    }
    if (!noctis_is_valid_voice(s->voice_gender)) {
        noctis_copy(s->voice_gender, sizeof(s->voice_gender), d.voice_gender);
        fixed++;
    }
    if (!noctis_is_valid_character(s->character_id)) {
        noctis_copy(s->character_id, sizeof(s->character_id), d.character_id);
        fixed++;
    }
    if (!noctis_is_valid_language(s->language)) {
        noctis_copy(s->language, sizeof(s->language), d.language);
        fixed++;
    }
    if (s->timezone[0] == '\0') {
        noctis_copy(s->timezone, sizeof(s->timezone), d.timezone);
        fixed++;
    }
    if (s->volume < 0) {
        s->volume = 0;
        fixed++;
    } else if (s->volume > 100) {
        s->volume = 100;
        fixed++;
    }
    if (s->revision < 0) {
        s->revision = 0;
        fixed++;
    }
    return fixed;
}

static const char* str_field(const cJSON* root, const char* name) {
    const cJSON* it = cJSON_GetObjectItemCaseSensitive(root, name);
    return cJSON_IsString(it) ? it->valuestring : NULL;
}


// Проверки для смены сети Wi-Fi: значения попадают в настройки радиомодуля и в ответ серверу
static bool wifi_ssid_ok(const char* s) {
    size_t n = strlen(s);
    if (n < 1 || n > 32) return false;
    for (; *s; s++)
        if ((unsigned char)*s < 0x20 || (unsigned char)*s == 0x7f) return false;
    return true;
}

static bool wifi_password_ok(const char* p) {
    size_t n = strlen(p);
    if (n == 0) return true;  // открытая сеть
    if (n < 8 || n > 64) return false;
    for (const char* q = p; *q; q++)
        if ((unsigned char)*q < 0x20 || (unsigned char)*q == 0x7f) return false;
    if (n == 64)
        for (const char* q = p; *q; q++)
            if (!isxdigit((unsigned char)*q)) return false;  // 64 символа — только PSK в hex
    return true;
}

static bool reqid_ok(const char* r) {
    size_t n = strlen(r);
    if (n > 16) return false;
    for (; *r; r++)
        if (!isalnum((unsigned char)*r) && *r != '-' && *r != '_') return false;
    return true;
}

noctis_msg_kind_t noctis_parse_server_message(const char* json, size_t len, noctis_settings_t* settings,
                                              noctis_command_t* cmd) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == NULL) return NOCTIS_MSG_OTHER;
    noctis_msg_kind_t kind = NOCTIS_MSG_OTHER;
    const char* type = str_field(root, "type");

    if (type && strcmp(type, "config") == 0 && settings) {
        const char* v;
        if ((v = str_field(root, "wake_word")) && noctis_is_valid_wake_word(v) &&
            strlen(v) < sizeof(settings->wake_word))
            noctis_copy(settings->wake_word, sizeof(settings->wake_word), v);
        if ((v = str_field(root, "voice_gender")) && noctis_is_valid_voice(v))
            noctis_copy(settings->voice_gender, sizeof(settings->voice_gender), v);
        if ((v = str_field(root, "character_id")) && noctis_is_valid_character(v))
            noctis_copy(settings->character_id, sizeof(settings->character_id), v);
        if ((v = str_field(root, "language")) && noctis_is_valid_language(v))
            noctis_copy(settings->language, sizeof(settings->language), v);
        if ((v = str_field(root, "timezone")) && v[0] && strlen(v) < sizeof(settings->timezone))
            noctis_copy(settings->timezone, sizeof(settings->timezone), v);
        const cJSON* vol = cJSON_GetObjectItemCaseSensitive(root, "volume");
        if (cJSON_IsNumber(vol) && vol->valuedouble >= 0 && vol->valuedouble <= 100)
            settings->volume = (int)vol->valuedouble;
        const cJSON* rev = cJSON_GetObjectItemCaseSensitive(root, "revision");
        if (cJSON_IsNumber(rev) && rev->valuedouble >= 0) settings->revision = (int)rev->valuedouble;
        kind = NOCTIS_MSG_CONFIG;
    } else if (type && strcmp(type, "command") == 0 && cmd) {
        memset(cmd, 0, sizeof(*cmd));
        const char* name = str_field(root, "name");
        if (name && strcmp(name, "reboot") == 0) {
            cmd->cmd = NOCTIS_CMD_REBOOT;
        } else if (name && strcmp(name, "factory_reset") == 0) {
            cmd->cmd = NOCTIS_CMD_FACTORY_RESET;
        } else if (name && strcmp(name, "stop_ring") == 0) {
            cmd->cmd = NOCTIS_CMD_STOP_RING;
        } else if (name && strcmp(name, "set_wifi") == 0) {
            const char* ssid = str_field(root, "ssid");
            const char* pass = str_field(root, "password");
            const char* rid = str_field(root, "request_id");
            if (ssid && wifi_ssid_ok(ssid) && (pass == NULL || wifi_password_ok(pass)) && (rid == NULL || reqid_ok(rid))) {
                cmd->cmd = NOCTIS_CMD_SET_WIFI;
                noctis_copy(cmd->ssid, sizeof(cmd->ssid), ssid);
                noctis_copy(cmd->password, sizeof(cmd->password), pass ? pass : "");
                noctis_copy(cmd->request_id, sizeof(cmd->request_id), rid ? rid : "");
            }
        } else if (name && strcmp(name, "ota") == 0) {
            const char* ver = str_field(root, "version");
            const char* sha = str_field(root, "sha256");
            uint8_t tmp[32];
            // OTA принимается только с корректной версией и SHA-256. Поле "url" из сообщения НЕ используется:
            // адрес образа прошивка строит сама из сохранённой ссылки на сервер (noctis_build_ota_url).
            if (ver && sha && noctis_version_compare(ver, "0.0.0") >= 0 && noctis_sha256_from_hex(sha, tmp) &&
                strlen(ver) < sizeof(cmd->version)) {
                cmd->cmd = NOCTIS_CMD_OTA;
                noctis_copy(cmd->version, sizeof(cmd->version), ver);
                noctis_copy(cmd->sha256, sizeof(cmd->sha256), sha);
                const cJSON* size = cJSON_GetObjectItemCaseSensitive(root, "size");
                cmd->size = cJSON_IsNumber(size) ? (int)size->valuedouble : 0;
            }
        }
        kind = NOCTIS_MSG_COMMAND;
    }
    cJSON_Delete(root);
    return kind;
}

bool noctis_parse_link(const char* json, size_t len, noctis_link_t* out) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == NULL) return false;
    const char* server = str_field(root, "server");
    const char* id = str_field(root, "device_id");
    const char* token = str_field(root, "token");
    bool ok = server && id && token && server[0] && id[0] && token[0] && strlen(server) < sizeof(out->server) &&
              strlen(id) < sizeof(out->device_id) && strlen(token) < sizeof(out->token);
    // Только безопасные символы: значения попадают в URL
    for (const char* p = server; ok && *p; p++) ok = isalnum((unsigned char)*p) || strchr(".-:_", *p);
    for (const char* p = id; ok && *p; p++) ok = isalnum((unsigned char)*p);
    for (const char* p = token; ok && *p; p++) ok = isalnum((unsigned char)*p) || *p == '-' || *p == '_';
    if (ok) {
        memset(out, 0, sizeof(*out));
        noctis_copy(out->server, sizeof(out->server), server);
        noctis_copy(out->device_id, sizeof(out->device_id), id);
        noctis_copy(out->token, sizeof(out->token), token);
        const cJSON* tls = cJSON_GetObjectItemCaseSensitive(root, "tls");
        out->tls = cJSON_IsTrue(tls);
    }
    cJSON_Delete(root);
    return ok;
}

bool noctis_parse_link_update(const char* json, size_t len, noctis_link_t* link, bool have_existing) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == NULL) return false;
    const char* server = str_field(root, "server");
    const char* id = str_field(root, "device_id");
    const char* token = str_field(root, "token");
    bool ok = server && server[0] && strlen(server) < sizeof(link->server);
    for (const char* p = server; ok && *p; p++) ok = isalnum((unsigned char)*p) || strchr(".-:_", *p);
    if (ok && ((id == NULL) != (token == NULL))) ok = false;  // только вместе
    if (ok && id == NULL && !have_existing) ok = false;       // нечего сохранять
    if (ok && id) {
        ok = id[0] && token[0] && strlen(id) < sizeof(link->device_id) && strlen(token) < sizeof(link->token);
        for (const char* p = id; ok && *p; p++) ok = isalnum((unsigned char)*p);
        for (const char* p = token; ok && *p; p++) ok = isalnum((unsigned char)*p) || *p == '-' || *p == '_';
    }
    if (ok) {
        noctis_link_t next;
        if (have_existing)
            next = *link;
        else
            memset(&next, 0, sizeof(next));
        noctis_copy(next.server, sizeof(next.server), server);
        if (id) {
            noctis_copy(next.device_id, sizeof(next.device_id), id);
            noctis_copy(next.token, sizeof(next.token), token);
        }
        const cJSON* tls = cJSON_GetObjectItemCaseSensitive(root, "tls");
        if (cJSON_IsBool(tls)) next.tls = cJSON_IsTrue(tls);
        *link = next;
    }
    cJSON_Delete(root);
    return ok;
}

int noctis_build_ws_uri(const noctis_link_t* link, char* out, size_t cap) {
    int n = snprintf(out, cap, "%s://%s/noctis/v1/", link->tls ? "wss" : "ws", link->server);
    return (n < 0 || (size_t)n >= cap) ? -1 : n;
}

int noctis_build_auth_headers(const noctis_link_t* link, char* out, size_t cap) {
    int n = snprintf(out, cap, "X-Device-Id: %s\r\nX-Device-Token: %s\r\n", link->device_id, link->token);
    return (n < 0 || (size_t)n >= cap) ? -1 : n;
}

int noctis_build_ota_url(const noctis_link_t* link, const char* version, char* out, size_t cap) {
    if (noctis_version_compare(version, "0.0.0") < 0) return -1;  // только корректная версия попадает в URL
    int n = snprintf(out, cap, "%s://%s/api/v1/device/ota/%s", link->tls ? "https" : "http", link->server, version);
    return (n < 0 || (size_t)n >= cap) ? -1 : n;
}

static int print_json(cJSON* o, char* out, size_t cap) {
    char* s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (s == NULL) return -1;
    size_t n = strlen(s);
    int r = -1;
    if (n < cap) {
        memcpy(out, s, n + 1);
        r = (int)n;
    }
    cJSON_free(s);
    return r;
}

int noctis_build_device_info(char* out, size_t cap, int config_revision, unsigned free_heap,
                             const char* reset_reason) {
    cJSON* o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", "device_info");
    cJSON_AddStringToObject(o, "firmware", NOCTIS_FIRMWARE_VERSION);
    cJSON_AddNumberToObject(o, "config_revision", config_revision);
    cJSON_AddNumberToObject(o, "free_heap", free_heap);
    cJSON_AddStringToObject(o, "reset_reason", reset_reason ? reset_reason : "unknown");
    return print_json(o, out, cap);
}

int noctis_build_ota_status(char* out, size_t cap, const char* version, const char* state, const char* error) {
    cJSON* o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", "ota_status");
    cJSON_AddStringToObject(o, "version", version ? version : "");
    cJSON_AddStringToObject(o, "state", state ? state : "");
    if (error) cJSON_AddStringToObject(o, "error", error);
    return print_json(o, out, cap);
}

static bool parse_version(const char* v, long out[3]) {
    char* end;
    for (int i = 0; i < 3; i++) {
        if (!isdigit((unsigned char)*v)) return false;
        out[i] = strtol(v, &end, 10);
        if (out[i] > 100000) return false;
        v = end;
        if (i < 2) {
            if (*v != '.') return false;
            v++;
        }
    }
    return *v == '\0';
}

int noctis_version_compare(const char* a, const char* b) {
    long x[3], y[3];
    if (a == NULL || b == NULL || !parse_version(a, x) || !parse_version(b, y)) return -2;
    for (int i = 0; i < 3; i++) {
        if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
    }
    return 0;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool noctis_sha256_from_hex(const char* hex, uint8_t out[32]) {
    if (hex == NULL || strlen(hex) != 64) return false;
    for (int i = 0; i < 32; i++) {
        int hi = hexval(hex[2 * i]), lo = hexval(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return true;
}

void noctis_sha256_to_hex(const uint8_t in[32], char out[65]) {
    static const char* d = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[2 * i] = d[in[i] >> 4];
        out[2 * i + 1] = d[in[i] & 15];
    }
    out[64] = '\0';
}

/* ============ Этап 4 ============ */

static uint32_t rotr32(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

static void sha256_block(uint32_t h[8], const uint8_t* b) {
    static const uint32_t K[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
        w[i] = (uint32_t)b[4 * i] << 24 | (uint32_t)b[4 * i + 1] << 16 | (uint32_t)b[4 * i + 2] << 8 | b[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = hh + S1 + ch + K[i] + w[i];
        uint32_t S0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        uint32_t mj = (a & bb) ^ (a & c) ^ (bb & c);
        uint32_t t2 = S0 + mj;
        hh = g; g = f; f = e; e = d + t1; d = c; c = bb; bb = a; a = t1 + t2;
    }
    h[0] += a; h[1] += bb; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

void noctis_sha256(const uint8_t* data, size_t len, uint8_t out[32]) {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    size_t full = len / 64;
    for (size_t i = 0; i < full; i++) sha256_block(h, data + 64 * i);
    uint8_t tail[128];
    size_t rem = len - 64 * full;
    memcpy(tail, data + 64 * full, rem);
    tail[rem] = 0x80;
    size_t tail_len = rem < 56 ? 64 : 128;
    memset(tail + rem + 1, 0, tail_len - rem - 1);
    uint64_t bits = (uint64_t)len * 8;
    for (int i = 0; i < 8; i++) tail[tail_len - 1 - i] = (uint8_t)(bits >> (8 * i));
    sha256_block(h, tail);
    if (tail_len == 128) sha256_block(h, tail + 64);
    for (int i = 0; i < 8; i++) {
        out[4 * i] = (uint8_t)(h[i] >> 24);
        out[4 * i + 1] = (uint8_t)(h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(h[i] >> 8);
        out[4 * i + 3] = (uint8_t)h[i];
    }
}

static const struct {
    const char* id;
    const char* model;
} ESP_SR_WORDS[] = {
    {"privet_esp", "privetesp"}, {"hi_esp", "hiesp"},   {"hallo_esp", "halloesp"}, {"hola_esp", "holaesp"},
    {"bonjour_esp", "bonjouresp"}, {"alexa", "alexa"}, {"computer", "computer"},   {"jarvis", "jarvis"},
};

noctis_engine_t noctis_wake_engine(const char* wake_word, const char** esp_sr_model) {
    if (esp_sr_model) *esp_sr_model = NULL;
    if (wake_word) {
        if (strcmp(wake_word, "nils") == 0 || strncmp(wake_word, "custom:", 7) == 0) return NOCTIS_ENGINE_MWW;
        for (size_t i = 0; i < COUNT(ESP_SR_WORDS); i++) {
            if (strcmp(wake_word, ESP_SR_WORDS[i].id) == 0) {
                if (esp_sr_model) *esp_sr_model = ESP_SR_WORDS[i].model;
                return NOCTIS_ENGINE_ESP_SR;
            }
        }
    }
    if (esp_sr_model) *esp_sr_model = "privetesp";  // запасной вариант
    return NOCTIS_ENGINE_ESP_SR;
}

bool noctis_parse_wake_model(const char* json, size_t len, char sha256_hex[65], int* size) {
    cJSON* root = cJSON_ParseWithLength(json, len);
    if (root == NULL) return false;
    bool ok = false;
    const cJSON* wm = cJSON_GetObjectItemCaseSensitive(root, "wake_model");
    if (cJSON_IsObject(wm)) {
        const char* sha = str_field(wm, "sha256");
        uint8_t tmp[32];
        const cJSON* sz = cJSON_GetObjectItemCaseSensitive(wm, "size");
        if (sha && noctis_sha256_from_hex(sha, tmp) && cJSON_IsNumber(sz) && sz->valuedouble > 0 &&
            sz->valuedouble <= NWWM_MAX_TOTAL) {
            noctis_copy(sha256_hex, 65, sha);
            for (char* p = sha256_hex; *p; p++) *p = (char)tolower((unsigned char)*p);
            if (size) *size = (int)sz->valuedouble;
            ok = true;
        }
    }
    cJSON_Delete(root);
    return ok;
}

bool noctis_wake_model_needed(const char* wake_word, const char* installed_hex, const char* server_hex) {
    if (noctis_wake_engine(wake_word, NULL) != NOCTIS_ENGINE_MWW) return false;
    if (server_hex == NULL || strlen(server_hex) != 64) return false;
    if (installed_hex == NULL || strlen(installed_hex) != 64) return true;
    for (int i = 0; i < 64; i++)
        if (tolower((unsigned char)installed_hex[i]) != tolower((unsigned char)server_hex[i])) return true;
    return false;
}

int noctis_build_wakeword_url(const noctis_link_t* link, const char* sha256_hex, char* out, size_t cap) {
    uint8_t tmp[32];
    if (!noctis_sha256_from_hex(sha256_hex, tmp)) return -1;  // только корректный хэш попадает в URL
    int n = snprintf(out, cap, "%s://%s/api/v1/device/wakeword/%s", link->tls ? "https" : "http", link->server,
                     sha256_hex);
    return (n < 0 || (size_t)n >= cap) ? -1 : n;
}

static uint32_t rd32(const uint8_t* p) { return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static size_t align16(size_t n) { return (n + 15) & ~(size_t)15; }

size_t nww_container_total_size(const uint8_t* blob, size_t avail) {
    if (blob == NULL || avail < NWWM_HEADER_SIZE || memcmp(blob, "NWWM", 4) != 0) return 0;
    size_t mlen = rd32(blob + 8), tlen = rd32(blob + 12);
    if (mlen == 0 || tlen < 16 || mlen > NWWM_MAX_TOTAL || tlen > NWWM_MAX_TOTAL) return 0;
    size_t total = align16(NWWM_HEADER_SIZE + mlen) + tlen;
    return (total > NWWM_MAX_TOTAL || total > avail) ? 0 : total;
}

bool nww_container_parse(const uint8_t* blob, size_t avail, nww_container_t* out, const char** err) {
    const char* e = NULL;
    memset(out, 0, sizeof(*out));
    if (blob == NULL || avail < NWWM_HEADER_SIZE) {
        e = "too small";
    } else if (memcmp(blob, "NWWM", 4) != 0 || blob[4] != 1 || blob[5] != 0) {
        e = "bad magic or version";
    } else {
        size_t mlen = rd32(blob + 8), tlen = rd32(blob + 12);
        size_t total = nww_container_total_size(blob, avail);
        if (total == 0) {
            e = "bad lengths";
        } else {
            size_t model_off = align16(NWWM_HEADER_SIZE + mlen);
            // хэш — по непрерывной области после заголовка (без копирования в RAM)
            uint8_t digest[32];
            noctis_sha256(blob + NWWM_HEADER_SIZE, total - NWWM_HEADER_SIZE, digest);
            {
                if (memcmp(digest, blob + 16, 32) != 0) {
                    e = "checksum mismatch";
                } else if (memcmp(blob + model_off + 4, "TFL3", 4) != 0) {
                    e = "not a TFLite model";
                } else {
                    cJSON* root = cJSON_ParseWithLength((const char*)blob + NWWM_HEADER_SIZE, mlen);
                    const cJSON* step = root ? cJSON_GetObjectItemCaseSensitive(root, "feature_step_size") : NULL;
                    const cJSON* cut = root ? cJSON_GetObjectItemCaseSensitive(root, "probability_cutoff") : NULL;
                    const cJSON* win = root ? cJSON_GetObjectItemCaseSensitive(root, "sliding_window_size") : NULL;
                    const cJSON* arena = root ? cJSON_GetObjectItemCaseSensitive(root, "tensor_arena_size") : NULL;
                    const char* ww = root ? str_field(root, "wake_word") : NULL;
                    if (!root || !ww || !cJSON_IsNumber(step) || !cJSON_IsNumber(cut) || !cJSON_IsNumber(win) ||
                        !cJSON_IsNumber(arena)) {
                        e = "bad manifest";
                    } else if (step->valuedouble != 10 || cut->valuedouble <= 0 || cut->valuedouble > 1 ||
                               win->valuedouble < 1 || win->valuedouble > 20 || arena->valuedouble < 1024 ||
                               arena->valuedouble > 512 * 1024) {
                        e = "bad detection parameters";
                    } else {
                        noctis_copy(out->manifest.wake_word, sizeof(out->manifest.wake_word), ww);
                        const char* label = str_field(root, "label");
                        noctis_copy(out->manifest.label, sizeof(out->manifest.label), label ? label : ww);
                        out->manifest.feature_step_size = 10;
                        out->manifest.probability_cutoff = (float)cut->valuedouble;
                        out->manifest.sliding_window_size = (int)win->valuedouble;
                        out->manifest.tensor_arena_size = (int)arena->valuedouble;
                        out->model = blob + model_off;
                        out->model_len = tlen;
                        out->total_len = total;
                        memcpy(out->sha256, digest, 32);
                    }
                    cJSON_Delete(root);
                }
            }
        }
    }
    if (err) *err = e;
    return e == NULL;
}

void nww_window_init(nww_window_t* w, int size, float cutoff) {
    memset(w, 0, sizeof(*w));
    w->size = size < 1 ? 1 : (size > NWW_WINDOW_MAX ? NWW_WINDOW_MAX : size);
    w->cutoff = cutoff;
}

void nww_window_reset(nww_window_t* w, int ignore) {
    memset(w->buf, 0, sizeof(w->buf));
    w->pos = 0;
    w->ignore = ignore < 0 ? 0 : ignore;
}

bool nww_window_push(nww_window_t* w, float probability) {
    w->buf[w->pos] = probability;
    w->pos = (w->pos + 1) % w->size;
    if (w->ignore > 0) {
        w->ignore--;
        return false;
    }
    float sum = 0;
    for (int i = 0; i < w->size; i++) sum += w->buf[i];
    return sum > w->cutoff * (float)w->size;
}

/* ============ Смена сети Wi-Fi и связи без сброса платы ============ */

int noctis_build_wifi_result(char* out, size_t cap, const char* request_id, bool ok, const char* error) {
    cJSON* o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", "wifi_result");
    cJSON_AddStringToObject(o, "request_id", request_id ? request_id : "");
    cJSON_AddStringToObject(o, "state", ok ? "ok" : "failed");
    if (error && error[0]) cJSON_AddStringToObject(o, "error", error);
    return print_json(o, out, cap);
}

void noctis_wifi_switch_begin(noctis_wifi_switch_t* sw, const char* request_id, int64_t now_ms, int64_t timeout_ms) {
    memset(sw, 0, sizeof(*sw));
    sw->trying = true;
    sw->started_ms = now_ms;
    sw->timeout_ms = timeout_ms;
    noctis_copy(sw->request_id, sizeof(sw->request_id), request_id ? request_id : "");
}

noctis_wifi_decision_t noctis_wifi_switch_poll(noctis_wifi_switch_t* sw, int64_t now_ms, bool server_ok) {
    if (!sw->trying) return NOCTIS_WIFI_NONE;
    if (server_ok) {
        sw->trying = false;
        sw->report_pending = true;
        sw->report_ok = true;
        return NOCTIS_WIFI_COMMIT;
    }
    if (now_ms - sw->started_ms >= sw->timeout_ms) {
        sw->trying = false;
        sw->report_pending = true;
        sw->report_ok = false;
        return NOCTIS_WIFI_ROLLBACK;
    }
    return NOCTIS_WIFI_NONE;
}

bool noctis_wifi_switch_take_report(noctis_wifi_switch_t* sw, bool server_ok, bool* ok, char request_id[NOCTIS_REQID_MAX]) {
    if (!sw->report_pending || !server_ok) return false;
    sw->report_pending = false;
    if (ok) *ok = sw->report_ok;
    if (request_id) noctis_copy(request_id, NOCTIS_REQID_MAX, sw->request_id);
    return true;
}

void noctis_offline_init(noctis_offline_t* t) { memset(t, 0, sizeof(*t)); }

bool noctis_offline_poll(noctis_offline_t* t, int64_t now_ms, bool server_ok, int64_t threshold_ms) {
    if (server_ok) {
        t->offline = false;
        t->fired = false;
        return false;
    }
    if (!t->offline) {
        t->offline = true;
        t->fired = false;
        t->since_ms = now_ms;
        return false;
    }
    if (!t->fired && now_ms - t->since_ms >= threshold_ms) {
        t->fired = true;
        return true;
    }
    return false;
}
