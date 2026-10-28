"""Contract 1.3.0 -> 1.4.0 (integration 2026-10-22; design claude-fable-5-1, implementation claude-sonnet-5-5). Additive.

The input is history/openapi-1.3.0.json, the contract as it was when 1.3.0 was recorded. The working openapi.json that
followed it already carried the additions of PR #1 (generation-only search, explicit training runs, candidate structure
check) without a changelog entry or a snapshot; part A records them (PR1_OPS, derived mechanically from the diff between the
1.3.0 snapshot and that working file). Part B is this integration round (CONTRACTS_v2 section 2, PLAN DD-2/3/4/7/9/10/21/27/29/33/
35/36/37). Replaying this script on openapi-1.3.0.json yields openapi-1.4.0.json (= openapi.json) byte for byte; the script
asserts the sha256 of its result when it is run on the 1.3.0 snapshot.

Usage: python migrate_1.3.0_to_1.4.0.py [path-to-copy-of-openapi-1.3.0.json]   (rewrites the given file in place)
"""
import hashlib, json, sys
from pathlib import Path

p = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / 'openapi.json')
raw = Path(p).read_bytes()
SNAPSHOT_1_3_0_SHA256 = 'ee9b0fbb4585909b6ccbe7696ddb3a05e967adb651cf7e07620d5ced05eae34e'
EXPECTED_1_4_0_SHA256 = 'b89047dfd3d3a055d6f4d14e19c300170a62ec4328cbb625f82e757f4d621bb9'
d = json.loads(raw.decode('utf-8')); S = d['components']['schemas']
assert d['info']['version'] == '1.3.0', d['info']['version']
d['info']['version'] = '1.4.0'
REF = '#/components/schemas/'

# ---- Part A: PR #1 additions that were in the working 1.3.0 file but never recorded ----------------------------------------
PR1_OPS = json.loads(r'''[
 {
  "path": [
   "paths",
   "/api/v1/runs/{run_id}/candidates/{candidate_id}/structure-check"
  ],
  "after": "/api/v1/archive/live",
  "set": {
   "post": {
    "operationId": "checkCandidateStructure",
    "summary": "Explicit CPU structure check; no training or performance evaluation",
    "parameters": [
     {
      "name": "run_id",
      "in": "path",
      "required": true,
      "schema": {
       "type": "string"
      }
     },
     {
      "name": "candidate_id",
      "in": "path",
      "required": true,
      "schema": {
       "type": "string"
      }
     }
    ],
    "responses": {
     "200": {
      "description": "PASS, FAIL or UNVERIFIED with diagnostics",
      "content": {
       "application/json": {
        "schema": {
         "type": "object",
         "additionalProperties": true
        }
       }
      }
     }
    }
   }
  }
 },
 {
  "path": [
   "paths",
   "/api/v1/training-runs"
  ],
  "after": "/api/v1/runs/{run_id}/candidates/{candidate_id}/structure-check",
  "set": {
   "post": {
    "operationId": "createTrainingRun",
    "summary": "Select baseline, user config or candidate for a separate single-model training run",
    "requestBody": {
     "required": true,
     "content": {
      "application/json": {
       "schema": {
        "type": "object",
        "required": [
         "source",
         "protocol",
         "idempotency_key"
        ],
        "additionalProperties": false,
        "properties": {
         "source": {
          "type": "object",
          "required": [
           "kind"
          ],
          "properties": {
           "kind": {
            "type": "string",
            "enum": [
             "baseline",
             "user",
             "search",
             "generated"
            ]
           },
           "run_id": {
            "type": "string"
           },
           "candidate_id": {
            "type": "string"
           },
           "baseline_id": {
            "type": "string"
           },
           "model_path": {
            "type": "string"
           }
          },
          "additionalProperties": false
         },
         "protocol": {
          "type": "object",
          "additionalProperties": true
         },
         "mode": {
          "type": "string",
          "enum": [
           "scratch",
           "pretrained",
           "resume",
           "evaluate_only"
          ]
         },
         "validation_enabled": {
          "type": "boolean"
         },
         "checkpoint": {
          "type": [
           "string",
           "null"
          ]
         },
         "idempotency_key": {
          "type": "string",
          "minLength": 1
         }
        }
       }
      }
     }
    },
    "responses": {
     "200": {
      "description": "Idempotently created or resumed training run",
      "content": {
       "application/json": {
        "schema": {
         "$ref": "#/components/schemas/RunCreated"
        }
       }
      }
     }
    }
   }
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "paths",
   "properties",
   "dataset_yaml",
   "type"
  ],
  "set": [
   "string",
   "null"
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "paths",
   "properties",
   "train_root",
   "type"
  ],
  "set": [
   "string",
   "null"
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "paths",
   "properties",
   "search_val_root",
   "type"
  ],
  "set": [
   "string",
   "null"
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "paths",
   "properties",
   "final_eval_root",
   "type"
  ],
  "set": [
   "string",
   "null"
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "paths",
   "required"
  ],
  "set": [
   "output_root"
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "evaluation_enabled"
  ],
  "after": "search_space_resolved",
  "set": {
   "type": "boolean",
   "description": "Defaults to true on the server; false selects structural random generation only."
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "candidate_count"
  ],
  "after": "evaluation_enabled",
  "set": {
   "type": "integer",
   "minimum": 1
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "max_attempts"
  ],
  "after": "candidate_count",
  "set": {
   "type": "integer",
   "minimum": 1
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "allow_duplicates"
  ],
  "after": "max_attempts",
  "set": {
   "type": "boolean"
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "nc"
  ],
  "after": "allow_duplicates",
  "set": {
   "type": "integer",
   "minimum": 1
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "input_size"
  ],
  "after": "nc",
  "set": {
   "type": "integer",
   "minimum": 32
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "families"
  ],
  "after": "input_size",
  "set": {
   "type": "array",
   "items": {
    "type": "string"
   }
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "requested_method"
  ],
  "after": "families",
  "set": {
   "type": "array",
   "items": {
    "type": "string"
   }
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "properties",
   "search",
   "properties",
   "effective_method"
  ],
  "after": "requested_method",
  "set": {
   "type": "string"
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "Protocol",
   "properties",
   "console",
   "required"
  ],
  "set": [
   "paths",
   "search"
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "Artifact",
   "properties",
   "metadata"
  ],
  "after": "matching",
  "set": {
   "type": "object",
   "additionalProperties": true,
   "description": "Allowlisted recorded image/configuration identifiers; no internal source or traceback. Unknown sample, epoch or checkpoint remains null."
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateEvaluation",
   "properties",
   "split_hash",
   "type"
  ],
  "set": [
   "string",
   "null"
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateEvaluation",
   "properties",
   "split_hash",
   "description"
  ],
  "after": "minLength",
  "set": "Actual evaluated split hash; null for training-only progress with validation disabled."
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateEvaluation",
   "allOf"
  ],
  "set": [
   {
    "if": {
     "properties": {
      "status": {
       "const": "EVALUATED"
      }
     },
     "required": [
      "status"
     ]
    },
    "then": {
     "properties": {
      "ap": {
       "type": "number",
       "minimum": 0,
       "maximum": 1
      },
      "recall": {
       "type": "number",
       "minimum": 0,
       "maximum": 1
      },
      "latency_ms": {
       "type": "number",
       "exclusiveMinimum": 0
      },
      "size_mb": {
       "type": "number",
       "exclusiveMinimum": 0
      },
      "fitness": {
       "type": "number",
       "minimum": 0,
       "maximum": 1
      },
      "latency_source": {
       "enum": [
        "measured",
        "calibrated_proxy",
        "unverified_proxy"
       ]
      },
      "checkpoint_path": {
       "type": "string",
       "minLength": 1
      },
      "split_hash": {
       "type": "string",
       "minLength": 1
      }
     },
     "required": [
      "comparison_group",
      "protocol_hash",
      "batch",
      "resolved_input_shape"
     ]
    }
   },
   {
    "if": {
     "properties": {
      "status": {
       "const": "EVALUATED"
      },
      "arm": {
       "enum": [
        "GA",
        "IndependentRandom"
       ]
      }
     },
     "required": [
      "status",
      "arm"
     ]
    },
    "then": {
     "required": [
      "search_seed"
     ]
    }
   }
  ]
 },
 {
  "path": [
   "components",
   "schemas",
   "Snapshot",
   "properties",
   "generated_candidates"
  ],
  "after": "last_seq",
  "set": {
   "type": "array",
   "items": {
    "$ref": "#/components/schemas/GeneratedCandidate"
   }
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateDetail",
   "properties",
   "structure"
  ],
  "after": "audit",
  "set": {
   "type": "object",
   "additionalProperties": true
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateDetail",
   "properties",
   "structure_status"
  ],
  "after": "structure",
  "set": {
   "type": "string",
   "enum": [
    "PASS",
    "FAIL",
    "UNVERIFIED"
   ]
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateDetail",
   "properties",
   "evaluation_status"
  ],
  "after": "structure_status",
  "set": {
   "type": "string"
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateDetail",
   "properties",
   "origin"
  ],
  "after": "evaluation_status",
  "set": {
   "type": "string"
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "CandidateDetail",
   "properties",
   "artifacts"
  ],
  "after": "origin",
  "set": {
   "type": "array",
   "items": {
    "$ref": "#/components/schemas/Artifact"
   }
  }
 },
 {
  "path": [
   "components",
   "schemas",
   "GeneratedCandidate"
  ],
  "after": "ArchiveCompare",
  "set": {
   "type": "object",
   "required": [
    "id",
    "candidate_id",
    "run_id",
    "name",
    "family",
    "evaluation_status",
    "structure_status"
   ],
   "properties": {
    "id": {
     "type": "string"
    },
    "candidate_id": {
     "type": "string"
    },
    "run_id": {
     "type": "string"
    },
    "name": {
     "type": "string"
    },
    "family": {
     "type": "string"
    },
    "arm": {
     "type": "string"
    },
    "origin": {
     "type": "string"
    },
    "candidate_status": {
     "type": "string"
    },
    "requested_method": {
     "anyOf": [
      {
       "type": "array",
       "items": {
        "type": "string"
       }
      },
      {
       "type": "string"
      },
      {
       "type": "null"
      }
     ]
    },
    "effective_method": {
     "type": "string"
    },
    "evaluation_status": {
     "const": "NOT_EVALUATED",
     "type": "string"
    },
    "structure_status": {
     "type": "string",
     "enum": [
      "PASS",
      "FAIL",
      "UNVERIFIED"
     ]
    },
    "generation_order": {
     "type": [
      "integer",
      "null"
     ],
     "minimum": 0
    },
    "parameters": {
     "type": [
      "integer",
      "null"
     ],
     "minimum": 0
    },
    "artifacts": {
     "type": "array",
     "items": {
      "$ref": "#/components/schemas/Artifact"
     }
    },
    "ap": {
     "type": "null"
    },
    "recall": {
     "type": "null"
    },
    "latency_ms": {
     "type": "null"
    },
    "fitness": {
     "type": "null"
    }
   },
   "additionalProperties": true,
   "description": "Generation results in proposal order. No evaluation row or measured performance is invented."
  }
 }
]''')


