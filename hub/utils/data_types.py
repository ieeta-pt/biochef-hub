import re
import json
import zlib

def validate_fasta(content):
    lines = content.strip().split('\n') if content else []
    if not lines or not lines[0].startswith('>'):
        return False
    sequence = ''.join(lines[1:]).strip()
    return bool(sequence) and bool(re.fullmatch(r'[A-Z*.\-\s]+', sequence, re.I))

def validate_multi_fasta(content):
    if not content: return False
    
    headers = re.findall(r'^>', content, re.M)
    entries = [entry for entry in content.split('>') if entry.strip()]
    if len(headers) != len(entries):
        return False
    return all(validate_fasta('>' + entry.strip()) for entry in entries)

def validate_efa(content):
    if not content:
        return False

    sections = [section.strip() for section in re.split(r'(?=^<)', content, flags=re.M) if section.strip()]
    if not sections:
        return False

    for section in sections:
        lines = section.splitlines()
        if len(lines) < 3 or not lines[0].startswith('<'):
            return False
        if not validate_multi_fasta('\n'.join(lines[1:])):
            return False

    return True

def validate_fastq(content):
    lines = content.strip().split('\n')
    if len(lines) % 4 != 0:
        return False
    for i in range(0, len(lines), 4):
        header, sequence, plus_line, quality = lines[i:i+4]
        if not header.startswith('@') or not plus_line.startswith('+'):
            return False
        if not re.fullmatch(r'[A-Z\s]+', sequence, re.I):
            return False
        if not re.fullmatch(r'[\x21-\x7E]+', quality):
            return False
    return True

def validate_dna(content):
    return bool(re.fullmatch(r'[ACGTN]+', content.strip(), re.I))

def validate_rna(content):
    return bool(re.fullmatch(r'[ACGUN]+', content.strip(), re.I))

def validate_amino_acids(content):
    return bool(re.fullmatch(r'[ACDEFGHIKLMNPQRSTUVWY-]+', content.strip()))

def validate_num(content):
    lines = content.strip().split('\n')
    return all(re.fullmatch(r'[+-]?(\d+(\.\d*)?|\.\d+)', line.strip()) for line in lines)

def validate_bin(content):
    lines = content.strip().split('\n')
    return all(re.fullmatch(r'[01]+', line.strip()) for line in lines)

def validate_packaged_fastq(content):
    # TODO:
    return False

def validate_vcf(content):
    if not content:
        return False

    lines = content.strip().split('\n')
    header_found = False

    for line in lines:
        if line.startswith('##'):
            continue
        
        if line.startswith('#CHROM'):
            header_found = True
            if len(line.split()) < 8:
                return False
            continue
        
        if not header_found:
            return False

        fields = line.split()
        if len(fields) < 8:
            return False

        chrom, pos, _id, ref, alt, qual, flt, info = fields[:8]

        if not pos.isdigit():
            return False

        if not re.fullmatch(r'[ACGTN]+', ref, re.I):
            return False

        # Besides bases, VCF allows no ALT (.), a deletion placeholder (*),
        # symbolic alleles such as <*> or <DEL>, and breakends such as G]2:42].
        if not all(re.fullmatch(r'[ACGTN]+|[.*]|<[^<>]+>|[ACGTN.]*[\[\]].*', a, re.I) for a in alt.split(',')):
            return False

        if qual != '.' and not re.fullmatch(r'\d+(\.\d+)?([eE][-+]?\d+)?', qual):
            return False

    return header_found

def validate_sam(content):
    if not content:
        return False

    lines = content.strip().split('\n')
    for line in lines:
        if line.startswith('@'):  # header
            continue
        
        fields = line.split('\t')
        if len(fields) < 11:
            return False

        qname, flag, rname, pos = fields[0], fields[1], fields[2], fields[3]

        if not flag.isdigit():
            return False

        if not pos.isdigit():
            return False

    return True

def validate_bed(content):
    if not content:
        return False

    lines = content.strip().split('\n')
    for line in lines:
        if line.startswith('track') or line.startswith('browser'):
            continue
        
        fields = line.split()
        if len(fields) < 3:
            return False

        chrom, start, end = fields[:3]

        if not start.isdigit() or not end.isdigit():
            return False

    return True

def validate_gff(content):
    if not content:
        return False

    lines = content.strip().split('\n')
    for line in lines:
        if line.startswith('#'):
            continue
        
        fields = line.split('\t')
        if len(fields) != 9:
            return False

        seqid, source, type_, start, end, score, strand, phase, attributes = fields

        if not start.isdigit() or not end.isdigit():
            return False

        if strand not in ['+', '-', '.']:
            return False

        if phase not in ['0', '1', '2', '.']:
            return False

    return True

