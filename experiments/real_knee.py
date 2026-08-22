"""Real-data knee check: does the novelty filter's knee survive on actual
mxbai-embed-large embeddings of real code, or is the bimodal/valley a synthetic
artifact?

The honest test. A field of real Java Couchbase-SDK snippets (what Membrane
"solved"). A workload of: more Couchbase (routine noise), other Java libs
(borderline -- same language/domain, off the specific manifold), and
Python/Go/SQL/infra (clearly novel). Embed all with the local Ollama model,
compute each workload query's novelty off the field, and look for a findable
knee in the real distribution.

If the valley exists here, the filter is real on actual embeddings -- the whole
value proposition holds. If the distribution is a shapeless blob with no valley,
the synthetic result was an artifact and the design is a castle on sand.

Run:  PYTHONPATH=. python experiments/real_knee.py   (needs local Ollama + mxbai-embed-large)
"""

from __future__ import annotations

import numpy as np

from membrane.field import Exemplar, ExemplarStore
from membrane.variety import novelty, effective_rank, spread
from membrane.embed import OllamaEmbedder
from membrane.knee import valley_knee, otsu_threshold, ascii_hist, confusion

# ---- the field: Java Couchbase SDK patterns Membrane has "solved" ----
FIELD = [
    'Cluster cluster = Cluster.connect(host, username, password); Bucket bucket = cluster.bucket("travel");',
    'Collection collection = bucket.defaultCollection(); collection.upsert("doc1", JsonObject.create().put("name","alice"));',
    'collection.get("doc1").contentAs(JsonObject.class);',
    'cluster.searchQuery(SearchRequest.create(MatchQuery.query("airport")));',
    'QueryResult result = cluster.query("SELECT * FROM travel WHERE type=\\"airport\\"");',
    'collection.remove("doc1");',
    'collection.exists("doc1");',
    'bucket.defaultScope().collection("inventory").insert("item", content);',
    'MutationResult mr = collection.replace("doc1", json);',
    'LookupInResult lr = collection.lookupIn("doc1", Arrays.asList(LookupInSpec.get("path")));',
    'collection.mutateIn("doc1", Arrays.asList(MutateInSpec.upsert("path", val)));',
    'QueryResult qr = cluster.query("UPDATE travel SET name=\\"x\\" WHERE id=1");',
    'bucket.collections().createCollection("scope","coll");',
    'cluster.diagnostics().report();',
    'collection.getAndTouch("doc1", Duration.ofSeconds(30));',
]

# ---- routine noise: more Couchbase-Java (on-manifold) ----
NOISE = [
    'Cluster.connect("localhost","admin","secret").bucket("beer").defaultCollection().upsert("k", v);',
    'collection.get("user:42").contentAs(Map.class);',
    'cluster.query("SELECT count(*) FROM `travel`");',
    'collection.upsert("key", JsonObject.empty().put("a", 1));',
    'collection.replace("user:7", json);',
    'bucket.defaultCollection().get("order:99");',
    'cluster.searchQuery(SearchRequest.create(MatchQuery.query("hotel")));',
    'collection.remove("stale:1");',
    'collection.mutateIn("c", Arrays.asList(MutateInSpec.insert("p", 3)));',
    'cluster.query("INSERT INTO travel VALUES (\"k\", {});");',
    'bucket.defaultScope().collection("inv").upsert("i", doc);',
    'collection.exists("k");',
    'LookupInResult r = collection.lookupIn("d", Arrays.asList(LookupInSpec.exists("f")));',
    'cluster.bucket("default").defaultCollection().upsert("x", 1);',
    'QueryResult q = cluster.query("SELECT meta().id FROM `travel`");',
]

