/* Хост-обёртка над микро-фронтендом прошивки (те же исходники и те же параметры, что на ESP32) — для теста паритета. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "nww_frontend_config.h"

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif

API void* nww_fe_create(void) {
    struct FrontendState* st = calloc(1, sizeof(*st));
    struct FrontendConfig cfg;
    nww_frontend_fill_config(&cfg);
    if (!FrontendPopulateState(&cfg, st, 16000)) return NULL;
    return st;
}

/* Возвращает число признаков (0 или 40), в *read — сколько отсчётов потреблено */
API int nww_fe_process(void* h, const int16_t* samples, size_t n, uint16_t* out40, size_t* read) {
    struct FrontendOutput o = FrontendProcessSamples((struct FrontendState*)h, samples, n, read);
    if (o.size == NWW_FEATURE_SIZE) memcpy(out40, o.values, NWW_FEATURE_SIZE * sizeof(uint16_t));
    return (int)o.size;
}

API void nww_fe_destroy(void* h) {
    FrontendFreeStateContents((struct FrontendState*)h);
    free(h);
}
