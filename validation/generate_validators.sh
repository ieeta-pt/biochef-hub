VALIDATOR_DIR="src/validators"
LIST="validators.list"
OUTPUT="src/validators_generated.h"

cat > "$OUTPUT" << 'EOF'
#ifndef VALIDATORS_GENERATED_H
#define VALIDATORS_GENERATED_H

EOF

# generate includes
while IFS= read -r name || [[ -n "$name" ]]; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    echo "#include \"validators/$name.h\"" >> "$OUTPUT"
done < "$LIST"

cat >> "$OUTPUT" << 'EOF'
struct Validator {
    const char *name;
    int (*validate)(char *, int);
};

EOF

cat >> "$OUTPUT" << 'EOF'
static struct Validator validators[] = {
EOF

# generate validator entries
while IFS= read -r name || [[ -n "$name" ]]; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    echo "    {\"$name\", validate_$name}," >> "$OUTPUT"
done < "$LIST"

cat >> "$OUTPUT" << 'EOF'
};
EOF

cat >> "$OUTPUT" << 'EOF'

#define VALIDATOR_COUNT (sizeof(validators) / sizeof(validators[0]))

#endif
EOF