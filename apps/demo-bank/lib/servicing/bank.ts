/** Synthetic training rules, not a financial product or a production ledger. */
export type Tenant = "harbor" | "summit";
export type Role = "servicing" | "inquiry" | "supervisor";
export type AccountKind = "Checking" | "Savings" | "Auto loan";
export interface Member {
  id: string;
  name: string;
  city: string;
  branch: string;
  since: string;
  status: "Active" | "Restricted";
  notice: string;
}
export interface Account {
  id: string;
  memberId: string;
  kind: AccountKind;
  suffix: string;
  openingCents: number;
  status: "Open" | "Frozen";
  aprBps: number;
  accruedCents: number;
}
export interface Posting {
  id: string;
  accountId: string;
  date: string;
  description: string;
  cents: number;
  status: "Posted" | "Pending";
  reference: string;
}
export interface Hold {
  id: string;
  accountId: string;
  cents: number;
  reason: string;
  status: "Active" | "Released";
}
export interface Card {
  id: string;
  accountId: string;
  suffix: string;
  holder: string;
  status: "Active" | "Temporarily locked" | "Expired";
}
export interface ServiceCase {
  id: string;
  memberId: string;
  accountId: string;
  category: string;
  subject: string;
  status: "Open" | "Resolved";
  resolution: string;
}
export interface Receipt {
  reference: string;
  title: string;
  details: [string, string][];
}
export interface AuditEntry {
  reference: string;
  operation: string;
  memberId: string;
  role: Role;
  date: string;
}
export interface Bank {
  tenant: Tenant;
  role: Role;
  businessDate: string;
  revision: number;
  members: Member[];
  accounts: Account[];
  postings: Posting[];
  holds: Hold[];
  cards: Card[];
  cases: ServiceCase[];
  journal: AuditEntry[];
  completed: Record<string, { fingerprint: string; receipt: Receipt }>;
}
export type Command =
  | {
      kind: "transfer";
      memberId: string;
      from: string;
      to: string;
      amount: string;
      memo: string;
    }
  | {
      kind: "card";
      memberId: string;
      cardId: string;
      desired: "Active" | "Temporarily locked";
      reason: string;
    }
  | {
      kind: "hold";
      memberId: string;
      accountId: string;
      amount: string;
      reason: string;
    }
  | { kind: "release"; memberId: string; holdId: string; reason: string }
  | { kind: "quote"; memberId: string; accountId: string; date: string }
  | {
      kind: "case";
      memberId: string;
      accountId: string;
      category: string;
      subject: string;
    }
  | { kind: "resolve"; memberId: string; caseId: string; resolution: string };

export class BankError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}
function requireRule(
  condition: unknown,
  code: string,
  message: string,
): asserts condition {
  if (!condition) throw new BankError(code, message);
}
export function money(cents: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}
export function parseMoney(value: string): number {
  requireRule(
    /^(0|[1-9]\d{0,6})(\.\d{1,2})?$/.test(value.trim()),
    "invalid_amount",
    "Enter dollars and cents, without commas; at most two decimal places.",
  );
  const [whole, fraction = ""] = value.trim().split(".");
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  requireRule(
    cents > 0 && cents <= 100_000_000,
    "amount_limit",
    "Amount must be between $0.01 and $1,000,000.00.",
  );
  return cents;
}
export function ledger(bank: Bank, id: string): number {
  const account = bank.accounts.find((item) => item.id === id);
  requireRule(account, "account_missing", "Account no longer exists.");
  return (
    account.openingCents +
    bank.postings
      .filter((p) => p.accountId === id && p.status === "Posted")
      .reduce((sum, p) => sum + p.cents, 0)
  );
}
export function available(bank: Bank, id: string): number {
  return (
    ledger(bank, id) -
    bank.holds
      .filter((h) => h.accountId === id && h.status === "Active")
      .reduce((sum, h) => sum + h.cents, 0)
  );
}
export function searchMembers(bank: Bank, query: string): Member[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return bank.members.filter((m) =>
    [m.id, m.name, m.city].some((value) =>
      value.toLowerCase().includes(needle),
    ),
  );
}
function owned(bank: Bank, memberId: string, accountId: string): Account {
  const member = bank.members.find((m) => m.id === memberId);
  requireRule(member, "member_missing", "Select a valid member.");
  requireRule(
    member.status !== "Restricted",
    "member_restricted",
    "Member is restricted. Refer to the branch supervisor; no servicing changes are allowed.",
  );
  const account = bank.accounts.find(
    (a) => a.id === accountId && a.memberId === memberId,
  );
  requireRule(
    account,
    "account_mismatch",
    "Account does not belong to the selected member.",
  );
  requireRule(
    account.status === "Open",
    "account_frozen",
    "Account is frozen. This operation cannot proceed.",
  );
  return account;
}
function reason(value: string): string {
  requireRule(
    value.trim().length >= 8 && value.trim().length <= 160,
    "reason_required",
    "Enter a meaningful reason of 8–160 characters.",
  );
  return value.trim();
}
function dateNumber(value: string): number {
  requireRule(
    /^\d{4}-\d{2}-\d{2}$/.test(value),
    "invalid_date",
    "Use YYYY-MM-DD for the date.",
  );
  const parsed = Date.parse(`${value}T00:00:00Z`);
  requireRule(
    Number.isFinite(parsed) &&
      new Date(parsed).toISOString().slice(0, 10) === value,
    "invalid_date",
    "That calendar date does not exist.",
  );
  return parsed;
}
export function payoff(
  bank: Bank,
  memberId: string,
  accountId: string,
  date: string,
) {
  const account = owned(bank, memberId, accountId);
  requireRule(
    account.kind === "Auto loan",
    "not_loan",
    "Select a loan account.",
  );
  const days = (dateNumber(date) - dateNumber(bank.businessDate)) / 86_400_000;
  requireRule(
    days >= 0 && days <= 30,
    "date_window",
    "Quote date must be within 30 days of the business date, inclusive.",
  );
  const principal = ledger(bank, accountId);
  // Simple ACT/365 interest; round once to cents, half upward, using integer arithmetic.
  const numerator = BigInt(principal) * BigInt(account.aprBps) * BigInt(days);
  const interest = Number((numerator + 1_825_000n) / 3_650_000n);
  return {
    principal,
    accrued: account.accruedCents,
    interest,
    total: principal + account.accruedCents + interest,
    days,
  };
}

