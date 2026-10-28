"""Negative control (scratch): re-install the ORIGINAL check_user_model body (before the D1 fix) into the live module."""
import ast

import mbnas_core.console.model_services as ms

ORIG = "<scratchpad>/fix5/orig/mbnas_core_src_mbnas_core_console_model_services.py (byte copy of model_services.py before this fix, sha256 24ba7965...)"
tree = ast.parse(open(ORIG, encoding="utf-8").read())
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_user_model")
exec(compile(ast.Module(body=[fn], type_ignores=[]), ORIG, "exec"), ms.__dict__)
print("NEGATIVE CONTROL: original check_user_model installed", flush=True)
