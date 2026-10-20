// Детектор microWakeWord: микро-фронтенд TFLM + потоковая модель TFLite Micro.
// Параметры фронтенда — те же, что при обучении (microwakeword / pymicro-features).
#include <math.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "frontend.h"
#include "frontend_util.h"
#include "nww.h"
#include "nww_frontend_config.h"
#include "tensorflow/lite/micro/micro_allocator.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/micro_resource_variable.h"
#include "tensorflow/lite/schema/schema_generated.h"

static const char* TAG = "nww";

namespace {

constexpr int kFeatureSize = NWW_FEATURE_SIZE;  // mel-каналов на срез
constexpr size_t kVarArenaBytes = 4096;   // память переменных-состояний потоковой модели
constexpr int kIgnoreAfterReset = 10;  // ≈0,3 с (запуск модели каждые 30 мс)
constexpr float kFeatureScale = 0.0390625f;  // 1/25.6: перевод uint16 фронтенда в float признака (как в обучении)

// Операции потокового MixedNet (набор определён по модели microWakeWord; проверяется тестом на реальной модели)
using OpResolver = tflite::MicroMutableOpResolver<20>;

bool register_ops(OpResolver& r) {
    return r.AddCallOnce() == kTfLiteOk && r.AddVarHandle() == kTfLiteOk && r.AddReshape() == kTfLiteOk &&
           r.AddReadVariable() == kTfLiteOk && r.AddStridedSlice() == kTfLiteOk &&
           r.AddConcatenation() == kTfLiteOk && r.AddAssignVariable() == kTfLiteOk && r.AddConv2D() == kTfLiteOk &&
           r.AddMul() == kTfLiteOk && r.AddAdd() == kTfLiteOk && r.AddMean() == kTfLiteOk &&
           r.AddFullyConnected() == kTfLiteOk && r.AddLogistic() == kTfLiteOk && r.AddQuantize() == kTfLiteOk &&
           r.AddDepthwiseConv2D() == kTfLiteOk && r.AddAveragePool2D() == kTfLiteOk &&
           r.AddMaxPool2D() == kTfLiteOk && r.AddPad() == kTfLiteOk && r.AddPack() == kTfLiteOk &&
           r.AddSplitV() == kTfLiteOk;
}

struct Detector {
    SemaphoreHandle_t lock = nullptr;
    FrontendState frontend{};
    bool frontend_ok = false;

    uint8_t* arena = nullptr;
    size_t arena_size = 0;
    uint8_t* var_arena = nullptr;
    tflite::MicroAllocator* allocator = nullptr;
    tflite::MicroResourceVariables* variables = nullptr;
    OpResolver* resolver = nullptr;
    tflite::MicroInterpreter* interpreter = nullptr;
    TfLiteTensor* input = nullptr;
    TfLiteTensor* output = nullptr;

    int slices_per_inference = 3;
    int slice_fill = 0;
    float in_scale = 1.0f;
    int in_zero = 0;

