from cerberus import Validator

allowed_common_types = ["TEXT", "FASTA", "FASTQ", "NUM", "DNA", "Multi-FASTA", "BIN", "RNA", "AminoAcids", "PackagedFASTQ"]
allowed_input_types = allowed_common_types + ["POS"]
allowed_output_types = allowed_common_types + ["SVG", "Group"]

schema = {
    'apiVersion': {
        'type': 'string',
        'allowed': ['v1']
    },
    'kind': {
        'type': 'string',
        'allowed': ['suite', 'tool']
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
            },
            'version': {
                'type': 'string',
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
                            'buildsystem': {'type': 'string', 'allowed': ['make']},
                            'workDir': {'type': 'string'},
                            'outputDir': {'type': 'string'},
                            'commands': {'type': 'list', 'schema': {'type': 'string'}},
                            'env': {'type': 'list', 'schema': {'type': 'string'}},
                        },
                        'required': False
                    }
                }
            },
            # TODO stuff other than wasm
        }
    },
    'runtime': {
        'type': 'dict',
        'schema': {
            'modes': {
                'type': 'list',
                'schema': {
                    'type': 'string',
                    'allowed': ['wasm', 'local', 'remote']
                }
            }
        }
    },
    'suite': {
        # TODO non suite
        'type': 'dict',
        'schema': {
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
                                        }
                                    }
                                },
                                'outputs': {
                                    'type': 'list',
                                    'schema': {
                                        'type': 'dict',
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
                                    'type': {'type': 'string'},
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
    }
}


def validate_recipe(recipe: dict):
    v = Validator(schema)
    v.require_all = True
    result = v.validate(recipe)

    if not result:
        print(v.errors)

    return result