def validate_gtf(content):
    if not content:
        return False
    lines = content.strip().split('\n')
    for line in lines:
        if line.startswith('#') or not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) != 9:
            return False
        seqid, source, type_, start, end, score, strand, frame, attributes = fields
        if not start.isdigit() or not end.isdigit():
            return False
        if strand not in ['+', '-', '.']:
            return False
        if frame not in ['0', '1', '2', '.']:
            return False
        # GTF attribute column uses key "value"; quoted-value pairs separated by ';'
        if 'gene_id' not in attributes and 'transcript_id' not in attributes:
            return False
    return True

def validate_paf(content):
    if not content:
        return False
    lines = content.strip().split('\n')
    for line in lines:
        if not line.strip():
            continue
        fields = line.split('\t')
        # PAF requires at least 12 mandatory columns
        if len(fields) < 12:
            return False
        # Numeric fields: query_len, query_start, query_end, target_len,
        # target_start, target_end, matching_bases, alignment_block_len, mapq
        for idx in (1, 2, 3, 6, 7, 8, 9, 10, 11):
            if not fields[idx].lstrip('-').isdigit():
                return False
        if fields[4] not in ('+', '-'):
            return False
    return True

def validate_gfa(content):
    if not content:
        return False
    lines = content.strip().split('\n')
    has_segment = False
    for line in lines:
        if not line.strip():
            continue
        fields = line.split('\t')
        # Every record starts with a one-letter type. GFA1 uses H/S/L/C/P/W/J,
        # GFA2 adds E/F/G/O/U, and the spec tells readers to skip types they do
        # not know, which tools rely on: miniasm writes 'a' and 'x' records.
        if line[0] != '#' and (len(fields[0]) != 1 or not fields[0].isalpha()):
            return False
        if fields[0] == 'S':
            if len(fields) < 3:
                return False
            has_segment = True
    return has_segment

def validate_gvcf(content):
    # A gVCF is a VCF that also covers the reference stretches between its
    # variants, in blocks: a record whose ALT is only the "no variant here"
    # allele and whose END says where the block stops. GATK writes that allele
    # as <NON_REF>, DeepVariant as <*> and bcftools call -g as "."; GATK also
    # declares its block bands in ##GVCFBlock lines. A structural variant's
    # END (ALT <DEL> and the like) is not a block.
    if not validate_vcf(content):
        return False
    for line in content.strip().split('\n'):
        if line.startswith('##GVCFBlock'):
            return True
        if line.startswith('#'):
            continue
        fields = line.split('\t')
        if len(fields) >= 8 and fields[4] in ('.', '<*>', '<NON_REF>') \
                and re.search(r'(^|;)END=\d+(;|$)', fields[7]):
            return True
    return False

def validate_sample_list(content):
    # Sample names, one per line, as bcftools query -l writes them and
    # bcftools view -S, vt subset -s and the like read them. A name cannot
    # hold a tab, which separates the columns of a VCF, and a line opening
    # with '>', '@' or '#' is a FASTA, FASTQ or VCF header, not a name.
    if not content or not content.strip():
        return False
    return all(line.strip() and '\t' not in line and line[0] not in '>@#'
               for line in content.strip().split('\n'))

def validate_list(content):
    if not content.strip(): 
        return False 
    lines = content.splitlines()
    for line in lines:
        if not line.strip(): 
            continue
        seq_id = line.split('\t')[0]        
        if not seq_id or ' ' in seq_id:
            return False
    return True

def validate_clustal(content):
    if not content or not content.strip():
        return False
    lines = content.strip().split('\n')
    # Clustal, MAFFT --clustalout and MAFFT f2cl all open with a CLUSTAL banner
    if not lines[0].startswith('CLUSTAL'):
        return False
    has_sequence = False
    for line in lines[1:]:
        if not line.strip():
            continue
        if line[0].isspace():
            # conservation line under each block: '*', ':', '.' and spaces
            if line.strip(' *:.'):
                return False
            continue
        fields = line.split()
        if len(fields) not in (2, 3):
            return False
        if not re.fullmatch(r'[A-Za-z*.\-]+', fields[1]):
            return False
        if len(fields) == 3 and not fields[2].isdigit():
            return False
        has_sequence = True
    return has_sequence

def validate_snd(content):
    # bhtsne's SND table: optional '#' header, then one row per sample,
    # a name followed by tab-separated numbers, every row the same width
    if not content or not content.strip():
        return False
    width = None
    for line in content.strip().split('\n'):
        if not line.strip() or line.startswith('#'):
            continue
        fields = line.rstrip('\r').split('\t')
        if len(fields) < 2:
            return False
        if width is None:
            width = len(fields)
        elif len(fields) != width:
            return False
        for value in fields[1:]:
            if not re.fullmatch(r'[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?', value):
                return False
    return width is not None

