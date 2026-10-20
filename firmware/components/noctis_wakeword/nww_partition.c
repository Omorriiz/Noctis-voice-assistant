#include <string.h>

#include "esp_log.h"
#include "esp_partition.h"
#include "nww.h"

static const char* TAG = "nww_part";

static const esp_partition_t* find_part(void) {
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, (esp_partition_subtype_t)0x40, "wakeword");
}

bool nww_partition_load(nww_container_t* c, char installed_sha_hex[65]) {
    installed_sha_hex[0] = '\0';
    const esp_partition_t* part = find_part();
    if (part == NULL) {
        ESP_LOGE(TAG, "Раздел wakeword не найден (проверьте partitions.csv)");
        return false;
    }
    const void* ptr = NULL;
    esp_partition_mmap_handle_t handle;
    if (esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &ptr, &handle) != ESP_OK) {
        ESP_LOGE(TAG, "Не удалось отобразить раздел wakeword");
        return false;
    }
    const char* err = NULL;
    if (!nww_container_parse((const uint8_t*)ptr, part->size, c, &err)) {
        ESP_LOGW(TAG, "В разделе wakeword нет корректной модели (%s)", err ? err : "?");
        esp_partition_munmap(handle);
        return false;
    }
    uint8_t digest[32];
    noctis_sha256((const uint8_t*)ptr, c->total_len, digest);
    noctis_sha256_to_hex(digest, installed_sha_hex);
    ESP_LOGI(TAG, "Модель «%s»: %u байт, sha256 %.12s…", c->manifest.label, (unsigned)c->total_len, installed_sha_hex);
    return true;  // отображение остаётся активным: модель читается прямо из flash
}

bool nww_partition_write(const uint8_t* blob, size_t len) {
    const esp_partition_t* part = find_part();
    if (part == NULL || len == 0 || len > part->size) return false;
    size_t erase_len = (len + 4095) & ~(size_t)4095;
    if (esp_partition_erase_range(part, 0, erase_len) != ESP_OK) {
        ESP_LOGE(TAG, "Ошибка стирания раздела");
        return false;
    }
    if (esp_partition_write(part, 0, blob, len) != ESP_OK) {
        ESP_LOGE(TAG, "Ошибка записи раздела");
        return false;
    }
    // Проверка чтением: записанное должно совпасть с источником
    uint8_t buf[256];
    for (size_t off = 0; off < len; off += sizeof(buf)) {
        size_t n = len - off < sizeof(buf) ? len - off : sizeof(buf);
        if (esp_partition_read(part, off, buf, n) != ESP_OK || memcmp(buf, blob + off, n) != 0) {
            ESP_LOGE(TAG, "Проверка записи не прошла на смещении %u", (unsigned)off);
            return false;
        }
    }
    return true;
}
