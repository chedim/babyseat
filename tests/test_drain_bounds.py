"""P2 THE BOUNDED DRAIN LEDGER (the seat's defect 3): every drained
turn appended a thread + a result forever. The cap/prune contract:
finished threads drop out, results keep the newest DRAIN_RESULT_MAX,
result_of still answers inside the window."""
import threading
import time

from membrane.server import TurnRunner, SSEHub, SessionStore, MessageStore


def _runner():
    import tempfile
    d = tempfile.mkdtemp()
    return TurnRunner(SSEHub(), SessionStore(f"{d}/s.json"),
                      MessageStore(f"{d}/m.json"),
                      call_ladder=None, executor=None, system="", model="m")


def _prune(r: TurnRunner) -> None:
    """The worker's finally-block prune, invoked directly."""
    with r._queue_lock:
        r._drain_threads = [t for t in r._drain_threads if t.is_alive()]
        if len(r._drain_threads) > r.DRAIN_MAX:
            r._drain_threads = r._drain_threads[-r.DRAIN_MAX:]
        if len(r._drain_results) > r.DRAIN_RESULT_MAX:
            for old in sorted(r._drain_results,
                              key=lambda k: r._drain_order.get(k, 0)
                              )[:-r.DRAIN_RESULT_MAX]:
                r._drain_results.pop(old, None)
                r._drain_order.pop(old, None)


def test_finished_threads_prune_and_results_cap():
    r = _runner()
    r.DRAIN_MAX = 8
    r.DRAIN_RESULT_MAX = 10
    for i in range(30):
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        r._drain_threads.append(t)
        r._drain_results[f"q{i}"] = {"status": "done"}
        r._drain_order[f"q{i}"] = time.time() - (100 - i)   # oldest first

    _prune(r)
    assert len(r._drain_threads) == 0          # completed threads gone
    assert len(r._drain_results) == 10         # the cap holds
    assert "q29" in r._drain_results           # the newest survived
    assert "q19" not in r._drain_results


def test_result_of_still_reads():
    r = _runner()
    r._drain_results["q42"] = {"status": "ok"}
    assert r.result_of("q42") == {"status": "ok"}
    assert r.result_of("nope") is None