def validate_json(content):
    if not content or not content.strip():
        return False

    try:
        json.loads(content)
    except json.JSONDecodeError:
        return False

    return True

def validate_fai(content):
    if not content or not content.strip():
        return False

    lines = content.strip().split('\n')
    for line in lines:
        parts = line.split('\t')
        if len(parts) < 5:
            return False
        seq_name, length, offset, line_bases, line_width = parts[:5]
        # Check numeric fields
        if not all(p.isdigit() for p in [length, offset, line_bases, line_width]):
            return False
        # Sequence name should be non-empty
        if not seq_name.strip():
            return False

    return True

def validate_newick(content):
    """Check ordinary Newick syntax, including multiple trees and comments."""

    if not isinstance(content, str):
        return False
    if content.startswith("\ufeff"):
        content = content[1:]
    if not content.strip(" \t\r\n"):
        return False

    tokens = re.compile(r"[ \t\r\n]+|'(?:[^'\r\n]|'')*'|[(),:;]|[^ \t\r\n(),:;\[\]']+")
    number = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
    pos = depth = trees = 0
    state, seen_node = "node", False
    while pos < len(content):
        if content[pos] == "[":
            comments = 1
            pos += 1
            while pos < len(content) and comments:
                comments += (content[pos] == "[") - (content[pos] == "]")
                pos += 1
            if comments:
                return False
            continue
        match = tokens.match(content, pos)
        if not match:
            return False
        token, pos = match.group(), match.end()
        if token[0] in " \t\r\n":
            continue
        if token == "(":
            if state != "node":
                return False
            depth += 1
            seen_node = True
        elif token == ")":
            if not depth or state == "length":
                return False
            depth -= 1
            state = "label"
        elif token == ",":
            if not depth or state == "length":
                return False
            state = "node"
        elif token == ":":
            if state not in ("node", "label", "suffix"):
                return False
            state = "length"
            seen_node = True
        elif token == ";":
            if depth or state == "length":
                return False
            trees += 1
            state, seen_node = "node", False
        elif state == "length":
            if not number.fullmatch(token):
                return False
            state = "end"
        elif state in ("node", "label") and token.replace("\t", "").isprintable():
            state, seen_node = "suffix", True
        else:
            return False
    return trees > 0 and depth == 0 and not seen_node

ALL_TYPES = [
    {'type': 'FASTA', 'validator': validate_fasta},
    {'type': 'Multi-FASTA', 'validator': validate_multi_fasta},
    {'type': 'EFA', 'validator': validate_efa},
    {'type': 'FASTQ', 'validator': validate_fastq},
    {'type': 'PackagedFASTQ', 'validator': validate_packaged_fastq},
    {'type': 'NUM', 'validator': validate_num},
    {'type': 'BIN', 'validator': validate_bin},
    {'type': 'DNA', 'validator': validate_dna},
    {'type': 'RNA', 'validator': validate_rna},
    {'type': 'AminoAcids', 'validator': validate_amino_acids},
    {'type': 'GVCF', 'validator': validate_gvcf},
    {'type': 'VCF', 'validator': validate_vcf},
    {'type': 'SAM', 'validator': validate_sam},
    {'type': 'BED', 'validator': validate_bed},
    {'type': 'LIST', 'validator': validate_list},
    # A list of names looks like much plain text, so it is only chosen where a
    # list of samples is expected
    {'type': 'SAMPLE_LIST', 'validator': validate_sample_list, 'only_when_expected': True},
    {'type': 'GFF', 'validator': validate_gff},
    {'type': 'GTF', 'validator': validate_gtf},
    {'type': 'PAF', 'validator': validate_paf},
    {'type': 'GFA', 'validator': validate_gfa},
    {'type': 'JSON', 'validator': validate_json},
    {'type': 'CLUSTAL', 'validator': validate_clustal},
    {'type': 'SND', 'validator': validate_snd},
    {'type': 'FAI', 'validator': validate_fai},
    {'type': 'NEWICK', 'validator': validate_newick},
    {'type': 'TEXT', 'validator': lambda x: True},  # Default fallback
]

def detect_data_type(data, expected=[]):
    if not isinstance(data, str):
        return 'UNKNOWN'
    
    # First check types in expected list
    for type_info in ALL_TYPES:
        if type_info['type'] in expected and type_info['validator'](data):
            return type_info['type']

    # Then check remaining types
    for type_info in ALL_TYPES:
        if type_info['type'] == 'TEXT' or type_info.get('only_when_expected'): continue
        if type_info['type'] not in expected and type_info['validator'](data):
            return type_info['type']

    return 'UNKNOWN' if data else None


