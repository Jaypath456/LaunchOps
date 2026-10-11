export const ownerLabel = role => ({ hr: 'HR', it: 'IT', manager: 'your manager', employee: 'the employee' }[role] || role);
export const isMine = (step, workflow, user) => step.owner_role === user?.role &&
  (user.role !== 'employee' || workflow.employee === user.username) &&
  (user.role !== 'manager' || workflow.manager === user.username);
export const stateLabel = (step, workflow, user) => step.state === 'completed' ? 'Completed' :
  step.state === 'blocked' ? 'Prerequisites needed' : isMine(step, workflow, user) ? 'Ready for you' : `Waiting for ${ownerLabel(step.owner_role)}`;
export const formatDate = value => new Date(value.length === 10 ? `${value}T12:00:00` : value).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
