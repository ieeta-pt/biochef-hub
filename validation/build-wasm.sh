bash generate_validators.sh
emcc -Wall -Wextra -Ivalidators -o validator.js src/main.c src/validators/*.c
