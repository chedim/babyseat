//! #1025: the sentence vectors read in place. The store's append-only float16 file (`sentences.f16`) is
//! memory-mapped in fixed, page-aligned chunks. With the `metal` feature and `MEMBRANE_GPU_TOPK=1`, each full
//! chunk is handed to Metal as a no-copy buffer (`newBufferWithBytesNoCopy`), so the GPU reads the same pages the
//! store already maps: no second copy and no upload. Rows past the last full chunk (the file's growing tail) are
//! scored on the CPU from the file. Without a GPU, the CPU scores the mapped chunks the same way.
//!
//! Safety of the mapping: the file only grows (appends). A reset unlinks it and writes a new file, which never
//! shrinks the inode we map, and the caller drops this store on a reset (`_Matrix.reset`). A chunk is mapped only
//! once the file holds all of its rows, so no mapped page is ever past the end of the file.
use std::fs::File;
use std::os::unix::fs::FileExt;
use std::os::unix::io::AsRawFd;
use std::path::PathBuf;

/// Rows per chunk: 8192 rows × 1024 dims × 2 bytes = 16 MiB, a multiple of every page size (4 KiB, 16 KiB).
pub const CHUNK_ROWS: usize = 8192;

pub fn f16_to_f32(h: u16) -> f32 {
    let s = ((h >> 15) & 1) as u32;
    let e = ((h >> 10) & 0x1f) as i32;
    let m = (h & 0x3ff) as u32;
    let bits = if e == 0 {
        if m == 0 { s << 31 } else {
            // subnormal: normalise the mantissa
            let (mut e2, mut m2) = (127 - 15 + 1, m);
            while m2 & 0x400 == 0 { m2 <<= 1; e2 -= 1; }
            (s << 31) | ((e2 as u32) << 23) | ((m2 & 0x3ff) << 13)
        }
    } else if e == 31 {
        (s << 31) | (0xff << 23) | (m << 13)
    } else {
        (s << 31) | (((e + 127 - 15) as u32) << 23) | (m << 13)
    };
    f32::from_bits(bits)
}

fn cosine_f16(row: &[u16], q: &[f32], qn: f32) -> f32 {
    let (mut d, mut s) = (0f32, 0f32);
    for (h, x) in row.iter().zip(q) { let v = f16_to_f32(*h); d += v * x; s += v * v; }
    d / (s.sqrt().max(1e-12) * qn)
}

struct Chunk {
    ptr: *mut libc::c_void,
    len: usize,
    #[cfg(feature = "metal")]
    buf: Option<objc2::rc::Retained<objc2::runtime::ProtocolObject<dyn objc2_metal::MTLBuffer>>>,
}

impl Drop for Chunk {
    fn drop(&mut self) {
        #[cfg(feature = "metal")]
        { self.buf = None; }   // the Metal buffer goes before the pages it wraps
        unsafe { libc::munmap(self.ptr, self.len); }
    }
}

impl Chunk {
    fn rows(&self) -> &[u16] { unsafe { std::slice::from_raw_parts(self.ptr as *const u16, self.len / 2) } }
}

pub struct Mapped {
    path: PathBuf,
    file: File,
    dim: usize,
    chunks: Vec<Chunk>,
    #[cfg(feature = "metal")]
    gpu: Option<gpu::Gpu>,
}

// The chunks are read-only mapped pages, and the Metal objects (device, queue, pipeline, buffers) are thread-safe
// in Metal; every query makes its own command buffer. The Python class guards this with a mutex.
unsafe impl Send for Mapped {}
unsafe impl Sync for Mapped {}

