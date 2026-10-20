idf_component_register(
    SRCS "nww_detector.cpp" "nww_partition.c"
    INCLUDE_DIRS "include"
    REQUIRES noctis_core esp_partition mbedtls
    PRIV_REQUIRES esp_timer
)
