/* Хост-тесты переносимой логики прошивки (noctis_core). Запуск: python tests/run_host_tests.py */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "check.h"
#include "noctis_core.h"

int failed = 0, total = 0;

static void test_defaults_match_spec(void) {
    noctis_settings_t s;
    noctis_settings_defaults(&s);
    CHECK(STREQ(s.wake_word, "nils"));
    CHECK(STREQ(s.voice_gender, "male"));
    CHECK(STREQ(s.character_id, "friendly"));
    CHECK(STREQ(s.language, "ru"));
    CHECK(s.volume == 70);
    CHECK(noctis_settings_sanitize(&s) == 0);
}

static void test_sanitize_fixes_garbage(void) {
    noctis_settings_t s;
    noctis_settings_defaults(&s);
    strcpy(s.voice_gender, "robot");
    strcpy(s.character_id, "evil");
    strcpy(s.language, "xx");
    s.wake_word[0] = '\0';
    s.volume = 250;
    CHECK(noctis_settings_sanitize(&s) == 5);
    CHECK(STREQ(s.voice_gender, "male") && STREQ(s.character_id, "friendly") && STREQ(s.language, "ru"));
    CHECK(STREQ(s.wake_word, "nils") && s.volume == 100);
    s.volume = -5;
    noctis_settings_sanitize(&s);
    CHECK(s.volume == 0);
}

static void test_catalogs(void) {
    const char* chars[] = {"friendly", "formal", "humorous", "sarcastic", "motivating",
                           "calm", "energetic", "wise", "caring", "neutral"};
    for (int i = 0; i < 10; i++) CHECK(noctis_is_valid_character(chars[i]));
    CHECK(!noctis_is_valid_character("") && !noctis_is_valid_character(NULL));
    const char* langs[] = {"ru", "en", "es", "de", "fr"};
    for (int i = 0; i < 5; i++) CHECK(noctis_is_valid_language(langs[i]));
    CHECK(!noctis_is_valid_language("it"));
    CHECK(noctis_is_valid_voice("male") && noctis_is_valid_voice("female") && !noctis_is_valid_voice("Male"));
}

static void test_wake_words(void) {
    const char* ok[] = {"nils", "privet_esp", "hi_esp", "hallo_esp", "hola_esp", "bonjour_esp", "alexa", "computer",
                        "jarvis", "custom:Ok", "custom:Hey Noctis", "custom:ÐÐ¾ÐºÑÐ¸Ñ",
                        "custom:Jean-Luc"};
    for (size_t i = 0; i < sizeof(ok) / sizeof(ok[0]); i++) CHECK(noctis_is_valid_wake_word(ok[i]));
    const char* bad[] = {"", "NILS", "siri", "custom:", "custom:a", "custom:ab1", "custom: ab", "custom:ab ",
                         "custom:-ab", "custom:a_b", "custom:a&b", "custom:abc;def", "custom:123", "Custom:abc"};
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) CHECK(!noctis_is_valid_wake_word(bad[i]));
    CHECK(!noctis_is_valid_wake_word(NULL));
    CHECK(noctis_is_valid_wake_word("custom:abcdefghijklmnopqrstuvwxyzabcdef"));   // 32
    CHECK(!noctis_is_valid_wake_word("custom:abcdefghijklmnopqrstuvwxyzabcdefg")); // 33
}

static void test_parse_config(void) {
    noctis_settings_t s;
    noctis_settings_defaults(&s);
    const char* j = "{\"type\":\"config\",\"wake_word\":\"computer\",\"voice_gender\":\"female\","
                    "\"character_id\":\"wise\",\"language\":\"de\",\"volume\":40,\"timezone\":\"Europe/Berlin\","
                    "\"revision\":7}";
    CHECK(noctis_parse_server_message(j, strlen(j), &s, NULL) == NOCTIS_MSG_CONFIG);
    CHECK(STREQ(s.wake_word, "computer") && STREQ(s.voice_gender, "female") && STREQ(s.character_id, "wise"));
    CHECK(STREQ(s.language, "de") && s.volume == 40 && s.revision == 7 && STREQ(s.timezone, "Europe/Berlin"));
}

static void test_parse_config_partial_and_invalid(void) {
    noctis_settings_t s;
    noctis_settings_defaults(&s);
    const char* j = "{\"type\":\"config\",\"voice_gender\":\"robot\",\"volume\":999,\"language\":\"fr\"}";
    CHECK(noctis_parse_server_message(j, strlen(j), &s, NULL) == NOCTIS_MSG_CONFIG);
    CHECK(STREQ(s.voice_gender, "male"));  // неверное значение отброшено
    CHECK(s.volume == 70);                 // вне диапазона отброшено
    CHECK(STREQ(s.language, "fr"));        // верное применено
    CHECK(STREQ(s.wake_word, "nils"));     // отсутствующее не тронуто
}

