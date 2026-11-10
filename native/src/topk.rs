//! #1001 k2: nearest-sentence recall top-k by cosine. CPU reference always; a wgpu (Metal) kernel behind the
//! `metal` cargo feature. Runtime kill switch `MEMBRANE_GPU_TOPK=0` (default ON); no adapter -> CPU fallback.
//! Vectors are row-major f32, `dim` wide. Cosine = dot / (|a||b|); the GPU does the dot+norm per row, the host picks top-k.

/// Cosine score of every row against the query (CPU).
pub fn scores_cpu(rows: &[f32], dim: usize, q: &[f32]) -> Vec<f32> {
    let qn = q.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
    rows.chunks_exact(dim).map(|r| {
        let (mut d, mut n) = (0f32, 0f32);
        for i in 0..dim { d += r[i] * q[i]; n += r[i] * r[i]; }
        d / (n.sqrt().max(1e-12) * qn)
    }).collect()
}

/// Top-k ids from scores: score desc, id asc on ties.
pub fn top_k(scores: &[f32], k: usize) -> Vec<usize> {
    let mut ids: Vec<usize> = (0..scores.len()).collect();
    let cmp = |a: &usize, b: &usize| scores[*b].partial_cmp(&scores[*a]).unwrap_or(std::cmp::Ordering::Equal).then(a.cmp(b));
    let k = k.min(ids.len());
    if k == 0 { return vec![]; }
    if k < ids.len() { ids.select_nth_unstable_by(k - 1, cmp); ids.truncate(k); }
    ids.sort_by(cmp);
    ids
}

pub fn topk_cpu(rows: &[f32], dim: usize, q: &[f32], k: usize) -> Vec<usize> { top_k(&scores_cpu(rows, dim, q), k) }

/// On by default (no adapter -> CPU); `MEMBRANE_GPU_TOPK=0` is the kill switch.
pub fn gpu_enabled() -> bool { std::env::var("MEMBRANE_GPU_TOPK").map(|v| v.trim() != "0").unwrap_or(true) }

/// Dispatcher: GPU when switched on, built with `metal` and an adapter exists; else CPU.
pub fn topk(rows: &[f32], dim: usize, q: &[f32], k: usize) -> Vec<usize> {
    #[cfg(feature = "metal")]
    if gpu_enabled() {
        if let Some(s) = gpu::scores_gpu(rows, dim, q) { return top_k(&s, k); }
    }
    topk_cpu(rows, dim, q, k)
}

