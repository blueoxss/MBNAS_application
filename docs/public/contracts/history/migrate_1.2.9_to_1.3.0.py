"""Contract 1.2.9 -> 1.3.0 (fable/sonnet 2026-10-15): read-only archive API for ETRI_NAS run artifacts (/api/v1/archive/*). Additive.

The components below are generated from src/mbnas_server/schemas/archive.py (pydantic mirrors, names identical); the paths
are written by hand. Replaying this script on openapi-1.2.9.json yields openapi-1.3.0.json byte for byte.
"""
import json, sys
from pathlib import Path

p = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / 'openapi.json')
d = json.load(open(p)); S = d['components']['schemas']
assert d['info']['version'] == '1.2.9', d['info']['version']
d['info']['version'] = '1.3.0'

COMPONENTS = json.loads(r'''{
 "ArchiveSource": {
  "additionalProperties": true,
  "description": "Where the response came from: the run directory and the run-relative files that were read.",
  "properties": {
   "run_dir": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "files": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "run_dir",
   "files"
  ],
  "type": "object"
 },
 "ArchiveRoot": {
  "additionalProperties": true,
  "properties": {
   "path": {
    "type": "string"
   },
   "family": {
    "type": "string"
   },
   "exists": {
    "type": "boolean"
   }
  },
  "required": [
   "path",
   "family",
   "exists"
  ],
  "type": "object"
 },
 "ArchiveVerdicts": {
  "additionalProperties": true,
  "properties": {
   "notebook": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "transition": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "confirm": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "smoke_check": {
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
   "notebook",
   "transition",
   "confirm",
   "smoke_check"
  ],
  "type": "object"
 },
 "ArchiveGenCounts": {
  "additionalProperties": true,
  "properties": {
   "planned": {
    "anyOf": [
     {
      "minimum": 0,
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "done": {
    "minimum": 0,
    "type": "integer"
   }
  },
  "required": [
   "planned",
   "done"
  ],
  "type": "object"
 },
 "ArchiveCandCounts": {
  "additionalProperties": true,
  "properties": {
   "total": {
    "minimum": 0,
    "type": "integer"
   },
   "ok": {
    "minimum": 0,
    "type": "integer"
   },
   "failed": {
    "minimum": 0,
    "type": "integer"
   },
   "by_status": {
    "anyOf": [
     {
      "additionalProperties": {
       "type": "integer"
      },
      "type": "object"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "total",
   "ok",
   "failed"
  ],
  "type": "object"
 },
 "ArchiveBest": {
  "additionalProperties": true,
  "properties": {
   "map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "small_target_recall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_min_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "map50_95",
   "small_target_recall",
   "latency_min_ms"
  ],
  "type": "object"
 },
 "ArchiveJudgeBaseline": {
  "additionalProperties": true,
  "properties": {
   "name": {
    "type": "string"
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "status": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "params_deploy": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "name",
   "tier",
   "map50_95",
   "latency_median_ms",
   "params_deploy"
  ],
  "type": "object"
 },
 "ArchiveArm": {
  "additionalProperties": true,
  "properties": {
   "space": {},
   "method": {},
   "seed": {}
  },
  "required": [
   "space",
   "method",
   "seed"
  ],
  "type": "object"
 },
 "ArchiveHashes": {
  "additionalProperties": true,
  "properties": {
   "protocol_v2_train": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "metric_protocol": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "dataset_manifest": {
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
   "protocol_v2_train",
   "metric_protocol",
   "dataset_manifest"
  ],
  "type": "object"
 },
 "ArchiveHas": {
  "additionalProperties": true,
  "properties": {
   "baselines": {
    "type": "boolean"
   },
   "generations": {
    "type": "boolean"
   },
   "pareto": {
    "type": "boolean"
   },
   "fidelity": {
    "type": "boolean"
   },
   "confirm": {
    "type": "boolean"
   },
   "plots": {
    "type": "boolean"
   },
   "live": {
    "type": "boolean"
   }
  },
  "required": [
   "baselines",
   "generations",
   "pareto",
   "fidelity",
   "confirm",
   "plots",
   "live"
  ],
  "type": "object"
 },
 "ArchiveRunSummary": {
  "additionalProperties": true,
  "properties": {
   "id": {
    "type": "string"
   },
   "family": {
    "type": "string"
   },
   "path": {
    "type": "string"
   },
   "dataset_id": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "scale": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "status": {
    "enum": [
     "running",
     "done",
     "failed",
     "unknown"
    ],
    "type": "string"
   },
   "status_detail": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "started_at": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "ended_at": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "duration_s": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "verdicts": {
    "$ref": "#/components/schemas/ArchiveVerdicts"
   },
   "generations": {
    "$ref": "#/components/schemas/ArchiveGenCounts"
   },
   "candidates": {
    "$ref": "#/components/schemas/ArchiveCandCounts"
   },
   "best": {
    "$ref": "#/components/schemas/ArchiveBest"
   },
   "judge_baseline": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveJudgeBaseline"
     },
     {
      "type": "null"
     }
    ]
   },
   "arm": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveArm"
     },
     {
      "type": "null"
     }
    ]
   },
   "hashes": {
    "$ref": "#/components/schemas/ArchiveHashes"
   },
   "has": {
    "$ref": "#/components/schemas/ArchiveHas"
   }
  },
  "required": [
   "id",
   "family",
   "path",
   "dataset_id",
   "scale",
   "status",
   "status_detail",
   "started_at",
   "ended_at",
   "duration_s",
   "verdicts",
   "generations",
   "candidates",
   "best",
   "judge_baseline",
   "arm",
   "hashes",
   "has"
  ],
  "type": "object"
 },
 "ArchiveRunList": {
  "additionalProperties": true,
  "properties": {
   "roots": {
    "items": {
     "$ref": "#/components/schemas/ArchiveRoot"
    },
    "type": "array"
   },
   "runs": {
    "items": {
     "$ref": "#/components/schemas/ArchiveRunSummary"
    },
    "type": "array"
   },
   "scanned_at": {
    "type": "string"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "roots",
   "runs",
   "scanned_at",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveStage": {
  "additionalProperties": true,
  "properties": {
   "label": {
    "type": "string"
   },
   "started_at": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "ended_at": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "duration_s": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "label",
   "started_at",
   "ended_at",
   "duration_s"
  ],
  "type": "object"
 },
 "ArchiveGate": {
  "additionalProperties": true,
  "properties": {
   "name": {
    "type": "string"
   },
   "status": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "hard": {
    "anyOf": [
     {
      "type": "boolean"
     },
     {
      "type": "null"
     }
    ]
   },
   "measured": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "criterion": {
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
   "name",
   "status",
   "hard",
   "measured",
   "criterion"
  ],
  "type": "object"
 },
 "ArchiveRole": {
  "additionalProperties": true,
  "properties": {
   "role": {
    "type": "string"
   },
   "candidate_id": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "metric": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "value": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "values": {
    "anyOf": [
     {
      "additionalProperties": {
       "anyOf": [
        {
         "type": "number"
        },
        {
         "type": "null"
        }
       ]
      },
      "type": "object"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "role",
   "candidate_id",
   "metric",
   "value"
  ],
  "type": "object"
 },
 "ArchiveFidelitySummary": {
  "additionalProperties": true,
  "properties": {
   "spearman": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "kendall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "n": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "snr": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "spearman",
   "kendall",
   "n"
  ],
  "type": "object"
 },
 "ArchiveGpuGuardEpisode": {
  "additionalProperties": true,
  "properties": {
   "gpu": {
    "type": "string"
   },
   "waited_s": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "external_mib_max": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "start": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "end": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "outcome": {
    "enum": [
     "proceed",
     "giveup",
     "open"
    ],
    "type": "string"
   }
  },
  "required": [
   "gpu",
   "waited_s",
   "external_mib_max",
   "start",
   "end",
   "outcome"
  ],
  "type": "object"
 },
 "ArchiveGpuGuard": {
  "additionalProperties": true,
  "properties": {
   "records": {
    "minimum": 0,
    "type": "integer"
   },
   "episodes": {
    "items": {
     "$ref": "#/components/schemas/ArchiveGpuGuardEpisode"
    },
    "type": "array"
   },
   "giveups": {
    "minimum": 0,
    "type": "integer"
   }
  },
  "required": [
   "records",
   "episodes",
   "giveups"
  ],
  "type": "object"
 },
 "ArchivePlotRef": {
  "additionalProperties": true,
  "properties": {
   "kind": {
    "type": "string"
   },
   "exists": {
    "type": "boolean"
   }
  },
  "required": [
   "kind",
   "exists"
  ],
  "type": "object"
 },
 "ArchiveDriver": {
  "additionalProperties": true,
  "properties": {
   "path": {
    "type": "string"
   },
   "status_json": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "pipeline_log_tail": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "path",
   "status_json",
   "pipeline_log_tail"
  ],
  "type": "object"
 },
 "ArchiveRunningRun": {
  "additionalProperties": true,
  "properties": {
   "id": {
    "type": "string"
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
   "nb_pid_alive": {
    "type": "boolean"
   },
   "kernel_pid_alive": {
    "type": "boolean"
   },
   "confirm_pid_alive": {
    "anyOf": [
     {
      "type": "boolean"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "budget_s": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "elapsed_s": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "watchdog_last": {
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
   "id",
   "stage",
   "nb_pid_alive",
   "kernel_pid_alive",
   "budget_s",
   "elapsed_s",
   "watchdog_last"
  ],
  "type": "object"
 },
 "ArchiveGpu": {
  "additionalProperties": true,
  "properties": {
   "index": {
    "type": "integer"
   },
   "name": {
    "type": "string"
   },
   "used_mib": {
    "type": "number"
   },
   "total_mib": {
    "type": "number"
   },
   "util_percent": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "index",
   "name",
   "used_mib",
   "total_mib",
   "util_percent"
  ],
  "type": "object"
 },
 "ArchiveLive": {
  "additionalProperties": true,
  "properties": {
   "drivers": {
    "items": {
     "$ref": "#/components/schemas/ArchiveDriver"
    },
    "type": "array"
   },
   "running_runs": {
    "items": {
     "$ref": "#/components/schemas/ArchiveRunningRun"
    },
    "type": "array"
   },
   "gpus": {
    "anyOf": [
     {
      "items": {
       "$ref": "#/components/schemas/ArchiveGpu"
      },
      "type": "array"
     },
     {
      "type": "null"
     }
    ]
   },
   "server_time": {
    "type": "string"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "drivers",
   "running_runs",
   "gpus",
   "server_time",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveRun": {
  "additionalProperties": true,
  "properties": {
   "id": {
    "type": "string"
   },
   "family": {
    "type": "string"
   },
   "path": {
    "type": "string"
   },
   "dataset_id": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "scale": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "status": {
    "enum": [
     "running",
     "done",
     "failed",
     "unknown"
    ],
    "type": "string"
   },
   "status_detail": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "started_at": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "ended_at": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "duration_s": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "verdicts": {
    "$ref": "#/components/schemas/ArchiveVerdicts"
   },
   "generations": {
    "$ref": "#/components/schemas/ArchiveGenCounts"
   },
   "candidates": {
    "$ref": "#/components/schemas/ArchiveCandCounts"
   },
   "best": {
    "$ref": "#/components/schemas/ArchiveBest"
   },
   "judge_baseline": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveJudgeBaseline"
     },
     {
      "type": "null"
     }
    ]
   },
   "arm": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveArm"
     },
     {
      "type": "null"
     }
    ]
   },
   "hashes": {
    "$ref": "#/components/schemas/ArchiveHashes"
   },
   "has": {
    "$ref": "#/components/schemas/ArchiveHas"
   },
   "stages": {
    "items": {
     "$ref": "#/components/schemas/ArchiveStage"
    },
    "type": "array"
   },
   "gates": {
    "items": {
     "$ref": "#/components/schemas/ArchiveGate"
    },
    "type": "array"
   },
   "budget": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "preset": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "dataset": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "accuracy_floor": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "roles": {
    "items": {
     "$ref": "#/components/schemas/ArchiveRole"
    },
    "type": "array"
   },
   "final_val": {
    "items": {
     "additionalProperties": true,
     "type": "object"
    },
    "type": "array"
   },
   "fidelity_summary": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveFidelitySummary"
     },
     {
      "type": "null"
     }
    ]
   },
   "gpu_guard": {
    "$ref": "#/components/schemas/ArchiveGpuGuard"
   },
   "stop_rule": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "transition": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "orphan_check": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "live": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveLive"
     },
     {
      "type": "null"
     }
    ]
   },
   "plots": {
    "items": {
     "$ref": "#/components/schemas/ArchivePlotRef"
    },
    "type": "array"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "id",
   "family",
   "path",
   "dataset_id",
   "scale",
   "status",
   "status_detail",
   "started_at",
   "ended_at",
   "duration_s",
   "verdicts",
   "generations",
   "candidates",
   "best",
   "judge_baseline",
   "arm",
   "hashes",
   "has",
   "stages",
   "gates",
   "budget",
   "preset",
   "dataset",
   "accuracy_floor",
   "roles",
   "final_val",
   "fidelity_summary",
   "gpu_guard",
   "stop_rule",
   "transition",
   "orphan_check",
   "live",
   "plots",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveConfirmRef": {
  "additionalProperties": true,
  "properties": {
   "val_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "test_tiles_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "test_native_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "n_success": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "val_map50_95_std": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "val_map50_95",
   "test_tiles_map50_95",
   "test_native_map50_95",
   "latency_median_ms"
  ],
  "type": "object"
 },
 "ArchiveBaseline": {
  "additionalProperties": true,
  "properties": {
   "name": {
    "type": "string"
   },
   "kind": {
    "enum": [
     "baseline",
     "anchor"
    ],
    "type": "string"
   },
   "cfg_name": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "epochs": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "is_judge": {
    "type": "boolean"
   },
   "status": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "map50": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "small_target_recall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_exclusive_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "forward_only_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "t_post_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "params_deploy": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "params_train": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "peak_mem_mb": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "train_time_s": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "head_mode": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_ckpt": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "checkpoint_sha256": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "confirm": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveConfirmRef"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "name",
   "kind",
   "cfg_name",
   "tier",
   "epochs",
   "is_judge",
   "status",
   "map50_95",
   "map50",
   "small_target_recall",
   "latency_median_ms",
   "latency_exclusive_ms",
   "forward_only_median_ms",
   "t_post_ms",
   "params_deploy",
   "params_train",
   "peak_mem_mb",
   "train_time_s",
   "head_mode",
   "best_ckpt",
   "checkpoint_sha256",
   "confirm"
  ],
  "type": "object"
 },
 "ArchiveAnchor": {
  "additionalProperties": true,
  "properties": {
   "candidate_id": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "fingerprint": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "metrics": {
    "additionalProperties": true,
    "type": "object"
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "epochs": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "candidate_id",
   "fingerprint",
   "metrics"
  ],
  "type": "object"
 },
 "ArchiveBaselineList": {
  "additionalProperties": true,
  "properties": {
   "judge_baseline": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveJudgeBaseline"
     },
     {
      "type": "null"
     }
    ]
   },
   "baselines": {
    "items": {
     "$ref": "#/components/schemas/ArchiveBaseline"
    },
    "type": "array"
   },
   "anchor": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveAnchor"
     },
     {
      "type": "null"
     }
    ]
   },
   "notes": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "judge_baseline",
   "baselines",
   "anchor",
   "notes",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveObjective": {
  "additionalProperties": true,
  "properties": {
   "key": {
    "type": "string"
   },
   "direction": {
    "enum": [
     "max",
     "min"
    ],
    "type": "string"
   },
   "label": {
    "type": "string"
   }
  },
  "required": [
   "key",
   "direction",
   "label"
  ],
  "type": "object"
 },
 "ArchiveSpread": {
  "additionalProperties": true,
  "properties": {
   "min": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "med": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "max": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "min",
   "med",
   "max"
  ],
  "type": "object"
 },
 "ArchiveDepth": {
  "additionalProperties": true,
  "properties": {
   "n_backbone": {
    "$ref": "#/components/schemas/ArchiveSpread"
   },
   "n_head": {
    "$ref": "#/components/schemas/ArchiveSpread"
   },
   "stage_count": {
    "$ref": "#/components/schemas/ArchiveSpread"
   }
  },
  "required": [
   "n_backbone",
   "n_head",
   "stage_count"
  ],
  "type": "object"
 },
 "ArchiveModuleDelta": {
  "additionalProperties": true,
  "properties": {
   "module": {
    "type": "string"
   },
   "count": {
    "type": "integer"
   },
   "count_prev": {
    "type": "integer"
   },
   "delta": {
    "type": "integer"
   },
   "delta_per_candidate": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "module",
   "count",
   "count_prev",
   "delta",
   "delta_per_candidate"
  ],
  "type": "object"
 },
 "ArchiveCharacteristics": {
  "additionalProperties": true,
  "properties": {
   "n": {
    "minimum": 0,
    "type": "integer"
   },
   "n_cfg": {
    "minimum": 0,
    "type": "integer"
   },
   "n_cfg_missing": {
    "minimum": 0,
    "type": "integer"
   },
   "n_prev": {
    "minimum": 0,
    "type": "integer"
   },
   "n_prev_cfg": {
    "minimum": 0,
    "type": "integer"
   },
   "module_histogram": {
    "additionalProperties": {
     "type": "integer"
    },
    "type": "object"
   },
   "module_histogram_prev": {
    "additionalProperties": {
     "type": "integer"
    },
    "type": "object"
   },
   "module_candidates": {
    "additionalProperties": {
     "type": "integer"
    },
    "type": "object"
   },
   "head_mode": {
    "additionalProperties": {
     "type": "integer"
    },
    "type": "object"
   },
   "depth": {
    "$ref": "#/components/schemas/ArchiveDepth"
   },
   "params_deploy": {
    "$ref": "#/components/schemas/ArchiveSpread"
   },
   "latency_median_ms": {
    "$ref": "#/components/schemas/ArchiveSpread"
   },
   "top_modules_delta": {
    "items": {
     "$ref": "#/components/schemas/ArchiveModuleDelta"
    },
    "type": "array"
   }
  },
  "required": [
   "n",
   "n_cfg",
   "n_cfg_missing",
   "n_prev",
   "n_prev_cfg",
   "module_histogram",
   "module_histogram_prev",
   "module_candidates",
   "head_mode",
   "depth",
   "params_deploy",
   "latency_median_ms",
   "top_modules_delta"
  ],
  "type": "object"
 },
 "ArchiveDeltaPrev": {
  "additionalProperties": true,
  "properties": {
   "hypervolume": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_small_recall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_latency": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "hypervolume",
   "best_map50_95",
   "best_small_recall",
   "best_latency"
  ],
  "type": "object"
 },
 "ArchiveDeltaBaseline": {
  "additionalProperties": true,
  "properties": {
   "best_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_latency": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "cum_best_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "baseline_name": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "baseline_tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "note": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "best_map50_95",
   "best_latency"
  ],
  "type": "object"
 },
 "ArchiveGeneration": {
  "additionalProperties": true,
  "properties": {
   "generation": {
    "minimum": 0,
    "type": "integer"
   },
   "has_summary": {
    "type": "boolean"
   },
   "phase": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "population": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "valid": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "train_fail": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "degenerate": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "unique_fingerprints": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "archive_size": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "hypervolume": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "hv_improved": {
    "anyOf": [
     {
      "type": "boolean"
     },
     {
      "type": "null"
     }
    ]
   },
   "stagnation": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_small_recall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "best_latency": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "mutation": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "n_rows": {
    "minimum": 0,
    "type": "integer"
   },
   "status_counts": {
    "additionalProperties": {
     "type": "integer"
    },
    "type": "object"
   },
   "new_fingerprints": {
    "minimum": 0,
    "type": "integer"
   },
   "new_fingerprint_ids": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "lineage_modes": {
    "additionalProperties": {
     "type": "integer"
    },
    "type": "object"
   },
   "delta_vs_prev": {
    "$ref": "#/components/schemas/ArchiveDeltaPrev"
   },
   "delta_vs_baseline": {
    "$ref": "#/components/schemas/ArchiveDeltaBaseline"
   },
   "cum_best": {
    "additionalProperties": {
     "anyOf": [
      {
       "type": "number"
      },
      {
       "type": "null"
      }
     ]
    },
    "type": "object"
   },
   "front2d": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "front4d": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "front_added": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "front_removed": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "front4d_added": {
    "anyOf": [
     {
      "items": {
       "type": "string"
      },
      "type": "array"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "front4d_removed": {
    "anyOf": [
     {
      "items": {
       "type": "string"
      },
      "type": "array"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "characteristics": {
    "$ref": "#/components/schemas/ArchiveCharacteristics"
   }
  },
  "required": [
   "generation",
   "has_summary",
   "phase",
   "population",
   "valid",
   "train_fail",
   "degenerate",
   "unique_fingerprints",
   "archive_size",
   "hypervolume",
   "hv_improved",
   "stagnation",
   "best_map50_95",
   "best_small_recall",
   "best_latency",
   "mutation",
   "n_rows",
   "status_counts",
   "new_fingerprints",
   "new_fingerprint_ids",
   "lineage_modes",
   "delta_vs_prev",
   "delta_vs_baseline",
   "cum_best",
   "front2d",
   "front4d",
   "front_added",
   "front_removed",
   "characteristics"
  ],
  "type": "object"
 },
 "ArchiveGenerationList": {
  "additionalProperties": true,
  "properties": {
   "objectives": {
    "items": {
     "$ref": "#/components/schemas/ArchiveObjective"
    },
    "type": "array"
   },
   "generations": {
    "items": {
     "$ref": "#/components/schemas/ArchiveGeneration"
    },
    "type": "array"
   },
   "hv_history": {
    "items": {
     "type": "number"
    },
    "type": "array"
   },
   "stagnation_rule": {
    "type": "string"
   },
   "notes": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "objectives",
   "generations",
   "hv_history",
   "stagnation_rule",
   "notes",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveLineage": {
  "additionalProperties": true,
  "properties": {
   "mode": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "parents": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "mutation": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "cache_hit": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "notes_parse": {
    "anyOf": [
     {
      "enum": [
       "ok",
       "truncated",
       "missing"
      ],
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "mode",
   "parents",
   "mutation",
   "cache_hit"
  ],
  "type": "object"
 },
 "ArchiveCandHashes": {
  "additionalProperties": true,
  "properties": {
   "config": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "genotype": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "metric_protocol": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "training_protocol": {
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
   "config",
   "genotype",
   "metric_protocol",
   "training_protocol"
  ],
  "type": "object"
 },
 "ArchiveDeltaCand": {
  "additionalProperties": true,
  "properties": {
   "map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "params_deploy": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "baseline": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "baseline_tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "map50_95",
   "latency_median_ms",
   "params_deploy"
  ],
  "type": "object"
 },
 "ArchiveCandidateRow": {
  "additionalProperties": true,
  "properties": {
   "candidate_id": {
    "type": "string"
   },
   "generation": {
    "minimum": 0,
    "type": "integer"
   },
   "fingerprint": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "status": {
    "type": "string"
   },
   "error": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "seed": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "epochs": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "metrics": {
    "additionalProperties": true,
    "type": "object"
   },
   "lineage": {
    "$ref": "#/components/schemas/ArchiveLineage"
   },
   "roles": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "is_final": {
    "type": "boolean"
   },
   "on_front2d": {
    "type": "boolean"
   },
   "on_front4d": {
    "type": "boolean"
   },
   "new_fingerprint": {
    "anyOf": [
     {
      "type": "boolean"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "delta_vs_baseline": {
    "$ref": "#/components/schemas/ArchiveDeltaCand"
   },
   "hashes": {
    "$ref": "#/components/schemas/ArchiveCandHashes"
   },
   "has_cfg": {
    "type": "boolean"
   },
   "has_checkpoint": {
    "type": "boolean"
   }
  },
  "required": [
   "candidate_id",
   "generation",
   "fingerprint",
   "tier",
   "status",
   "error",
   "seed",
   "epochs",
   "metrics",
   "lineage",
   "roles",
   "is_final",
   "on_front2d",
   "on_front4d",
   "delta_vs_baseline",
   "hashes",
   "has_cfg",
   "has_checkpoint"
  ],
  "type": "object"
 },
 "ArchiveCandidateList": {
  "additionalProperties": true,
  "properties": {
   "total": {
    "minimum": 0,
    "type": "integer"
   },
   "candidates": {
    "items": {
     "$ref": "#/components/schemas/ArchiveCandidateRow"
    },
    "type": "array"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "total",
   "candidates",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveImprovement": {
  "additionalProperties": true,
  "properties": {
   "objective": {
    "type": "string"
   },
   "prev_best": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "new_best": {
    "type": "number"
   },
   "candidate_id": {
    "type": "string"
   },
   "delta": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "kind": {
    "anyOf": [
     {
      "enum": [
       "initial",
       "improved"
      ],
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "objective",
   "prev_best",
   "new_best",
   "candidate_id",
   "delta"
  ],
  "type": "object"
 },
 "ArchiveFrontPoint": {
  "additionalProperties": true,
  "properties": {
   "candidate_id": {
    "type": "string"
   },
   "map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "small_target_recall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "params_deploy": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "candidate_id",
   "map50_95",
   "latency_median_ms",
   "small_target_recall",
   "params_deploy"
  ],
  "type": "object"
 },
 "ArchiveLineageEntry": {
  "additionalProperties": true,
  "properties": {
   "candidate_id": {
    "type": "string"
   },
   "mode": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "parents": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "mutation": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "cache_hit": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "notes_parse": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "candidate_id",
   "mode",
   "parents",
   "mutation",
   "cache_hit"
  ],
  "type": "object"
 },
 "ArchiveGenerationDetail": {
  "additionalProperties": true,
  "properties": {
   "generation": {
    "$ref": "#/components/schemas/ArchiveGeneration"
   },
   "candidates": {
    "items": {
     "$ref": "#/components/schemas/ArchiveCandidateRow"
    },
    "type": "array"
   },
   "improvements": {
    "items": {
     "$ref": "#/components/schemas/ArchiveImprovement"
    },
    "type": "array"
   },
   "front2d_points": {
    "items": {
     "$ref": "#/components/schemas/ArchiveFrontPoint"
    },
    "type": "array"
   },
   "prev_front2d_points": {
    "items": {
     "$ref": "#/components/schemas/ArchiveFrontPoint"
    },
    "type": "array"
   },
   "lineage": {
    "items": {
     "$ref": "#/components/schemas/ArchiveLineageEntry"
    },
    "type": "array"
   },
   "characteristics": {
    "$ref": "#/components/schemas/ArchiveCharacteristics"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "generation",
   "candidates",
   "improvements",
   "front2d_points",
   "prev_front2d_points",
   "lineage",
   "characteristics",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveEvaluation": {
  "additionalProperties": true,
  "properties": {
   "stage": {
    "enum": [
     "search",
     "final",
     "exclusive",
     "confirm"
    ],
    "type": "string"
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "epochs": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "seed": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "status": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "metrics": {
    "additionalProperties": true,
    "type": "object"
   },
   "source": {
    "type": "string"
   }
  },
  "required": [
   "stage",
   "tier",
   "epochs",
   "seed",
   "status",
   "metrics",
   "source"
  ],
  "type": "object"
 },
 "ArchiveStageRow": {
  "additionalProperties": true,
  "properties": {
   "stage_index": {
    "type": "integer"
   },
   "global_start": {
    "type": "integer"
   },
   "modules": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "downsamples": {
    "type": "integer"
   }
  },
  "required": [
   "stage_index",
   "global_start",
   "modules",
   "downsamples"
  ],
  "type": "object"
 },
 "ArchiveArchSource": {
  "additionalProperties": true,
  "properties": {
   "kind": {
    "enum": [
     "own",
     "fingerprint_twin"
    ],
    "type": "string"
   },
   "candidate_id": {
    "type": "string"
   }
  },
  "required": [
   "kind",
   "candidate_id"
  ],
  "type": "object"
 },
 "ArchiveArchitecture": {
  "additionalProperties": true,
  "properties": {
   "yaml_text": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "cfg": {
    "additionalProperties": true,
    "type": "object"
   },
   "n_backbone": {
    "minimum": 0,
    "type": "integer"
   },
   "n_head": {
    "minimum": 0,
    "type": "integer"
   },
   "stage_count": {
    "minimum": 0,
    "type": "integer"
   },
   "stages": {
    "items": {
     "$ref": "#/components/schemas/ArchiveStageRow"
    },
    "type": "array"
   },
   "stages_source": {
    "type": "string"
   },
   "module_histogram": {
    "additionalProperties": {
     "type": "integer"
    },
    "type": "object"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveArchSource"
   }
  },
  "required": [
   "yaml_text",
   "cfg",
   "n_backbone",
   "n_head",
   "stage_count",
   "stages",
   "stages_source",
   "module_histogram",
   "source"
  ],
  "type": "object"
 },
 "ArchiveParent": {
  "additionalProperties": true,
  "properties": {
   "candidate_id": {
    "type": "string"
   },
   "kind": {
    "enum": [
     "candidate",
     "external_config"
    ],
    "type": "string"
   },
   "fingerprint": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "generation": {
    "anyOf": [
     {
      "type": "integer"
     },
     {
      "type": "null"
     }
    ]
   },
   "metrics_summary": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "has_cfg": {
    "type": "boolean"
   }
  },
  "required": [
   "candidate_id",
   "kind",
   "fingerprint",
   "generation",
   "metrics_summary",
   "has_cfg"
  ],
  "type": "object"
 },
 "ArchiveStageDiff": {
  "additionalProperties": true,
  "properties": {
   "stage_index": {
    "type": "integer"
   },
   "parent_modules": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "child_modules": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "stage_index",
   "parent_modules",
   "child_modules"
  ],
  "type": "object"
 },
 "ArchiveParentDiff": {
  "additionalProperties": true,
  "properties": {
   "parent": {
    "type": "string"
   },
   "modules_added": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "modules_removed": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "module_delta": {
    "anyOf": [
     {
      "additionalProperties": {
       "type": "integer"
      },
      "type": "object"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "stage_diff": {
    "items": {
     "$ref": "#/components/schemas/ArchiveStageDiff"
    },
    "type": "array"
   },
   "n_rows_parent": {
    "minimum": 0,
    "type": "integer"
   },
   "n_rows_child": {
    "minimum": 0,
    "type": "integer"
   }
  },
  "required": [
   "parent",
   "modules_added",
   "modules_removed",
   "stage_diff",
   "n_rows_parent",
   "n_rows_child"
  ],
  "type": "object"
 },
 "ArchiveCandLineage": {
  "additionalProperties": true,
  "properties": {
   "mode": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "parents": {
    "items": {
     "$ref": "#/components/schemas/ArchiveParent"
    },
    "type": "array"
   },
   "mutation": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "cache_hit": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "notes_parse": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "diff_vs_parents": {
    "items": {
     "$ref": "#/components/schemas/ArchiveParentDiff"
    },
    "type": "array"
   }
  },
  "required": [
   "mode",
   "parents",
   "mutation",
   "cache_hit",
   "diff_vs_parents"
  ],
  "type": "object"
 },
 "ArchiveLatency": {
  "additionalProperties": true,
  "properties": {
   "median": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "p90": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "mean": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "std": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "cv": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "forward_only": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "t_post": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "head_mode": {
    "anyOf": [
     {
      "type": "string"
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
   "exclusive": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "median",
   "p90",
   "mean",
   "std",
   "cv",
   "forward_only",
   "t_post",
   "head_mode",
   "protocol_hash",
   "exclusive"
  ],
  "type": "object"
 },
 "ArchiveCheckpoint": {
  "additionalProperties": true,
  "properties": {
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "path": {
    "type": "string"
   },
   "exists": {
    "anyOf": [
     {
      "type": "boolean"
     },
     {
      "type": "null"
     }
    ]
   },
   "sha256": {
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
   "tier",
   "path",
   "exists",
   "sha256"
  ],
  "type": "object"
 },
 "ArchiveProvenance": {
  "additionalProperties": true,
  "properties": {
   "cfg_path": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "yaml_path": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "jsonl_path": {
    "type": "string"
   }
  },
  "required": [
   "cfg_path",
   "yaml_path",
   "jsonl_path"
  ],
  "type": "object"
 },
 "ArchiveCandidateDetail": {
  "additionalProperties": true,
  "properties": {
   "row": {
    "$ref": "#/components/schemas/ArchiveCandidateRow"
   },
   "evaluations": {
    "items": {
     "$ref": "#/components/schemas/ArchiveEvaluation"
    },
    "type": "array"
   },
   "architecture": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveArchitecture"
     },
     {
      "type": "null"
     }
    ]
   },
   "lineage": {
    "$ref": "#/components/schemas/ArchiveCandLineage"
   },
   "latency": {
    "$ref": "#/components/schemas/ArchiveLatency"
   },
   "checkpoints": {
    "items": {
     "$ref": "#/components/schemas/ArchiveCheckpoint"
    },
    "type": "array"
   },
   "roles": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "confirm": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "provenance": {
    "$ref": "#/components/schemas/ArchiveProvenance"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "row",
   "evaluations",
   "architecture",
   "lineage",
   "latency",
   "checkpoints",
   "roles",
   "confirm",
   "provenance",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchivePareto": {
  "additionalProperties": true,
  "properties": {
   "objectives": {
    "items": {
     "$ref": "#/components/schemas/ArchiveObjective"
    },
    "type": "array"
   },
   "archive_entries": {
    "items": {
     "additionalProperties": true,
     "type": "object"
    },
    "type": "array"
   },
   "front4d_ids": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "front2d_ids": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "hv_history": {
    "items": {
     "type": "number"
    },
    "type": "array"
   },
   "runtime_archive_ids": {
    "anyOf": [
     {
      "items": {
       "type": "string"
      },
      "type": "array"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "objectives",
   "archive_entries",
   "front4d_ids",
   "front2d_ids",
   "hv_history",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveFidelity": {
  "additionalProperties": true,
  "properties": {
   "rank_correlation": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "noise": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "corr_by_head_mode": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "bootstrap_ci": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "evidence_note": {
    "type": "string"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "rank_correlation",
   "noise",
   "corr_by_head_mode",
   "bootstrap_ci",
   "evidence_note",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveConfirm": {
  "additionalProperties": true,
  "properties": {
   "final_verdict": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "seeds": {
    "anyOf": [
     {
      "items": {
       "type": "integer"
      },
      "type": "array"
     },
     {
      "type": "null"
     }
    ]
   },
   "baseline_name": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "reference": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "summary": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "verdicts": {
    "anyOf": [
     {
      "additionalProperties": true,
      "type": "object"
     },
     {
      "type": "null"
     }
    ]
   },
   "max_det_observed": {},
   "test_unlock_used": {
    "anyOf": [
     {
      "type": "boolean"
     },
     {
      "type": "null"
     }
    ]
   },
   "final_report_md": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "final_verdict",
   "seeds",
   "baseline_name",
   "reference",
   "summary",
   "verdicts",
   "max_det_observed",
   "test_unlock_used",
   "final_report_md",
   "source",
   "warnings"
  ],
  "type": "object"
 },
 "ArchiveCurvePoint": {
  "additionalProperties": true,
  "properties": {
   "generation": {
    "type": "integer"
   },
   "cum_train_time_s": {
    "type": "number"
   },
   "value": {
    "type": "number"
   },
   "candidate_id": {
    "type": "string"
   },
   "cum_train_time_s_no_cache": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "generation",
   "cum_train_time_s",
   "value",
   "candidate_id"
  ],
  "type": "object"
 },
 "ArchiveHvPoint": {
  "additionalProperties": true,
  "properties": {
   "generation": {
    "type": "integer"
   },
   "value": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   }
  },
  "required": [
   "generation",
   "value"
  ],
  "type": "object"
 },
 "ArchiveCurves": {
  "additionalProperties": true,
  "properties": {
   "best_so_far_map50_95": {
    "items": {
     "$ref": "#/components/schemas/ArchiveCurvePoint"
    },
    "type": "array"
   },
   "hypervolume": {
    "items": {
     "$ref": "#/components/schemas/ArchiveHvPoint"
    },
    "type": "array"
   }
  },
  "required": [
   "best_so_far_map50_95",
   "hypervolume"
  ],
  "type": "object"
 },
 "ArchiveFinalist": {
  "additionalProperties": true,
  "properties": {
   "candidate_id": {
    "type": "string"
   },
   "val_map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "val_map50": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "small_target_recall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "params_deploy": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "roles": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "candidate_id",
   "val_map50_95",
   "val_map50",
   "small_target_recall",
   "latency_median_ms",
   "params_deploy",
   "roles"
  ],
  "type": "object"
 },
 "ArchiveCompareBaseline": {
  "additionalProperties": true,
  "properties": {
   "name": {
    "type": "string"
   },
   "kind": {
    "type": "string"
   },
   "tier": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "map50_95": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "small_target_recall": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "latency_median_ms": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "params_deploy": {
    "anyOf": [
     {
      "type": "number"
     },
     {
      "type": "null"
     }
    ]
   },
   "is_judge": {
    "type": "boolean"
   }
  },
  "required": [
   "name",
   "kind",
   "tier",
   "map50_95",
   "small_target_recall",
   "latency_median_ms",
   "params_deploy",
   "is_judge"
  ],
  "type": "object"
 },
 "ArchiveCompareRun": {
  "additionalProperties": true,
  "properties": {
   "id": {
    "type": "string"
   },
   "family": {
    "type": "string"
   },
   "arm": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveArm"
     },
     {
      "type": "null"
     }
    ]
   },
   "dataset_id": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "judge_baseline": {
    "anyOf": [
     {
      "$ref": "#/components/schemas/ArchiveJudgeBaseline"
     },
     {
      "type": "null"
     }
    ]
   },
   "curves": {
    "$ref": "#/components/schemas/ArchiveCurves"
   },
   "finalists": {
    "items": {
     "$ref": "#/components/schemas/ArchiveFinalist"
    },
    "type": "array"
   },
   "baselines": {
    "anyOf": [
     {
      "items": {
       "$ref": "#/components/schemas/ArchiveCompareBaseline"
      },
      "type": "array"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   },
   "comparable": {
    "type": "boolean"
   },
   "incomparable_reason": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "metric_protocol_hash": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ],
    "default": null
   }
  },
  "required": [
   "id",
   "family",
   "arm",
   "dataset_id",
   "judge_baseline",
   "curves",
   "finalists",
   "comparable",
   "incomparable_reason"
  ],
  "type": "object"
 },
 "ArchiveCompare": {
  "additionalProperties": true,
  "properties": {
   "runs": {
    "items": {
     "$ref": "#/components/schemas/ArchiveCompareRun"
    },
    "type": "array"
   },
   "comparable": {
    "type": "boolean"
   },
   "incomparable_reason": {
    "anyOf": [
     {
      "type": "string"
     },
     {
      "type": "null"
     }
    ]
   },
   "notes": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "source": {
    "$ref": "#/components/schemas/ArchiveSource"
   },
   "warnings": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "runs",
   "comparable",
   "incomparable_reason",
   "notes",
   "source",
   "warnings"
  ],
  "type": "object"
 }
}''')
PATHS = json.loads(r'''{
 "/api/v1/archive/runs": {
  "get": {
   "operationId": "archiveListRuns",
   "summary": "List archive runs",
   "description": "Runs found under the configured read-only archive roots (MBNAS_ARCHIVE_ROOTS) with status, verdicts, counts, best values and the judge baseline.",
   "tags": [
    "archive"
   ],
   "parameters": [],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveRunList"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}": {
  "get": {
   "operationId": "archiveGetRun",
   "summary": "Run overview",
   "description": "Summary plus stage timeline, pre-registered gates, budget, preset, roles, final-tier validation rows, GPU-guard episodes and live state.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveRun"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/baselines": {
  "get": {
   "operationId": "archiveGetBaselines",
   "summary": "Baselines and the search anchor",
   "description": "protocol/baselines_v2.json rows (final tier) and the gen-0 anchor (search tier); every row names its tier.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveBaselineList"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/generations": {
  "get": {
   "operationId": "archiveGetGenerations",
   "summary": "Per-generation progress and characteristics",
   "description": "Generation summaries, deltas, cumulative Pareto fronts, new fingerprints, lineage modes and module histograms. No scalar fitness exists (NSGA-II).",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveGenerationList"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/generations/{g}": {
  "get": {
   "operationId": "archiveGetGeneration",
   "summary": "Generation detail",
   "description": "One generation: candidates, improvements over the earlier best, front points, lineage, characteristics.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    },
    {
     "name": "g",
     "in": "path",
     "required": true,
     "schema": {
      "type": "integer",
      "minimum": 0
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveGenerationDetail"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/candidates": {
  "get": {
   "operationId": "archiveListCandidates",
   "summary": "Candidate evaluations",
   "description": "Rows of search/candidates/candidate_evaluations.jsonl in file order, optionally filtered.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    },
    {
     "name": "generation",
     "in": "query",
     "required": false,
     "schema": {
      "type": "integer",
      "minimum": 0
     }
    },
    {
     "name": "tier",
     "in": "query",
     "required": false,
     "schema": {
      "type": "string",
      "maxLength": 64
     }
    },
    {
     "name": "status",
     "in": "query",
     "required": false,
     "schema": {
      "type": "string",
      "maxLength": 32
     }
    },
    {
     "name": "limit",
     "in": "query",
     "required": false,
     "schema": {
      "type": "integer",
      "minimum": 1,
      "maximum": 5000,
      "default": 500
     }
    },
    {
     "name": "offset",
     "in": "query",
     "required": false,
     "schema": {
      "type": "integer",
      "minimum": 0,
      "default": 0
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveCandidateList"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/candidates/{cid}": {
  "get": {
   "operationId": "archiveGetCandidate",
   "summary": "Candidate detail",
   "description": "All evaluations, architecture (yaml/cfg/stages), lineage with parent diffs, latency breakdown, checkpoints, roles, confirm.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    },
    {
     "name": "cid",
     "in": "path",
     "required": true,
     "description": "Candidate id (e.g. g003_c0060); same pattern as run_id.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveCandidateDetail"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/pareto": {
  "get": {
   "operationId": "archiveGetPareto",
   "summary": "Pareto archive",
   "description": "Runtime non-dominated archive (search/archive.json) plus the 2D/4D fronts recomputed from the candidate rows.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchivePareto"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/fidelity": {
  "get": {
   "operationId": "archiveGetFidelity",
   "summary": "Fidelity (T1/T2 rank correlation) artifacts",
   "description": "rank_correlation, noise, corr_by_head_mode, bootstrap_ci; evidence_note states when n is too small to be evidence.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveFidelity"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/confirm": {
  "get": {
   "operationId": "archiveGetConfirm",
   "summary": "3-seed confirm stage",
   "description": "three_seed/three_seed_summary.json plus final_report.md; fields are null while the stage has not produced them.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveConfirm"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/runs/{run_id}/files/{kind}": {
  "get": {
   "operationId": "archiveGetFile",
   "summary": "Registered artifact file",
   "tags": [
    "archive"
   ],
   "description": "Bytes of one registered file kind (never a path). Cache-Control: no-store; at most 8 MiB; run_log_tail is the last 200 lines with ANSI escapes removed.",
   "parameters": [
    {
     "name": "run_id",
     "in": "path",
     "required": true,
     "description": "Run directory name under an archive root; ^[A-Za-z0-9._-]{1,128}$, must not start with '.'.",
     "schema": {
      "type": "string",
      "pattern": "^[A-Za-z0-9._-]{1,128}$",
      "minLength": 1,
      "maxLength": 128
     }
    },
    {
     "name": "kind",
     "in": "path",
     "required": true,
     "schema": {
      "type": "string",
      "enum": [
       "plot_pareto_map50_latency",
       "plot_final_pareto_val",
       "plot_hypervolume",
       "plot_params_vs_map50",
       "plot_rank_correlation",
       "viz_train",
       "viz_val",
       "final_report_md",
       "run_log_tail"
      ]
     }
    }
   ],
   "responses": {
    "200": {
     "description": "File bytes",
     "content": {
      "image/png": {
       "schema": {
        "type": "string",
        "format": "binary"
       }
      },
      "text/markdown": {
       "schema": {
        "type": "string"
       }
      },
      "text/plain": {
       "schema": {
        "type": "string"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "413": {
     "description": "ARCHIVE_FILE_TOO_LARGE (> 8 MiB)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/compare": {
  "get": {
   "operationId": "archiveCompare",
   "summary": "Compare runs",
   "description": "Best-so-far mAP50-95 against cumulative train time and hypervolume per generation for 1 to 6 runs; comparable=false when dataset_id or metric protocol hash differ.",
   "tags": [
    "archive"
   ],
   "parameters": [
    {
     "name": "runs",
     "in": "query",
     "required": true,
     "description": "Comma-separated run ids (1 to 6, distinct).",
     "schema": {
      "type": "string",
      "minLength": 1,
      "maxLength": 1024
     }
    }
   ],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveCompare"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "404": {
     "description": "ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "422": {
     "description": "ARCHIVE_BAD_ID or a malformed query/path value",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 },
 "/api/v1/archive/live": {
  "get": {
   "operationId": "archiveLive",
   "summary": "Live pipeline state",
   "description": "Driver STATUS.json / pipeline.log tails, running runs (process liveness from /proc) and GPU usage from NVML (null when unavailable).",
   "tags": [
    "archive"
   ],
   "parameters": [],
   "responses": {
    "200": {
     "description": "Successful response",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/ArchiveLive"
       }
      }
     }
    },
    "401": {
     "description": "Authentication required"
    },
    "403": {
     "description": "Not authorized"
    },
    "503": {
     "description": "ARCHIVE_ROOT_UNAVAILABLE (no configured archive root exists)",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    },
    "500": {
     "description": "RESPONSE_CONTRACT_VIOLATION or internal error",
     "content": {
      "application/json": {
       "schema": {
        "$ref": "#/components/schemas/Error"
       }
      }
     }
    }
   }
  }
 }
}''')

