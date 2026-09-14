import assert from "node:assert/strict";
import { test } from "node:test";
import { act, buildView, createWorkspace } from "../lib/servicing/workspace.ts";
import { ledger } from "../lib/servicing/bank.ts";

const member = () => act(createWorkspace("harbor"), "member:12345");
const transferForm = () => {
  const state = act(member(), "nav:transfers");
  return {
    ...state,
    fields: {
      ...state.fields,
      amount: "25.00",
      memo: "Member requested transfer",
    },
  };
};
test("review and cancel are non-mutating; confirmation commits once", () => {
  const start = transferForm();
  const review = act(start, "review-transfer");
  assert.equal(review.page, "review");
  assert.equal(review.bank, start.bank);
  assert.equal(act(review, "cancel").bank, start.bank);
  const done = act(review, "confirm");
  assert.equal(done.page, "receipt");
  assert.equal(done.bank.journal.length, 1);
  assert.equal(
    ledger(done.bank, "12345-01"),
    ledger(start.bank, "12345-01") - 2500,
  );
  assert.equal(act(done, "confirm").bank.journal.length, 1);
});
test("navigation cannot bypass member selection or carry a pending review to another member", () => {
  assert.match(
    act(createWorkspace("harbor"), "nav:cards").error,
    /member_required/,
  );
  const review = act(transferForm(), "review-transfer");
  const switched = act(review, "member:12346");
  assert.equal(switched.pending, undefined);
  assert.equal(act(switched, "confirm").bank.revision, 0);
});
test("notice acknowledgement gates servicing, not inquiry", () => {
  let state = act(createWorkspace("harbor"), "member:23456");
  state = act(state, "nav:loans");
  assert.match(act(state, "review-quote").error, /review_notice/);
  state = act(act(state, "nav:member"), "acknowledge");
  assert.equal(act(act(state, "nav:loans"), "review-quote").page, "review");
});
test("invalid transaction dates preserve previously applied filters", () => {
  const state = act(member(), "nav:transactions");
  const invalid = act(
    { ...state, fields: { ...state.fields, startDate: "2026-02-30" } },
    "apply-filter",
  );
  assert.match(invalid.error, /invalid_date/);
  assert.deepEqual(invalid.filter, state.filter);
});
test("transaction research filters real rows rather than returning a canned answer", () => {
  let state = act(member(), "nav:transactions");
  state = act(
    {
      ...state,
      fields: {
        ...state.fields,
        text: "NORTHWIND",
        postingStatus: "Posted",
        startDate: "2026-09-09",
        endDate: "2026-09-09",
      },
    },
    "apply-filter",
  );
  const table = buildView(state).blocks.find((b) => b.kind === "table");
  assert.equal(table.rows.length, 1);
  assert.equal(table.rows[0].cells[2], "POS-80420");
});
test("stale review is rejected at confirmation, not applied from the preview", () => {
  const review = act(transferForm(), "review-transfer");
  const changed = { ...review, bank: { ...review.bank, revision: 1 } };
  const result = act(changed, "confirm");
  assert.match(result.error, /stale_review/);
  assert.equal(result.bank.journal.length, 0);
});
test("role switching is explicit training configuration and affects business validation", () => {
  const settings = act(member(), "nav:settings");
  const inquiry = act({ ...settings, fields: { role: "inquiry" } }, "set-role");
  assert.equal(inquiry.bank.role, "inquiry");
  assert.match(
    act(act(inquiry, "nav:loans"), "review-quote").error,
    /permission_denied/,
  );
});
test("relationship history includes resolved cases without labeling them open", () => {
  let state = act(member(), "case:CASE-2104");
  state = {
    ...state,
    fields: { resolution: "Explained distinct merchant posting dates" },
  };
  state = act(act(state, "resolve"), "confirm");
  const view = buildView(act(state, "nav:member"));
  assert.ok(
    view.blocks.some(
      (b) => b.kind === "heading" && b.text === "Service request history",
    ),
  );
  const cases = view.blocks.find(
    (b) => b.kind === "table" && b.columns[0] === "Case ID",
  );
  assert.equal(cases.rows[0].cells[3], "Resolved");
});
