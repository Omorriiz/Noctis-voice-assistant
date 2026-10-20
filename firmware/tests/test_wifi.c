/* Хост-тесты: смена сети Wi-Fi с откатом, повторная настройка связи по Bluetooth, слежение за потерей связи. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "check.h"
#include "noctis_core.h"

static noctis_cmd_t parse_cmd(const char* json, noctis_command_t* cmd) {
    noctis_settings_t s;
    noctis_settings_defaults(&s);
    noctis_msg_kind_t k = noctis_parse_server_message(json, strlen(json), &s, cmd);
    return k == NOCTIS_MSG_COMMAND ? cmd->cmd : NOCTIS_CMD_NONE;
}

static void test_set_wifi_command(void) {
    noctis_command_t c;
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"Omorriz\",\"password\":\"12345678\",\"request_id\":\"ab12\"}", &c) ==
          NOCTIS_CMD_SET_WIFI);
    CHECK(STREQ(c.ssid, "Omorriz") && STREQ(c.password, "12345678") && STREQ(c.request_id, "ab12"));
    /* открытая сеть: пароль не нужен */
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"Cafe\"}", &c) == NOCTIS_CMD_SET_WIFI && c.password[0] == 0);
    /* кириллица в названии сети допустима */
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"\xD0\x94\xD0\xBE\xD0\xBC\",\"password\":\"password1\"}", &c) == NOCTIS_CMD_SET_WIFI);
    /* некорректное отбрасывается */
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\"}", &c) == NOCTIS_CMD_NONE);
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"\"}", &c) == NOCTIS_CMD_NONE);
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"123456789012345678901234567890123\"}", &c) == NOCTIS_CMD_NONE); /* 33 байта */
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"a\\nb\"}", &c) == NOCTIS_CMD_NONE);
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"Net\",\"password\":\"short\"}", &c) == NOCTIS_CMD_NONE);
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"Net\",\"password\":\"0123456789012345678901234567890123456789012345678901234567890123\"}", &c) ==
          NOCTIS_CMD_SET_WIFI);                                                                              /* 64 hex */
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"Net\",\"password\":\"zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz\"}", &c) ==
          NOCTIS_CMD_NONE);                                                                                  /* 64, но не hex */
    CHECK(parse_cmd("{\"type\":\"command\",\"name\":\"set_wifi\",\"ssid\":\"Net\",\"request_id\":\"a b\"}", &c) == NOCTIS_CMD_NONE);
}

static void test_wifi_result_builder(void) {
    char buf[200];
    CHECK(noctis_build_wifi_result(buf, sizeof(buf), "ab12", true, NULL) > 0);
    CHECK(strstr(buf, "\"type\":\"wifi_result\"") && strstr(buf, "\"request_id\":\"ab12\"") && strstr(buf, "\"state\":\"ok\""));
    CHECK(noctis_build_wifi_result(buf, sizeof(buf), "ab12", false, "timeout") > 0);
    CHECK(strstr(buf, "\"state\":\"failed\"") && strstr(buf, "\"error\":\"timeout\""));
    CHECK(strstr(buf, "password") == NULL);
}

static void test_switch_commit_when_server_reachable(void) {
    noctis_wifi_switch_t sw;
    bool ok = false;
    char rid[NOCTIS_REQID_MAX];
    noctis_wifi_switch_begin(&sw, "r1", 1000, 30000);
    CHECK(noctis_wifi_switch_poll(&sw, 2000, false) == NOCTIS_WIFI_NONE);   /* ещё подключаемся */
    CHECK(!noctis_wifi_switch_take_report(&sw, false, &ok, rid));
    CHECK(noctis_wifi_switch_poll(&sw, 9000, true) == NOCTIS_WIFI_COMMIT);  /* сервер на связи в новой сети */
    CHECK(noctis_wifi_switch_take_report(&sw, true, &ok, rid) && ok && STREQ(rid, "r1"));
    CHECK(!noctis_wifi_switch_take_report(&sw, true, &ok, rid));            /* итог отдаётся один раз */
    CHECK(noctis_wifi_switch_poll(&sw, 99999, false) == NOCTIS_WIFI_NONE);  /* после решения — тишина */
}

