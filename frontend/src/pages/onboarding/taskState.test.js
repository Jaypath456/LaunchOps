import { stateLabel, isMine, formatDate } from './taskState';
const workflow = { employee: 'hire', manager: 'boss' };
const ready = { state: 'ready', owner_role: 'it' };
test('prerequisites being ready never implies employee permission', () => {
  expect(stateLabel(ready, workflow, { username: 'hire', role: 'employee' })).toBe('Waiting for IT');
  expect(stateLabel(ready, workflow, { username: 'technician', role: 'it' })).toBe('Ready for you');
  expect(isMine({ ...ready, owner_role: 'manager' }, workflow, { username: 'other', role: 'manager' })).toBe(false);
  expect(stateLabel({ ...ready, state: 'blocked' }, workflow, { role: 'it' })).toBe('Prerequisites needed');
});
test('date-only start dates keep the calendar day in local time', () => {
  expect(formatDate('2026-10-04')).toContain('4');
  expect(formatDate('2026-10-04')).not.toContain('3,');
});
