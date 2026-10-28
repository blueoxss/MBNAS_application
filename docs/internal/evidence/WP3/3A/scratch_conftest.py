import os, sys
_ME = __file__
R = "/home/mgjeong/Desktop/etri_hd/mbnas_application/tests"
sys.path.append(R + "/_shared")
for _f in (R + "/owner/conftest.py", R + "/owner/api/conftest.py"):
    globals()["__file__"] = _f
    exec(compile(open(_f).read(), _f, "exec"), globals())
globals()["__file__"] = _ME