# ---- borderline: Java, but OTHER libraries/patterns (off the specific manifold) ----
BORDERLINE = [
    'MongoClient mongo = MongoClients.create("mongodb://localhost"); MongoDatabase db = mongo.getDatabase("test"); db.getCollection("c").insertOne(doc);',
    'JdbcTemplate jdbc = new JdbcTemplate(ds); jdbc.queryForList("SELECT * FROM users");',
    'HttpClient client = HttpClient.newHttpClient(); client.send(HttpRequest.newBuilder(URI.create(url)).build(), BodyHandlers.ofString());',
    'RestTemplate rest = new RestTemplate(); rest.getForObject("/api/x", String.class);',
    'EntityManager em = emf.createEntityManager(); em.createQuery("SELECT u FROM User u").getResultList();',
    'WebClient.builder().baseUrl(url).build().get().retrieve().bodyToMono(String.class).subscribe();',
    'Jedis redis = new Jedis("localhost"); redis.set("key","value"); String v = redis.get("key");',
    'PreparedStatement ps = conn.prepareStatement("INSERT INTO t VALUES(?)"); ps.setString(1, "x"); ps.executeUpdate();',
    'KafkaProducer<String,String> p = new KafkaProducer<>(props); p.send(new ProducerRecord("topic","value"));',
    'AmazonS3 s3 = AmazonS3ClientBuilder.defaultClient(); s3.putObject("bucket","key",file);',
]

# ---- clearly novel: non-Java / infra (off-manifold by domain) ----
NOVEL = [
    'import pandas as pd; df = pd.read_csv("data.csv"); df.groupby("city").mean()',
    'import requests; r = requests.get("https://api.example.com"); r.json()',
    'resp, err := http.Get("https://example.com"); defer resp.Body.Close()',
    'SELECT name, COUNT(*) FROM orders GROUP BY name HAVING COUNT(*) > 5;',
    'def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)',
    "const app = express(); app.get('/', (req,res)=>res.send('hi')); app.listen(3000);",
    'df = spark.read.parquet("s3://bucket/path/"); df.filter(col("x")>0).count()',
    'async function fetch(){ const r = await axios.get(url); return r.data; }',
    'cat log.txt | grep ERROR | wc -l',
    'git rebase -i HEAD~3; git push --force-with-lease',
    'kubectl apply -f deployment.yaml; kubectl rollout status deployment/x',
    'docker build -t app . && docker run -p 8080:8080 app',
]