    nww_window_t window;  // скользящее среднее вероятностей (логика — в noctis_core, тестируется на ПК)
    float last_prob = 0.0f;
    bool detected = false;
};

Detector* g = nullptr;

uint8_t* alloc_arena(size_t bytes) {
    // Быстрее во внутренней RAM; если не хватает — PSRAM
    uint8_t* p = (uint8_t*)heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    return p ? p : (uint8_t*)heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}

void destroy(Detector* d) {
    if (d == nullptr) return;
    delete d->interpreter;
    delete d->resolver;
    if (d->frontend_ok) FrontendFreeStateContents(&d->frontend);
    heap_caps_free(d->arena);
    heap_caps_free(d->var_arena);
    if (d->lock) vSemaphoreDelete(d->lock);
    delete d;
}

bool setup_frontend(Detector* d) {
    FrontendConfig cfg;
    nww_frontend_fill_config(&cfg);  // общий с хост-тестом паритета и с обучением
    if (!FrontendPopulateState(&cfg, &d->frontend, 16000)) return false;
    d->frontend_ok = true;
    return true;
}

bool setup_model(Detector* d, const nww_container_t* c) {
    const tflite::Model* model = tflite::GetModel(c->model);
    if (model->version() != TFLITE_SCHEMA_VERSION) {
        ESP_LOGE(TAG, "Версия схемы модели %u не поддерживается", (unsigned)model->version());
        return false;
    }
    d->var_arena = alloc_arena(kVarArenaBytes);
    if (d->var_arena == nullptr) return false;
    d->allocator = tflite::MicroAllocator::Create(d->var_arena, kVarArenaBytes);
    d->variables = tflite::MicroResourceVariables::Create(d->allocator, 20);
    d->resolver = new OpResolver();
    if (!register_ops(*d->resolver)) {
        ESP_LOGE(TAG, "Не удалось зарегистрировать операции TFLM");
        return false;
    }

    // Размер арены в манифесте — минимум; если не хватает, увеличиваем (до 192 КБ)
    size_t size = (size_t)c->manifest.tensor_arena_size;
    for (int attempt = 0; attempt < 4; attempt++, size *= 2) {
        d->arena = alloc_arena(size);
        if (d->arena == nullptr) return false;
        d->arena_size = size;
        d->interpreter = new tflite::MicroInterpreter(model, *d->resolver, d->arena, size, d->variables);
        if (d->interpreter->AllocateTensors() == kTfLiteOk) break;
        delete d->interpreter;
        d->interpreter = nullptr;
        heap_caps_free(d->arena);
        d->arena = nullptr;
    }
    if (d->interpreter == nullptr) {
        ESP_LOGE(TAG, "AllocateTensors не удался даже с ареной %u байт", (unsigned)size);
        return false;
    }
    d->input = d->interpreter->input(0);
    d->output = d->interpreter->output(0);
    if (d->input->type != kTfLiteInt8 || d->input->dims->size != 3 || d->input->dims->data[2] != kFeatureSize) {
        ESP_LOGE(TAG, "Неожиданная форма/тип входа модели (нужен int8 [1,N,%d])", kFeatureSize);
        return false;
    }
    d->slices_per_inference = d->input->dims->data[1];
    d->in_scale = d->input->params.scale;
    d->in_zero = d->input->params.zero_point;
    ESP_LOGI(TAG, "Модель: вход int8 [1,%d,%d], scale %.5f zp %d; арена %u байт, использовано %u",
             d->slices_per_inference, kFeatureSize, d->in_scale, d->in_zero, (unsigned)d->arena_size,
             (unsigned)d->interpreter->arena_used_bytes());
    return true;
}

float read_probability(const TfLiteTensor* out) {
    switch (out->type) {
        case kTfLiteUInt8: return out->params.scale * ((int)out->data.uint8[0] - out->params.zero_point);
        case kTfLiteInt8: return out->params.scale * ((int)out->data.int8[0] - out->params.zero_point);
        case kTfLiteFloat32: return out->data.f[0];
        default: return 0.0f;
    }
}

// Один срез признаков (шаг 10 мс). Каждые slices_per_inference срезов — один запуск модели.
void on_slice(Detector* d, const uint16_t* values) {
    int8_t* dst = d->input->data.int8 + d->slice_fill * kFeatureSize;
    for (int i = 0; i < kFeatureSize; i++) {
        float f = values[i] * kFeatureScale;
        int q = (int)lroundf(f / d->in_scale) + d->in_zero;
        dst[i] = (int8_t)(q < -128 ? -128 : (q > 127 ? 127 : q));
    }
    if (++d->slice_fill < d->slices_per_inference) return;
    d->slice_fill = 0;
    if (d->interpreter->Invoke() != kTfLiteOk) {
        ESP_LOGW(TAG, "Invoke не удался");
        return;
    }
    float p = read_probability(d->output);
    d->last_prob = p;
    if (nww_window_push(&d->window, p)) d->detected = true;
}

}  // namespace

extern "C" bool nww_start(const nww_container_t* c) {
    nww_stop();
    Detector* d = new Detector();
    d->lock = xSemaphoreCreateMutex();
    nww_window_init(&d->window, c->manifest.sliding_window_size, c->manifest.probability_cutoff);
    if (!setup_frontend(d) || !setup_model(d, c)) {
        destroy(d);
        return false;
    }
    g = d;
    ESP_LOGI(TAG, "Детектор «%s» запущен: порог %.2f, окно %d", c->manifest.label, d->window.cutoff, d->window.size);
    return true;
}

extern "C" bool nww_feed(const int16_t* pcm, size_t count) {
    Detector* d = g;
    if (d == nullptr) return false;
    xSemaphoreTake(d->lock, portMAX_DELAY);
    while (count > 0) {
        size_t read = 0;
        FrontendOutput out = FrontendProcessSamples(&d->frontend, pcm, count, &read);
        if (read == 0) break;  // защита от зацикливания
        pcm += read;
        count -= read;
        if (out.size == kFeatureSize) on_slice(d, out.values);
    }
    bool hit = d->detected;
    xSemaphoreGive(d->lock);
    return hit;
}

extern "C" void nww_reset(void) {
    Detector* d = g;
    if (d == nullptr) return;
    xSemaphoreTake(d->lock, portMAX_DELAY);
    nww_window_reset(&d->window, kIgnoreAfterReset);
    d->slice_fill = 0;
    d->detected = false;
    d->last_prob = 0.0f;
    if (d->variables) d->variables->ResetAll();  // обнулить состояние потоковой модели
    // Состояние фронтенда (оценка шума) намеренно сохраняется: его прогрев занял бы около секунды
    xSemaphoreGive(d->lock);
}

extern "C" float nww_last_probability(void) { return g ? g->last_prob : 0.0f; }

extern "C" void nww_stop(void) {
    Detector* d = g;
    g = nullptr;
    destroy(d);
}