static void test_switch_rolls_back_on_timeout(void) {
    noctis_wifi_switch_t sw;
    bool ok = true;
    char rid[NOCTIS_REQID_MAX];
    noctis_wifi_switch_begin(&sw, "r2", 0, 30000);
    CHECK(noctis_wifi_switch_poll(&sw, 29999, false) == NOCTIS_WIFI_NONE);
    CHECK(noctis_wifi_switch_poll(&sw, 30000, false) == NOCTIS_WIFI_ROLLBACK);
    /* итог можно отправить, только когда связь с сервером вернулась (уже в прежней сети) */
    CHECK(!noctis_wifi_switch_take_report(&sw, false, &ok, rid));
    CHECK(noctis_wifi_switch_take_report(&sw, true, &ok, rid) && !ok && STREQ(rid, "r2"));
}

static void test_link_update(void) {
    noctis_link_t link;
    const char* first = "{\"server\":\"192.168.0.136:8000\",\"tls\":false,\"device_id\":\"dev123\",\"token\":\"tok-ABC_9\"}";
    CHECK(noctis_parse_link(first, strlen(first), &link));
    /* новый адрес сервера, личность платы сохраняется */
    const char* upd = "{\"server\":\"10.176.189.233:8000\",\"tls\":false}";
    CHECK(noctis_parse_link_update(upd, strlen(upd), &link, true));
    CHECK(STREQ(link.server, "10.176.189.233:8000") && STREQ(link.device_id, "dev123") && STREQ(link.token, "tok-ABC_9") && !link.tls);
    /* переключение на TLS */
    const char* tls = "{\"server\":\"noctis.example.ru\",\"tls\":true}";
    CHECK(noctis_parse_link_update(tls, strlen(tls), &link, true) && link.tls && STREQ(link.token, "tok-ABC_9"));
    /* без существующей связи нужны device_id и token */
    noctis_link_t empty;
    memset(&empty, 0, sizeof(empty));
    CHECK(!noctis_parse_link_update(upd, strlen(upd), &empty, false));
    CHECK(noctis_parse_link_update(first, strlen(first), &empty, false) && STREQ(empty.device_id, "dev123"));
    /* некорректное не меняет связь */
    noctis_link_t before = link;
    const char* bad[] = {"{\"server\":\"h:1/evil?x=1\"}", "{\"server\":\"h\",\"device_id\":\"a&b\",\"token\":\"t\"}",
                         "{\"server\":\"h\",\"device_id\":\"a\"}", "{\"server\":\"h\",\"token\":\"t\"}", "{}", "not json"};
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) CHECK(!noctis_parse_link_update(bad[i], strlen(bad[i]), &link, true));
    CHECK(memcmp(&before, &link, sizeof(link)) == 0);
}

static void test_offline_tracker(void) {
    noctis_offline_t t;
    noctis_offline_init(&t);
    CHECK(!noctis_offline_poll(&t, 0, true, 180000));        /* на связи */
    CHECK(!noctis_offline_poll(&t, 1000, false, 180000));    /* связь пропала */
    CHECK(!noctis_offline_poll(&t, 100000, false, 180000));
    CHECK(noctis_offline_poll(&t, 181000, false, 180000));   /* 3 минуты без сервера: открыть Bluetooth-окно */
    CHECK(!noctis_offline_poll(&t, 200000, false, 180000));  /* один раз за период */
    CHECK(!noctis_offline_poll(&t, 210000, true, 180000));   /* связь вернулась */
    CHECK(!noctis_offline_poll(&t, 220000, false, 180000));  /* новый период начинается заново */
    CHECK(noctis_offline_poll(&t, 220000 + 180000, false, 180000));
}

void run_wifi_tests(void) {
    test_set_wifi_command();
    test_wifi_result_builder();
    test_switch_commit_when_server_reachable();
    test_switch_rolls_back_on_timeout();
    test_link_update();
    test_offline_tracker();
}