def put(root, path, value, after=None):
    node = root
    for key in path[:-1]:
        node = node[key]
    last = path[-1]
    if last in node:
        node[last] = value
        return
    items, rebuilt = list(node.items()), {}
    if after is None:
        rebuilt[last] = value
    for key, val in items:
        rebuilt[key] = val
        if key == after:
            rebuilt[last] = value
    node.clear(); node.update(rebuilt)


for op in PR1_OPS:
    put(d, op['path'], op['set'], op.get('after'))

# ---- Part B: integration round 1 -------------------------------------------------------------------------------------------
COMPONENTS = json.loads(r'''{
 "TrainingRequest": {
  "additionalProperties": false,
  "properties": {
   "source": {
    "additionalProperties": false,
    "properties": {
     "kind": {
      "enum": [
       "baseline",
       "user",
       "search",
       "generated"
      ],
      "type": "string"
     },
     "run_id": {
      "type": [
       "string",
       "null"
      ]
     },
     "candidate_id": {
      "type": [
       "string",
       "null"
      ]
     },
     "baseline_id": {
      "type": [
       "string",
       "null"
      ]
     },
     "model_path": {
      "type": [
       "string",
       "null"
      ]
     }
    },
    "required": [
     "kind"
    ],
    "type": "object",
    "description": "Immutable source of the new trial. baseline needs baseline_id, user needs model_path, search and generated need run_id and candidate_id.",
    "allOf": [
     {
      "if": {
       "properties": {
        "kind": {
         "const": "baseline"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "baseline_id": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "baseline_id"
       ]
      }
     },
     {
      "if": {
       "properties": {
        "kind": {
         "const": "user"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "model_path": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "model_path"
       ]
      }
     },
     {
      "if": {
       "properties": {
        "kind": {
         "const": "search"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "run_id": {
         "type": "string",
         "minLength": 1
        },
        "candidate_id": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "run_id",
        "candidate_id"
       ]
      }
     },
     {
      "if": {
       "properties": {
        "kind": {
         "const": "generated"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "run_id": {
         "type": "string",
         "minLength": 1
        },
        "candidate_id": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "run_id",
        "candidate_id"
       ]
      }
     }
    ]
   },
   "protocol": {
    "additionalProperties": true,
    "type": "object"
   },
   "mode": {
    "enum": [
     "scratch",
     "pretrained",
     "resume",
     "evaluate_only"
    ],
    "type": "string",
    "description": "Default scratch."
   },
   "validation_enabled": {
    "type": "boolean",
    "description": "Default true."
   },
   "checkpoint": {
    "type": [
     "string",
     "null"
    ],
    "description": "Default null. For mode resume: must be the latest recorded training_full checkpoint of the candidate when sent."
   },
   "idempotency_key": {
    "maxLength": 256,
    "minLength": 1,
    "type": "string"
   }
  },
  "required": [
   "source",
   "protocol",
   "idempotency_key"
  ],
  "type": "object",
  "description": "Explicit single-model training/evaluation selection (POST /training-runs). source.kind baseline needs baseline_id; user needs model_path (server-allowlisted detector YAML); search|generated need run_id and candidate_id of the immutable source run. mode=resume continues the latest RECORDED training_full checkpoint of an existing run (the checkpoint, if given, must be that path); a candidate without a recorded training_full state is refused with 409 EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED (no external full-state import). mode=evaluate_only (official evaluation) needs no epoch target: any console.training.epoch_target_total / scheduler_horizon_epochs is ignored and the run records epoch_target_total=0 (console.extra.training.epoch_target_total) with the supplied value kept as ignored_requested_epoch_target_total (DD-31); its CandidateEvaluation rows carry epochs=0 and epoch_target_total null. All other modes require a positive epoch_target_total (422 EPOCH_TARGET_INVALID). validation_enabled=false trains without val/test data and completes as candidate/evaluation status SEARCH_COMPLETE with performance_status NOT_EVALUATED. protocol is the explicit training policy (training, fitness, hard_constraints, console.paths, console.training); the source run's evaluation switch is never inherited."
 },
 "TrainingRunCreated": {
  "type": "object",
  "properties": {
   "run_id": {
    "type": "string",
    "minLength": 1
   },
   "status": {
    "type": "string",
    "minLength": 1
   },
   "protocol_hash": {
    "type": "string",
    "minLength": 1
   },
   "created": {
    "type": "boolean",
    "description": "false when the idempotency key replayed an existing run, or when mode=resume continued an existing run"
   },
   "candidate_id": {
    "type": "string",
    "minLength": 1,
    "description": "the single trial of the run (for mode=resume: the resumed candidate)"
   },
   "source": {
    "additionalProperties": false,
    "properties": {
     "kind": {
      "enum": [
       "baseline",
       "user",
       "search",
       "generated"
      ],
      "type": "string"
     },
     "run_id": {
      "type": [
       "string",
       "null"
      ]
     },
     "candidate_id": {
      "type": [
       "string",
       "null"
      ]
     },
     "baseline_id": {
      "type": [
       "string",
       "null"
      ]
     },
     "model_path": {
      "type": [
       "string",
       "null"
      ]
     }
    },
    "required": [
     "kind"
    ],
    "type": "object",
    "description": "Immutable source of the new trial. baseline needs baseline_id, user needs model_path, search and generated need run_id and candidate_id.",
    "allOf": [
     {
      "if": {
       "properties": {
        "kind": {
         "const": "baseline"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "baseline_id": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "baseline_id"
       ]
      }
     },
     {
      "if": {
       "properties": {
        "kind": {
         "const": "user"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "model_path": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "model_path"
       ]
      }
     },
     {
      "if": {
       "properties": {
        "kind": {
         "const": "search"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "run_id": {
         "type": "string",
         "minLength": 1
        },
        "candidate_id": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "run_id",
        "candidate_id"
       ]
      }
     },
     {
      "if": {
       "properties": {
        "kind": {
         "const": "generated"
        }
       },
       "required": [
        "kind"
       ]
      },
      "then": {
       "properties": {
        "run_id": {
         "type": "string",
         "minLength": 1
        },
        "candidate_id": {
         "type": "string",
         "minLength": 1
        }
       },
       "required": [
        "run_id",
        "candidate_id"
       ]
      }
     }
    ]
   },
   "mode": {
    "type": "string",
    "enum": [
     "scratch",
     "pretrained",
     "resume",
     "evaluate_only"
    ]
   }
  },
  "required": [
   "run_id",
   "status",
   "protocol_hash",
   "created",
   "candidate_id",
   "source",
   "mode"
  ],
  "additionalProperties": true,
  "description": "Response of POST /training-runs (idempotently created or resumed explicit training run)."
 },
 "StructureReport": {
  "type": "object",
  "additionalProperties": true,
  "required": [
   "schema",
   "status"
  ],
  "description": "Structure document mbnas-structure/2 of CandidateDetail.structure, POST /runs/{run_id}/candidates/{candidate_id}/structure-check, GET|POST /registry/{model_id}/structure[-check] and POST /structure-check. Additive over mbnas-structure/1 (v1 keys and values kept; saved v1 proofs are returned unchanged with schema mbnas-structure/1 and lack the v2-only keys, so every v2-only key is optional). DECLARED (D, from the configuration), DERIVED (R, computed from declared values, e.g. repeats_detail.scaled) and OBSERVED (O, read from the CPU build and forward hooks) values always live under separate keys; a derived value never fills an observed key, a connection that was not observed gets no edge (edges_unobserved), and GET endpoints never build a model. status PASS means the build and the reference forward ran and the individual rules held on the observed shapes; no executed check is UNVERIFIED, never PASS. Only values recorded by real code are present. flops is MEASURED only when a proposer counter supplied it (flops_source proposer_flop_counter); explicit checks do not count FLOPs (NOT_MEASURED). CandidateEvaluation.epoch_target_total is >= 1 or absent: an evaluate_only run records the explicit 0 in the run protocol console.extra.training.epoch_target_total, never in an evaluation row.",
  "properties": {
   "schema": {
    "type": "string",
    "description": "mbnas-structure/2 (mbnas-structure/1 for saved legacy proofs)."
   },
   "status": {
    "type": "string",
    "enum": [
     "PASS",
     "FAIL",
     "UNVERIFIED"
    ]
   },
   "backend": {
    "type": [
     "string",
     "null"
    ],
    "enum": [
     "ultralytics",
     "rfdetr",
     "etri_hybrid",
     null
    ],
    "description": "D. Absent in v1 documents."
   },
   "representation": {
    "type": [
     "string",
     "null"
    ],
    "enum": [
     "yaml_genes",
     "native_modules",
     null
    ],
    "description": "D. yaml_genes (Ultralytics, ETRI mixed) | native_modules (RF-DETR, ETRI hybrid: no genes, gene keys of blocks are null)."
   },
   "family": {
    "type": [
     "string",
     "null"
    ]
   },
   "template_id": {
    "type": [
     "string",
     "null"
    ]
   },
   "class_count": {
    "type": [
     "integer",
     "null"
    ]
   },
   "scale": {
    "type": [
     "string",
     "null"
    ]
   },
   "input_size": {
    "type": [
     "integer",
     "array",
     "null"
    ],
    "items": {
     "type": "integer"
    },
    "description": "Square input size of the check; candidate views fall back to the protocol requested_input_size, which may be [h, w]."
   },
   "evidence_source": {
    "type": [
     "string",
     "null"
    ],
    "description": "Where the document comes from: saved_structure_check (recorded check of this exact configuration) | declared_configuration (declaration only, nothing was built) on candidate views; registry views name their stored or executed evidence. Absent on a freshly executed check."
   },
   "candidate_id": {
    "type": [
     "string",
     "null"
    ]
   },
   "run_id": {
    "type": [
     "string",
     "null"
    ]
   },
   "name": {
    "type": [
     "string",
     "null"
    ]
   },
   "origin": {
    "type": [
     "string",
     "null"
    ],
    "description": "Candidate views only."
   },
   "seed": {
    "type": [
     "integer",
     "null"
    ]
   },
   "training_seed": {
    "type": [
     "integer",
     "null"
    ]
   },
   "source": {
    "type": [
     "object",
     "null"
    ],
    "additionalProperties": true,
    "description": "Candidate views: recorded source of the model (audit.source / training_source)."
   },
   "configuration": {
    "type": [
     "object",
     "null"
    ],
    "additionalProperties": true,
    "description": "Candidate views: which configuration the rows describe {kind execution|normalized|original|generated, yaml_hash, path, availability}.",
    "properties": {
     "kind": {
      "type": [
       "string",
       "null"
      ]
     },
     "yaml_hash": {
      "type": [
       "string",
       "null"
      ]
     },
     "path": {
      "type": [
       "string",
       "null"
      ]
     },
     "availability": {
      "type": [
       "string",
       "null"
      ]
     }
    }
   },
   "yaml_hash": {
    "type": [
     "string",
     "null"
    ],
    "description": "Hash of the canonical YAML dict (not of the file bytes; the file sha256 can differ)."
   },
   "architecture_hash": {
    "type": [
     "string",
     "null"
    ],
    "description": "O. Set only after a build."
   },
   "signature_hash": {
    "type": [
     "string",
     "null"
    ]
   },
   "parameters": {
    "type": [
     "integer",
     "null"
    ]
   },
   "flops": {
    "type": [
     "integer",
     "null"
    ]
   },
   "flops_status": {
    "type": [
     "string",
     "null"
    ],
    "enum": [
     "MEASURED",
     "NOT_MEASURED",
     null
    ]
   },
   "flops_source": {
    "type": [
     "string",
     "null"
    ],
    "enum": [
     "proposer_flop_counter",
     "structure_check",
     null
    ]
   },
   "observation": {
    "type": [
     "object",
     "null"
    ],
    "additionalProperties": true,
    "description": "OBSERVED (O). CPU environment of the check that produced the observed values; null for a declaration-only document, a static FAIL and an UNVERIFIED document (nothing ran). A reused proposer report keeps the environment of its own run (report_origin supplied).",
    "properties": {
     "method": {
      "type": [
       "string",
       "null"
      ],
      "description": "ultralytics_parse_model+forward_hook (YAML backend) | forward_hook (native backends)."
     },
     "device": {
      "type": [
       "string",
       "null"
      ],
      "description": "Always cpu."
     },
     "imgsz": {
      "type": [
       "integer",
       "null"
      ],
      "description": "Input size of the reference forward."
     },
     "python": {
      "type": [
       "string",
       "null"
      ]
     },
     "torch": {
      "type": [
       "string",
       "null"
      ]
     },
     "ultralytics": {
      "type": [
       "string",
       "null"
      ]
     },
     "rfdetr": {
      "type": [
       "string",
       "null"
      ]
     },
     "transformers": {
      "type": [
       "string",
       "null"
      ]
     },
     "parser_source_sha256": {
      "type": [
       "string",
       "null"
      ],
      "description": "sha256 of the source of the model parser registered in the checking process (recorded for ordinary YAML as well). It identifies the parser code that built the model, not the YAML file and not the architecture; a different value means a different parser, not a different model."
     },
     "fx_traceable": {
      "type": [
       "boolean",
       "null"
      ],
      "description": "false for native backends (hook observation instead of torch.fx)."
     },
     "dinov2_weights": {
      "type": [
       "string",
       "null"
      ],
      "enum": [
       "skipped",
       "loaded",
       null
      ],
      "description": "RF-DETR only: whether pretrained DINOv2 weights were loaded for the check; null for other backends."
     },
     "checked_at": {
      "type": [
       "string",
       "null"
      ],
      "description": "UTC ISO-8601 time of the check."
     },
     "report_origin": {
      "type": [
       "string",
       "null"
      ],
      "enum": [
       "executed",
       "supplied",
       null
      ],
      "description": "executed = this check ran the build and forward; supplied = a report recorded earlier (e.g. by the proposer) was reused."
     },
     "environment_recorded": {
      "type": [
       "boolean",
       "null"
      ],
      "description": "false only when a supplied report carried no environment."
     }
    }
   },
   "blocks": {
    "type": "array",
    "items": {
     "type": "object",
     "additionalProperties": true,
     "properties": {
      "id": {
       "type": "string",
       "description": "block-NNNN (kept for URLs and v1 consumers); equals block_id."
      },
      "block_id": {
       "type": "string"
      },
      "stable_id": {
       "type": [
        "string",
        "null"
       ],
       "description": "ul:{yaml_path}@{yaml_hash[7:19]} (YAML backend) | rf:{native_path}@{architecture_hash[7:19]} | etri:{native_path}@... (native backends)."
      },
      "index": {
       "type": [
        "integer",
        "null"
       ]
      },
      "position": {
       "type": [
        "integer",
        "null"
       ]
      },
      "gene_index": {
       "type": [
        "integer",
        "null"
       ],
       "description": "null for native backends (no gene)."
      },
      "name": {
       "type": [
        "string",
        "null"
       ]
      },
      "module": {
       "type": [
        "string",
        "null"
       ],
       "description": "DECLARED (D) operator name from the YAML row."
      },
      "module_class": {
       "type": [
        "string",
        "null"
       ],
       "description": "OBSERVED (O) type(m).__name__ of the built module."
      },
      "wrapper": {
       "type": [
        "string",
        "null"
       ],
       "description": "nn.Sequential when the parser wrapped a repeated block."
      },
      "wrapped_module": {
       "type": [
        "string",
        "null"
       ],
       "description": "Operator inside the wrapper."
      },
      "native_path": {
       "type": [
        "string",
        "null"
       ],
       "description": "YAML backend: model.{i}; native backends: named_children path (e.g. transformer.decoder.layers.1)."
      },
      "section": {
       "type": [
        "string",
        "null"
       ],
       "description": "YAML backend backbone|neck|head; native backends backbone|transformer|head|stem|encoder."
      },
      "section_basis": {
       "type": [
        "string",
        "null"
       ],
       "description": "Why the section was assigned (yaml:backbone | yaml:head+detection_module | derived:non-detection head row -> neck | native:<path prefix>); shown as is."
      },
      "connection_source": {
       "type": [
        "string",
        "null"
       ],
       "enum": [
        "yaml_from",
        "native_fixed",
        "observed",
        null
       ]
      },
      "yaml_section": {
       "type": [
        "string",
        "null"
       ]
      },
      "yaml_index": {
       "type": [
        "integer",
        "null"
       ]
      },
      "yaml_path": {
       "type": [
        "string",
        "null"
       ]
      },
      "from": {
       "type": [
        "integer",
        "array",
        "null"
       ],
       "items": {
        "type": "integer"
       },
       "description": "DECLARED (D) YAML from reference(s)."
      },
      "from_resolved": {
       "type": [
        "array",
        "null"
       ],
       "items": {
        "type": "integer"
       }
      },
      "repeats": {
       "type": [
        "integer",
        "null"
       ],
       "description": "DECLARED (D) YAML repeats."
      },
      "args": {
       "type": [
        "array",
        "null"
       ]
      },
      "declared": {
       "type": [
        "object",
        "null"
       ],
       "additionalProperties": true,
       "properties": {
        "repeats": {
         "type": [
          "integer",
          "null"
         ]
        },
        "channels": {
         "type": [
          "number",
          "null"
         ]
        }
       }
      },
      "repeats_detail": {
       "type": [
        "object",
        "null"
       ],
       "additionalProperties": true,
       "description": "The three repeat counts kept apart: declared (D), scaled (R, depth formula), observed (O, from the built module; null when not observed).",
       "properties": {
        "declared": {
         "type": [
          "integer",
          "null"
         ]
        },
        "scaled": {
         "type": [
          "integer",
          "null"
         ]
        },
        "observed": {
         "type": [
          "integer",
          "null"
         ]
        }
       }
      },
      "effective": {
       "type": [
        "object",
        "null"
       ],
       "additionalProperties": true,
       "description": "OBSERVED (O) values read from the built module and the forward hooks; null members mean \"not observed\", never a derived substitute.",
       "properties": {
        "c_in": {
         "type": [
          "integer",
          "null"
         ],
         "description": "Sum of the delivered input channels."
        },
        "c_out": {
         "type": [
          "integer",
          "null"
         ]
        },
        "h_in": {
         "type": [
          "integer",
          "null"
         ]
        },
        "w_in": {
         "type": [
          "integer",
          "null"
         ]
        },
        "h_out": {
         "type": [
          "integer",
          "null"
         ]
        },
        "w_out": {
         "type": [
          "integer",
          "null"
         ]
        },
        "stride": {
         "type": [
          "number",
          "null"
         ]
        },
        "params": {
         "type": [
          "integer",
          "null"
         ]
        },
        "inputs": {
         "type": [
          "array",
          "null"
         ],
         "description": "Tensors actually delivered, one per source (list-from rows have several).",
         "items": {
          "type": "object",
          "additionalProperties": true,
          "properties": {
           "source": {
            "type": [
             "integer",
             "null"
            ]
           },
           "c": {
            "type": [
             "integer",
             "null"
            ]
           },
           "h": {
            "type": [
             "integer",
             "null"
            ]
           },
           "w": {
            "type": [
             "integer",
             "null"
            ]
           }
          }
         }
        },
        "outputs": {
         "type": [
          "array",
          "null"
         ],
         "description": "Shape without batch axis of every returned tensor (Detect returns mixed ranks).",
         "items": {
          "type": "array",
          "items": {
           "type": [
            "integer",
            "null"
           ]
          }
         }
        },
        "repeats": {
         "type": [
          "integer",
          "null"
         ],
         "description": "Observed repeat count (len of m.m / m.blocks / Sequential); null when not observed. The depth-scaled formula value is repeats_scaled."
        },
        "repeats_scaled": {
         "type": [
          "integer",
          "null"
         ],
         "description": "DERIVED (R): the parser depth formula applied to the declared repeats."
        },
        "repeats_source": {
         "type": [
          "string",
          "null"
         ],
         "enum": [
          ".m",
          ".blocks",
          "sequential_wrapper",
          "sequential",
          "single_module",
          "unobserved",
          null
         ]
        },
        "head_strides": {
         "type": [
          "array",
          "null"
         ],
         "items": {
          "type": "number"
         },
         "description": "Detection head only: Detect.stride."
        },
        "nl": {
         "type": [
          "integer",
          "null"
         ],
         "description": "Detection head only: number of detection levels."
        },
        "end2end": {
         "type": [
          "boolean",
          "null"
         ],
         "description": "Detection head only."
        },
        "nc": {
         "type": [
          "integer",
          "null"
         ],
         "description": "Detection head only: head class count."
        },
        "observed_at_failure": {
         "type": [
          "boolean",
          "null"
         ],
         "description": "true on the failing row of a FAIL document, whose inputs are the tensors that reached it."
        }
       }
      },
      "internal_layers": {
       "type": [
        "array",
        "null"
       ],
       "description": "OBSERVED (O) named_modules of this block. A module instance shared by several blocks (e.g. Conv.default_act) appears under every block with shared true.",
       "items": {
        "type": "object",
        "additionalProperties": true,
        "properties": {
         "path": {
          "type": [
           "string",
           "null"
          ]
         },
         "module": {
          "type": [
           "string",
           "null"
          ]
         },
         "parameters": {
          "type": [
           "array",
           "null"
          ]
         },
         "attributes": {
          "type": [
           "object",
           "null"
          ],
          "additionalProperties": true
         },
         "shared": {
          "type": [
           "boolean",
           "null"
          ],
          "description": "null only for legacy reports without per-block records."
         }
        }
       }
      },
      "internal_mapping_status": {
       "type": [
        "string",
        "null"
       ],
       "enum": [
        "RECORDED",
        "UNVERIFIED",
        null
       ]
      },
      "checks_local": {
       "type": [
        "array",
        "null"
       ],
       "description": "Block-level diagnostics (e.g. merge source mismatch).",
       "items": {
        "type": "object",
        "additionalProperties": true,
        "properties": {
         "code": {
          "type": [
           "string",
           "null"
          ]
         },
         "status": {
          "type": [
           "string",
           "null"
          ]
         },
         "cause": {
          "type": [
           "string",
           "null"
          ]
         },
         "evidence_level": {
          "type": [
           "string",
           "null"
          ]
         }
        }
       }
      }
     }
    }
   },
   "edges": {
    "type": "array",
    "description": "Observed or declared connections only; no edge is invented.",
    "items": {
     "type": "object",
     "additionalProperties": true,
     "properties": {
      "source": {
       "type": [
        "integer",
        "null"
       ]
      },
      "target": {
       "type": [
        "integer",
        "null"
       ]
      },
      "from_ref": {
       "type": [
        "integer",
        "null"
       ]
      },
      "connection_source": {
       "type": [
        "string",
        "null"
       ],
       "enum": [
        "yaml_from",
        "native_fixed",
        "observed",
        null
       ],
       "description": "yaml_from (D) | native_fixed (code-fixed flow) | observed (seen through hooks only). Absent in v1."
      }
     }
    }
   },
   "edges_unobserved": {
    "type": [
     "array",
     "null"
    ],
    "description": "Native backends: modules whose input/output could not be observed (e.g. inside the decoder). [] for the YAML backend.",
    "items": {
     "type": "object",
     "additionalProperties": true,
     "properties": {
      "module_path": {
       "type": [
        "string",
        "null"
       ]
      },
      "reason": {
       "type": [
        "string",
        "null"
       ]
      }
     }
    }
   },
   "lineage": {
    "type": [
     "object",
     "null"
    ],
    "additionalProperties": true
   },
   "lineage_status": {
    "type": [
     "string",
     "null"
    ],
    "description": "RECORDED | NOT_APPLICABLE (native backends have no lineage)."
   },
   "diagnostics": {
    "type": "array",
    "items": {
     "type": "object",
     "additionalProperties": true,
     "properties": {
      "code": {
       "type": "string",
       "description": "e.g. SHAPE_MISMATCH, MERGE_INPUT_MISMATCH, HEAD_INPUT_INVALID, BLOCK_UNSUPPORTED, STRUCTURE_RUNTIME_UNAVAILABLE, STRUCTURE_CHECK_NOT_RECORDED, GENE_REPRESENTATION_NOT_APPLICABLE (UNVERIFIED: this model has no gene representation for the requested view; not a failure)."
      },
      "position": {
       "type": [
        "integer",
        "null"
       ]
      },
      "path": {
       "type": [
        "string",
        "null"
       ]
      },
      "cause": {
       "type": [
        "string",
        "null"
       ]
      },
      "suggestion": {
       "type": [
        "string",
        "null"
       ]
      },
      "evidence_level": {
       "type": [
        "string",
        "null"
       ]
      }
     }
    }
   },
   "checks": {
    "type": "array",
    "items": {
     "type": "object",
     "additionalProperties": true,
     "properties": {
      "name": {
       "type": "string"
      },
      "status": {
       "type": "string",
       "enum": [
        "PASS",
        "FAIL",
        "NOT_CHECKED"
       ]
      },
      "evidence_level": {
       "type": [
        "string",
        "null"
       ],
       "enum": [
        "declared",
        "built",
        "forward",
        "derived_from_forward",
        null
       ],
       "description": "A check without a rule of its own that passes only because the forward succeeded is derived_from_forward."
      },
      "rows_checked": {
       "type": [
        "integer",
        "null"
       ],
       "description": "Rows or pairs the rule looked at; 0 means the rule had nothing to judge."
      }
     }
    }
   },
   "reason_code": {
    "type": [
     "string",
     "null"
    ],
    "description": "Candidate audit only: STRUCTURE_FAILED (status FAIL) or STRUCTURE_UNVERIFIED (status UNVERIFIED) on a candidate the evaluation-ON proposer marked INVALID."
   }
  }
 },
 "ArchiveCompareConditions": {
  "additionalProperties": true,
  "description": "Conditions under which the run produced its numbers, read from the run artifacts. A value that cannot be read is null (never estimated); runs whose non-null conditions differ are not like for like.",
  "properties": {
   "dataset": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "split": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "stage": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "epochs_total": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "protocol_hash": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "device": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "precision": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "checkpoint": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "dataset",
   "split",
   "stage",
   "epochs_total",
   "protocol_hash",
   "device",
   "precision",
   "checkpoint"
  ],
  "type": "object"
 }
}''')
for name, schema in COMPONENTS.items():
    assert name not in S, name
    S[name] = schema


