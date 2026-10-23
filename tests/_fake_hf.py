# Stand-in torch/transformers for tests: mimics the real classifier interface (labels "ai"/"hum").
import sys, types, numpy as np, os
P_AI = float(os.environ.get("FAKE_P_AI", "0.05"))
class T:
    def __init__(s, a): s.a = np.asarray(a, float)
    def __getitem__(s, k): return T(s.a[k])
    def tolist(s): return s.a.tolist()
torch = types.ModuleType("torch")
class _NG:
    def __enter__(s): pass
    def __exit__(s, *a): pass
torch.no_grad = lambda: _NG()
torch.softmax = lambda x, dim=-1: T(np.exp(x.a) / np.exp(x.a).sum(axis=dim, keepdims=True))
tr = types.ModuleType("transformers")
class Proc:
    @classmethod
    def from_pretrained(c, *a, **k): return c()
    def __call__(s, images, return_tensors): return {"n": len(images)}
class Cfg: id2label = {0: "ai", 1: "hum"}
class Model:
    config = Cfg()
    @classmethod
    def from_pretrained(c, *a, **k): return c()
    def eval(s): return s
    def __call__(s, n):
        l = np.log(P_AI / (1 - P_AI))
        return types.SimpleNamespace(logits=T([[l, 0.0]] * n))
tr.AutoImageProcessor = Proc; tr.AutoModelForImageClassification = Model
sys.modules["torch"] = torch; sys.modules["transformers"] = tr
