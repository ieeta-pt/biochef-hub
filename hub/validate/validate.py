from cerberus import Validator
from utils.type_definitions import get_allowed_input_types, get_allowed_output_types, is_binary_type
from pathlib import Path
from license_expression import ExpressionError, get_spdx_licensing

allowed_input_types = get_allowed_input_types()
allowed_output_types = get_allowed_output_types()
allowed_parameter_types = ['string', 'integer', 'float', 'flag']
spdx_licensing = get_spdx_licensing()

def validate_output_mode(field, value, error):
    mode = value.get("mode")
    types = value.get("types", [])

    if mode == "stdout" and any(is_binary_type(t) for t in types):
        error(field, "binary outputs cannot use stdout")

    if mode == "file" and (value.get("flag") is None and not value.get("filename")):
        error(field, "file outputs must have either a 'flag' or a 'filename' defined")

def validate_source_identity(field, value, error):
    repo = value.get("repo")
    url = value.get("url")
    commit = value.get("commit")
    source_hash = value.get("sha256")

    if repo and url:
        error(field, "source must declare either 'repo' or 'url', not both")

    if repo and source_hash:
        error(field, "source.sha256 is only valid for source.url or vendored source declarations")

    if commit and not repo:
        error(field, "source.commit is only valid for git sources with source.repo")

    if repo and not commit:
        error(field, "git sources must declare an immutable source.commit")

    if url and not source_hash:
        error(field, "archive or single-file sources must declare source.sha256")

    if not repo and not url and not source_hash:
        error(field, "source must declare source.repo + source.commit, source.url + source.sha256, or a vendored source.sha256")

def validate_license_evidence(field, value, error):
    if not value.get("spdx"):
        error(field, "license.spdx is required")

    if not value.get("files") and not value.get("evidence_files") and not value.get("url"):
        error(field, "license must declare at least one of license.files, license.evidence_files, or license.url")

    if value.get("url") and not value.get("sha256"):
        error(field, "license.url requires license.sha256 so the fetched license evidence is reproducible")

    if value.get("sha256") and not value.get("url"):
        error(field, "license.sha256 is only valid with license.url")

def validate_spdx_license_expression(field, value, error):
    try:
        spdx_licensing.parse(value, validate=True)
    except ExpressionError as exc:
        error(field, f"invalid SPDX license expression: {exc}")

def validate_safe_relative_path(field, value, error):
    candidate = Path(value)
    if not value or "\\" in value or candidate.is_absolute() or ".." in candidate.parts:
        error(field, "path must be a safe relative POSIX path")

def builder_source_errors(recipe: dict) -> list[str]:
    source = recipe.get("source") or {}
    build = recipe.get("build") or {}
    errors = []
    if "native" in build and not source.get("repo"):
        errors.append("native builds currently require source.repo + source.commit")
    wasm = build.get("wasm") or {}
    if wasm.get("strategy") == "emscripten" and not source.get("repo"):
        errors.append("Emscripten builds currently require source.repo + source.commit")
    return errors

