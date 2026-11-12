VALIDATOR_DIR="src/validators"
EXAMPLES_DIR="examples"
LIST="validators.list"
OUTPUT="src/validators_generated.h"

cat > "$OUTPUT" << 'EOF'
#ifndef VALIDATORS_GENERATED_H
#define VALIDATORS_GENERATED_H

EOF

# generate includes
while IFS=';' read -r name _ || [[ -n "$name" ]]; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    echo "#include \"validators/$name.h\"" >> "$OUTPUT"
done < "$LIST"

echo "" >> "$OUTPUT"

# NOTE: ask if we should embed the examples like im doing
# generate examples
while IFS=';' read -r name _ _ example || [[ -n "$name" ]]; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    xxd -i -n "${name}_example" "$EXAMPLES_DIR/$example" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
done < "$LIST"

cat >> "$OUTPUT" << 'EOF'
enum ValidatorType {
    VALIDATOR_TEXT,
    VALIDATOR_BINARY
};

struct Validator {
    const char *name;
    int (*validate)(char *, int);
    enum ValidatorType data_type;
    const unsigned char *example;
    size_t example_len;
};

static struct Validator validators[] = {
EOF

# Generate validator entries
while IFS=';' read -r name display_name type example || [[ -n "$name" ]]; do
    [[ -z "$name" || "$name" == \#* ]] && continue

    case "$type" in
        TEXT)   ctype="VALIDATOR_TEXT" ;;
        BINARY) ctype="VALIDATOR_BINARY" ;;
        *)
            echo "Unknown validator type: $type" >&2
            exit 1
            ;;
    esac

    echo "    {\"$display_name\", validate_$name, $ctype, ${name}_example, sizeof(${name}_example)}," >> "$OUTPUT"
done < "$LIST"

cat >> "$OUTPUT" << 'EOF'
};

#define VALIDATOR_COUNT (sizeof(validators) / sizeof(validators[0]))

#endif
EOF