def ref(name):
    return {'$ref': REF + name}


def error(description):
    return {'description': description, 'content': {'application/json': {'schema': ref('Error')}}}


AUTH = {'401': {'description': 'Authentication required'}, '403': {'description': 'Not authorized'}}
E500 = {'500': error('RESPONSE_CONTRACT_VIOLATION or internal error')}

# CandidateDetail: typed structure, closed evaluation_status, documented audit keys
cd = S['CandidateDetail']['properties']
cd['structure'] = ref('StructureReport')
cd['evaluation_status'] = {'type': 'string', 'enum': ['EVALUATED', 'NOT_EVALUATED'],
    'description': 'EVALUATED when at least one evaluation row of the candidate has status EVALUATED, otherwise NOT_EVALUATED (generation-only candidates, training-only runs with validation disabled). Never a measured value.'}
cd['audit']['description'] = ('config estimate vs built-model operator audit. Keys written by the server: structure (latest structure evidence '
    '- StructureReport; reason_code STRUCTURE_FAILED or STRUCTURE_UNVERIFIED on a candidate the evaluation-ON proposer marked INVALID), '
    'structure_history[] (DD-7: earlier structure evidence superseded by an explicit re-check, chronological, newest last, at most 5 entries '
    '{checked_at, yaml_hash, status, observation, archived_at}), generation {index, attempt, seed, requested_method, effective_method, '
    'duplicate_scope per_seed, execution CPU, worker_id} for generation-only candidates, training_source.architecture_hash_verified '
    '(true/false; null = one side had no architecture hash, i.e. not verifiable), performance {status}.')

