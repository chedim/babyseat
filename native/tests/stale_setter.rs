//! A setter sees the getters as they were BEFORE the write that fired it (the engine's rule). A setter that reads a getter
//! which depends on its own trigger property reads a stale value: the loader refuses it and says what to do instead.
use membrane_vm::fields::Fields;

fn load(src: &str) -> Result<(), String> { Fields::goalgraph().set_pack("t", src) }

const STALE: &str = "PACK t 1
Light.shade getter: 'dark' if this.level < 3 else 'bright'
Light.level on-setter note:
  WHEN this.shade == 'bright':
    this.note = 'bright now'
";

#[test]
fn a_setter_reading_a_getter_of_its_own_trigger_is_refused_with_the_fix() {
    let e = load(STALE).unwrap_err();
    assert!(e.contains("stale"), "{e}");
    assert!(e.contains("Light.shade") && e.contains("Light.level"), "{e}");
    assert!(e.contains("on-setter on Light.shade"), "{e}");
}

#[test]
fn the_dependency_may_run_through_other_getters() {
    let e = load("PACK t 1
Light.a getter: this.level + 1
Light.b getter: this.a * 2
Light.level on-setter s:
  ALWAYS:
    this.out = this.b
").unwrap_err();
    assert!(e.contains("Light.b"), "{e}");
}

#[test]
fn acting_from_a_setter_on_the_getter_itself_is_fine() {
    load("PACK t 1
Light.shade getter: 'dark' if this.level < 3 else 'bright'
Light.shade on-setter note:
  WHEN this.shade == 'bright':
    this.note = 'bright now'
").unwrap();
}

#[test]
fn a_getter_that_does_not_read_the_trigger_is_fine() {
    load("PACK t 1
Light.tint getter: str(this.hue) + '!'
Light.level on-setter s:
  WHEN this.tint == 'x!':
    this.note = 1
").unwrap();
}

#[test]
fn a_getter_of_another_class_is_not_this_rule() {
    load("PACK t 1
Lamp.shade getter: 'dark' if this.level < 3 else 'bright'
Light.level on-setter s:
  WHEN this.lamp == 1:
    this.note = 1
").unwrap();
}
