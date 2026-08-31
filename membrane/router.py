"""LLM orchestration -- the escalation router.

The gate decides field-vs-model; the router decides model-vs-model. It is the
second half of "orchestrates LLM calls between local and cloud inference":

- registry: candidates discovered live from Ollama (/api/tags) -- local models
  AND cloud-backed entries (Ollama registers remotes with ``remote_host`` /
  ``size == 0``, so cloud candidates ride the same walk; nothing hardcoded).
- machine state as PRESSURE SCALARS (loadavg, /api/ps VRAM residency, measured
  local latency EWMA) -- never hypervector axes: the field's geometry is
  semantic; folding CPU% into it would alias "git diff at 3am" onto "SQL
  migration" because both ran under load. Pressure enters the score as delay,
  is audited per turn, and graduates into empirical calibration on its own
  merits (the calibration record below).
- imprints: each model is ASKED (once, cached) what it is good at; the 3
  bullets embed into a claimed region that seeds competence with K phantom
  prior mass. Self-claims are hypotheses the field's lived evidence
  overwrites: as real trials accrue the phantom mass dilutes toward zero.
- scoring one level down from the gate:
      score(m) = fit_hat(m, s) - lambda_time * urgency * delay(m)
                 - lambda_cost * price(m)
  the cost asymmetry is the hard floor -- cloud is the LAST RESORT: it wins
  the rank only when no local candidate exists, or measured local pressure
  (latency above the calibrated threshold / machine load) makes waiting the
  real cost AND the cloud's fit is genuinely better. A daily budget cap drops
  cloud from the candidate set entirely (score proposes; budget disposes).
- failover: the caller retries with the pre-router model on connect failure
  (degrade toward the safe direction, the same covenant as judge-error ->
  ACCEPT). Locals share ONE resource pool; failover never races locals.
- the measured pressure valve: every CLOUD route is audited with the pressure
  it was taken under and later resolved survived/re-asked; buckets of that
  joint give the local-slowness threshold EMPIRICALLY (calibrated_threshold),
  defaulting conservative until evidence accrues -- the sim_min bootstrap
  covenant.

Covenant kept: declared structures (tags, caps, sizes) FILTER; measured
records (wins, latency, calibration) RANK; self-claims only SEED. The
operator's hand stays the upper authority (budget, kill switches).
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.request
from dataclasses import dataclass, field as dfield
from pathlib import Path

import numpy as np

# ---- tuning knobs (overridable from flags in opencode.main) ----------------
CLOUD_PRICE = 3.0          # money units per unknown cloud turn (the floor table
                           # refines this; --price overrides win over both)
# The cost LADDER: local (0) -> cheap cloud -> expensive cloud. Ollama's API
# does NOT serve pricing, so these are declared, operator-editable defaults
# (substring-matched, first hit wins, case-insensitive). Values are order-of-
# magnitude relative money per turn -- the ROUTING uses the ordering, not the
# absolute number. Overridable via --price "MODEL=USD" (repeatable).
PRICE_TABLE: list[tuple[str, float]] = [
    ("glm-5.3-flash", 1.0), ("glm", 2.0),
    ("deepseek", 1.5), ("qwen", 1.5), ("mistral", 1.5), ("llama", 1.5),
    ("gemini", 4.0), ("gpt-5", 8.0), ("gpt", 5.0), ("claude", 6.0),
    ("kimi", 2.0),
]
LAMBDA_TIME = 0.05         # score units per pressure-inflated second
LAMBDA_COST = 1.0          # score units per money unit
COLD_LOAD_S_PER_GIB = 1.2  # cold-loading a local model costs ~1.2s/GiB
CLOUD_LATENCY = 4.0        # network + big-model baseline, seconds
PRIOR_MASS_K = 3.0         # phantom trials standing behind the imprint claim
DEFAULT_PRESSURE_THRESHOLD = 10.0   # local p95 (s) where cloud may win, pre-evidence
LATENCY_ALPHA = 0.25       # EWMA friction for measured latencies
COMPETENCE_ALPHA = 0.6     # EMA friction for the earned direction
CALIBRATION_MIN = 8        # calibration records before the measured line wins


def _unit(v) -> np.ndarray | None:
    a = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(a))
    if n < 1e-12:
        return None
    return a / n


@dataclass
class ModelRecord:
    """One discovered model: what it claims, what it has earned, what it costs."""
    name: str
    kind: str = "local"                 # "local" | "cloud" (remote-backed)
    size_bytes: int = 0
    caps: list = dfield(default_factory=list)   # ollama capability flags
    # the claimed region (self-description embeds) + its verbatim claim
    imprint: list = dfield(default_factory=list)  # unit vectors
    self_desc: str = ""
    # the earned record
    competence: list | None = None      # unit EMA of WON situation vectors
    wins: int = 0
    trials: int = 0
    # measured cost
    latency_ewma: float | None = None   # seconds
    last_used: float = 0.0

    # ---- scoring pieces ----
    def imprint_fit(self, situ) -> float:
        """Best cosine between the situation and the CLAIMED region."""
        if not self.imprint:
            return 0.0
        v = _unit(situ)
        if v is None:
            return 0.0
        M = np.asarray(self.imprint, dtype=float)
        return max(0.0, float((M @ v).max()))

    def competence_fit(self, situ) -> float:
        """Cosine against the EARNED direction (0 when no record yet)."""
        if not self.competence:
            return 0.0
        v = _unit(situ)
        if v is None:
            return 0.0
        return max(0.0, float(np.asarray(self.competence) @ v))

    def fit_hat(self, situ) -> float:
        """Laplace-blended expected fit: the claim seeds, the record votes.

        With no trials routing is 100% by claim; as trials accrue the
        phantom mass dilutes (30 trials -> the claim is ~10% of the vote).
        """
        claim = self.imprint_fit(situ)
        if self.trials <= 0:
            return claim
        earned = max(self.competence_fit(situ), claim)
        return (self.wins * earned + PRIOR_MASS_K * claim) \
            / (self.trials + PRIOR_MASS_K)

    def price(self) -> float:
        """Relative money per turn. local -> 0; cloud -> the cheapest table
        hit, else CLOUD_PRICE. The ladder IS the preference: prefer LOWER
        token-cost models unless really stuck (the pressure valve + fit
        margin are the "really stuck" conditions)."""
        if self.kind == "local":
            return 0.0
        low = self.name.lower()
        for frag, usd in PRICE_TABLE:
            if frag in low:
                return usd
        return CLOUD_PRICE

    def delay(self, resident: bool, pressure: float,
              calibrated_threshold: float) -> float:
        """Expected seconds this route costs (urgency applied in the score).

        local: measured EWMA + cold-load penalty when not in /api/ps,
        inflated by machine pressure. cloud: flat baseline (network + queue).
        Below the calibrated threshold the raw EWMA is damped so an idle box
        keeps cloud unambiguously last-resort.
        """
        if self.kind == "cloud":
            return CLOUD_LATENCY
        base = self.latency_ewma if self.latency_ewma is not None else 2.0
        if not resident and self.size_bytes > 0:
            base += COLD_LOAD_S_PER_GIB * (self.size_bytes / 2**30)
        if pressure > 0:
            base *= (1.0 + 0.5 * pressure)
        if base < calibrated_threshold:
            base = min(base, calibrated_threshold * 0.6)
        return base

    def to_doc(self) -> dict:
        return {"name": self.name, "kind": self.kind, "wins": self.wins,
                "trials": self.trials, "competence": self.competence,
                "latency_ewma": self.latency_ewma, "self_desc": self.self_desc,
                "imprint": self.imprint, "last_used": self.last_used,
                "size_bytes": self.size_bytes, "caps": self.caps}


class Registry:
    """Candidate discovery + routed bookkeeping.

    The scoring core is pure data; the live walk (_ollama/seed_imprints) is
    thin HTTP -- tests stub _ollama and feed canned payloads.
    """

    def __init__(self, backend: str, embedder=None, state_path=None,
                 explore: float = 0.05, cloud_budget: float | None = None,
                 rng: random.Random | None = None):
        self.backend = backend.rstrip("/")
        self.embedder = embedder
        self.state_path = Path(state_path) if state_path else None
        self.explore = explore
        self.cloud_budget = cloud_budget    # money/day; None = uncapped
        self.spend_today = 0.0
        self.spend_day = time.strftime("%Y-%m-%d")
        self.records: dict[str, ModelRecord] = {}
        self.resident: set[str] = set()
        self._lock = threading.Lock()
        self._rng = rng or random.Random()
        # the measured pressure valve: (pressure_at_route, cloud_survived)
        self.calibration: list[list] = []
        self._last_pressure: float = 0.0
        self._load_state()
        try:
            self.refresh()
        except Exception:
            pass

    # ---- discovery -----------------------------------------------------------

    def _ollama(self, path: str, timeout: float = 4.0):
        """GET <backend><path> -> parsed JSON, or None (tests stub this)."""
        try:
            with urllib.request.urlopen(self.backend + path,
                                        timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            return None

    def refresh(self) -> None:
        """Walk /api/tags (+ /api/ps): local AND cloud candidates, residency."""
        tags = self._ollama("/api/tags")
        if not tags:
            return
        ps = self._ollama("/api/ps") or {}
        self.resident = {m.get("name", "") for m in ps.get("models", [])}
        for m in tags.get("models", []) or []:
            name = m.get("name") or m.get("model") or ""
            if not name:
                continue
            rec = self.records.setdefault(name, ModelRecord(name=name))
            rec.kind = ("cloud" if (bool(m.get("remote_host"))
                                    or not m.get("size")) else "local")
            rec.size_bytes = int(m.get("size") or 0)
            caps = list(m.get("capabilities") or [])
            fams = list((m.get("details") or {}).get("families") or [])
            rec.caps = caps + [f for f in fams if f not in caps]

    def seed_imprints_async(self) -> None:
        threading.Thread(target=self.seed_imprints, daemon=True).start()

    def seed_imprints(self) -> None:
        """Ask each model (once, cached) what it is good for; embed the claim.

        A self-description is an OVERCLAIM by construction -- it only seeds
        the prior mass; lived evidence dilutes it. Background-safe: any
        failure leaves the claim unseeded (fit falls to 0; locals still
        route by latency + exploration until an imprint lands)."""
        for rec in list(self.records.values()):
            if rec.imprint or self.embedder is None:
                continue
            try:
                body = json.dumps({
                    "model": rec.name, "stream": False,
                    "messages": [{"role": "user", "content":
                                  "In exactly 3 short bullet lines: (1) the "
                                  "tasks you are best at, (2) the tasks you "
                                  "are worst at, (3) what you were trained "
                                  "for. Plain text, no preamble."}]}).encode()
                req = urllib.request.Request(
                    self.backend + "/api/chat", data=body,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    txt = (json.loads(r.read()).get("message") or {}) \
                        .get("content", "")
            except Exception:
                continue
            bullets = [ln.strip("-•* ").strip() for ln in txt.splitlines()
                       if len(ln.strip()) > 8][:3]
            if not bullets:
                continue
            try:
                E = np.asarray(self.embedder.embed(bullets), dtype=float)
            except Exception:
                continue
            En = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-12)
            with self._lock:
                rec.imprint = [list(map(float, row)) for row in En]
                rec.self_desc = " | ".join(bullets)[:400]
            self.save()

    # ---- pressure (machine state as scalars) ---------------------------------

    def pressure(self) -> float:
        """Machine pressure: loadavg over cores, clamped to [0, 4]."""
        try:
            load = os.getloadavg()[0]
            cpus = os.cpu_count() or 1
            return max(0.0, min(4.0, load / max(cpus, 1)))
        except Exception:
            return 0.0

    def calibrated_threshold(self) -> float:
        """The local-p95 line above which cloud has EARNED its money.

        Declared default until the joint record (route pressure -> outcome)
        accrues CALIBRATION_MIN entries with both outcomes; then the measured
        midline wins, clamped to a sane band."""
        wins = [p for p, ok in self.calibration if ok]
        losses = [p for p, ok in self.calibration if not ok]
        if len(self.calibration) >= CALIBRATION_MIN and wins and losses:
            thr = (sum(wins) / len(wins) + min(losses)) / 2
            return max(2.0, min(120.0, thr))
        return DEFAULT_PRESSURE_THRESHOLD

    # ---- routing ---------------------------------------------------------------

    def route(self, situ_vec, urgency: float = 1.0,
              needs_tools: bool = False) -> tuple[ModelRecord | None, dict]:
        """Pick the escalation target; return (record, audit_route dict).

        score(m) = fit_hat - LAMBDA_TIME*urgency*delay - LAMBDA_COST*price.
        Cloud wins the rank only when: no local exists, OR the valve is open
        (measured pressure / local latency above the calibrated threshold)
        AND its fit beats the best local by a real margin. eps-explore only
        ever samples unsampled LOCALS (cloud is never a curiosity spend).
        """
        situ = np.asarray(situ_vec, dtype=float) if situ_vec is not None else None
        day = time.strftime("%Y-%m-%d")
        if self.spend_day != day:
            self.spend_day, self.spend_today = day, 0.0
        thr = self.calibrated_threshold()
        pres = self.pressure()
        self._last_pressure = pres

        def cap_ok(r: ModelRecord) -> bool:
            # structure filters only when the capability set is KNOWN: a turn
            # that carries tools never routes to a model declared tools-less.
            if not needs_tools or not r.caps:
                return True
            return "tools" in r.caps

        locals_ = [r for r in self.records.values()
                   if r.kind == "local" and cap_ok(r)]
        clouds = [r for r in self.records.values()
                  if r.kind == "cloud" and cap_ok(r)]
        if self.cloud_budget is not None and self.spend_today >= self.cloud_budget:
            clouds = []
        if not locals_ and not clouds:
            return None, {"route": None, "kind": None,
                          "reason": "no candidates", "pressure": round(pres, 2)}

        def score(r: ModelRecord) -> float:
            d = r.delay(r.name in self.resident, pres, thr)
            return r.fit_hat(situ) - LAMBDA_TIME * urgency * d \
                - LAMBDA_COST * r.price()

        # eps-explore: try the least-tried LOCAL, so a new model can ever
        # build a record (chicken-and-egg killer). Local-only by design.
        explored = False
        if locals_ and self.explore > 0 \
                and self._rng.random() < self.explore:
            least = min(locals_, key=lambda r: r.trials)
            if least.trials == 0:
                explored = True
                best, reason = least, (f"exploring unsampled local "
                                       f"(fit={least.fit_hat(situ):.2f})")
                info = {"route": best.name, "kind": best.kind,
                        "reason": reason, "explored": True,
                        "pressure": round(pres, 2),
                        "imprint": bool(best.imprint),
                        "threshold": round(thr, 1), "ts": time.time()}
                return best, info
        if not locals_:
            best = max(clouds, key=score) if clouds else None
            reason = ("no local candidate; "
                      + (f"cloud fit={best.fit_hat(situ):.2f}" if best else "none"))
        else:
            top_local = max(locals_, key=score)
            # THE COST LADDER as the decision, not the raw score (an
            # expensive cloud + flat latency can never rank #1 by score
            # alone, which would make the valve dead code): walk the cloud
            # rungs CHEAPEST FIRST; a rung may step over the locals only
            # when the valve is OPEN (measured pressure / stuck local above
            # the calibrated threshold) AND its fit is tangibly better than
            # the best local's. Otherwise local.
            under_pressure = (pres >= 0.75 or
                              (top_local.latency_ewma or 0.0) >= thr)
            top_cloud = None
            if clouds and under_pressure:
                local_fit = top_local.fit_hat(situ)
                for c in sorted(clouds, key=lambda r: r.price()):
                    if c.fit_hat(situ) > local_fit + 0.10:
                        top_cloud = c
                        break
            if top_cloud is not None:
                best = top_cloud
                reason = (f"pressure-valve: local p95="
                          f"{top_local.latency_ewma or 0:.0f}s "
                          f"(thr={thr:.0f}s, load {pres:.2f}); cloud fit "
                          f"{top_cloud.fit_hat(situ):.2f}"
                          f">{top_local.fit_hat(situ):.2f}")
            else:
                best = top_local
                why = ("idle box" if not under_pressure else
                       f"fit {top_local.fit_hat(situ):.2f} suffices")
                reason = f"cost-floor: local first ({why})"
        info = {"route": best.name if best else None,
                "kind": best.kind if best else None,
                "reason": reason, "explored": explored,
                "pressure": round(pres, 2),
                "imprint": bool(best.imprint) if best else False,
                "threshold": round(thr, 1), "ts": time.time()}
        return best, info

    # ---- outcome feedback (the measured record) ---------------------------------

    def note_latency(self, name: str, seconds: float) -> None:
        rec = self.records.get(name)
        if rec is None:
            return
        prev = rec.latency_ewma
        rec.latency_ewma = (seconds if prev is None
                            else (1 - LATENCY_ALPHA) * prev
                            + LATENCY_ALPHA * seconds)
        rec.last_used = time.time()

    def note_outcome(self, name: str, situ_vec, survived: bool,
                     pressure: float | None = None) -> None:
        """A win (answer survived: no complaint followed) or a loss (re-ask /
        negative follow-up / tool error) against the ANSWERING model.

        Wins walk the competence EMA; every cloud route lands in the
        calibration record as (pressure, survived) -- the joint from which
        the valve threshold is measured."""
        with self._lock:
            rec = self.records.get(name)
            if rec is None:
                return
            rec.trials += 1
            if survived:
                rec.wins += 1
                v = _unit(situ_vec)
                if v is not None:
                    if rec.competence is None:
                        rec.competence = [float(x) for x in v]
                    else:
                        c = COMPETENCE_ALPHA * np.asarray(rec.competence) \
                            + (1 - COMPETENCE_ALPHA) * v
                        n = float(np.linalg.norm(c))
                        if n > 1e-12:
                            rec.competence = [float(x) / n for x in c]
            if rec.kind == "cloud":
                p = float(pressure) if pressure is not None \
                    else self._last_pressure
                self.calibration.append([round(p, 3), bool(survived)])
                self.calibration = self.calibration[-256:]
            rec.last_used = time.time()
        self.save()

    def record_spend(self, est_usd: float) -> None:
        self.spend_today += max(0.0, float(est_usd))
        self.save()

    # ---- persistence -----------------------------------------------------------

    def save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "spend": {"day": self.spend_day, "usd": self.spend_today},
                "records": {n: r.to_doc() for n, r in self.records.items()},
                "calibration": self.calibration[-256:],
            }))
            os.replace(tmp, self.state_path)
        except Exception:
            pass

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            d = json.loads(self.state_path.read_text())
        except Exception:
            return
        sp = d.get("spend") or {}
        if sp.get("day") == self.spend_day:
            self.spend_today = float(sp.get("usd", 0.0) or 0.0)
        self.calibration = [list(x) for x in
                            (d.get("calibration") or [])][-256:]
        for name, rd in (d.get("records") or {}).items():
            rec = ModelRecord(name=name, kind=rd.get("kind", "local"))
            rec.self_desc = rd.get("self_desc", "")
            rec.imprint = list(rd.get("imprint") or [])
            rec.competence = rd.get("competence")
            rec.wins = int(rd.get("wins") or 0)
            rec.trials = int(rd.get("trials") or 0)
            rec.latency_ewma = rd.get("latency_ewma")
            rec.last_used = float(rd.get("last_used") or 0.0)
            rec.size_bytes = int(rd.get("size_bytes") or 0)
            rec.caps = list(rd.get("caps") or [])
            self.records[name] = rec

    # ---- reporting -----------------------------------------------------------

    def summary(self) -> str:
        n_local = sum(1 for r in self.records.values() if r.kind == "local")
        n_cloud = len(self.records) - n_local
        spend = ""
        if self.spend_today or self.cloud_budget is not None:
            cap = (f"/{self.cloud_budget:.2f}"
                   if self.cloud_budget is not None else "")
            spend = f", spend ${self.spend_today:.2f}{cap}"
        lines = [f"- router: {len(self.records)} candidates "
                 f"({n_local} local, {n_cloud} cloud), "
                 f"resident {len(self.resident)}, "
                 f"pressure {self.pressure():.2f}, "
                 f"threshold {self.calibrated_threshold():.0f}s"
                 f", calibration {len(self.calibration)}{spend}"]
        for r in sorted(self.records.values(), key=lambda r: -r.trials)[:6]:
            wr = f"{r.wins}/{r.trials}" if r.trials else "-"
            lat = f"{r.latency_ewma:.1f}s" if r.latency_ewma else "-"
            lines.append(f"  - {r.name} [{r.kind}] win {wr} lat {lat} "
                         f"imprint={'y' if r.imprint else 'n'}"
                         + (f" — {r.self_desc[:76]}" if r.self_desc else ""))
        return "\n".join(lines)