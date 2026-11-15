//! The launcher's memory default (operator 2026-11-14): when no boot fact names a memory budget, the seat aims at 10% of the machine's memory (a container's limit
//! when it is lower), a batch of 200 and a hard line at 90% of the budget, so the VM's own eviction (host.rs `evict_under_pressure`) is on out of the box. A fact
//! given by the operator always wins, per fact; a machine whose memory cannot be read gets no default (as before: nothing is evicted).
use membrane_vm::host::{physical_memory_bytes, MemoryConfig};

const GIB: u64 = 1 << 30;

#[test]
fn no_facts_on_a_16_gib_machine_aim_at_ten_percent_with_a_batch_of_200_and_a_hard_line_at_90_percent() {
    let c = MemoryConfig::default().with_defaults(Some(16 * GIB));
    assert_eq!(c.budget_mb, Some(1638), "10% of 16384 MB, rounded down");
    assert_eq!(c.hard, Some(0.9));
    assert_eq!(c.batch, Some(200));
    assert_eq!(c.soft, None, "no soft line unless the operator names one");
}

#[test]
fn a_fact_the_operator_gave_wins_and_only_the_missing_ones_get_the_default() {
    let given = MemoryConfig { budget_mb: Some(500), soft: Some(0.5), hard: None, batch: Some(64) };
    let c = given.with_defaults(Some(16 * GIB));
    assert_eq!((c.budget_mb, c.soft, c.batch), (Some(500), Some(0.5), Some(64)), "named facts are kept");
    assert_eq!(c.hard, Some(0.9), "the unnamed one is filled");
    let all = MemoryConfig { budget_mb: Some(100), soft: None, hard: Some(0.8), batch: Some(10) };
    assert_eq!(all.clone().with_defaults(Some(16 * GIB)), all, "all named: nothing changes");
}

#[test]
fn a_machine_whose_memory_cannot_be_read_gets_no_default() {
    assert_eq!(MemoryConfig::default().with_defaults(None), MemoryConfig::default());
}

#[test]
fn this_machine_reports_its_memory() {
    let m = physical_memory_bytes().expect("the test machine's memory is readable");
    assert!(m > GIB, "more than a gigabyte: {m}");
}
