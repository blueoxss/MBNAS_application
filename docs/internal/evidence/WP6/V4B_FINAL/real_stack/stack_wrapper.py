"""C07 evaluator wrapper around scripts/dev/web_real_stack.py (unchanged): only MBNAS_HOME is redirected from
<ROOT>/runtime to <stack root>/mbnas_home so that no default path of any started process writes into the project tree."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path("<ROOT>")
spec = importlib.util.spec_from_file_location("web_real_stack", ROOT / "scripts" / "dev" / "web_real_stack.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class EvalStack(m.Stack):
    def base_env(self):
        env = super().base_env()
        env["MBNAS_HOME"] = str(Path(self.root) / "mbnas_home")
        return env


m.Stack = EvalStack
sys.exit(m.main())
