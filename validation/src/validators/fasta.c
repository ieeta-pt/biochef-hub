int _is_valid_fasta_sequence_char(char c)
{
    return (c >= 'A' && c <= 'Z') ||
           (c >= 'a' && c <= 'z') ||
           c == '*' || c == '.' || c == '-';
}

int validate_fasta(char *content, int size)
{
    if (!content || size == 0) return 0;

    int index = 0;
    if (content[index++] != '>') return 0;

    int header_length = 0;
    while (index < size && content[index] != '\n')
    {
        header_length++;
        index++;
    }
    if (header_length == 0) return 0;

    if (index < size && content[index] == '\n') index++;

    while (index < size)
    {
        char c = content[index++];
        if (c == '\n') continue;

        if (c == '>') return 0; // fasta != multi-fasta
        if (!_is_valid_fasta_sequence_char(c)) return 0;
    }

    return 1;
}