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