schema = {
    'apiVersion': {
        'type': 'string',
        'allowed': ['v1']
    },
    'id': {'type': 'string'},
    'name': {'type': 'string'},
    'description': {'type': 'string'},
    'version': {'type': 'string', 'regex': '^\\d+(\\.\\d+)*-bc\\.\\d+$'},
    'homepage': {'type': 'string'},
    'license': {
        'type': 'dict',
        'check_with': validate_license_evidence,
        'schema': {
            'spdx': {
                'type': 'string',
                'check_with': validate_spdx_license_expression,
                'required': True,
            },
            'files': {
                'type': 'list',
                'schema': {'type': 'string', 'check_with': validate_safe_relative_path},
                'minlength': 1,
                'required': False,
            },
            'evidence_files': {
                'type': 'list',
                'schema': {'type': 'string', 'check_with': validate_safe_relative_path},
                'minlength': 1,
                'required': False,
            },
            'url': {
                'type': 'string',
                'required': False,
            },
            'sha256': {
                'type': 'string',
                'regex': '^[a-fA-F0-9]{64}$',
                'required': False,
            }
        }
    },
    'source': {
        'type': 'dict',
        'check_with': validate_source_identity,
        'schema': {
            'repo': {
                'type': 'string',
                'required': False,
            },
            'url': {
                'type': 'string',
                'required': False,
            },
            'sha256': {
                'type': 'string',
                'regex': '^[a-fA-F0-9]{64}$',
                'required': False,
            },
            'tag': {
                'type': 'string',
                'required': False
            },
            'version': {
                'type': 'string',
                'required': True
            },
            'commit': {
                'type': 'string',
                'regex': '^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$',
                'required': False,
            }
        },
    },
    'maintainers': {
        'type': 'list',
        'schema': {
            'type': 'dict',
        },
        'required': False
        # I feel like there is no reason to have a schema
        # since this is for users to read and its *probably* not
        # gonna be parsed by a script
        # 'schema': {
        #     'name': {
        #         'type': 'string',
        #     },
        #     'contact': {
        #         'type': 'string',
        #     },
        # },
    },
    'status': {
        'type': 'string',
        'allowed': ['verified', 'experimental']
    },
    'build': {
        'type': 'dict',
        'schema': {
            'wasm': {
                'type': 'dict',
                'schema': {
                    'strategy': {
                        'type': 'string',
                        'allowed': ['auto', 'biowasm', 'emscripten']
                    },
                    'biowasm': {
                        'type': 'dict',
                        'schema': {
                            'package': {'type': 'string'}
                        },
                        'required': False
                    },
                    'emscripten': {
                        'type': 'dict',
                        'schema': {
                            # TODO maybe check if these exist
                            'buildsystem': {'type': 'string', 'allowed': ['make'], 'required': False},
                            'workDir': {'type': 'string', 'check_with': validate_safe_relative_path, 'required': False},
                            'outputDir': {'type': 'string', 'check_with': validate_safe_relative_path, 'required': False},
                            'commands': {'type': 'list', 'schema': {'type': 'string'}, 'required': False},
                            'env': {'type': 'list', 'schema': {'type': 'string'}, 'required': False},
                        },
                        'required': False
                    }
                }
            },
            'native': {
                'type': 'dict',
                'schema': {
                    'buildsystem': {'type': 'string', 'allowed': ['make']},
                    'workDir': {'type': 'string', 'check_with': validate_safe_relative_path, 'required': False},
                    'outputDir': {'type': 'string', 'check_with': validate_safe_relative_path, 'required': False},
                },
                'required': False
            }
        }
    },
    'runtime': {
        'type': 'dict',
        'schema': {
            'modes': {
                'type': 'list',
                'schema': {
                    'type': 'string',
                    'allowed': ['wasm', 'native', 'remote']
                }
            }
        }
    },
    'operations': {
        'type': 'list',
        'schema': {
            'type': 'dict',
            'schema': {
                'id': {'type': 'string', 'regex': r'^[a-zA-Z0-9.]+$'},
                'name': {'type': 'string'},
                'category': {'type': 'string', 'required': False},
                'bin': {'type': 'string'},
                'description': {'type': 'string'},
                'io': {
                    'type': 'dict',
                    'schema': {
                        'inputs': {
                            'type': 'list',
                            'schema': {
                                'type': 'dict',
                                'schema': {
                                    'name': {'type': 'string'},
                                    'types': {
                                        'type': 'list',
                                        'schema': {
                                            'type': 'string',
                                            'allowed': allowed_input_types
                                        },
                                    },
                                    'mode': {
                                        'type': 'string',
                                        'allowed': ['file', 'stdin']
                                    },
                                    'flag': {
                                        'type': 'string',
                                        'required': False
                                    },
                                    'filename': {
                                        'type': 'string',
                                        'required': False
                                    },
                                }
                            }
                        },
                        'outputs': {
                            'type': 'list',
                            'schema': {
                                'type': 'dict',
                                'check_with': validate_output_mode,
                                'schema': {
                                    'name': {'type': 'string'},
                                    'types': {
                                        'type': 'list',
                                        'schema': {
                                            'type': 'string',
                                            'allowed': allowed_output_types
                                        },
                                    },
                                    'mode': {
                                        'type': 'string',
                                        'allowed': ['file', 'stdout', 'files']
                                    },
                                    'flag': {
                                        'type': 'string',
                                        'required': False
                                    },
                                    'filename': {
                                        'type': 'string',
                                        'required': False
                                    },
                                }
                            }
                        }
                    },
                },
                'parameters': {
                    'type': 'list',
                    'schema': {
                        'type': 'dict',
                        'schema': {
                            'name': {'type': 'string'},
                            'type': {'type': 'string', 'allowed': allowed_parameter_types},
                            'flag': {'type': 'string', 'required': False},
                            'default': {'type': ['string', 'number'], 'required': False},
                            'required': {'type': 'boolean', 'required': False},
                            'hidden': {'type': 'boolean', 'required': False},
                        }
                    }
                }
            }
        }
    }
}


def validate_recipe(recipe: dict):
    v = Validator(schema)
    v.require_all = True
    result = v.validate(recipe)

    if not result:
        print(v.errors)
        return False

    source_errors = builder_source_errors(recipe)
    if source_errors:
        print({"source": source_errors})
        result = False

    return result