#[cfg(feature = "metal")]
pub mod gpu {
    use wgpu::util::DeviceExt;
    const WGSL: &str = r#"
struct P { n: u32, dim: u32, qn: f32, pad: u32 };
@group(0) @binding(0) var<storage, read> rows: array<f32>;
@group(0) @binding(1) var<storage, read> q: array<f32>;
@group(0) @binding(2) var<storage, read_write> out: array<f32>;
@group(0) @binding(3) var<uniform> p: P;
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) g: vec3<u32>) {
  let i = g.x + g.y * 65535u * 64u;
  if (i >= p.n) { return; }
  var d = 0.0; var s = 0.0;
  let base = i * p.dim;
  for (var j = 0u; j < p.dim; j = j + 1u) { let v = rows[base + j]; d = d + v * q[j]; s = s + v * v; }
  out[i] = d / (max(sqrt(s), 1e-12) * p.qn);
}
"#;

    pub fn adapter_available() -> bool {
        let inst = wgpu::Instance::default();
        pollster::block_on(inst.request_adapter(&wgpu::RequestAdapterOptions::default())).is_ok()
    }

    /// None when there is no adapter / device or anything fails (caller falls back to CPU).
    pub fn scores_gpu(rows: &[f32], dim: usize, q: &[f32]) -> Option<Vec<f32>> {
        let n = rows.len() / dim;
        if n == 0 { return Some(vec![]); }
        let inst = wgpu::Instance::default();
        let ad = pollster::block_on(inst.request_adapter(&wgpu::RequestAdapterOptions::default())).ok()?;
        let lim = ad.limits();
        let (dev, queue) = pollster::block_on(ad.request_device(&wgpu::DeviceDescriptor {
            required_limits: lim.clone(), ..Default::default() })).ok()?;
        let max_buf = (lim.max_storage_buffer_binding_size as u64).min(lim.max_buffer_size) as usize;
        let rows_per = (max_buf / (dim * 4)).max(1).min(n);
        let qn = q.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
        let sh = dev.create_shader_module(wgpu::ShaderModuleDescriptor { label: None, source: wgpu::ShaderSource::Wgsl(WGSL.into()) });
        let pipe = dev.create_compute_pipeline(&wgpu::ComputePipelineDescriptor { label: None, layout: None, module: &sh,
            entry_point: Some("main"), compilation_options: Default::default(), cache: None });
        let qb = dev.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::cast_slice(q), usage: wgpu::BufferUsages::STORAGE });
        let mut all = Vec::with_capacity(n);
        let mut start = 0;
        while start < n {
            let m = rows_per.min(n - start);
            let chunk = &rows[start * dim..(start + m) * dim];
            let rb = dev.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::cast_slice(chunk), usage: wgpu::BufferUsages::STORAGE });
            let sz = (m * 4) as u64;
            let ob = dev.create_buffer(&wgpu::BufferDescriptor { label: None, size: sz, usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC, mapped_at_creation: false });
            let rd = dev.create_buffer(&wgpu::BufferDescriptor { label: None, size: sz, usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST, mapped_at_creation: false });
            let pb = dev.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None,
                contents: bytemuck::cast_slice(&[m as u32, dim as u32, qn.to_bits(), 0u32]), usage: wgpu::BufferUsages::UNIFORM });
            let bg = dev.create_bind_group(&wgpu::BindGroupDescriptor { label: None, layout: &pipe.get_bind_group_layout(0), entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: rb.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 1, resource: qb.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 2, resource: ob.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 3, resource: pb.as_entire_binding() } ] });
            let mut enc = dev.create_command_encoder(&Default::default());
            { let mut pass = enc.begin_compute_pass(&Default::default());
              pass.set_pipeline(&pipe); pass.set_bind_group(0, &bg, &[]);
              let groups = ((m + 63) / 64) as u32;
              pass.dispatch_workgroups(groups.min(65535), (groups + 65534) / 65535, 1); }
            enc.copy_buffer_to_buffer(&ob, 0, &rd, 0, sz);
            queue.submit(Some(enc.finish()));
            let sl = rd.slice(..);
            let (tx, rx) = std::sync::mpsc::channel();
            sl.map_async(wgpu::MapMode::Read, move |r| { let _ = tx.send(r); });
            let _ = dev.poll(wgpu::PollType::wait_indefinitely());
            rx.recv().ok()?.ok()?;
            { let v = sl.get_mapped_range().ok()?; all.extend_from_slice(bytemuck::cast_slice::<u8, f32>(&v)); }
            rd.unmap();
            start += m;
        }
        Some(all)
    }

    /// Resident rows on the device: uploaded once, appended in place; a query uploads only its own vector.
    pub struct Resident {
        dev: wgpu::Device, queue: wgpu::Queue, pipe: wgpu::ComputePipeline,
        dim: usize, rows_per: usize,
        chunks: Vec<(wgpu::Buffer, usize, usize)>, // buffer, rows filled, row capacity
    }

    impl Resident {
        pub fn new(dim: usize) -> Option<Resident> {
            let inst = wgpu::Instance::default();
            let ad = pollster::block_on(inst.request_adapter(&wgpu::RequestAdapterOptions::default())).ok()?;
            let lim = ad.limits();
            let (dev, queue) = pollster::block_on(ad.request_device(&wgpu::DeviceDescriptor { required_limits: lim.clone(), ..Default::default() })).ok()?;
            let max_buf = (lim.max_storage_buffer_binding_size as u64).min(lim.max_buffer_size) as usize;
            let sh = dev.create_shader_module(wgpu::ShaderModuleDescriptor { label: None, source: wgpu::ShaderSource::Wgsl(WGSL.into()) });
            let pipe = dev.create_compute_pipeline(&wgpu::ComputePipelineDescriptor { label: None, layout: None, module: &sh,
                entry_point: Some("main"), compilation_options: Default::default(), cache: None });
            Some(Resident { dev, queue, pipe, dim, rows_per: (max_buf / (dim * 4)).max(1), chunks: vec![] })
        }

        fn buf(&self, rows: usize) -> wgpu::Buffer {
            self.dev.create_buffer(&wgpu::BufferDescriptor { label: None, size: (rows * self.dim * 4) as u64,
                usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::COPY_SRC, mapped_at_creation: false })
        }

        /// Append rows (row-major) to the resident buffers; only the new rows cross to the device.
        pub fn append(&mut self, mut rows: &[f32]) {
            let dim = self.dim;
            while !rows.is_empty() {
                let need = rows.len() / dim;
                let full = self.chunks.last().map(|c| c.1 >= self.rows_per).unwrap_or(true);
                if full { let cap = need.max(1024).min(self.rows_per); let b = self.buf(cap); self.chunks.push((b, 0, cap)); }
                let li = self.chunks.len() - 1;
                if self.chunks[li].1 + need > self.chunks[li].2 && self.chunks[li].2 < self.rows_per {
                    let cap = (self.chunks[li].2 * 2).max(self.chunks[li].1 + need).min(self.rows_per);
                    let nb = self.buf(cap);
                    let mut enc = self.dev.create_command_encoder(&Default::default());
                    enc.copy_buffer_to_buffer(&self.chunks[li].0, 0, &nb, 0, (self.chunks[li].1 * dim * 4) as u64);
                    self.queue.submit(Some(enc.finish()));
                    self.chunks[li].0 = nb; self.chunks[li].2 = cap;
                }
                let room = self.chunks[li].2 - self.chunks[li].1;
                let m = room.min(need);
                self.queue.write_buffer(&self.chunks[li].0, (self.chunks[li].1 * dim * 4) as u64, bytemuck::cast_slice(&rows[..m * dim]));
                self.chunks[li].1 += m;
                rows = &rows[m * dim..];
            }
        }

        pub fn scores(&self, q: &[f32]) -> Option<Vec<f32>> {
            let qn = q.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
            let qb = self.dev.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None, contents: bytemuck::cast_slice(q), usage: wgpu::BufferUsages::STORAGE });
            let mut all = Vec::new();
            for (rb, n, _) in &self.chunks {
                let n = *n; if n == 0 { continue; }
                let sz = (n * 4) as u64;
                let ob = self.dev.create_buffer(&wgpu::BufferDescriptor { label: None, size: sz, usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC, mapped_at_creation: false });
                let rd = self.dev.create_buffer(&wgpu::BufferDescriptor { label: None, size: sz, usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST, mapped_at_creation: false });
                let pb = self.dev.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: None,
                    contents: bytemuck::cast_slice(&[n as u32, self.dim as u32, qn.to_bits(), 0u32]), usage: wgpu::BufferUsages::UNIFORM });
                let bg = self.dev.create_bind_group(&wgpu::BindGroupDescriptor { label: None, layout: &self.pipe.get_bind_group_layout(0), entries: &[
                    wgpu::BindGroupEntry { binding: 0, resource: rb.as_entire_binding() },
                    wgpu::BindGroupEntry { binding: 1, resource: qb.as_entire_binding() },
                    wgpu::BindGroupEntry { binding: 2, resource: ob.as_entire_binding() },
                    wgpu::BindGroupEntry { binding: 3, resource: pb.as_entire_binding() } ] });
                let mut enc = self.dev.create_command_encoder(&Default::default());
                { let mut pass = enc.begin_compute_pass(&Default::default());
                  pass.set_pipeline(&self.pipe); pass.set_bind_group(0, &bg, &[]);
                  let groups = ((n + 63) / 64) as u32;
                  pass.dispatch_workgroups(groups.min(65535), (groups + 65534) / 65535, 1); }
                enc.copy_buffer_to_buffer(&ob, 0, &rd, 0, sz);
                self.queue.submit(Some(enc.finish()));
                let sl = rd.slice(..);
                let (tx, rx) = std::sync::mpsc::channel();
                sl.map_async(wgpu::MapMode::Read, move |r| { let _ = tx.send(r); });
                let _ = self.dev.poll(wgpu::PollType::wait_indefinitely());
                rx.recv().ok()?.ok()?;
                { let v = sl.get_mapped_range().ok()?; all.extend_from_slice(bytemuck::cast_slice::<u8, f32>(&v)); }
                rd.unmap();
            }
            Some(all)
        }
    }
}