export function execute(
  bank: Bank,
  command: Command,
  key: string,
  expectedRevision: number,
): { bank: Bank; receipt: Receipt } {
  requireRule(
    bank.role !== "inquiry",
    "permission_denied",
    "Inquiry-only operator: changes and quote issuance require servicing permission.",
  );
  requireRule(
    /^[a-zA-Z0-9-]{8,80}$/.test(key),
    "invalid_request",
    "Invalid operation reference.",
  );
  const fingerprint = JSON.stringify(command);
  const completed = bank.completed[key];
  if (completed) {
    requireRule(
      completed.fingerprint === fingerprint,
      "request_conflict",
      "This operation reference was already used for different instructions.",
    );
    return { bank, receipt: structuredClone(completed.receipt) };
  }
  requireRule(
    expectedRevision === bank.revision,
    "stale_review",
    "Records changed after review. Cancel and review the operation again.",
  );
  const next = structuredClone(bank);
  const reference = `${bank.tenant === "harbor" ? "HBR" : "SUM"}-${String(bank.revision + 1).padStart(6, "0")}`;
  let details: [string, string][] = [];
  let title = "";
  if (command.kind === "transfer") {
    const from = owned(bank, command.memberId, command.from);
    const to = owned(bank, command.memberId, command.to);
    requireRule(
      from.id !== to.id,
      "same_account",
      "Debit and credit accounts must differ.",
    );
    requireRule(
      from.kind !== "Auto loan" && to.kind !== "Auto loan",
      "deposit_only",
      "Internal transfers support deposit accounts only.",
    );
    const cents = parseMoney(command.amount);
    requireRule(
      cents <= available(bank, from.id),
      "insufficient_funds",
      "Insufficient available funds after active holds.",
    );
    const memo = reason(command.memo);
    next.postings.push(
      {
        id: `${reference}-D`,
        accountId: from.id,
        cents: -cents,
        date: bank.businessDate,
        description: `Transfer to ${to.suffix}: ${memo}`,
        status: "Posted",
        reference,
      },
      {
        id: `${reference}-C`,
        accountId: to.id,
        cents,
        date: bank.businessDate,
        description: `Transfer from ${from.suffix}: ${memo}`,
        status: "Posted",
        reference,
      },
    );
    title = "Internal transfer posted";
    details = [
      ["Debit account", from.id],
      ["Credit account", to.id],
      ["Amount", money(cents)],
      ["Purpose", memo],
      ["Source available", money(available(next, from.id))],
    ];
  } else if (command.kind === "card") {
    const card = next.cards.find((c) => c.id === command.cardId);
    requireRule(card, "card_missing", "Select an existing card.");
    owned(bank, command.memberId, card.accountId);
    reason(command.reason);
    requireRule(
      card.status !== "Expired",
      "card_expired",
      "Expired cards cannot be locked or unlocked.",
    );
    requireRule(
      card.status !== command.desired,
      "already_in_state",
      `Card is already ${card.status.toLowerCase()}; no change was made.`,
    );
    card.status = command.desired;
    title =
      command.desired === "Active"
        ? "Card unlocked"
        : "Card temporarily locked";
    details = [
      ["Card ID", card.id],
      ["Card last 4", card.suffix],
      ["Cardholder", card.holder],
      ["Lock status", card.status],
      ["Reason", command.reason.trim()],
    ];
  } else if (command.kind === "hold") {
    requireRule(
      bank.role === "supervisor",
      "permission_denied",
      "Placing an administrative hold requires the supervisor role.",
    );
    const account = owned(bank, command.memberId, command.accountId);
    requireRule(
      account.kind !== "Auto loan",
      "deposit_only",
      "Holds apply to deposit accounts only.",
    );
    const cents = parseMoney(command.amount);
    requireRule(
      cents <= available(bank, account.id),
      "insufficient_funds",
      "Hold cannot exceed available funds.",
    );
    next.holds.push({
      id: reference,
      accountId: account.id,
      cents,
      reason: reason(command.reason),
      status: "Active",
    });
    title = "Administrative hold placed";
    details = [
      ["Account", account.id],
      ["Amount", money(cents)],
      ["Reason", command.reason.trim()],
      ["Available balance", money(available(next, account.id))],
    ];
  } else if (command.kind === "release") {
    requireRule(
      bank.role === "supervisor",
      "permission_denied",
      "Releasing a hold requires the supervisor role.",
    );
    const hold = next.holds.find((h) => h.id === command.holdId);
    requireRule(hold, "hold_missing", "Hold no longer exists.");
    owned(bank, command.memberId, hold.accountId);
    reason(command.reason);
    requireRule(
      hold.status === "Active",
      "hold_released",
      "Hold has already been released.",
    );
    hold.status = "Released";
    title = "Hold released";
    details = [
      ["Hold reference", hold.id],
      ["Released amount", money(hold.cents)],
      ["Reason", command.reason.trim()],
      ["Available balance", money(available(next, hold.accountId))],
    ];
  } else if (command.kind === "quote") {
    const quote = payoff(
      bank,
      command.memberId,
      command.accountId,
      command.date,
    );
    title = "Payoff quote issued";
    details = [
      ["Loan account", command.accountId],
      ["Principal", money(quote.principal)],
      ["Accrued interest", money(quote.accrued)],
      ["Additional interest", money(quote.interest)],
      ["Payoff amount", money(quote.total)],
      ["Good through", command.date],
    ];
  } else if (command.kind === "case") {
    owned(bank, command.memberId, command.accountId);
    requireRule(
      ["Transaction inquiry", "Account maintenance", "Card inquiry"].includes(
        command.category,
      ),
      "invalid_category",
      "Select a supported service category.",
    );
    next.cases.push({
      id: reference,
      memberId: command.memberId,
      accountId: command.accountId,
      category: command.category,
      subject: reason(command.subject),
      status: "Open",
      resolution: "",
    });
    title = "Service case opened";
    details = [
      ["Case ID", reference],
      ["Account", command.accountId],
      ["Category", command.category],
      ["Request summary", command.subject.trim()],
      ["Status", "Open"],
    ];
  } else {
    const item = next.cases.find(
      (c) => c.id === command.caseId && c.memberId === command.memberId,
    );
    requireRule(
      item,
      "case_missing",
      "Case does not belong to the selected member.",
    );
    owned(bank, command.memberId, item.accountId);
    requireRule(
      item.status === "Open",
      "case_resolved",
      "Case is already resolved.",
    );
    item.status = "Resolved";
    item.resolution = reason(command.resolution);
    title = "Service case resolved";
    details = [
      ["Case ID", item.id],
      ["Status", item.status],
      ["Resolution", item.resolution],
    ];
  }
  const receipt: Receipt = {
    reference,
    title,
    details: [
      ["Member ID", command.memberId],
      ...details,
      ["Business date", bank.businessDate],
    ],
  };
  next.revision += 1;
  next.journal.push({
    reference,
    operation: title,
    memberId: command.memberId,
    role: bank.role,
    date: bank.businessDate,
  });
  next.completed[key] = { fingerprint, receipt: structuredClone(receipt) };
  return { bank: next, receipt };
}

