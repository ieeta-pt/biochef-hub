# Adding a New Data Type

This document explains how to add a new data type to BioChef.

- Use the same type id everywhere: hub, frontend, and recipes.
- `hub/utils/type_definitions.py` controls which types recipes are allowed to use.
- `hub/utils/data_types.py` controls how hub tests detect output content.
- Add a validator on `biochef-hub/hub/utils/data_types.py`.


## Add The Type To The Hub Catalog

On `hub/utils/type_definitions.py` add one entry to `TYPE_DEFINITIONS`:

```python
{
    "id": "NewType",
    "input": True,
    "output": True,
    "validator": "new_type",
    "example": "example content",
},
```

Meaning:

- `id`: the type name used in recipe YAML files.
- `input`: whether recipes can use it as an input type.
- `output`: whether recipes can use it as an output type.
- `validator`: the detector name used in `data_types.py`.
- `example`: sample input for that type.

A binary type (`"kind": "binary"`) cannot carry its example as a string. Put the file in `hub/utils/examples/` and name it with `example_file` instead of `example`:

```python
{
    "id": "NewBinaryType",
    "input": True,
    "output": True,
    "kind": "binary",
    "example_file": "example.newbin",
},
```

Make it from the text examples, so that every example describes the same data, and add the command to `hub/utils/examples/make-examples.sh`.


## Add The Hub Detector


On `hub/utils/data_types.py` add a validator function only if the format is known:

```python
def validate_new_type(content):
    # Validation Logic
    return True
```

Then add it to `ALL_TYPES` before `TEXT`:

```python
{'type': 'NewType', 'validator': validate_new_type},
```

Do not add a fake validator just to complete the mapping. A bad validator can make tests pass for the wrong reason.

A binary type's validator takes `bytes` and goes in `BINARY_TYPES` instead:

```python
{'type': 'NewBinaryType', 'validator': _starts_with(b'NEW\x01')},
```

Check what the file is, not how it is compressed. BAM, BCF, CSI and tabix indexes, and bgzipped VCF are all BGZF, so their gzip header is the same: `_magic_raw_or_bgzf` and `_starts_with(..., inflated=True)` look at the inflated data, and `_bgzf_text` runs a text validator on it. A binary output whose declared types have a validator is checked by `hub test`; one without stays unchecked.

Order matters in both lists. Detection tries the declared types first, in list order, then the rest, so put the narrower type first: GVCF before VCF, GVCF_GZ before VCF_GZ.


## Update The Frontend

Use the exact same type id as the hub on the frontend.

On `Biochef/src/utils/typeDefinitions.js` add one entry to `typeDefinitions`:

```
 {
    id: 'NewType',
    validator: 'newType',
    defaultExtension: '.newtype',
    scriptExtension: 'newtype',
    uploadExtensions: ['.newtype', '.nt'],
    edgeColor: '#000000',
    example: 'example content',
  }
```

Then add the validator function on `Biochef/src/utils/detectDataType.js`:

```javascript
function validateNewType(content) {
  // Validation Logic
  return true;
}
```

Add specific detectable types before `TEXT` in `typeDefinitions`, because `TEXT` is the fallback and matches any text.

The frontend validator is used for UI content detection, not recipe validation.
