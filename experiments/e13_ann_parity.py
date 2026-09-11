import json, urllib.request
import numpy as np
auth = "Basic " + __import__("base64").b64encode(
    b"Administrator:password").decode()
stmt = ("SELECT RAW e.vector_data FROM `cortex-core`.`_default`.`entities` e "
        "WHERE ARRAY_LENGTH(e.vector_data) = 1024 LIMIT 200")
body = json.dumps({"statement": stmt}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8093/query/service", data=body, method="POST",
    headers={"Content-Type": "application/json", "Authorization": auth})
with urllib.request.urlopen(req, timeout=60) as r:
    V = np.asarray(json.loads(r.read())["results"], dtype=float)
probe = V[np.random.RandomState(7).randint(0, len(V))]
sims = V @ (probe / (np.linalg.norm(probe) + 1e-12))
ram_top1 = int(np.argmax(sims))
print("RAM top1 idx:", ram_top1, "sim:", round(float(sims[ram_top1]), 4))
b = json.dumps({"top_n": 5, "knn": [{"field": "vector_data",
                                     "vector": probe.tolist(), "k": 5}]}).encode()
q = urllib.request.Request(
    "http://127.0.0.1:8094/api/index/membrane_entities_vec/query",
    data=b, method="POST",
    headers={"Content-Type": "application/json", "Authorization": auth})
with urllib.request.urlopen(q, timeout=30) as r:
    d = json.loads(r.read())
ann = d.get("hits", [])
print("ANN top ids:", [h.get("id") for h in ann[:2]])
# fetch the ANN top-1's vec and compare sim
if ann:
    stmt2 = (f'SELECT RAW e.vector_data FROM `cortex-core`.`_default`.`entities` e '
             f'WHERE META(e).id = {json.dumps(ann[0].get("id"))}')
    b2 = json.dumps({"statement": stmt2}).encode()
    q2 = urllib.request.Request(
        "http://127.0.0.1:8093/query/service", data=b2, method="POST",
        headers={"Content-Type": "application/json", "Authorization": auth})
    with urllib.request.urlopen(q2, timeout=30) as r2:
        ann_vec = json.loads(r2.read())["results"][0]
    a = np.asarray(ann_vec, dtype=float)
    a = a / (np.linalg.norm(a) + 1e-12)
    p = probe / (np.linalg.norm(probe) + 1e-12)
    print("ANN top1 cosine to probe:", round(float(a @ p), 4))