static void test_parse_rejects_garbage(void) {
    noctis_settings_t s;
    noctis_settings_defaults(&s);
    CHECK(noctis_parse_server_message("not json", 8, &s, NULL) == NOCTIS_MSG_OTHER);
    CHECK(noctis_parse_server_message("{}", 2, &s, NULL) == NOCTIS_MSG_OTHER);
    const char* j = "{\"type\":\"hello\"}";
    CHECK(noctis_parse_server_message(j, strlen(j), &s, NULL) == NOCTIS_MSG_OTHER);
    // слишком длинное слово пробуждения не должно переполнить буфер
    char big[300];
    strcpy(big, "{\"type\":\"config\",\"wake_word\":\"");
    memset(big + strlen(big), 'a', 100);
    strcpy(big + strlen(big), "\"}");
    CHECK(noctis_parse_server_message(big, strlen(big), &s, NULL) == NOCTIS_MSG_CONFIG);
    CHECK(STREQ(s.wake_word, "nils"));
}

static const char* SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

static void test_commands(void) {
    noctis_command_t c;
    const char* r = "{\"type\":\"command\",\"name\":\"reboot\"}";
    CHECK(noctis_parse_server_message(r, strlen(r), NULL, &c) == NOCTIS_MSG_COMMAND && c.cmd == NOCTIS_CMD_REBOOT);
    const char* f = "{\"type\":\"command\",\"name\":\"factory_reset\"}";
    CHECK(noctis_parse_server_message(f, strlen(f), NULL, &c) == NOCTIS_MSG_COMMAND && c.cmd == NOCTIS_CMD_FACTORY_RESET);

    char ota[600];
    snprintf(ota, sizeof ota,
             "{\"type\":\"command\",\"name\":\"ota\",\"version\":\"1.2.0\","
             "\"url\":\"file:///ignored\",\"sha256\":\"%s\",\"size\":1234}", SHA);
    CHECK(noctis_parse_server_message(ota, strlen(ota), NULL, &c) == NOCTIS_MSG_COMMAND);
    CHECK(c.cmd == NOCTIS_CMD_OTA && STREQ(c.version, "1.2.0") && c.size == 1234 && STREQ(c.sha256, SHA));

    // OTA без хэша / с плохим хэшем / с плохой версией не выполняется
    snprintf(ota, sizeof ota, "{\"type\":\"command\",\"name\":\"ota\",\"version\":\"1.2.0\"}");
    noctis_parse_server_message(ota, strlen(ota), NULL, &c);
    CHECK(c.cmd == NOCTIS_CMD_NONE);
    snprintf(ota, sizeof ota, "{\"type\":\"command\",\"name\":\"ota\",\"version\":\"1.2.0\",\"sha256\":\"zz\"}");
    noctis_parse_server_message(ota, strlen(ota), NULL, &c);
    CHECK(c.cmd == NOCTIS_CMD_NONE);
    snprintf(ota, sizeof ota, "{\"type\":\"command\",\"name\":\"ota\",\"version\":\"../x\",\"sha256\":\"%s\"}", SHA);
    noctis_parse_server_message(ota, strlen(ota), NULL, &c);
    CHECK(c.cmd == NOCTIS_CMD_NONE);
    const char* u = "{\"type\":\"command\",\"name\":\"format_disk\"}";
    noctis_parse_server_message(u, strlen(u), NULL, &c);
    CHECK(c.cmd == NOCTIS_CMD_NONE);
}

