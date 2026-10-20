idf_component_register(
    SRCS "main.c" "settings.c" "provisioning.c" "display.c" "control.c" "ota.c" "wakeword_sync.c" "clock.c" "schedule.c" "music.c" "netwatch.c"
    INCLUDE_DIRS "." "../include"
    REQUIRES esp_wifi esp_netif esp_event nvs_flash esp_timer esp_driver_i2s esp_driver_i2c esp_driver_gpio esp_lcd
             json mbedtls wifi_provisioning app_update esp_http_client esp-tls esp_system lwip driver noctis_core noctis_wakeword
)
