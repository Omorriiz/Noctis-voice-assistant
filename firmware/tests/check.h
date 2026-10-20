/* Общие макросы хост-тестов прошивки. Счётчики определены в test_core.c. */
#pragma once

#include <stdio.h>
#include <string.h>

extern int failed, total;

#define CHECK(cond)                                                      \
    do {                                                                 \
        total++;                                                         \
        if (!(cond)) {                                                   \
            failed++;                                                    \
            printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);     \
        }                                                                \
    } while (0)
#define STREQ(a, b) (strcmp((a), (b)) == 0)