# Binary types. BGZF, the blocked gzip of htslib, compresses BAM, BCF, the
# CSI and tabix indexes, and bgzipped VCF alike, so its gzip header says
# nothing about what is inside: the type is in the first bytes of the
# inflated data. Plain gzip (no BC extra field) is not BGZF and cannot be
# indexed, so it is not taken as any of these.
BGZF_INFLATE_LIMIT = 1 << 20

def bgzf_inflate(data, limit=BGZF_INFLATE_LIMIT):
    """Inflates the BGZF blocks at the start of data, up to about limit bytes.

    Returns (bytes, complete): complete is True when the whole file was
    inflated. Returns (None, False) when data does not start with a BGZF block.
    """
    out = bytearray()
    pos = 0
    while pos < len(data):
        if len(out) >= limit:
            return bytes(out), False
        head = data[pos:pos + 12]
        if len(head) < 12 or head[:4] != b'\x1f\x8b\x08\x04':
            break
        xlen = int.from_bytes(head[10:12], 'little')
        extra = data[pos + 12:pos + 12 + xlen]
        bsize = None
        i = 0
        while i + 4 <= len(extra):
            slen = int.from_bytes(extra[i + 2:i + 4], 'little')
            if extra[i:i + 2] == b'BC' and slen == 2:
                bsize = int.from_bytes(extra[i + 4:i + 6], 'little')
            i += 4 + slen
        end = pos + (bsize or 0) + 1
        if bsize is None or end > len(data):
            break
        try:
            out += zlib.decompressobj(-15).decompress(data[pos + 12 + xlen:end - 8])
        except zlib.error:
            break
        pos = end
    if pos == 0:
        return None, False
    return bytes(out), pos == len(data)

def _starts_with(magic, inflated=False):
    def check(data):
        if not inflated:
            return data[:len(magic)] == magic
        text, _ = bgzf_inflate(data, len(magic))
        return text is not None and text[:len(magic)] == magic
    return check

def _magic_raw_or_bgzf(magic):
    raw, packed = _starts_with(magic), _starts_with(magic, inflated=True)
    return lambda data: raw(data) or packed(data)

def _bgzf_text(text_validator):
    def check(data):
        inflated, complete = bgzf_inflate(data)
        if not inflated:
            return False
        try:
            text = inflated.decode('utf-8')
        except UnicodeDecodeError:
            # A cut through a multi-byte character at the inflate limit
            text = inflated.decode('utf-8', errors='ignore') if not complete else None
        if text is None:
            return False
        if not complete:
            text = text[:text.rfind('\n') + 1]
        return text_validator(text)
    return check

BINARY_TYPES = [
    {'type': 'BAM', 'validator': _magic_raw_or_bgzf(b'BAM\x01')},
    {'type': 'BCF', 'validator': _magic_raw_or_bgzf(b'BCF\x02')},
    {'type': 'CRAM', 'validator': _starts_with(b'CRAM')},
    {'type': 'BAI', 'validator': _starts_with(b'BAI\x01')},
    {'type': 'CSI', 'validator': _magic_raw_or_bgzf(b'CSI\x01')},
    {'type': 'TBI', 'validator': _starts_with(b'TBI\x01', inflated=True)},
    {'type': 'GVCF_GZ', 'validator': _bgzf_text(validate_gvcf)},
    {'type': 'VCF_GZ', 'validator': _bgzf_text(validate_vcf)},
    # Any other BGZF file, such as a bgzipped BED, GFF or SAM; after the
    # specific types above, so that it only takes what they do not
    {'type': 'BGZF', 'validator': lambda d: bgzf_inflate(d, 1)[0] is not None},
    {'type': 'MMI', 'validator': _starts_with(b'MMI\x02')},
    {'type': 'FMD', 'validator': _starts_with(b'RLD\x03')},
    {'type': 'BIGBED', 'validator': lambda d: d[:4] in (b'\xeb\xf2\x89\x87', b'\x87\x89\xf2\xeb')},
    {'type': 'BIGWIG', 'validator': lambda d: d[:4] in (b'\x26\xfc\x8f\x88', b'\x88\x8f\xfc\x26')},
]

BINARY_VALIDATORS = {t['type']: t['validator'] for t in BINARY_TYPES}

def detect_binary_type(data, expected=[]):
    """Like detect_data_type, for bytes. None when no binary type matches."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    for type_info in BINARY_TYPES:
        if type_info['type'] in expected and type_info['validator'](data):
            return type_info['type']
    for type_info in BINARY_TYPES:
        if type_info['type'] not in expected and type_info['validator'](data):
            return type_info['type']
    return None
