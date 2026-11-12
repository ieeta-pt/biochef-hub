#include "fasta.h"

int validate_multi_fasta(char *content, int size)
{
    if (!content || size == 0) return 0;

    int start = 0;

    while (start < size)
    {
        if (content[start] != '>') return 0;

        int end = start + 1;

        while (end < size && content[end] != '>')
            end++;

        if (!validate_fasta(content + start, end - start))
            return 0;

        start = end;
    }

    return 1;
}