def main() -> None:
    emb = OllamaEmbedder()
    print(f"embedding {len(FIELD)+len(NOISE)+len(BORDERLINE)+len(NOVEL)} real code snippets "
          f"via {emb.model} ...")
    # embed field and workload in two batches
    fvecs = emb.embed(FIELD)
    wtexts = NOISE + BORDERLINE + NOVEL
    wlabels = (["noise"] * len(NOISE)) + (["signal"] * (len(BORDERLINE) + len(NOVEL)))
    wvecs = emb.embed(wtexts)
    print(f"embedding dim = {fvecs.shape[1]}\n")

    # build the field store (what Membrane has solved)
    store = ExemplarStore()
    for v in fvecs:
        store.append(Exemplar("couchbase-java", "actA", "ok", v.tolist(), safe=True))
    print(f"field: {len(store)} exemplars  effective_rank={effective_rank(store):.2f}  spread={spread(store):.3f}\n")

    # novelty of each workload query off the field
    novs = np.array([novelty(v, store) for v in wvecs])

    print("== per-class novelty off the real-embedding field (min / median / max) ==")
    for lab in ("noise", "signal"):
        a = novs[[i for i, l in enumerate(wlabels) if l == lab]]
        print(f"    {lab:7s} {a.min():7.2f} / {np.median(a):7.2f} / {a.max():7.2f}")
    # also split signal into borderline vs novel for the read
    b = novs[:0]
    for tag, lo, hi in (("borderline", len(NOISE), len(NOISE) + len(BORDERLINE)),
                       ("novel    ", len(NOISE) + len(BORDERLINE), len(wtexts))):
        a = novs[lo:hi]
        print(f"      {tag} {a.min():7.2f} / {np.median(a):7.2f} / {a.max():7.2f}")
    print()

    knee = valley_knee(novs)
    otsu = otsu_threshold(novs)
    print("== novelty histogram over the real workload ==")
    print(ascii_hist(novs, {"valley (FIRST, correct)": knee, "Otsu (BIGGEST gap, trap)": otsu}))
    print(f"\n  valley knee = {knee:.2f}   otsu = {otsu:.2f}\n")

    print("== filter at the valley (conservative anchor) ==")
    c = confusion(novs, wlabels, knee)
    for k, v in c.items(): print(f"  {k:30s} {v}")
    print()
    print("== risk-scaled: hold valley (risky) vs loosen into borderline (safe) ==")
    for factor, tag in [(1.0, "risky (valley)"), (1.5, "safe  (loosen)")]:
        c = confusion(novs, wlabels, knee * factor)
        print(f"  cutoff={c['cutoff']:.2f} [{tag}]  "
              f"absorb_signal(FN/safety)={c['absorb_signal (FN/safety)']:14s}  "
              f"pass_noise(FP/efficiency)={c['pass_noise (FP/efficiency)']}")

    # ---- localize the failure: metric (novelty) vs field/embeddings ----
    # plain nearest-cosine: max cosine of each workload query to any field exemplar.
    # If cosine separates (noise high sim, novel low sim) where novelty didn't, the
    # null-space-residual metric is the problem. If cosine also overlaps, the field
    # is too small/broad for mxbai to separate at all.
    print("\n== diagnostic: plain nearest-cosine to the field (does it separate?) ==")
    Fn = fvecs / (np.linalg.norm(fvecs, axis=1, keepdims=True) + 1e-12)
    Wn = wvecs / (np.linalg.norm(wvecs, axis=1, keepdims=True) + 1e-12)
    maxsim = (Wn @ Fn.T).max(axis=1)          # higher = more routine
    for lab in ("noise", "signal"):
        a = maxsim[[i for i, l in enumerate(wlabels) if l == lab]]
        print(f"    {lab:7s} max-cos (min / median / max): {a.min():.3f} / {np.median(a):.3f} / {a.max():.3f}")
    for tag, lo, hi in (("borderline", len(NOISE), len(NOISE) + len(BORDERLINE)),
                       ("novel     ", len(NOISE) + len(BORDERLINE), len(wtexts))):
        a = maxsim[lo:hi]
        print(f"      {tag} max-cos (min / median / max): {a.min():.3f} / {np.median(a):.3f} / {a.max():.3f}")
    # cosine filter: escalate if sim < cos_cut (low sim = novel). cut = midpoint of
    # noise-median and novel-median.
    n_med = np.median(maxsim[:len(NOISE)])
    v_med = np.median(maxsim[len(NOISE) + len(BORDERLINE):])
    cos_cut = (n_med + v_med) / 2
    n_sig = sum(1 for l in wlabels if l == "signal")
    n_noi = sum(1 for l in wlabels if l == "noise")
    fn = sum(1 for s, l in zip(maxsim, wlabels) if l == "signal" and s >= cos_cut)
    fp = sum(1 for s, l in zip(maxsim, wlabels) if l == "noise" and s < cos_cut)
    print(f"\n  cosine filter at cut={cos_cut:.3f} (escalate if sim < cut):")
    print(f"    absorb_signal (FN/safety)    = {fn}/{n_sig} = {fn/max(n_sig,1):.0%}")
    print(f"    pass_noise    (FP/efficiency)= {fp}/{n_noi} = {fp/max(n_noi,1):.0%}")
    print("\n  (if cosine separates and novelty didn't -> the null-space metric is the\n"
          "   problem on dense embeddings; if cosine also overlaps -> the field is too\n"
          "   small/broad for mxbai to separate routine from novel.)")


if __name__ == "__main__":
    main()