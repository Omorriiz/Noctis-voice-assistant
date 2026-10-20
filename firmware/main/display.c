#include "display.h"

#include <stdio.h>
#include <string.h>

#include "config.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_lcd_io_i2c.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_ssd1306.h"
#include "font5x7.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "noctis_core.h"

#ifndef OLED_I2C_SDA
#define OLED_I2C_SDA 8
#endif
#ifndef OLED_I2C_SCL
#define OLED_I2C_SCL 9
#endif
#ifndef OLED_I2C_ADDR
#define OLED_I2C_ADDR 0x3C
#endif

#define W 128
#define H 64
#define PAGES (H / 8)
#define COLS_PER_LINE 21  // 6 пикселей на символ

static const char* TAG = "display";
static esp_lcd_panel_handle_t s_panel = NULL;
static SemaphoreHandle_t s_lock = NULL;
static int64_t s_hold_until_us = 0;  // пока не истекло, главный экран (display_home) не затирает сообщение
static uint8_t s_fb[W * PAGES];  // формат SSD1306: страница = 8 строк пикселей, байт = столбец

static void fb_clear(void) { memset(s_fb, 0, sizeof(s_fb)); }

/** Рисует строку ASCII (кириллица не поддерживается шрифтом: заменяется на '?'). scale = 1 или 2. */
static void fb_text(int x, int row, const char* text, int scale) {
    int px = x;
    for (const unsigned char* p = (const unsigned char*)text; *p; p++) {
        unsigned char c = *p >= 0x80 ? '?' : *p;
        const unsigned char* glyph = &kFont5x7[c * 5];
        for (int col = 0; col < 6; col++) {
            unsigned char bits = col < 5 ? glyph[col] : 0;
            for (int sx = 0; sx < scale; sx++) {
                int fx = px + col * scale + sx;
                if (fx >= W) return;
                for (int bit = 0; bit < 7; bit++) {
                    if (!(bits & (1 << bit))) continue;
                    for (int sy = 0; sy < scale; sy++) {
                        int y = row * 8 + bit * scale + sy;
                        if (y < H) s_fb[(y / 8) * W + fx] |= (uint8_t)(1 << (y % 8));
                    }
                }
            }
        }
        px += 6 * scale;
    }
}

static void fb_flush(void) {
    if (s_panel) esp_lcd_panel_draw_bitmap(s_panel, 0, 0, W, H, s_fb);
}

bool display_init(void) {
    s_lock = xSemaphoreCreateMutex();
    i2c_master_bus_config_t bus_cfg = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .i2c_port = I2C_NUM_0,
        .sda_io_num = OLED_I2C_SDA,
        .scl_io_num = OLED_I2C_SCL,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    if (i2c_new_master_bus(&bus_cfg, &bus) != ESP_OK) {
        ESP_LOGW(TAG, "I2C недоступен — работаем без экрана");
        return false;
    }
    // Неплотный контакт на макетной плате: экран иногда отвечает не сразу — несколько попыток
    esp_err_t probe = ESP_FAIL;
    for (int i = 0; i < 6 && probe != ESP_OK; i++) {
        probe = i2c_master_probe(bus, OLED_I2C_ADDR, 200);
        if (probe != ESP_OK) vTaskDelay(pdMS_TO_TICKS(150));
    }
    if (probe != ESP_OK) {
        ESP_LOGW(TAG, "Экран SSD1306 (0x%02X) не найден — работаем без экрана", OLED_I2C_ADDR);
        i2c_del_master_bus(bus);
        return false;
    }
    esp_lcd_panel_io_handle_t io;
    esp_lcd_panel_io_i2c_config_t io_cfg = {
        .dev_addr = OLED_I2C_ADDR,
        .scl_speed_hz = 400000,
        .control_phase_bytes = 1,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .dc_bit_offset = 6,
    };
    esp_lcd_panel_dev_config_t panel_cfg = {.bits_per_pixel = 1, .reset_gpio_num = -1};
    esp_lcd_panel_ssd1306_config_t ssd_cfg = {.height = H};
    panel_cfg.vendor_config = &ssd_cfg;
    if (esp_lcd_new_panel_io_i2c(bus, &io_cfg, &io) != ESP_OK ||
        esp_lcd_new_panel_ssd1306(io, &panel_cfg, &s_panel) != ESP_OK) {
        ESP_LOGW(TAG, "Не удалось инициализировать SSD1306");
        s_panel = NULL;
        return false;
    }
    esp_lcd_panel_reset(s_panel);
    esp_lcd_panel_init(s_panel);
    esp_lcd_panel_disp_on_off(s_panel, true);
    display_status("NOCTIS", "starting...", "fw " NOCTIS_FIRMWARE_VERSION);
    ESP_LOGI(TAG, "SSD1306: SDA=%d SCL=%d", OLED_I2C_SDA, OLED_I2C_SCL);
    return true;
}