for name, schema in COMPONENTS.items():
    assert name not in S, name
    S[name] = schema
for path, item in PATHS.items():
    assert path not in d['paths'], path
    d['paths'][path] = item
d['components']['schemas']['Health']['properties']['allowlists']['properties']['archive_roots'] = {
    'type': 'array', 'items': {'type': 'string'},
    'description': 'Read-only roots served by /archive/* (MBNAS_ARCHIVE_ROOTS); separate from output_roots, never written.'}
d['x-contract-changelog'].insert(0, {'version': '1.3.0', 'date': '2026-10-15', 'author': 'fable/sonnet 2026-10-15',
  'changes': json.loads(r'''[
 "Read-only archive API for ETRI_NAS yolo_ga_full runs: GET /archive/runs, /archive/runs/{run_id}, .../baselines, .../generations, .../generations/{g}, .../candidates, .../candidates/{cid}, .../pareto, .../fidelity, .../confirm, .../files/{kind}, GET /archive/compare, GET /archive/live (bearer, like every route).",
 "New components: Archive* (run list/run/baselines/generations/candidates/pareto/fidelity/confirm/compare/live and their parts). Numbers are copied from the run artifacts, missing values are null, derived values (fronts, deltas, histograms, curves) follow docs/ARCHIVE_API.md; no scalar fitness is offered.",
 "Error codes: ARCHIVE_RUN_NOT_FOUND / ARCHIVE_NOT_FOUND (404), ARCHIVE_BAD_ID / ARCHIVE_BAD_KIND (422), ARCHIVE_ROOT_UNAVAILABLE (503), ARCHIVE_FILE_TOO_LARGE (413).",
 "Health.allowlists.archive_roots (read-only archive roots; never part of output_roots)."
]''')})
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=1); print('contract', d['info']['version'])
