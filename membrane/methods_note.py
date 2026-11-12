"""Entity methods are being retired (operator, 2026-11-12): the note every method answer carries, and the refusal of a new one."""
METHODS_DEPRECATED = ("DEPRECATED: entity methods are being retired in favor of cliffhanger scripts. Read with a getter (a pack's "
                      "`Class.prop getter: ...`) and act by WRITING to an entity (a setter on a property, or a driver verb such as "
                      "`Command:<name>.run = {...}`); write the pack with the `pack` tool. help('cliffhanger') has the syntax.")
DEFINE_DISABLED = "method_define is disabled: creating or editing a method is no longer allowed. " + METHODS_DEPRECATED
