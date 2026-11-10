//! #1001 k2: resident vector store + cached `near` getter.
use membrane_vm::topk::*;

#[test]
fn near_cached_until_query_or_set_changes() {
    let mut s = VecStore::new(2);
    s.add("a", &[1.0, 0.0]); s.add("b", &[0.0, 1.0]); s.add("c", &[0.7, 0.7]);
    let r = s.near(&[1.0, 0.0], 2);
    assert_eq!(r.iter().map(|x| x.0.as_str()).collect::<Vec<_>>(), vec!["a", "c"]);
    assert_eq!(s.computes(), 1);
    s.near(&[1.0, 0.0], 2);
    assert_eq!(s.computes(), 1, "same query, same set: cached");
    s.near(&[0.0, 1.0], 2);
    assert_eq!(s.computes(), 2, "query changed: recompute");
    s.add("d", &[0.0, 0.9]);
    let r = s.near(&[0.0, 1.0], 1);
    assert_eq!(s.computes(), 3, "set changed: recompute");
    assert_eq!(r[0].0, "b");
}

#[test]
fn append_matches_fresh_store() {
    let mut s = VecStore::new(3);
    let mut all = vec![];
    for i in 0..50u32 { let v = [(i as f32).sin(), (i as f32 * 1.3).cos(), i as f32 / 50.0]; s.add(&format!("s{i}"), &v); all.extend_from_slice(&v); }
    let q = [0.3, 0.9, 0.1];
    let want: Vec<String> = topk_cpu(&all, 3, &q, 5).into_iter().map(|i| format!("s{i}")).collect();
    let got: Vec<String> = s.near(&q, 5).into_iter().map(|x| x.0).collect();
    assert_eq!(got, want);
}

#[cfg(feature = "metal")]
#[test]
fn resident_gpu_matches_cpu_with_appends() {
    let mut s = VecStore::with_gpu(4, true);
    let mut all = vec![];
    for i in 0..300u32 { let v = [(i as f32).sin(), (i as f32 * 0.7).cos(), (i % 7) as f32, 1.0 + i as f32 / 300.0]; s.add(&format!("s{i}"), &v); all.extend_from_slice(&v);
        if i == 100 { s.near(&[1.0, 0.0, 0.0, 0.0], 3); } }
    if !s.on_gpu() { eprintln!("no adapter: cpu fallback"); }
    let q = [0.2, 0.5, 0.3, 0.9];
    let want: Vec<String> = topk_cpu(&all, 4, &q, 8).into_iter().map(|i| format!("s{i}")).collect();
    let got: Vec<String> = s.near(&q, 8).into_iter().map(|x| x.0).collect();
    assert_eq!(got, want);
}
