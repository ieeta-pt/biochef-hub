bash generate_validators.sh && emcc \
    -Wall -Wextra \
    -I src/validators \
    -o validator.js \
    src/main.c src/validators/*.c \
    -s MODULARIZE=1 \
    -s EXPORT_NAME="createValidator" \
    -s ENVIRONMENT=web \
    -s ALLOW_MEMORY_GROWTH=1 \
    -s INVOKE_RUN=0 \
    -s EXPORTED_FUNCTIONS='["_validate_data","_malloc","_free"]' \
    -s EXPORTED_RUNTIME_METHODS='["UTF8ToString","HEAPU8"]'