/// Resident sentence-vector store: one flat row-major f32 column (host) mirrored once on the device
/// (when `MEMBRANE_GPU_TOPK=1`, built with `metal`, adapter present). `near` is a cached getter:
/// recomputed only when the query vector or the sentence set changed (#994 rule 1: its own result is never an input).
pub struct VecStore {
    dim: usize, rows: Vec<f32>, ids: Vec<String>, gen: u64,
    #[cfg(feature = "metal")] gpu: Option<gpu::Resident>,
    #[cfg(feature = "metal")] synced: usize, // rows already on the device
    cache: Option<(Vec<f32>, usize, u64, Vec<(String, f32)>)>,
    computes: usize,
}

impl VecStore {
    pub fn new(dim: usize) -> Self { Self::with_gpu(dim, gpu_enabled()) }
    pub fn with_gpu(dim: usize, want_gpu: bool) -> Self {
        let _ = want_gpu;
        VecStore { dim, rows: vec![], ids: vec![], gen: 0,
            #[cfg(feature = "metal")] gpu: if want_gpu { gpu::Resident::new(dim) } else { None },
            #[cfg(feature = "metal")] synced: 0,
            cache: None, computes: 0 }
    }
    pub fn len(&self) -> usize { self.ids.len() }
    pub fn computes(&self) -> usize { self.computes }
    pub fn on_gpu(&self) -> bool { #[cfg(feature = "metal")] { return self.gpu.is_some(); } #[allow(unreachable_code)] false }
    /// Append one sentence vector (wrong width is ignored).
    pub fn add(&mut self, id: &str, v: &[f32]) {
        if v.len() != self.dim { return; }
        self.rows.extend_from_slice(v); self.ids.push(id.to_string()); self.gen += 1;
    }
    pub fn near(&mut self, q: &[f32], k: usize) -> Vec<(String, f32)> {
        if let Some((cq, ck, cg, r)) = &self.cache { if *cg == self.gen && *ck == k && cq.as_slice() == q { return r.clone(); } }
        self.computes += 1;
        let mut sc: Option<Vec<f32>> = None;
        #[cfg(feature = "metal")]
        if let Some(g) = self.gpu.as_mut() {
            if self.synced < self.ids.len() { g.append(&self.rows[self.synced * self.dim..]); self.synced = self.ids.len(); }
            sc = g.scores(q);
        }
        let sc = sc.unwrap_or_else(|| scores_cpu(&self.rows, self.dim, q));
        let r: Vec<(String, f32)> = top_k(&sc, k).into_iter().map(|i| (self.ids[i].clone(), sc[i])).collect();
        self.cache = Some((q.to_vec(), k, self.gen, r.clone()));
        r
    }
}
