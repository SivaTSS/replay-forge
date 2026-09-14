import assert from "node:assert/strict";
import { test } from "node:test";
import {
  available,
  createBank,
  execute,
  ledger,
  parseMoney,
  payoff,
  searchMembers,
} from "../lib/servicing/bank.ts";

const transfer = {
  kind: "transfer",
  memberId: "12345",
  from: "12345-01",
  to: "12345-02",
  amount: "125.50",
  memo: "Member requested allocation",
};
const run = (bank, command, key = "request-0001", revision = bank.revision) =>
  execute(bank, command, key, revision);
const rejected = (bank, command, code) => {
  const before = structuredClone(bank);
  assert.throws(
    () => run(bank, command, "rejected-request"),
    (error) => error.code === code,
  );
  assert.deepEqual(
    bank,
    before,
    "a rejected operation must not partially mutate state",
  );
};

test("money uses exact cents and rejects ambiguous or excessive values", () => {
  assert.equal(parseMoney("125.50"), 12550);
  assert.equal(parseMoney("0.01"), 1);
  for (const value of [
    "0",
    "-1",
    "1e3",
    "1.001",
    "NaN",
    "1,200",
    "1000000.01",
    "",
    "01",
  ])
    assert.throws(() => parseMoney(value));
});
test("search exposes genuine ambiguity and unknown members", () => {
  const bank = createBank("harbor");
  assert.equal(searchMembers(bank, "Morgan").length, 3);
  assert.equal(searchMembers(bank, "12345")[0].name, "Alex Morgan");
  assert.deepEqual(searchMembers(bank, "nobody"), []);
  assert.deepEqual(searchMembers(bank, ""), []);
});
test("posted balance excludes pending authorization; available subtracts its hold once", () => {
  const bank = createBank("harbor");
  assert.equal(ledger(bank, "12345-01"), 404662);
  assert.equal(available(bank, "12345-01"), 399452);
});
test("transfer conserves funds and posts a linked debit and credit atomically", () => {
  const bank = createBank("harbor");
  const result = run(bank, transfer);
  assert.equal(
    ledger(result.bank, transfer.from),
    ledger(bank, transfer.from) - 12550,
  );
  assert.equal(
    ledger(result.bank, transfer.to),
    ledger(bank, transfer.to) + 12550,
  );
  assert.equal(
    result.bank.postings.slice(-2).reduce((sum, p) => sum + p.cents, 0),
    0,
  );
  assert.equal(result.bank.postings.at(-1).reference, result.receipt.reference);
  assert.equal(bank.revision, 0);
  assert.equal(result.bank.journal.length, 1);
});
test("duplicate confirmation is idempotent and conflicting reuse is rejected", () => {
  const result = run(createBank("harbor"), transfer);
  assert.deepEqual(run(result.bank, transfer, "request-0001", 0), result);
  assert.throws(
    () => run(result.bank, { ...transfer, amount: "1" }),
    (e) => e.code === "request_conflict",
  );
});
test("transfer checks funds, account ownership, account kinds and frozen state", () => {
  const bank = createBank("harbor");
  rejected(bank, { ...transfer, amount: "4000" }, "insufficient_funds");
  rejected(bank, { ...transfer, to: transfer.from }, "same_account");
  rejected(bank, { ...transfer, to: "12346-01" }, "account_mismatch");
  rejected(bank, { ...transfer, to: "12345-03" }, "deposit_only");
  rejected(
    bank,
    { ...transfer, memberId: "45678", from: "45678-01", to: "45678-02" },
    "account_frozen",
  );
});
test("permissions, restricted members and stale reviews fail without mutation", () => {
  rejected(
    { ...createBank("harbor"), role: "inquiry" },
    transfer,
    "permission_denied",
  );
  rejected(
    createBank("harbor"),
    { ...transfer, memberId: "34567", from: "34567-01", to: "34567-02" },
    "member_restricted",
  );
  assert.throws(
    () => run(createBank("harbor"), transfer, "request-0001", 4),
    (e) => e.code === "stale_review",
  );
});
test("card mutations bind full card identity, preserve other cards and permit explicit inverse", () => {
  const bank = createBank("harbor");
  const command = {
    kind: "card",
    memberId: "12345",
    cardId: "12345-D1",
    desired: "Temporarily locked",
    reason: "Member reports misplaced card",
  };
  const locked = run(bank, command);
  assert.equal(
    locked.bank.cards.find((c) => c.id === command.cardId).status,
    "Temporarily locked",
  );
  assert.equal(
    locked.bank.cards.find((c) => c.id === "12346-D1").status,
    "Active",
  );
  assert.equal(
    run(
      locked.bank,
      { ...command, desired: "Active" },
      "request-0002",
    ).bank.cards.find((c) => c.id === command.cardId).status,
    "Active",
  );
  rejected(bank, { ...command, cardId: "12346-D1" }, "account_mismatch");
  rejected(bank, { ...command, cardId: "12345-D3" }, "card_expired");
  rejected(bank, { ...command, desired: "Active" }, "already_in_state");
});
test("supervisor holds affect availability but not posted ledger and release exactly once", () => {
  const bank = { ...createBank("harbor"), role: "supervisor" };
  const command = {
    kind: "hold",
    memberId: "12345",
    accountId: "12345-01",
    amount: "20",
    reason: "Administrative review hold",
  };
  const held = run(bank, command);
  assert.equal(
    ledger(held.bank, command.accountId),
    ledger(bank, command.accountId),
  );
  assert.equal(
    available(held.bank, command.accountId),
    available(bank, command.accountId) - 2000,
  );
  const release = {
    kind: "release",
    memberId: "12345",
    holdId: held.receipt.reference,
    reason: "Review completed by branch",
  };
  const released = run(held.bank, release, "request-0002");
  assert.equal(
    available(released.bank, command.accountId),
    available(bank, command.accountId),
  );
  rejected(released.bank, release, "hold_released");
  rejected(createBank("harbor"), command, "permission_denied");
});
test("payoff dates are real UTC dates; interest rounds once with ACT/365 arithmetic", () => {
  const bank = createBank("harbor");
  assert.deepEqual(payoff(bank, "12345", "12345-03", "2026-09-13"), {
    principal: 780000,
    accrued: 2140,
    interest: 0,
    total: 782140,
    days: 0,
  });
  assert.equal(payoff(bank, "12345", "12345-03", "2026-09-20").interest, 1085);
  for (const date of ["2026-02-30", "2026-09-12", "2026-10-14", "09/20/2026"])
    assert.throws(() => payoff(bank, "12345", "12345-03", date));
  const issued = run(bank, {
    kind: "quote",
    memberId: "12345",
    accountId: "12345-03",
    date: "2026-09-20",
  });
  assert.equal(ledger(issued.bank, "12345-03"), ledger(bank, "12345-03"));
});
test("cases have a bounded lifecycle and require resolution detail", () => {
  const bank = createBank("harbor");
  const opened = run(bank, {
    kind: "case",
    memberId: "12345",
    accountId: "12345-01",
    category: "Transaction inquiry",
    subject: "Review merchant posting date",
  });
  const command = {
    kind: "resolve",
    memberId: "12345",
    caseId: opened.receipt.reference,
    resolution: "Explained the two distinct posting dates",
  };
  const resolved = run(opened.bank, command, "request-0002");
  assert.equal(resolved.bank.cases.at(-1).status, "Resolved");
  rejected(resolved.bank, command, "case_resolved");
  rejected(opened.bank, { ...command, resolution: "ok" }, "reason_required");
});
test("tenant and session state are isolated and returned receipts are detached", () => {
  const harbor = createBank("harbor");
  const summit = createBank("summit");
  const result = run(harbor, transfer);
  result.receipt.details[0][1] = "changed";
  assert.equal(run(result.bank, transfer).receipt.details[0][1], "12345");
  assert.equal(summit.revision, 0);
  assert.equal(createBank("harbor").revision, 0);
  assert.notEqual(harbor.members[0].id, summit.members[0].id);
});
