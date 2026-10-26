import type { TaskOwner, TaskState, WorkflowOwners } from '../../types/onboarding';

export const ownerLabel = (role: string): string => (({ hr: 'HR', it: 'IT', manager: 'your manager', employee: 'the employee' } as Record<string, string>)[role] || role);
export const isMine = (step: TaskState, workflow: WorkflowOwners, user: TaskOwner | null | undefined): boolean => step.owner_role === user?.role &&
  (user.role !== 'employee' || workflow.employee === user.username) &&
  (user.role !== 'manager' || workflow.manager === user.username);
export const stateLabel = (step: TaskState, workflow: WorkflowOwners, user: TaskOwner | null | undefined): string => step.state === 'completed' ? 'Completed' :
  step.state === 'blocked' ? 'Prerequisites needed' : isMine(step, workflow, user) ? 'Ready for you' : `Waiting for ${ownerLabel(step.owner_role)}`;
export const formatDate = (value: string): string => new Date(value.length === 10 ? `${value}T12:00:00` : value).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
