"""Validate the versioned workflow graph before it can create any instances."""

from collections import deque

from .blueprint import BLUEPRINT, FORMS


ROLES = frozenset(('hr', 'it', 'manager', 'employee'))
KINDS = frozenset(('approval', 'provisioning', 'training', 'orientation'))


def validate_blueprint(blueprint=None, forms=None):
    """Return step keys in topological order, or raise ValueError on bad input."""
    blueprint = BLUEPRINT if blueprint is None else blueprint
    forms = FORMS if forms is None else forms
    steps = {}
    for key, _title, _description, role, kind, _offset, prerequisites in blueprint:
        if key in steps:
            raise ValueError(f'Duplicate step key: {key}')
        if role not in ROLES:
            raise ValueError(f'Unknown role for {key}: {role}')
        if kind not in KINDS:
            raise ValueError(f'Unknown kind for {key}: {kind}')
        steps[key] = prerequisites

    for key in forms:
        if key not in steps:
            raise ValueError(f'Form has no blueprint step: {key}')
    for key in steps:
        if key not in forms:
            raise ValueError(f'Missing form for step: {key}')

    dependents = {key: [] for key in steps}
    indegree = {}
    for key, prerequisites in steps.items():
        indegree[key] = len(prerequisites)
        for prerequisite in prerequisites:
            if prerequisite not in steps:
                raise ValueError(f'Unknown prerequisite for {key}: {prerequisite}')
            dependents[prerequisite].append(key)

    ready = deque(key for key in steps if indegree[key] == 0)
    order = []
    while ready:
        key = ready.popleft()
        order.append(key)
        for dependent in dependents[key]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(order) != len(steps):
        raise ValueError('Blueprint contains a cycle.')
    return order
