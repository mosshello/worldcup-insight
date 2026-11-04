import { test } from "node:test";
import assert from "node:assert/strict";
import {
  workspaceData,
  taskBody,
  reportFields,
} from "../src/model-workspace/view-model.js";
test("workspace never reads another competition model", () => {
  const data = {
    competition_workspaces: {
      "jp.j1": { training: { model: { run_id: "jp" } } },
      "kr.k1": { training: { model: { run_id: "kr" } } },
    },
  };
  assert.equal(workspaceData(data, "kr.k1").training.model.run_id, "kr");
});
test("fixed model evaluation requires model and ordered dates", () => {
  assert.throws(() => taskBody({ kind: "evaluate" }, "digest"));
  assert.throws(() =>
    taskBody(
      {
        kind: "evaluate",
        run_id: "r",
        date_from: "2026-02-01",
        date_to: "2026-01-01",
      },
      "d",
    ),
  );
  assert.deepEqual(
    taskBody(
      {
        kind: "evaluate",
        target: "had",
        run_id: "r",
        date_from: "2026-01-01",
        date_to: "2026-02-01",
        algorithm: "poisson",
      },
      "d",
    ),
    {
      kind: "evaluate",
      target: "had",
      dataset_digest: "d",
      run_id: "r",
      date_from: "2026-01-01",
      date_to: "2026-02-01",
    },
  );
});
test("fit pins algorithm and digest without implicitly requesting evaluation", () => {
  assert.deepEqual(
    taskBody(
      {
        kind: "fit",
        target: "full_time",
        algorithm: "poisson",
        half_life: 180,
      },
      "d",
    ),
    {
      kind: "fit",
      target: "full_time",
      dataset_digest: "d",
      algorithm: "poisson",
      half_life: 180,
    },
  );
  assert.throws(() =>
    taskBody({ kind: "fit", algorithm: "poisson", half_life: 180 }, ""),
  );
});
test("exact report preserves requested identity and no candidate fallback", () => {
  const fields = reportFields(
    { report: { run_id: "selected", report_type: "fixed_artifact" } },
    "selected",
  );
  assert.equal(fields[0][1], "selected");
  assert.ok(
    fields.some(
      ([label, value]) => label === "报告类型" && value === "fixed_artifact",
    ),
  );
  assert.ok(!JSON.stringify(fields).includes("candidate"));
});
test("experiment explicitly requests orchestration without pretending a single algorithm", () => {
  assert.deepEqual(
    taskBody(
      {
        kind: "experiment",
        target: "full_time",
        algorithm: "poisson",
        half_life: 180,
      },
      "d",
    ),
    { kind: "experiment", target: "full_time", dataset_digest: "d" },
  );
});