# GeneratedCandidate: duplicate policy and execution site
gc = S['GeneratedCandidate']['properties']
gc['duplicate_scope'] = {'type': ['string', 'null'], 'enum': ['per_seed', None],
    'description': 'Duplicate architectures are rejected inside one seed journal only; seeds never share duplicate detection (seed independence, replay). Candidates generated before this field existed report per_seed, the policy that applied.'}
gc['execution'] = {'type': ['string', 'null'],
    'description': 'Where the structural build ran. CPU for every generation-only job, whichever worker leased it (the child process runs with CUDA_VISIBLE_DEVICES empty); null for candidates recorded before the field existed.'}

# Protocol: resolved-only duplicate scope
S['Protocol']['properties']['console']['properties']['search']['properties']['duplicate_scope'] = {
    'type': 'string', 'enum': ['per_seed'],
    'description': 'Resolved-only (written by Validate, hashed). Duplicate architectures are rejected inside one seed journal; seeds never share duplicate detection, which keeps seed independence and replay. Any other requested value is FIELD_INVALID.'}

# CandidateEvaluation: keep epoch_target_total >= 1, say why evaluate_only rows do not carry 0 (DD-35b)
S['CandidateEvaluation']['properties']['epoch_target_total']['description'] = (
    'Planned total epochs of the trial, >= 1 when present. Official evaluation (training mode evaluate_only) needs no epoch target: its rows carry '
    'epochs 0 and no epoch_target_total; the explicit 0 is recorded only in the run protocol console.extra.training.epoch_target_total '
    '(the validated protocol keeps a structural placeholder of 1).')
