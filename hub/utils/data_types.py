import re
import json

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

        if not all(re.fullmatch(r'[ACGTN,]+', a, re.I) for a in alt.split(',')):
            return False

        if qual != '.' and not re.fullmatch(r'\d+(\.\d+)?', qual):
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

def validate_json(content):
    if not content or not content.strip():
        return False

    try:
        json.loads(content)
    except json.JSONDecodeError:
        return False

    return True

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
    {'type': 'VCF', 'validator': validate_vcf},
    {'type': 'SAM', 'validator': validate_sam},
    {'type': 'BED', 'validator': validate_bed},
    {'type': 'LIST', 'validator': validate_list},
    {'type': 'GFF', 'validator': validate_gff},
    {'type': 'JSON', 'validator': validate_json},
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
        if type_info['type'] == 'TEXT': continue
        if type_info['type'] not in expected and type_info['validator'](data):
            return type_info['type']

    return 'UNKNOWN' if data else None
