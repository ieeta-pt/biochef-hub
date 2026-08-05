from cerberus import Validator
from utils.type_definitions import get_allowed_input_types, get_allowed_output_types, is_binary_type

allowed_input_types = get_allowed_input_types()
allowed_output_types = get_allowed_output_types()
allowed_parameter_types = ['string', 'integer', 'float', 'flag']

def validate_output_mode(field, value, error):
    mode = value.get("mode")
    types = value.get("types", [])

    if mode == "stdout" and any(is_binary_type(t) for t in types):
        error(field, "binary outputs cannot use stdout")

    if mode == "file" and (value.get("flag") is None and not value.get("filename")):
        error(field, "file outputs must have either a 'flag' or a 'filename' defined")


def validate_wasm_strategy(field, value, error):
    """Require the configuration block belonging to the declared strategy.

    Each strategy reads its own sub-document, and the builder indexes into it
    directly. Without this a recipe can name a strategy and omit its settings,
    which validates cleanly and then fails much later in the build with a
    KeyError naming nothing the recipe author would recognise.

    'auto' is exempt: it tries biowasm first and falls back to emscripten, so
    it is the one strategy that can legitimately carry either block.
    """
    # `build.wasm:` with nothing under it parses as None, which would raise an
    # AttributeError out of the validator rather than reporting a bad recipe.
    strategy = (value or {}).get("strategy")

    if strategy in ("emscripten", "r") and not value.get(strategy):
        error(field, f"build.wasm declares strategy '{strategy}' but has no '{strategy}' settings")


def validate_build_combination(field, value, error):
    """Reject an R wasm build alongside a native one.

    `bin` means different things to the two: a script shipped with the recipe
    for R, a binary the build produced for native. A recipe declaring both
    would validate, then fail in the native copy step looking for a compiled
    artifact under the script's name.
    """
    wasm = value.get("wasm") or {}

    if wasm.get("strategy") == "r" and value.get("native"):
        error(field, "an R wasm build cannot be combined with a native build")

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
        'schema': {
            'spdx': {
                'type': 'string',
                # TODO check with spdx license list
            },
            'files': {
                'type': 'list',
                'schema': {'type': 'string'},
                'minlength': 1,
                # TODO check if license exists
            }
        }
    },
    'source': {
        # TODO: perhaps check if they exist
        'type': 'dict',
        'schema': {
            'repo': {
                'type': 'string',
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
        'check_with': validate_build_combination,
        'schema': {
            'wasm': {
                'type': 'dict',
                'schema': {
                    'strategy': {
                        'type': 'string',
                        'allowed': ['auto', 'biowasm', 'emscripten', 'r']
                    },
                    # R packages are cross-compiled to wasm by rwasm inside the
                    # webR container, which supplies Emscripten and a
                    # wasm-targeting LLVM flang. Unlike the C strategies this
                    # produces no per-operation binary: the artifact is a
                    # package library plus the R scripts that drive it, so an
                    # operation's `bin` names a script shipped with the recipe
                    # rather than something the build compiles.
                    'r': {
                        'type': 'dict',
                        'schema': {
                            # pkgdepends references, e.g. "ape" or
                            # "ape@5.8.1". The first is the package an
                            # operation script is expected to library().
                            'packages': {
                                'type': 'list',
                                'schema': {'type': 'string'},
                                'minlength': 1,
                            },
                            # Passed through to rwasm::add_pkg(). Its default
                            # is FALSE, meaning dependencies are not built, so
                            # anything with hard dependencies needs NA.
                            'dependencies': {
                                'type': 'string',
                                'allowed': ['FALSE', 'NA', 'TRUE'],
                                'required': False,
                            },
                            # Pins the toolchain per recipe, as
                            # emscriptenVersion does for the emscripten
                            # strategy. The wasm binaries are only loadable by
                            # the webR release they were built against.
                            'webrVersion': {'type': 'string'},
                        },
                        'required': False
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
                            'outputDir': {'type': 'string', 'required': False},
                            'buildScript': {'type': 'string', 'required': True},
                            'emscriptenVersion': {'type': 'string', 'required': True},
                        },
                        'required': False
                    }
                },
                'check_with': validate_wasm_strategy
            },
            'native': {
                'type': 'dict',
                'schema': {
                    'buildsystem': {'type': 'string', 'allowed': ['make']},
                    'workDir': {'type': 'string', 'required': False},
                    'outputDir': {'type': 'string', 'required': False},
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
                # Constrained to a bare filename. Under the R strategy `bin` names a
                # script inside the contributed recipe directory and is used to build a
                # path, so an unconstrained value would let a recipe reach outside its
                # own directory and have the result published.
                'bin': {'type': 'string', 'maxlength': 128,
                        # \A and \Z rather than ^ and $: Python's $ also matches
                        # before a trailing newline, so "summary.R\n" would pass.
                        'regex': r'\A[A-Za-z0-9][A-Za-z0-9._-]*\Z'},
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

    return result