S['ValidateResponse']['properties']['capabilities']['description'] += (
    ' For evaluation-disabled protocols generation = {execution CPU, structure_status UNVERIFIED, evaluation_enabled false}: validation is static and '
    'builds nothing, so the structure is UNVERIFIED until a CPU check ran (the former value NOT_CHECKED was retired in 1.4.0 and no longer occurs in any API value).')

# ArchiveCompareRun.conditions (DD-33; the server fills it from run artifacts, WP-S5)
S['ArchiveCompareRun']['properties']['conditions'] = {
    'anyOf': [ref('ArchiveCompareConditions'), {'type': 'null'}],
    'description': 'Conditions of the run (dataset, split, stage, epochs, protocol hash, device, precision, checkpoint) read from its artifacts; any member that cannot be read is null. Optional: absent on servers that do not provide it.'}

# POST /training-runs: named request and response components, documented errors
tr = d['paths']['/api/v1/training-runs']['post']
tr['requestBody']['content']['application/json']['schema'] = ref('TrainingRequest')
tr['responses'] = {
    '200': {'description': 'Idempotently created or resumed training run', 'content': {'application/json': {'schema': ref('TrainingRunCreated')}}},
    **AUTH,
    '404': error('SOURCE_NOT_FOUND (source run, candidate or baseline does not exist)'),
    '409': error('IDEMPOTENCY_CONFLICT, IDEMPOTENCY_KEY_MISMATCH, RESUME_RECIPE_MISMATCH, RESUME_CHECKPOINT_MISMATCH, EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED (mode resume for a candidate without a recorded training_full checkpoint and a checkpoint path was sent)'),
    '422': error('Semantic validation failed: TRAINING_RECIPE_MISSING, CLASS_COUNT_MISMATCH, STRUCTURE_INVALID, EPOCH_TARGET_INVALID, MODEL_CONFIG_INVALID, BASELINE_UNAVAILABLE, path codes'),
    **E500}

