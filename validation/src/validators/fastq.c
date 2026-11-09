#include <stdio.h>

int _is_valid_fastq_sequence_char(char c)
{
    return (c >= 'A' && c <= 'Z') ||
           (c >= 'a' && c <= 'z') ||
           c == '*' || c == '.' || c == '-';
}

int _is_valid_fastq_quality_char(char c)
{
    return (c >= 0x21 && c <= 0x7e);
}

int validate_fastq(char *content, int size)
{
    if (!content || size == 0) return 0;
    int index = 0;

    int num_lines = 1;
    while (index++ < size)
    {
        // don't count a newline at the end as an extra line
        if(content[index] == '\n' && index + 1 != size) {
            num_lines++;
        }
    }

    // each sequence must be exactly 4 lines
    if (num_lines % 4 != 0) return 0;
    int num_sequences = num_lines / 4;

    index = 0;
    for(int current_sequence = 0; current_sequence < num_sequences; current_sequence++)
    {
        if (content[index++] != '@') return 0;

        // validate header
        int header_length = 0;
        while (index < size && content[index] != '\n')
        {
            index++;
            header_length++;
        }
        if (header_length == 0) return 0;
        if (content[index++] != '\n') return 0;

        // validate sequence
        int sequence_length = 0;
        while (index < size && content[index] != '\n')
        {
            char c = content[index++];
            if (!_is_valid_fastq_sequence_char(c)) return 0;
            sequence_length++;
        }
        if (sequence_length == 0) return 0;
        if (content[index++] != '\n') return 0;

        // validate + line
        if (content[index++] != '+') return 0;
        while (index < size && content[index] != '\n')
        {
            index++;
        }
        if (content[index++] != '\n') return 0;

        // validate quality
        int quality_length = 0;
        while (index < size && content[index] != '\n')
        {
            char c = content[index++];
            if (!_is_valid_fastq_quality_char(c)) return 0;
            quality_length++;
        }
        if (quality_length == 0) return 0;

        // sequences must be the same length
        if (quality_length != sequence_length) return 0;

        // skip the \n if there is still more sequences
        if (current_sequence + 1 != num_sequences && content[index++] != '\n') return 0;
    }

    return 1;
}