-- The goal graph (#773, #994) in cliffhanger's field syntax (native/src/syntax.rs).
-- Migrated from packs/goalgraph.json by the translator; tests/syntax_994.rs holds the two equal.
--
-- The getter (built into the engine, fields.rs `recount`): a task's or wait's open blockers are counted, never
-- decremented, so a dropped or doubled write can't make them drift:
--   Task.open_blockers getter: the blockers of this (Task, Decision, Wait) that are not finished
--   finished: Task and Wait -> status == 'done'; Decision -> status == 'closed'

-- a task with nothing open in its way is owed (on the count, and on its own status)
Task.open_blockers on-setter ready:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'owed'
Task.status on-setter ready-on-status:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'owed'

-- a task or a card that ends asks its goal to replan
Task.status on-setter replan-task:
  WHEN (this.status == 'done' or this.status == 'failed' or this.status == 'stuck'):
    this.goal.replan = 'task ' + this.eid + ' ' + this.status
Decision.status on-setter replan-card:
  WHEN (this.status == 'closed' or this.status == 'dropped'):
    this.goal.replan = 'card ' + this.eid + ' ' + this.status

-- a wait with nothing open in its way is armed; when it is due, its adapter checks
Wait.open_blockers on-setter wait-armed:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'armed'
    this.armed_since = clock
Wait.status on-setter wait-armed-on-status:
  WHEN this.open_blockers < 0.5 and this.status == 'open':
    this.status = 'armed'
    this.armed_since = clock
Wait.due on-setter wait-check:
  WHEN this.due:
    this.check = 'due'

-- a goal asked to replan calls the planner (and again after a reopen with a new reason)
Goal.replan on-setter plan:
  WHEN (this.status or 'open') == 'open' and this.replan:
    membrane::llm.call = {'about': this.eid, 'prompt': 'plan the goal: ' + this.replan, 'lane': 'planner'}
Goal.status on-setter plan-after-reopen:
  WHEN this.status == 'open' and this.replan and this.planned_ts and this.replan != this.planned_replan:
    membrane::llm.call = {'about': this.eid, 'prompt': 'plan the goal: ' + this.replan, 'lane': 'planner'}

-- getters (#994 rule 2): computed fields, recalculated when what they read changes
Task.finished getter: this.status == 'done'
Wait.finished getter: this.status == 'done'
Decision.finished getter: this.status == 'closed'
Task.open_blockers getter: len([b for b in referencing(this, 'blocks') if not b.finished])
Wait.open_blockers getter: len([b for b in referencing(this, 'blocks') if not b.finished])
-- a wait is due when its every_s has passed since it was last checked (or armed)
Wait.due getter:
  this.status == 'armed' and clock - (this.last_check_ts or this.armed_since or this.armed_ts or clock) >= (this.every_s or 10)
