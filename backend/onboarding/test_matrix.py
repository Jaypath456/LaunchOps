"""Port of v1 test_matrix.py: blueprint integrity, owner, prerequisite and evidence matrices."""

from collections import Counter, deque

from django.test import SimpleTestCase

from .blueprint import BLUEPRINT, FORMS
from .testing import WorkflowTestCase, events, mark_done, reset_steps


class BlueprintIntegrityTests(SimpleTestCase):
    def test_blueprint_integrity(self):
        roles = {'hr', 'it', 'manager', 'employee'}
        keys = [row[0] for row in BLUEPRINT]
        self.assertEqual(len(BLUEPRINT), 20)
        self.assertEqual(len(set(keys)), len(keys))
        self.assertEqual(Counter(row[3] for row in BLUEPRINT), {role: 5 for role in roles})
        indegree = {}
        dependents = {key: [] for key in keys}
        for key, _, _, role, _, _, dependencies in BLUEPRINT:
            with self.subTest(key=key):
                self.assertIn(role, roles)
                self.assertIn(key, FORMS)
                self.assertNotIn(key, dependencies)
                self.assertTrue(set(dependencies).issubset(keys))
            indegree[key] = len(dependencies)
            for dependency in dependencies:
                dependents[dependency].append(key)
        queue = deque(key for key in keys if indegree[key] == 0)
        visited = []
        while queue:
            key = queue.popleft()
            visited.append(key)
            for dependent in dependents[key]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
        self.assertEqual(set(visited), set(keys), 'Dependency graph has a cycle')


class MatrixHelpers(WorkflowTestCase):
    def complete_others(self, key):
        reset_steps(self.wid)
        mark_done(self.wid, [other for other in FORMS if other != key])


class OwnerMatrixTests(MatrixHelpers):
    def test_owner_matrix(self):
        for key, _, _, owner, _, _, _ in BLUEPRINT:
            self.complete_others(key)
            non_owners = [user for role, user in self.users.items() if role != owner]
            for user in non_owners + [self.other]:
                with self.subTest(key=key, actor=user['username']):
                    self.as_user(user)
                    response = self.complete(key)
                    # Permission checks run in the repository build, including for invisible users.
                    self.assertEqual(response.status_code, 403, response.data)
                    self.assert_unchanged(key)
            with self.subTest(key=key, actor=owner):
                self.as_role(owner)
                self.assertEqual(self.complete(key).status_code, 200)
                self.assertEqual(self.step(key)['status'], 'done')
                self.assertEqual(len(events(self.wid, 'completed', key)), 1)


class PrerequisiteMatrixTests(MatrixHelpers):
    def test_prerequisite_matrix(self):
        for key, _, _, owner, _, _, dependencies in BLUEPRINT:
            if not dependencies:
                continue
            with self.subTest(key=key):
                reset_steps(self.wid)
                self.as_role(owner)
                response = self.complete(key)
                # Incomplete prerequisites raise ValidationError, which maps to 400.
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_unchanged(key)
                mark_done(self.wid, dependencies)
                if key == 'ready':
                    # Final signoff also needs every other task, not just its listed prerequisites.
                    response = self.complete(key)
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertEqual(self.step(key)['status'], 'open')
                    mark_done(self.wid, [other for other in FORMS if other != key and other not in dependencies])
                response = self.complete(key)
                # Success after the listed prerequisites; ready also needs all other tasks.
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(self.step(key)['status'], 'done')


class EvidenceMatrixTests(MatrixHelpers):
    def test_evidence_matrix(self):
        for key, _, _, owner, _, _, _ in BLUEPRINT:
            self.complete_others(key)
            self.as_role(owner)
            valid = self.evidence(key)
            cases = [('missing_' + field['name'], {
                name: value for name, value in valid.items() if name != field['name']})
                for field in FORMS[key] if field['required']]
            cases.extend([('unknown_field', {**valid, 'unknown_field': 'extra'}), ('empty', {})])
            for case, evidence in cases:
                with self.subTest(key=key, case=case):
                    response = self.complete(key, evidence)
                    # validate_evidence requires the exact field set; violations return 400.
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assert_unchanged(key)
