#include <stdio.h>
#include <stdlib.h>

#include "validators_generated.h"

#define KB 1000
#define MB (KB * 1000)
#define GB (MB * 1000)

#define MAX_READ (100 * MB)

int main(void)
{
    char *buffer = malloc(MAX_READ);
    if (!buffer) return 1;

    size_t bytes_read = fread(buffer, 1, MAX_READ, stdin);

    for (size_t i = 0; i < VALIDATOR_COUNT; i++)
    {
        struct Validator validator = validators[i];
        if (validator.validate(buffer, bytes_read)) {
            printf("this file is %s\n", validator.name);
        }
    }

    free(buffer);
    return 0;
}