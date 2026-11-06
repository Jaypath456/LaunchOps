#!/usr/bin/env bash
set -euo pipefail

PROJECT=${PROJECT:-launchops-smoke}
cd "$(dirname "${BASH_SOURCE[0]}")/.."
compose=(docker compose -p "$PROJECT")

cleanup() {
  result=$?
  trap - EXIT
  if (( result != 0 )); then
    "${compose[@]}" logs --no-color >&2 || true
  fi
  if ! "${compose[@]}" down -v --remove-orphans; then
    "${compose[@]}" logs --no-color >&2 || true
    result=1
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# This project is disposable; task t11w explicitly requires volume cleanup.
"${compose[@]}" down -v --remove-orphans
export ERROR_RATE=0 HANG_RATE=0 DROP_AFTER_COMMIT_RATE=0
"${compose[@]}" up -d --build --wait --wait-timeout 120 mongo mock-saas provisioner

"${compose[@]}" exec -T mongo mongosh --quiet --file /dev/stdin <<'JS'
const workflows = db.getSiblingDB("launchops").workflow_instances;
const accounts = db.getSiblingDB("mocksaas").accounts;
const id = new ObjectId();
const now = new Date();
const connectors = ["workspace", "slack", "github"];
const payload = {
  username: "smoke_employee",
  display_name: "Smoke Employee",
  department: "Engineering"
};
const inserted = workflows.insertOne({
  _id: id,
  version: NumberInt(0),
  steps: [],
  issues: [],
  events: [],
  provisioning: connectors.map(connector => ({
    connector,
    idempotency_key: id.toHexString() + ":" + connector,
    payload,
    status: "pending",
    attempts: NumberInt(0),
    fence: NumberLong("0"),
    lease_owner: null,
    lease_until: null,
    next_attempt_at: now,
    last_error: null,
    external_ref: null,
    override_reason: null,
    updated_at: now
  }))
});
assert(inserted.acknowledged, "workflow insert was not acknowledged");
assert(workflows.countDocuments({}) === 1, "expected exactly one workflow");

const deadline = Date.now() + 90000;
let instance;
while (true) {
  instance = workflows.findOne({_id: id});
  assert(instance, "workflow disappeared");
  assert(instance.provisioning.length === 3, "expected three jobs");
  if (instance.provisioning.every(job => job.status === "succeeded")) break;
  if (instance.provisioning.some(job => job.status === "dead") || Date.now() >= deadline) {
    printjson(instance);
    throw new Error("provisioning failed or exceeded the 90-second deadline");
  }
  sleep(500);
}

assert(accounts.countDocuments({}) === 3, "expected exactly three simulator accounts");
for (const connector of connectors) {
  const job = instance.provisioning.find(job => job.connector === connector);
  assert(job, "missing job for " + connector);
  const account = accounts.findOne({connector, username: payload.username});
  assert(account, "missing simulator account for " + connector);
  assert(account._id === job.external_ref, "external_ref mismatch for " + connector);
}
print("PASS: all three provisioning jobs succeeded and match their simulator accounts");
JS