static void test_link(void) {
    noctis_link_t l;
    const char* ok = "{\"server\":\"192.168.1.5:8000\",\"device_id\":\"abc123XYZ\",\"token\":\"tok-en_1\",\"tls\":false}";
    CHECK(noctis_parse_link(ok, strlen(ok), &l));
    char uri[256];
    CHECK(noctis_build_ws_uri(&l, uri, sizeof uri) > 0);
    CHECK(STREQ(uri, "ws://192.168.1.5:8000/noctis/v1/"));
    CHECK(strstr(uri, "tok-en") == NULL);  // секрета нет в URL
    char hdr[160];
    CHECK(noctis_build_auth_headers(&l, hdr, sizeof hdr) > 0);
    CHECK(STREQ(hdr, "X-Device-Id: abc123XYZ\r\nX-Device-Token: tok-en_1\r\n"));
    CHECK(noctis_build_auth_headers(&l, hdr, 10) == -1);
    char ota[160];
    CHECK(noctis_build_ota_url(&l, "1.2.0", ota, sizeof ota) > 0);
    CHECK(STREQ(ota, "http://192.168.1.5:8000/api/v1/device/ota/1.2.0"));
    CHECK(noctis_build_ota_url(&l, "../../x", ota, sizeof ota) == -1);
    l.tls = true;
    noctis_build_ws_uri(&l, uri, sizeof uri);
    CHECK(strncmp(uri, "wss://", 6) == 0);
    CHECK(noctis_build_ws_uri(&l, uri, 20) == -1);  // не помещается
    // инъекция в URL недопустима
    const char* bad = "{\"server\":\"h:1/x?y=1\",\"device_id\":\"a\",\"token\":\"b\"}";
    CHECK(!noctis_parse_link(bad, strlen(bad), &l));
    const char* bad2 = "{\"server\":\"h\",\"device_id\":\"a&b\",\"token\":\"t\"}";
    CHECK(!noctis_parse_link(bad2, strlen(bad2), &l));
    const char* miss = "{\"server\":\"h\",\"device_id\":\"a\"}";
    CHECK(!noctis_parse_link(miss, strlen(miss), &l));
}

static void test_versions(void) {
    CHECK(noctis_version_compare("1.2.3", "1.2.3") == 0);
    CHECK(noctis_version_compare("1.2.3", "1.10.0") < 0);
    CHECK(noctis_version_compare("2.0.0", "1.99.99") > 0);
    CHECK(noctis_version_compare("1.2", "1.2.3") == -2);
    CHECK(noctis_version_compare("1.2.x", "1.2.3") == -2);
    CHECK(noctis_version_compare(NULL, "1.0.0") == -2);
}

static void test_sha_hex(void) {
    uint8_t b[32];
    char hex[65];
    CHECK(noctis_sha256_from_hex(SHA, b));
    CHECK(b[0] == 0xe3 && b[31] == 0x55);
    noctis_sha256_to_hex(b, hex);
    CHECK(STREQ(hex, SHA));
    CHECK(noctis_sha256_from_hex("E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855", b));
    CHECK(!noctis_sha256_from_hex("abc", b));
    CHECK(!noctis_sha256_from_hex("g3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", b));
}

static void test_builders(void) {
    char out[200];
    int n = noctis_build_device_info(out, sizeof out, 3, 123456, "poweron");
    CHECK(n > 0 && strstr(out, "\"type\":\"device_info\"") && strstr(out, "\"firmware\":\"" NOCTIS_FIRMWARE_VERSION "\""));
    CHECK(strstr(out, "\"config_revision\":3"));
    CHECK(noctis_build_device_info(out, 10, 1, 1, "x") == -1);
    n = noctis_build_ota_status(out, sizeof out, "1.2.0", "failed", "sha mismatch");
    CHECK(n > 0 && strstr(out, "\"state\":\"failed\"") && strstr(out, "sha mismatch"));
}


static void hex_of(const char* s, size_t len, char out[65]) {
    uint8_t d[32];
    noctis_sha256((const uint8_t*)s, len, d);
    noctis_sha256_to_hex(d, out);
}

