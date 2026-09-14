import assert from "node:assert/strict";
import { test } from "node:test";
import {
  available,
  createBank,
  execute,
  ledger,
  parseMoney,
  payoff,
} from "../lib/servicing/bank.ts";

const operations = (bank, memberId) => [
  {
    kind: "transfer",
    memberId,
    from: `${memberId}-01`,
    to: `${memberId}-02`,
    amount: "1.25",
    memo: "Member requested allocation",
  },
  {
    kind: "card",
    memberId,
    cardId: `${memberId}-D1`,
    desired: "Temporarily locked",
    reason: "Member misplaced original card",
  },
  {
    kind: "hold",
    memberId,
    accountId: `${memberId}-01`,
    amount: "1.25",
    reason: "Supervisor administrative review",
  },
  {
    kind: "release",
    memberId,
    holdId: bank.holds.find((h) => h.accountId === `${memberId}-01`).id,
    reason: "Supervisor completed review",
  },
  { kind: "quote", memberId, accountId: `${memberId}-03`, date: "2026-09-20" },
  {
    kind: "case",
    memberId,
    accountId: `${memberId}-01`,
    category: "Transaction inquiry",
    subject: "Investigate distinct merchant postings",
  },
];

test("every operation respects each tenant/member/role boundary without partial writes", () => {
  for (const tenant of ["harbor", "summit"]) {
    for (const role of ["inquiry", "servicing", "supervisor"]) {
      const bank = { ...createBank(tenant), role };
      for (const member of bank.members) {
        for (const command of operations(bank, member.id)) {
          const before = structuredClone(bank);
          const denial =
            role === "inquiry" ||
            (role !== "supervisor" &&
              ["hold", "release"].includes(command.kind))
              ? "permission_denied"
              : member.status === "Restricted"
                ? "member_restricted"
                : command.kind === "transfer" && member.id === "45678"
                  ? "account_frozen"
                  : null;
          if (denial) {
            assert.throws(
              () => execute(bank, command, "matrix-request", 0),
              (error) => error.code === denial,
            );
          } else {
            const result = execute(bank, command, "matrix-request", 0);
            assert.equal(result.bank.revision, 1);
            assert.equal(result.bank.journal[0].memberId, member.id);
            assert.ok(
              result.receipt.reference.startsWith(
                tenant === "harbor" ? "HBR-" : "SUM-",
              ),
            );
            assert.equal(
              result.receipt.details.find(([key]) => key === "Member ID")[1],
              member.id,
            );
          }
          assert.deepEqual(bank, before);
        }
      }
    }
  }
});

test("all command types are idempotent, including quote issuance and case resolution", () => {
  const bank = { ...createBank("harbor"), role: "supervisor" };
  for (const command of [
    ...operations(bank, "12345"),
    {
      kind: "resolve",
      memberId: "12345",
      caseId: "CASE-2104",
      resolution: "Explained the distinct posting dates",
    },
  ]) {
    const first = execute(bank, command, "duplicate-request", 0);
    const second = execute(first.bank, command, "duplicate-request", 0);
    assert.equal(second.bank, first.bank);
    assert.deepEqual(second.receipt, first.receipt);
    assert.equal(second.bank.journal.length, 1);
  }
});

test("valid request keys cannot collide with inherited object properties", () => {
  const bank = createBank("harbor");
  const command = operations(bank, "12345")[0];
  const result = execute(bank, command, "constructor", 0);
  assert.equal(result.bank.journal.length, 1);
  assert.equal(
    execute(result.bank, command, "constructor", 0).bank.revision,
    1,
  );
});

test("amount, reason, category and calendar boundary cases are enforced", () => {
  assert.equal(parseMoney("1000000.00"), 100_000_000);
  assert.equal(parseMoney(" 0.01 "), 1);
  const bank = createBank("harbor");
  for (let days = 0; days <= 30; days++) {
    const date = new Date(Date.UTC(2026, 8, 13 + days))
      .toISOString()
      .slice(0, 10);
    const quote = payoff(bank, "12345", "12345-03", date);
    assert.equal(quote.days, days);
    assert.equal(
      quote.interest,
      Number((780000n * 725n * BigInt(days) + 1825000n) / 3650000n),
    );
  }
  const command = operations(bank, "12345").find((c) => c.kind === "case");
  for (const length of [0, 7, 161]) {
    assert.throws(
      () =>
        execute(
          bank,
          { ...command, subject: "X".repeat(length) },
          "boundary-request",
          0,
        ),
      (e) => e.code === "reason_required",
    );
  }
  for (const length of [8, 160]) {
    assert.equal(
      execute(
        bank,
        { ...command, subject: "X".repeat(length) },
        "boundary-request",
        0,
      ).bank.cases.at(-1).subject.length,
      length,
    );
  }
  assert.throws(
    () =>
      execute(
        bank,
        { ...command, category: "Unsupported" },
        "boundary-request",
        0,
      ),
    (e) => e.code === "invalid_category",
  );
});

test("repeated mixed servicing conserves posted money and reconciles available balances", () => {
  let bank = { ...createBank("harbor"), role: "supervisor" };
  const deposits = bank.accounts.filter((a) => a.kind !== "Auto loan");
  const total = (state) =>
    deposits.reduce((sum, a) => sum + ledger(state, a.id), 0);
  const startingTotal = total(bank);
  for (let index = 0; index < 100; index++) {
    const from = index % 2 === 0 ? "12345-01" : "12345-02";
    const to = from === "12345-01" ? "12345-02" : "12345-01";
    const before = bank;
    bank = execute(
      bank,
      {
        kind: "transfer",
        memberId: "12345",
        from,
        to,
        amount: "1.25",
        memo: "Deterministic regression sequence",
      },
      `sequence-${index}`,
      bank.revision,
    ).bank;
    assert.equal(total(bank), startingTotal);
    assert.equal(ledger(bank, from), ledger(before, from) - 125);
    assert.equal(ledger(bank, to), ledger(before, to) + 125);
    for (const account of deposits) {
      assert.equal(
        available(bank, account.id),
        ledger(bank, account.id) -
          bank.holds
            .filter((h) => h.accountId === account.id && h.status === "Active")
            .reduce((sum, h) => sum + h.cents, 0),
      );
      assert.ok(Number.isSafeInteger(ledger(bank, account.id)));
    }
  }
  assert.equal(new Set(bank.journal.map((j) => j.reference)).size, 100);
  assert.equal(
    new Set(bank.postings.map((p) => p.id)).size,
    bank.postings.length,
  );
});