# candidate structure check answers with the typed document
d['paths']['/api/v1/runs/{run_id}/candidates/{candidate_id}/structure-check']['post']['responses']['200']['content']['application/json']['schema'] = ref('StructureReport')

# new paths: model-level structure (baseline, registry model, user YAML)
MODEL_ID = {'name': 'model_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'},
            'description': 'Registry entry id (RegistryEntry.id).'}
REGISTRY_STRUCTURE = {'allOf': [ref('StructureReport'), {
    'type': 'object', 'required': ['model_id', 'evidence_source'],
    'properties': {'model_id': {'type': 'string'},
                   'evidence_source': {'type': 'string', 'description': 'declared_configuration when no check was ever recorded (declaration only, status UNVERIFIED, diagnostic STRUCTURE_CHECK_NOT_RECORDED); otherwise names the stored or the just executed check evidence.'}},
    'additionalProperties': True}]}
INPUT_SIZE = {'type': 'object', 'additionalProperties': False, 'properties': {'input_size': {'type': 'integer', 'minimum': 32, 'description': 'Multiple of 32; absent = the model default.'}}}
d['paths']['/api/v1/registry/{model_id}/structure'] = {'get': {
    'operationId': 'getRegistryModelStructure',
    'summary': 'Recorded structure evidence of a registry model (no build)',
    'description': 'Returns the stored structure check of the model (<output_root>/registry_structure/<model_id>/<architecture_hash>.json) or, when none was recorded, a declaration-only document (status UNVERIFIED, evidence_source declared_configuration, diagnostic STRUCTURE_CHECK_NOT_RECORDED). GET never builds a model.',
    'parameters': [MODEL_ID],
    'responses': {'200': {'description': 'Structure document with its evidence source', 'content': {'application/json': {'schema': REGISTRY_STRUCTURE}}},
                  **AUTH, '404': error('MODEL_NOT_FOUND'), **E500}}}