static void hold_for_ms(int ms) { s_hold_until_us = esp_timer_get_time() + (int64_t)ms * 1000; }

static void clip(char* dst, const char* src) {
    snprintf(dst, COLS_PER_LINE + 1, "%s", src ? src : "");
}

void display_status(const char* title, const char* line1, const char* line2) {
    if (!s_panel) return;
    char a[COLS_PER_LINE + 1], b[COLS_PER_LINE + 1];
    xSemaphoreTake(s_lock, portMAX_DELAY);
    hold_for_ms(8000);
    fb_clear();
    fb_text(0, 0, title, 2);
    clip(a, line1);
    clip(b, line2);
    fb_text(0, 3, a, 1);
    fb_text(0, 4, b, 1);
    fb_flush();
    xSemaphoreGive(s_lock);
}

void display_pairing(const char* ble_name, const char* pop) {
    if (!s_panel) return;
    char a[COLS_PER_LINE + 1], b[COLS_PER_LINE + 1];
    xSemaphoreTake(s_lock, portMAX_DELAY);
    hold_for_ms(15 * 60 * 1000);
    fb_clear();
    fb_text(0, 0, "PAIRING", 2);
    clip(a, ble_name);
    snprintf(b, sizeof(b), "CODE %s", pop);
    fb_text(0, 3, a, 1);
    fb_text(0, 4, b, 1);
    fb_text(0, 6, "Open Noctis app", 1);
    fb_flush();
    xSemaphoreGive(s_lock);
}

void display_error(const char* title, const char* detail) { display_status(title, detail, ""); }

void display_idle(const char* wake_word, const char* ip, const char* fw) {
    if (!s_panel) return;
    char a[COLS_PER_LINE + 1], b[COLS_PER_LINE + 1], c[COLS_PER_LINE + 1];
    xSemaphoreTake(s_lock, portMAX_DELAY);
    hold_for_ms(5000);
    fb_clear();
    fb_text(0, 0, "NOCTIS", 2);
    snprintf(a, sizeof(a), "Say: %s", wake_word);
    clip(b, ip);
    snprintf(c, sizeof(c), "fw %s", fw);
    fb_text(0, 3, a, 1);
    fb_text(0, 5, b, 1);
    fb_text(0, 6, c, 1);
    fb_flush();
    xSemaphoreGive(s_lock);
}

void display_home(const char* hhmm, const char* line1, const char* line2, const char* line3) {
    if (!s_panel) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (esp_timer_get_time() < s_hold_until_us) {   // показывается сообщение (ошибка, OTA, сопряжение, звонок)
        xSemaphoreGive(s_lock);
        return;
    }
    char a[COLS_PER_LINE + 1], b[COLS_PER_LINE + 1], c[COLS_PER_LINE + 1];
    fb_clear();
    fb_text(19, 0, hhmm, 3);          // "HH:MM" крупно, 5 знаков по 18 px
    clip(a, line1);
    clip(b, line2);
    clip(c, line3);
    fb_text(0, 4, a, 1);
    fb_text(0, 5, b, 1);
    fb_text(0, 6, c, 1);
    fb_flush();
    xSemaphoreGive(s_lock);
}