export function createBank(tenant: Tenant): Bank {
  const members: Member[] = [
    {
      id: "12345",
      name: "Alex Morgan",
      city: "Port Mason",
      branch: "014 / Central",
      since: "2009-04-18",
      status: "Active",
      notice: "",
    },
    {
      id: "12346",
      name: "Alexandra Morgan",
      city: "Fairhaven",
      branch: "022 / North",
      since: "2016-08-03",
      status: "Active",
      notice: "",
    },
    {
      id: "23456",
      name: "Jordan Patel",
      city: "Port Mason",
      branch: "014 / Central",
      since: "2012-11-27",
      status: "Active",
      notice:
        "Address review outstanding. Confirm the member record before proceeding.",
    },
    {
      id: "34567",
      name: "Taylor Chen",
      city: "Westbridge",
      branch: "031 / West",
      since: "2019-02-12",
      status: "Restricted",
      notice: "Restricted servicing. Contact branch supervisor.",
    },
    {
      id: "45678",
      name: "Sam Rivera",
      city: "Fairhaven",
      branch: "022 / North",
      since: "2021-05-19",
      status: "Active",
      notice: "",
    },
    {
      id: "56789",
      name: "Morgan Lee",
      city: "Westbridge",
      branch: "031 / West",
      since: "2010-09-22",
      status: "Active",
      notice: "",
    },
  ];
  const accounts: Account[] = members.flatMap((m, index) => [
    {
      id: `${m.id}-01`,
      memberId: m.id,
      kind: "Checking" as const,
      suffix: String(110 + index * 113).padStart(4, "0"),
      openingCents: 245000 + index * 37115,
      status: "Open" as const,
      aprBps: 0,
      accruedCents: 0,
    },
    {
      id: `${m.id}-02`,
      memberId: m.id,
      kind: "Savings" as const,
      suffix: String(421 + index * 117).padStart(4, "0"),
      openingCents: 142057 + index * 72500,
      status: index === 4 ? ("Frozen" as const) : ("Open" as const),
      aprBps: 0,
      accruedCents: 0,
    },
    {
      id: `${m.id}-03`,
      memberId: m.id,
      kind: "Auto loan" as const,
      suffix: String(9902 - index * 131),
      openingCents: 780000 + index * 120000,
      status: "Open" as const,
      aprBps: 725,
      accruedCents: 2140,
    },
  ]);
  const postings: Posting[] = members.flatMap((m, index) => [
    {
      id: `${m.id}-P1`,
      accountId: `${m.id}-01`,
      date: "2026-09-11",
      description: "ACH CREDIT / PAYROLL NORTHSTAR",
      cents: 185000 + index * 2000,
      status: "Posted" as const,
      reference: `ACH-${1001 + index}`,
    },
    {
      id: `${m.id}-P2`,
      accountId: `${m.id}-01`,
      date: "2026-09-08",
      description: "NORTHWIND MARKET / POS PURCHASE",
      cents: -8427,
      status: "Posted" as const,
      reference: `POS-${80419 + index * 10}`,
    },
    {
      id: `${m.id}-P3`,
      accountId: `${m.id}-01`,
      date: "2026-09-09",
      description: "NORTHWIND MARKET / POS PURCHASE",
      cents: -3162,
      status: "Posted" as const,
      reference: `POS-${80420 + index * 10}`,
    },
    {
      id: `${m.id}-P4`,
      accountId: `${m.id}-01`,
      date: "2026-09-13",
      description: "CONTOSO FUEL / AUTHORIZATION",
      cents: -5210,
      status: "Pending" as const,
      reference: `AUTH-${7001 + index}`,
    },
    {
      id: `${m.id}-P5`,
      accountId: `${m.id}-01`,
      date: "2026-09-10",
      description: "ONLINE PAYMENT / UTILITIES",
      cents: -13749,
      status: "Posted" as const,
      reference: `BILL-${4001 + index}`,
    },
  ]);
  const cards: Card[] = members.flatMap((m) => [
    {
      id: `${m.id}-D1`,
      accountId: `${m.id}-01`,
      suffix: "0110",
      holder: m.name,
      status: "Active" as const,
    },
    {
      id: `${m.id}-D2`,
      accountId: `${m.id}-01`,
      suffix: "7824",
      holder: `${m.name} / Joint`,
      status: "Temporarily locked" as const,
    },
    {
      id: `${m.id}-D3`,
      accountId: `${m.id}-01`,
      suffix: "9301",
      holder: m.name,
      status: "Expired" as const,
    },
  ]);
  return {
    tenant,
    role: "servicing",
    businessDate: "2026-09-13",
    revision: 0,
    members: tenant === "summit" ? [...members].reverse() : members,
    accounts,
    postings,
    cards,
    holds: members.map((m, index) => ({
      id: `AUTH-${7001 + index}`,
      accountId: `${m.id}-01`,
      cents: 5210,
      reason: "Contoso Fuel authorization",
      status: "Active",
    })),
    cases: [
      {
        id: "CASE-2104",
        memberId: "12345",
        accountId: "12345-01",
        category: "Transaction inquiry",
        subject: "Member requests duplicate merchant charge review",
        status: "Open",
        resolution: "",
      },
    ],
    journal: [],
    completed: {},
  };
}