d['paths']['/api/v1/registry/{model_id}/structure-check'] = {'post': {
    'operationId': 'checkRegistryModelStructure',
    'summary': 'Explicit CPU structure observation of a registry model; the result is stored',
    'description': 'Builds the model on CPU with the backend-specific observation (Ultralytics parse + forward hooks, or native forward hooks for RF-DETR and ETRI hybrid), stores the document under <output_root>/registry_structure/<model_id>/ and returns it. UNVERIFIED (runtime missing, offline weights unavailable) is still 200: the answer is in the status field.',
    'parameters': [MODEL_ID],
    'requestBody': {'required': False, 'content': {'application/json': {'schema': INPUT_SIZE}}},
    'responses': {'200': {'description': 'PASS, FAIL or UNVERIFIED with diagnostics', 'content': {'application/json': {'schema': REGISTRY_STRUCTURE}}},
                  **AUTH, '404': error('MODEL_NOT_FOUND'), '422': error('INPUT_SIZE_INVALID'), **E500}}}
d['paths']['/api/v1/structure-check'] = {'post': {
    'operationId': 'checkModelFileStructure',
    'summary': 'Explicit CPU structure check of a user detector YAML (not stored)',
    'description': 'model_path must be a YAML file (up to 1 MiB) inside the server allowlist (output roots, weights directory, ETRI_NAS root). The document is returned and not stored.',
    'requestBody': {'required': True, 'content': {'application/json': {'schema': {
        'type': 'object', 'required': ['model_path'], 'additionalProperties': False,
        'properties': {'model_path': {'type': 'string', 'minLength': 1}, 'input_size': {'type': 'integer', 'minimum': 32},
                       'class_count': {'type': 'integer', 'minimum': 1}}}}}},
    'responses': {'200': {'description': 'PASS, FAIL or UNVERIFIED with diagnostics', 'content': {'application/json': {'schema': ref('StructureReport')}}},
                  **AUTH, '422': error('PATH_OUTSIDE_ALLOWLIST, PATH_NOT_FOUND, MODEL_CONFIG_INVALID, INPUT_SIZE_INVALID'), **E500}}}

