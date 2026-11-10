//! #1001 k2: GPU top-k ids == CPU top-k ids (ties tolerant: compare by score within eps), plus a 221k x 1024 timed run.
//! Run: cargo test --release --features metal --test topk_gpu -- --nocapture --test-threads=1 (heavy one is #[ignore]).
use membrane_vm::topk::*;

struct Rng(u64);
impl Rng { fn f(&mut self) -> f32 { self.0 ^= self.0 << 13; self.0 ^= self.0 >> 7; self.0 ^= self.0 << 17; ((self.0 >> 40) as f32 / 16777216.0) * 2.0 - 1.0 } }

fn same(cpu_s: &[f32], a: &[usize], b: &[usize]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x == y || (cpu_s[*x] - cpu_s[*y]).abs() < 1e-4)
}
fn rss_mb() -> f64 {
    let o = std::process::Command::new("ps").args(["-o", "rss=", "-p", &std::process::id().to_string()]).output().unwrap();
    String::from_utf8_lossy(&o.stdout).trim().parse::<f64>().unwrap_or(0.0) / 1024.0
}

#[test]
fn on_by_default() { assert!(gpu_enabled() || std::env::var("MEMBRANE_GPU_TOPK").map(|v| v.trim() == "0").unwrap_or(false)); }

#[test]
fn cpu_topk_sane() {
    let rows = vec![1.0, 0.0, 0.0, 1.0, 0.7, 0.7];
    assert_eq!(topk_cpu(&rows, 2, &[1.0, 0.0], 3), vec![0, 2, 1]);
}

#[cfg(feature = "metal")]
fn check(n: usize, dim: usize, k: usize, seed: u64) -> (f64, f64) {
    let mut r = Rng(seed);
    let rows: Vec<f32> = (0..n * dim).map(|_| r.f()).collect();
    let q: Vec<f32> = (0..dim).map(|_| r.f()).collect();
    let t = std::time::Instant::now();
    let cs = scores_cpu(&rows, dim, &q); let c = top_k(&cs, k);
    let tc = t.elapsed().as_secs_f64();
    if !gpu::adapter_available() { eprintln!("no adapter: CPU fallback only"); return (tc, 0.0); }
    let t = std::time::Instant::now();
    let g = top_k(&gpu::scores_gpu(&rows, dim, &q).expect("gpu"), k);
    let tg = t.elapsed().as_secs_f64();
    assert!(same(&cs, &c, &g), "gpu {:?} != cpu {:?}", g, c);
    eprintln!("n={n} dim={dim} k={k}: cpu {tc:.3}s gpu {tg:.3}s (incl. upload+device init) rss {:.0} MB", rss_mb());
    (tc, tg)
}

#[cfg(feature = "metal")]
#[test]
fn random_small() { for (i, n) in [1usize, 7, 100, 5000].iter().enumerate() { check(*n, 64, 10, 88172645463325252 + i as u64); } }

#[cfg(feature = "metal")]
#[test]
#[ignore]
fn heavy_221k_x_1024() { check(221_000, 1024, 20, 0x9E3779B97F4A7C15); }
