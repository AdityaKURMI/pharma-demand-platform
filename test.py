import pandas as pd, numpy as np
p = pd.read_parquet("data/modeling/predictions.parquet")
p = p[p.mase_scale > 0].copy()
p["ase"] = (p.prescriptions - p.y_pred).abs() / p.mase_scale

vol = p.groupby(["state","ingredient"])["prescriptions"].sum().rename("vol")
p = p.merge(vol.reset_index(), on=["state","ingredient"])
p["tier"] = pd.qcut(p["vol"], q=[0, .5, .9, 1], labels=["small","mid","large"])
print(p.groupby(["model","tier"])["ase"].mean().unstack().round(3))