# x-status-vocabulary additions (the lists above stay as they were; new keys only)
d['x-status-vocabulary'].update({
    'run_reason_codes_added': ['GENERATION_EMPTY', 'GENERATION_PARTIAL'],
    'run_reason_codes_note': 'Run.reason_code of a COMPLETED generation-only run: GENERATION_EMPTY = every seed produced 0 candidates; GENERATION_PARTIAL = at least one seed produced fewer than candidate_count (a seed without a summary counts as 0).',
    'evaluation_row_reason_codes_added': ['ARCHITECTURE_HASH_MISMATCH'],
    'evaluation_row_reason_codes_note': 'CandidateEvaluation.reason_code of an EVAL_FAILED row (metrics null) or of a validation-disabled SEARCH_COMPLETE row whose trainer architecture hash differs from the candidate hash.',
    'candidate_audit_structure_reason_codes_added': ['STRUCTURE_FAILED', 'STRUCTURE_UNVERIFIED'],
    'structure_diagnostic_codes_added': ['GENE_REPRESENTATION_NOT_APPLICABLE', 'MERGE_INPUT_MISMATCH', 'HEAD_INPUT_INVALID'],
    'structure_diagnostic_codes_note': 'StructureReport.diagnostics[].code. GENE_REPRESENTATION_NOT_APPLICABLE: the model has no gene representation for the requested view (status UNVERIFIED, not a failure).',
    'error_codes_added': {'FINAL_RETRAIN_NOT_APPLICABLE': 409, 'EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED': 409},
    'worker_log_reason_codes_added': ['CHECKPOINT_REPORT_REJECTED'],
    'validator_capabilities_generation_structure_status': ['UNVERIFIED'],
    'validator_capabilities_generation_structure_status_note': 'NOT_CHECKED was removed in 1.4.0; the value is UNVERIFIED when the CPU structure runtime is unavailable.'})

CHANGES = json.loads(r'''[
 "PR #1 (generation-only search, explicit training runs; these additions were already in the working 1.3.0 file but were never recorded in 1.3.0's changelog or snapshot): Protocol.console.search gains evaluation_enabled, candidate_count, max_attempts, allow_duplicates, nc, input_size, families, requested_method, effective_method; Protocol.console.paths: dataset_yaml, train_root, search_val_root and final_eval_root become nullable and only output_root (and search) stay required, console.required is [paths, search] (evaluation-disabled protocols need no dataset roots or baselines).",
 "PR #1: new component GeneratedCandidate (a structural proposal, never an evaluation row) and Snapshot.generated_candidates; new path POST /runs/{run_id}/candidates/{candidate_id}/structure-check (explicit CPU structure check); new path POST /training-runs (explicit single-model training or official evaluation from a baseline, a user YAML or a search/generated candidate).",
 "PR #1: CandidateDetail gains structure, structure_status, evaluation_status, origin, artifacts; Artifact gains metadata; CandidateEvaluation.split_hash becomes nullable (null for training-only progress with validation disabled) and the EVALUATED rule requires a non-empty split_hash.",
 "Round 1 components: TrainingRequest (= the pydantic TrainingRunRequest; replaces the inline schema of POST /training-runs, adds maxLength 256 on idempotency_key, mode semantics for resume and evaluate_only), TrainingRunCreated (response of POST /training-runs, replaces RunCreated there), StructureReport (structure document mbnas-structure/2, additionalProperties true: backend, representation, observation, flops_status/flops_source, edges[].connection_source, edges_unobserved, blocks[].stable_id/native_path/module_class/wrapper/wrapped_module/section_basis/connection_source/repeats_detail/effective.inputs|outputs|head_strides|nl|end2end/internal_layers[].shared, checks[].evidence_level/rows_checked, diagnostics[].evidence_level; declared, derived and observed values are separate keys), ArchiveCompareConditions.",
 "Round 1: CandidateDetail.structure is $ref StructureReport (candidate structure-check answers with StructureReport too); CandidateDetail.evaluation_status is the closed enum EVALUATED | NOT_EVALUATED; CandidateDetail.audit documents structure_history[] (DD-7, last 5 earlier evidence entries), generation.{execution, worker_id, duplicate_scope}, training_source.architecture_hash_verified and structure.reason_code.",
 "Round 1: Protocol.console.search.duplicate_scope (resolved-only, const per_seed; seed-internal duplicate rejection) and GeneratedCandidate.duplicate_scope / execution (CPU); CandidateEvaluation.epoch_target_total documents that evaluate_only rows carry epochs 0 and no epoch_target_total (minimum stays 1); ValidateResponse.capabilities documents generation.structure_status UNVERIFIED (NOT_CHECKED retired).",
 "Round 1 paths: GET /registry/{model_id}/structure (recorded evidence or declaration-only document, never builds), POST /registry/{model_id}/structure-check (explicit CPU observation, stored), POST /structure-check (user detector YAML inside the server allowlist, not stored); responses are StructureReport plus model_id and evidence_source for the registry paths; errors MODEL_NOT_FOUND (404), INPUT_SIZE_INVALID, PATH_OUTSIDE_ALLOWLIST, PATH_NOT_FOUND, MODEL_CONFIG_INVALID (422). The server implementation of these three routes follows in the next work package.",
 "Round 1: ArchiveCompareRun.conditions (optional, ArchiveCompareConditions {dataset, split, stage, epochs_total, protocol_hash, device, precision, checkpoint}, every member nullable: only values readable from the run artifacts are given).",
 "Round 1: x-status-vocabulary gains reason/error code lists (GENERATION_EMPTY, GENERATION_PARTIAL, ARCHITECTURE_HASH_MISMATCH, STRUCTURE_FAILED, STRUCTURE_UNVERIFIED, GENE_REPRESENTATION_NOT_APPLICABLE, MERGE_INPUT_MISMATCH, HEAD_INPUT_INVALID, FINAL_RETRAIN_NOT_APPLICABLE 409, EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED 409, CHECKPOINT_REPORT_REJECTED) and the validator capability structure_status UNVERIFIED; the run, candidate and terminal status lists are unchanged."
]''')
d['x-contract-changelog'].insert(0, {'version': '1.4.0', 'date': '2026-10-22',
  'author': 'integration 2026-10-22 (design: claude-fable-5-1; implementation: claude-sonnet-5-5)', 'changes': CHANGES})

out = json.dumps(d, ensure_ascii=False, indent=1)
Path(p).write_text(out, encoding='utf-8')
print('contract', d['info']['version'])
if hashlib.sha256(raw).hexdigest() == SNAPSHOT_1_3_0_SHA256:
    got = hashlib.sha256(out.encode('utf-8')).hexdigest()
    assert got == EXPECTED_1_4_0_SHA256, f'replay mismatch: {got} != {EXPECTED_1_4_0_SHA256}'
    print('replay ok: sha256', got)
