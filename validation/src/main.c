#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "validators_generated.h"

#define KiB(n) ((n) << 10)
#define MiB(n) ((n) << 20)
#define GiB(n) ((n) << 30)

// NOTE: ask if we are supposed to test the entire file
#define MAX_READ GiB(1)

int validate() {
    char *buffer = malloc(MAX_READ);
    if (!buffer) return 1;

    // NOTE: ask about input vs file
    size_t bytes_read = fread(buffer, 1, MAX_READ, stdin);

    for (size_t i = 0; i < VALIDATOR_COUNT; i++) {
        struct Validator validator = validators[i];

        if (validator.validate(buffer, bytes_read)) {
            printf("%s\n", validator.name);
        }
    }

    free(buffer);

    return 0;
}

int main(int argc, char **argv)
{
    const char *command = (argc > 1) ? argv[1] : "validate";

    if (strcmp(command, "validate") == 0) {
        int validation_status = validate();

        if (validation_status != 0) {
            fprintf(stderr, "error while validating\n");
            return 1;
        }
    }
    else if (strcmp(command, "list") == 0) {
        for (size_t i = 0; i < VALIDATOR_COUNT; i++)
        {
            printf("%s\n", validators[i].name);
        }
    }
    else if (strcmp(command, "example") == 0) {
        if (argc < 3) {
            fprintf(stderr, "usage: %s example <format>\n", argv[0]);
            return 1;
        }

        int found = 0;
        for (size_t i = 0; i < VALIDATOR_COUNT; i++) {
            if (strcmp(argv[2], validators[i].name) == 0) {
                fwrite(validators[i].example, 1, validators[i].example_len, stdout);
                found = 1;
                break;
            }
        }

        if (!found) {
            fprintf(stderr, "unknown validator: %s\n", argv[2]);
            return 1;
        }
    }
    else if (strcmp(command, "type") == 0) {
        if (argc < 3) {
            fprintf(stderr, "usage: %s type <format>\n", argv[0]);
            return 1;
        }

        int found = 0;
        for (size_t i = 0; i < VALIDATOR_COUNT; i++) {
            if (strcmp(argv[2], validators[i].name) == 0) {
                if (validators[i].data_type == VALIDATOR_TEXT) {
                    printf("TEXT\n");
                }
                else if (validators[i].data_type == VALIDATOR_BINARY) {
                    printf("BINARY\n");
                }
                found = 1;
                break;
            }
        }

        if (!found) {
            fprintf(stderr, "unknown validator: %s\n", argv[2]);
            return 1;
        }
    }

    return 0;
}