static void test_sha256_vectors(void) {
    char hex[65];
    hex_of("", 0, hex);
    CHECK(STREQ(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    hex_of("abc", 3, hex);
    CHECK(STREQ(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    const char* m56 = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";  // 56 байт: паддинг в 2 блока
    hex_of(m56, strlen(m56), hex);
    CHECK(STREQ(hex, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"));
    char* million = malloc(1000000);
    memset(million, 'a', 1000000);
    hex_of(million, 1000000, hex);
    CHECK(STREQ(hex, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"));
    free(million);
    // границы блоков 55/56/63/64/65 байт не должны падать и должны давать разные хэши
    char prev[65] = "";
    for (int n = 55; n <= 65; n++) {
        char buf[80];
        memset(buf, 'x', sizeof buf);
        hex_of(buf, (size_t)n, hex);
        CHECK(!STREQ(hex, prev));
        strcpy(prev, hex);
    }
}

static void test_engine_mapping(void) {
    const char* m = NULL;
    CHECK(noctis_wake_engine("nils", &m) == NOCTIS_ENGINE_MWW && m == NULL);
    CHECK(noctis_wake_engine("custom:Ноктис", &m) == NOCTIS_ENGINE_MWW);
    CHECK(noctis_wake_engine("privet_esp", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "privetesp"));
    CHECK(noctis_wake_engine("hi_esp", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "hiesp"));
    CHECK(noctis_wake_engine("hallo_esp", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "halloesp"));
    CHECK(noctis_wake_engine("hola_esp", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "holaesp"));
    CHECK(noctis_wake_engine("bonjour_esp", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "bonjouresp"));
    CHECK(noctis_wake_engine("alexa", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "alexa"));
    CHECK(noctis_wake_engine("computer", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "computer"));
    CHECK(noctis_wake_engine("jarvis", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "jarvis"));
    CHECK(noctis_wake_engine("garbage", &m) == NOCTIS_ENGINE_ESP_SR && STREQ(m, "privetesp"));  // запасной
}

static const char* SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static const char* SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

static void test_wake_model_sync_decision(void) {
    char sha[65];
    int size = 0;
    const char* j = "{\"type\":\"config\",\"wake_model\":{\"sha256\":\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\",\"size\":4096}}";
    CHECK(noctis_parse_wake_model(j, strlen(j), sha, &size) && STREQ(sha, SHA_A) && size == 4096);  // приведено к нижнему
    const char* none = "{\"type\":\"config\",\"wake_model\":null}";
    CHECK(!noctis_parse_wake_model(none, strlen(none), sha, &size));
    const char* bad = "{\"wake_model\":{\"sha256\":\"zz\",\"size\":10}}";
    CHECK(!noctis_parse_wake_model(bad, strlen(bad), sha, &size));
    const char* huge = "{\"wake_model\":{\"sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"size\":99999999}}";
    CHECK(!noctis_parse_wake_model(huge, strlen(huge), sha, &size));

    CHECK(noctis_wake_model_needed("nils", "", SHA_A));          // ничего не установлено
    CHECK(noctis_wake_model_needed("nils", SHA_B, SHA_A));       // другая версия
    CHECK(!noctis_wake_model_needed("nils", SHA_A, SHA_A));      // уже установлена
    CHECK(!noctis_wake_model_needed("nils", SHA_A, NULL));       // сервер модели не прислал
    CHECK(!noctis_wake_model_needed("computer", "", SHA_A));     // встроенному слову модель не нужна
    CHECK(noctis_wake_model_needed("custom:Hey Noctis", SHA_B, SHA_A));

    noctis_link_t l;
    memset(&l, 0, sizeof l);
    strcpy(l.server, "h:8000");
    char url[200];
    CHECK(noctis_build_wakeword_url(&l, SHA_A, url, sizeof url) > 0);
    CHECK(STREQ(url, "http://h:8000/api/v1/device/wakeword/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    CHECK(noctis_build_wakeword_url(&l, "../../etc", url, sizeof url) == -1);
}

// Собирает контейнер NWWM так же, как server/noctis_server/nwwm.py
static size_t make_container(uint8_t* out, size_t cap, const char* manifest, const uint8_t* model, size_t mlen) {
    size_t jl = strlen(manifest);
    size_t off = (48 + jl + 15) & ~(size_t)15;
    size_t total = off + mlen;
    if (total > cap) return 0;
    memset(out, 0, total);
    memcpy(out, "NWWM", 4);
    out[4] = 1;
    out[8] = (uint8_t)jl;
    out[9] = (uint8_t)(jl >> 8);
    out[12] = (uint8_t)mlen;
    out[13] = (uint8_t)(mlen >> 8);
    out[14] = (uint8_t)(mlen >> 16);
    memcpy(out + 48, manifest, jl);
    memcpy(out + off, model, mlen);
    noctis_sha256(out + 48, total - 48, out + 16);
    return total;
}

static void test_container(void) {
    static uint8_t buf[4096];
    uint8_t model[200];
    memset(model, 7, sizeof model);
    memcpy(model + 4, "TFL3", 4);
    const char* mf = "{\"wake_word\":\"nils\",\"label\":\"Nils\",\"feature_step_size\":10,\"probability_cutoff\":0.97,"
                     "\"sliding_window_size\":5,\"tensor_arena_size\":26080}";
    size_t ctotal = make_container(buf, sizeof buf, mf, model, sizeof model);
    nww_container_t c;
    const char* err = NULL;
    CHECK(ctotal > 0 && nww_container_parse(buf, sizeof buf, &c, &err));
    CHECK(c.total_len == ctotal && c.model_len == sizeof model && c.model == buf + ((48 + strlen(mf) + 15) & ~(size_t)15));
    CHECK(STREQ(c.manifest.wake_word, "nils") && c.manifest.sliding_window_size == 5 && c.manifest.tensor_arena_size == 26080);
    CHECK(c.manifest.probability_cutoff > 0.969f && c.manifest.probability_cutoff < 0.971f);
    CHECK(nww_container_total_size(buf, sizeof buf) == ctotal);  // в разделе после контейнера может быть мусор

    buf[ctotal - 1] ^= 1;  // подмена байта модели
    CHECK(!nww_container_parse(buf, sizeof buf, &c, &err) && STREQ(err, "checksum mismatch"));
    buf[ctotal - 1] ^= 1;
    CHECK(!nww_container_parse(buf, ctotal - 1, &c, &err) && STREQ(err, "bad lengths"));  // обрезан
    CHECK(!nww_container_parse(buf, 10, &c, &err));
    buf[0] = 'X';
    CHECK(!nww_container_parse(buf, sizeof buf, &c, &err) && STREQ(err, "bad magic or version"));
    buf[0] = 'N';
    memset(buf, 0xFF, 64);  // «стёртый» flash
    CHECK(!nww_container_parse(buf, sizeof buf, &c, &err));
    CHECK(nww_container_total_size(buf, sizeof buf) == 0);

    // манифест с неподдерживаемым шагом признаков и модель без TFL3
    const char* bad = "{\"wake_word\":\"x\",\"feature_step_size\":20,\"probability_cutoff\":0.9,\"sliding_window_size\":5,\"tensor_arena_size\":26080}";
    make_container(buf, sizeof buf, bad, model, sizeof model);
    CHECK(!nww_container_parse(buf, sizeof buf, &c, &err) && STREQ(err, "bad detection parameters"));
    model[4] = 'X';
    make_container(buf, sizeof buf, mf, model, sizeof model);
    CHECK(!nww_container_parse(buf, sizeof buf, &c, &err) && STREQ(err, "not a TFLite model"));
}


static void test_detection_window(void) {
    nww_window_t w;
    nww_window_init(&w, 5, 0.97f);
    for (int i = 0; i < 4; i++) CHECK(!nww_window_push(&w, 1.0f));  // окно ещё не заполнено: среднее < порога
    CHECK(nww_window_push(&w, 1.0f));                               // 5 из 5 → срабатывание
    nww_window_init(&w, 5, 0.97f);
    for (int i = 0; i < 20; i++) CHECK(!nww_window_push(&w, 0.5f));  // стабильно ниже порога
    for (int i = 0; i < 5; i++) CHECK(!nww_window_push(&w, i == 2 ? 1.0f : 0.0f));  // одиночный всплеск не будит
    nww_window_init(&w, 3, 0.9f);
    CHECK(!nww_window_push(&w, 0.95f) && !nww_window_push(&w, 0.95f) && nww_window_push(&w, 0.95f));
    CHECK(!nww_window_push(&w, 0.2f));                               // упала — снова ниже порога

    // сброс: окно очищено, первые N оценок игнорируются
    nww_window_init(&w, 5, 0.97f);
    for (int i = 0; i < 5; i++) nww_window_push(&w, 1.0f);
    nww_window_reset(&w, 10);
    int fired = 0;
    for (int i = 0; i < 9; i++) fired += nww_window_push(&w, 1.0f);
    CHECK(fired == 0);                                               // 9 из 10 игнорируемых
    CHECK(nww_window_push(&w, 1.0f) == false);                       // 10-я ещё игнорируется
    CHECK(nww_window_push(&w, 1.0f));                                // дальше слово снова ловится
    // границы размера окна
    nww_window_init(&w, 0, 0.5f);
    CHECK(w.size == 1);
    nww_window_init(&w, 99, 0.5f);
    CHECK(w.size == NWW_WINDOW_MAX);
}

static void test_copy(void) {
    char d[4];
    CHECK(noctis_copy(d, sizeof d, "abc"));
    CHECK(!noctis_copy(d, sizeof d, "abcdef") && STREQ(d, "abc"));
    CHECK(noctis_copy(d, sizeof d, NULL) && d[0] == '\0');
}

void run_stage5_tests(void);
void run_wifi_tests(void);

int main(void) {
    test_defaults_match_spec();
    test_sanitize_fixes_garbage();
    test_catalogs();
    test_wake_words();
    test_parse_config();
    test_parse_config_partial_and_invalid();
    test_parse_rejects_garbage();
    test_commands();
    test_link();
    test_versions();
    test_sha_hex();
    test_builders();
    test_sha256_vectors();
    test_engine_mapping();
    test_wake_model_sync_decision();
    test_container();
    test_detection_window();
    test_copy();
    run_stage5_tests();
    run_wifi_tests();
    printf("%d checks, %d failed\n", total, failed);
    return failed ? 1 : 0;
}