impl Mapped {
    pub fn open(path: &str, dim: usize, want_gpu: bool) -> std::io::Result<Mapped> {
        let file = File::open(path)?;
        let _ = want_gpu;
        Ok(Mapped { path: PathBuf::from(path), file, dim, chunks: vec![],
            #[cfg(feature = "metal")] gpu: if want_gpu && dim % 4 == 0 { gpu::Gpu::new() } else { None } })
    }

    pub fn on_gpu(&self) -> bool {
        #[cfg(feature = "metal")] { return self.gpu.is_some(); }
        #[allow(unreachable_code)] false
    }

    pub fn chunks(&self) -> usize { self.chunks.len() }

    /// Map every chunk the first `n` rows fill completely.
    fn seal(&mut self, n: usize) -> std::io::Result<()> {
        let bytes = CHUNK_ROWS * self.dim * 2;
        while (self.chunks.len() + 1) * CHUNK_ROWS <= n {
            let off = (self.chunks.len() * bytes) as libc::off_t;
            let ptr = unsafe { libc::mmap(std::ptr::null_mut(), bytes, libc::PROT_READ, libc::MAP_SHARED, self.file.as_raw_fd(), off) };
            if ptr == libc::MAP_FAILED { return Err(std::io::Error::last_os_error()); }
            #[allow(unused_mut)]
            let mut c = Chunk { ptr, len: bytes, #[cfg(feature = "metal")] buf: None };
            #[cfg(feature = "metal")]
            if let Some(g) = &self.gpu { c.buf = g.wrap(ptr, bytes); if c.buf.is_none() { self.gpu = None; } }
            self.chunks.push(c);
        }
        Ok(())
    }

    /// Cosine of every one of the first `n` rows against `q` (row order).
    pub fn scores(&mut self, q: &[f32], n: usize) -> std::io::Result<Vec<f32>> {
        if q.len() != self.dim { return Err(std::io::Error::new(std::io::ErrorKind::InvalidInput, "query width")); }
        let have = self.file.metadata()?.len() as usize / (self.dim * 2);
        let n = n.min(have);
        self.seal(n)?;
        let qn = q.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
        let sealed = self.chunks.len() * CHUNK_ROWS;
        #[allow(unused_mut)]
        let mut out: Option<Vec<f32>> = None;
        #[cfg(feature = "metal")]
        if let Some(g) = &self.gpu {
            // the full chunks as they are mapped; the tail read once into a small buffer, scored in the same pass
            let mut parts: Vec<(objc2::rc::Retained<objc2::runtime::ProtocolObject<dyn objc2_metal::MTLBuffer>>, usize)> =
                self.chunks.iter().filter_map(|c| c.buf.clone().map(|b| (b, CHUNK_ROWS))).collect();
            if parts.len() == self.chunks.len() {
                let tail = n.saturating_sub(sealed);
                let mut ok = true;
                if tail > 0 {
                    match g.read_into(&self.file, (sealed * self.dim * 2) as u64, tail * self.dim * 2) {
                        Some(b) => parts.push((b, tail)),
                        None => ok = false,
                    }
                }
                if ok { if let Some(mut v) = g.scores(&parts, self.dim, q, qn) { v.truncate(n); return Ok(v); } }
            }
        }
        #[allow(unused_mut)]
        let mut out = match out {
            Some(v) => v,
            None => {
                let mut v = Vec::with_capacity(n);
                for c in &self.chunks { for r in c.rows().chunks_exact(self.dim) { v.push(cosine_f16(r, q, qn)); } }
                v
            }
        };
        // the tail: rows past the last full chunk, read from the file
        if n > sealed {
            let mut buf = vec![0u8; (n - sealed) * self.dim * 2];
            self.file.read_exact_at(&mut buf, (sealed * self.dim * 2) as u64)?;
            let tail: Vec<u16> = buf.chunks_exact(2).map(|b| u16::from_le_bytes([b[0], b[1]])).collect();
            for r in tail.chunks_exact(self.dim) { out.push(cosine_f16(r, q, qn)); }
        }
        out.truncate(n);
        Ok(out)
    }

    /// The top `k` rows: [(row, cosine)], best first, ties by row.
    pub fn near(&mut self, q: &[f32], k: usize, n: usize) -> std::io::Result<Vec<(usize, f32)>> {
        let sc = self.scores(q, n)?;
        Ok(crate::topk::top_k(&sc, k).into_iter().map(|i| (i, sc[i])).collect())
    }

    pub fn path(&self) -> &std::path::Path { &self.path }
}

#[cfg(feature = "metal")]
mod gpu {
    use objc2::rc::Retained;
    use objc2::runtime::ProtocolObject;
    use objc2_foundation::NSString;
    use objc2_metal::{MTLBuffer, MTLCommandBuffer, MTLCommandEncoder, MTLCommandQueue, MTLComputeCommandEncoder,
        MTLComputePipelineState, MTLCreateSystemDefaultDevice, MTLDevice, MTLLibrary, MTLResourceOptions, MTLSize};
    use std::ptr::NonNull;

    const MSL: &str = r#"
#include <metal_stdlib>
using namespace metal;
struct P { uint n; uint dim; float qn; uint out_off; };
kernel void cosine(device const half4* rows [[buffer(0)]], device const float4* q [[buffer(1)]],
                   device float* out [[buffer(2)]], constant P& p [[buffer(3)]],
                   uint gid [[thread_position_in_grid]], uint lane [[thread_index_in_simdgroup]]) {
  // one 32-wide SIMD group per row: neighbouring lanes read neighbouring memory
  uint i = gid / 32;
  if (i >= p.n) return;
  uint w = p.dim / 4, base = i * w;
  float4 d = 0.0f, s = 0.0f;
  for (uint j = lane; j < w; j += 32) { float4 v = float4(rows[base + j]); d += v * q[j]; s += v * v; }
  float dd = simd_sum(d.x + d.y + d.z + d.w), ss = simd_sum(s.x + s.y + s.z + s.w);
  if (lane == 0) out[p.out_off + i] = dd / (max(sqrt(ss), 1e-12f) * p.qn);
}
"#;

    #[repr(C)]
    struct P { n: u32, dim: u32, qn: f32, out_off: u32 }

    pub struct Gpu {
        dev: Retained<ProtocolObject<dyn MTLDevice>>,
        queue: Retained<ProtocolObject<dyn MTLCommandQueue>>,
        pipe: Retained<ProtocolObject<dyn MTLComputePipelineState>>,
    }

    impl Gpu {
        pub fn new() -> Option<Gpu> {
            let dev = MTLCreateSystemDefaultDevice()?;
            let lib = dev.newLibraryWithSource_options_error(&NSString::from_str(MSL), None).ok()?;
            let f = lib.newFunctionWithName(&NSString::from_str("cosine"))?;
            let pipe = dev.newComputePipelineStateWithFunction_error(&f).ok()?;
            let queue = dev.newCommandQueue()?;
            Some(Gpu { dev, queue, pipe })
        }

        /// A no-copy Metal buffer over mapped pages (page-aligned pointer and length).
        pub fn wrap(&self, ptr: *mut libc::c_void, len: usize) -> Option<Retained<ProtocolObject<dyn MTLBuffer>>> {
            let p = NonNull::new(ptr)?;
            unsafe { self.dev.newBufferWithBytesNoCopy_length_options_deallocator(p, len, MTLResourceOptions::StorageModeShared, None) }
        }

        /// A shared buffer holding `len` bytes of `file` from `off` (the growing tail; read, not mapped).
        pub fn read_into(&self, file: &std::fs::File, off: u64, len: usize) -> Option<Retained<ProtocolObject<dyn MTLBuffer>>> {
            use std::os::unix::fs::FileExt;
            let b = self.dev.newBufferWithLength_options(len.max(4), MTLResourceOptions::StorageModeShared)?;
            let dst = unsafe { std::slice::from_raw_parts_mut(b.contents().as_ptr() as *mut u8, len) };
            file.read_exact_at(dst, off).ok()?;
            Some(b)
        }

        pub fn scores(&self, parts: &[(Retained<ProtocolObject<dyn MTLBuffer>>, usize)], dim: usize, q: &[f32], qn: f32) -> Option<Vec<f32>> {
            let total: usize = parts.iter().map(|p| p.1).sum();
            if total == 0 { return Some(vec![]); }
            let qb = self.dev.newBufferWithLength_options(q.len() * 4, MTLResourceOptions::StorageModeShared)?;
            unsafe { std::ptr::copy_nonoverlapping(q.as_ptr(), qb.contents().as_ptr() as *mut f32, q.len()); }
            let ob = self.dev.newBufferWithLength_options(total * 4, MTLResourceOptions::StorageModeShared)?;
            let cb = self.queue.commandBuffer()?;
            let enc = cb.computeCommandEncoder()?;
            enc.setComputePipelineState(&self.pipe);
            let mut off = 0usize;
            for (b, rows) in parts {
                let rows = *rows;
                let p = P { n: rows as u32, dim: dim as u32, qn, out_off: off as u32 };
                off += rows;
                unsafe {
                    enc.setBuffer_offset_atIndex(Some(b), 0, 0);
                    enc.setBuffer_offset_atIndex(Some(&qb), 0, 1);
                    enc.setBuffer_offset_atIndex(Some(&ob), 0, 2);
                    enc.setBytes_length_atIndex(NonNull::new(&p as *const P as *mut std::ffi::c_void)?, std::mem::size_of::<P>(), 3);
                }
                // 32 lanes per row, 8 rows per threadgroup; the kernel skips lanes past the last row
                let tg = 256usize;
                enc.dispatchThreadgroups_threadsPerThreadgroup(
                    MTLSize { width: (rows * 32 + tg - 1) / tg, height: 1, depth: 1 },
                    MTLSize { width: tg, height: 1, depth: 1 });
            }
            enc.endEncoding();
            cb.commit();
            cb.waitUntilCompleted();
            let out = unsafe { std::slice::from_raw_parts(ob.contents().as_ptr() as *const f32, total) }.to_vec();
            Some(out)
        }
    }
}
