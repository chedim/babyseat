//! #1025: the sentence vectors read in place (a mapped float16 file; Metal no-copy buffers or the CPU).
use membrane_vm::mapped::{f16_to_f32, Mapped, CHUNK_ROWS};
use std::io::Write;

const DIM: usize = 1024;

fn f32_to_f16(x: f32) -> u16 {           // round-to-nearest for normal values in range (test data is unit vectors)
    let b = x.to_bits();
    let s = ((b >> 16) & 0x8000) as u16;
    let e = ((b >> 23) & 0xff) as i32 - 127 + 15;
    if e <= 0 { return s; }
    let m = (b & 0x7fffff) + 0x1000;
    if m & 0x800000 != 0 { return s | (((e + 1) as u16) << 10); }
    s | ((e as u16) << 10) | ((m >> 13) as u16)
}

fn rows(n: usize, seed: u64) -> Vec<u16> {
    let mut x = seed;
    let mut next = || { x ^= x << 13; x ^= x >> 7; x ^= x << 17; ((x >> 11) as f64 / (1u64 << 53) as f64) as f32 - 0.5 };
    let mut out = Vec::with_capacity(n * DIM);
    for _ in 0..n {
        let v: Vec<f32> = (0..DIM).map(|_| next()).collect();
        let norm = v.iter().map(|a| a * a).sum::<f32>().sqrt();
        out.extend(v.iter().map(|a| f32_to_f16(a / norm)));
    }
    out
}

fn append(path: &std::path::Path, r: &[u16]) {
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(path).unwrap();
    let bytes: Vec<u8> = r.iter().flat_map(|h| h.to_le_bytes()).collect();
    f.write_all(&bytes).unwrap();
}

fn reference(all: &[u16], q: &[f32]) -> Vec<f32> {
    let f: Vec<f32> = all.iter().map(|h| f16_to_f32(*h)).collect();
    membrane_vm::topk::scores_cpu(&f, DIM, q)
}

#[test]
fn f16_round_trip() {
    for x in [0.0f32, 1.0, -0.5, 0.03125, 0.0001, 0.7071] { assert!((f16_to_f32(f32_to_f16(x)) - x).abs() <= x.abs() * 1e-3 + 1e-4, "{}", x); }
}

#[test]
fn mapped_scores_equal_the_reference_on_cpu_and_gpu_and_follow_appends() {
    let dir = std::env::temp_dir().join(format!("mapped1025-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("sentences.f16");
    let _ = std::fs::remove_file(&path);
    let n1 = 2 * CHUNK_ROWS + 37;                     // two full chunks and a tail
    let mut all = rows(n1, 7);
    append(&path, &all);
    let q: Vec<f32> = { let r = rows(1, 99); r.iter().map(|h| f16_to_f32(*h)).collect() };
    for gpu in [false, true] {
        let mut m = Mapped::open(path.to_str().unwrap(), DIM, gpu).unwrap();
        let s = m.scores(&q, n1).unwrap();
        let want = reference(&all, &q);
        assert_eq!(s.len(), n1);
        let worst = s.iter().zip(&want).map(|(a, b)| (a - b).abs()).fold(0f32, f32::max);
        assert!(worst < 1e-4, "gpu={} on_gpu={} worst {}", gpu, m.on_gpu(), worst);
        assert_eq!(m.chunks(), 2);
        let top = m.near(&q, 20, n1).unwrap();
        let ref_top = membrane_vm::topk::top_k(&want, 20);
        assert_eq!(top.iter().map(|t| t.0).collect::<Vec<_>>(), ref_top, "gpu={}", gpu);
        println!("gpu requested {} -> on_gpu {}", gpu, m.on_gpu());
    }
    // the file grows: a third chunk fills, and a new tail starts; asking for fewer rows than the file has works
    let more = rows(CHUNK_ROWS + 5, 11);
    append(&path, &more);
    all.extend_from_slice(&more);
    let n2 = all.len() / DIM;
    let mut m = Mapped::open(path.to_str().unwrap(), DIM, true).unwrap();
    let _ = m.scores(&q, n1).unwrap();
    assert_eq!(m.chunks(), 2);
    let s = m.scores(&q, n2).unwrap();
    assert_eq!(m.chunks(), 3);
    let want = reference(&all, &q);
    assert!(s.iter().zip(&want).all(|(a, b)| (a - b).abs() < 1e-4));
    // asking past the end of the file is clamped to what's there
    assert_eq!(m.scores(&q, n2 + 100).unwrap().len(), n2);
    std::fs::remove_dir_all(&dir).ok();
}
