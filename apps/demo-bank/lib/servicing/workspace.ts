import {
  available,
  BankError,
  createBank,
  execute,
  ledger,
  money,
  searchMembers,
} from "./bank.ts";
import type { Bank, Command, Receipt, Tenant } from "./bank.ts";

export interface Option {
  value: string;
  label: string;
}
export type Block =
  | { kind: "note"; text: string; tone?: "warning" | "error" }
  | { kind: "heading"; text: string }
  | { kind: "values"; rows: [string, string][] }
  | {
      kind: "fields";
      fields: {
        key: string;
        label: string;
        options?: Option[];
        maxLength?: number;
      }[];
    }
  | {
      kind: "actions";
      items: { id: string; label: string; primary?: boolean }[];
    }
  | {
      kind: "table";
      columns: string[];
      rows: { cells: string[]; action?: string; label?: string }[];
    };
export type Page =
  | "inquiry"
  | "member"
  | "accounts"
  | "transactions"
  | "transaction"
  | "transfers"
  | "cards"
  | "card"
  | "holds"
  | "loans"
  | "cases"
  | "case"
  | "journal"
  | "settings"
  | "review"
  | "receipt";
export interface Workspace {
  bank: Bank;
  page: Page;
  memberId: string;
  accountId: string;
  selectedId: string;
  fields: Record<string, string>;
  query: string;
  filter: { text: string; from: string; to: string; status: string };
  error: string;
  noticeAccepted: string[];
  requestNumber: number;
  pending?: {
    command: Command;
    key: string;
    revision: number;
    receipt: Receipt;
    returnPage: Page;
    returnFields: Record<string, string>;
  };
  receipt?: Receipt;
}
export const NAV: { id: Page; label: string }[] = [
  { id: "inquiry", label: "Member inquiry" },
  { id: "member", label: "Relationship summary" },
  { id: "accounts", label: "Account balances" },
  { id: "transactions", label: "Transaction research" },
  { id: "transfers", label: "Internal transfers" },
  { id: "cards", label: "Card maintenance" },
  { id: "holds", label: "Holds / restrictions" },
  { id: "loans", label: "Loan servicing" },
  { id: "cases", label: "Service cases" },
  { id: "journal", label: "Activity journal" },
  { id: "settings", label: "Workstation" },
];
export function createWorkspace(tenant: Tenant): Workspace {
  return {
    bank: createBank(tenant),
    page: "inquiry",
    memberId: "",
    accountId: "",
    selectedId: "",
    fields: { query: "", role: "servicing" },
    query: "",
    filter: { text: "", from: "", to: "", status: "All" },
    error: "",
    noticeAccepted: [],
    requestNumber: 1,
  };
}
function accountOptions(state: Workspace, loans = false): Option[] {
  return state.bank.accounts
    .filter(
      (a) =>
        a.memberId === state.memberId &&
        (loans ? a.kind === "Auto loan" : a.kind !== "Auto loan"),
    )
    .map((a) => ({ value: a.id, label: `${a.kind} ${a.id} / ${a.status}` }));
}
function enter(state: Workspace, page: Page): Workspace {
  if (page === "inquiry") {
    state = {
      ...state,
      memberId: "",
      accountId: "",
      selectedId: "",
      receipt: undefined,
      filter: { text: "", from: "", to: "", status: "All" },
    };
  }
  const deposits = accountOptions(state);
  const selected = deposits.some((a) => a.value === state.accountId)
    ? state.accountId
    : (deposits[0]?.value ?? "");
  return {
    ...state,
    page,
    error: "",
    pending: undefined,
    fields: {
      query: state.query,
      account: selected,
      fromAccount: selected,
      toAccount: deposits.find((a) => a.value !== selected)?.value ?? "",
      loan: accountOptions(state, true)[0]?.value ?? "",
      quoteDate: state.bank.businessDate,
      text: state.filter.text,
      startDate: state.filter.from,
      endDate: state.filter.to,
      postingStatus: state.filter.status,
      category: "Transaction inquiry",
      role: state.bank.role,
    },
  };
}
export function act(state: Workspace, action: string): Workspace {
  try {
    if (action.startsWith("nav:")) {
      const page = NAV.find((n) => n.id === action.slice(4))?.id;
      if (!page) return state;
      if (!["inquiry", "journal", "settings"].includes(page) && !state.memberId)
        throw new BankError(
          "member_required",
          "Select a member from Member inquiry first.",
        );
      return enter(state, page);
    }
    if (action === "search")
      return { ...state, query: state.fields.query?.trim() ?? "", error: "" };
    if (action.startsWith("member:")) {
      const id = action.slice(7);
      if (!state.bank.members.some((m) => m.id === id)) return state;
      return enter(
        {
          ...state,
          memberId: id,
          accountId: `${id}-01`,
          selectedId: "",
          filter: { text: "", from: "", to: "", status: "All" },
        },
        "member",
      );
    }
    if (action === "acknowledge")
      return {
        ...state,
        noticeAccepted: [...state.noticeAccepted, state.memberId],
      };
    if (action.startsWith("account:"))
      return enter({ ...state, accountId: action.slice(8) }, "transactions");
    if (action.startsWith("transaction:"))
      return {
        ...state,
        page: "transaction",
        selectedId: action.slice(12),
        error: "",
      };
    if (action.startsWith("card:"))
      return {
        ...state,
        page: "card",
        selectedId: action.slice(5),
        fields: { reason: "" },
        error: "",
      };
    if (action.startsWith("case:"))
      return {
        ...state,
        page: "case",
        selectedId: action.slice(5),
        fields: { resolution: "" },
        error: "",
      };
    if (action === "apply-filter") {
      const start = state.fields.startDate ?? "";
      const end = state.fields.endDate ?? "";
      for (const date of [start, end]) {
        if (
          date &&
          (!/^\d{4}-\d{2}-\d{2}$/.test(date) ||
            !Number.isFinite(Date.parse(`${date}T00:00:00Z`)) ||
            new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date)
        )
          throw new BankError(
            "invalid_date",
            "Filter dates must be valid YYYY-MM-DD dates.",
          );
      }
      if (start && end && start > end)
        throw new BankError(
          "invalid_range",
          "Start date must not follow end date.",
        );
      return {
        ...state,
        accountId: state.fields.account ?? "",
        filter: {
          text: state.fields.text ?? "",
          from: start,
          to: end,
          status: state.fields.postingStatus ?? "All",
        },
        error: "",
      };
    }
    if (action === "clear-filter")
      return enter(
        { ...state, filter: { text: "", from: "", to: "", status: "All" } },
        "transactions",
      );
    if (action === "set-role") {
      const role = state.fields.role;
      if (role !== "inquiry" && role !== "servicing" && role !== "supervisor")
        return state;
      return {
        ...state,
        bank: { ...state.bank, role },
        error: "",
        pending: undefined,
      };
    }
    if (action === "cancel") {
      const pending = state.pending;
      const returned = enter(state, pending?.returnPage ?? "member");
      return pending
        ? { ...returned, fields: { ...pending.returnFields } }
        : returned;
    }
    if (action === "confirm" && state.pending) {
      const result = execute(
        state.bank,
        state.pending.command,
        state.pending.key,
        state.pending.revision,
      );
      return {
        ...state,
        bank: result.bank,
        page: "receipt",
        receipt: result.receipt,
        pending: undefined,
        error: "",
      };
    }
    const value = (key: string) => state.fields[key] ?? "";
    let command: Command | undefined;
    if (action === "review-transfer")
      command = {
        kind: "transfer",
        memberId: state.memberId,
        from: value("fromAccount"),
        to: value("toAccount"),
        amount: value("amount"),
        memo: value("memo"),
      };
    if (action === "lock" || action === "unlock")
      command = {
        kind: "card",
        memberId: state.memberId,
        cardId: state.selectedId,
        desired: action === "lock" ? "Temporarily locked" : "Active",
        reason: value("reason"),
      };
    if (action === "review-hold")
      command = {
        kind: "hold",
        memberId: state.memberId,
        accountId: value("account"),
        amount: value("amount"),
        reason: value("reason"),
      };
    if (action.startsWith("release:"))
      command = {
        kind: "release",
        memberId: state.memberId,
        holdId: action.slice(8),
        reason: value("reason"),
      };
    if (action === "review-quote")
      command = {
        kind: "quote",
        memberId: state.memberId,
        accountId: value("loan"),
        date: value("quoteDate"),
      };
    if (action === "review-case")
      command = {
        kind: "case",
        memberId: state.memberId,
        accountId: value("account"),
        category: value("category"),
        subject: value("subject"),
      };
    if (action === "resolve")
      command = {
        kind: "resolve",
        memberId: state.memberId,
        caseId: state.selectedId,
        resolution: value("resolution"),
      };
    if (!command) return state;
    const member = state.bank.members.find((m) => m.id === state.memberId);
    if (member?.notice && !state.noticeAccepted.includes(member.id))
      throw new BankError(
        "review_notice",
        "Open Relationship summary and acknowledge the member notice before servicing.",
      );
    const key = `request-${String(state.requestNumber).padStart(8, "0")}`;
    // Validate against a detached state for review; only confirmation publishes the new state.
    const preview = execute(state.bank, command, key, state.bank.revision);
    return {
      ...state,
      page: "review",
      pending: {
        command,
        key,
        revision: state.bank.revision,
        receipt: preview.receipt,
        returnPage: state.page,
        returnFields: { ...state.fields },
      },
      requestNumber: state.requestNumber + 1,
      error: "",
    };
  } catch (error) {
    if (error instanceof BankError)
      return { ...state, error: `${error.code}: ${error.message}` };
    throw error;
  }
}

const heading = (text: string): Block => ({ kind: "heading", text });
const note = (text: string, tone?: "warning" | "error"): Block => ({
  kind: "note",
  text,
  tone,
});
const actions = (
  ...items: { id: string; label: string; primary?: boolean }[]
): Block => ({ kind: "actions", items });
const values = (...rows: [string, string][]): Block => ({
  kind: "values",
  rows,
});
const fields = (
  ...items: Extract<Block, { kind: "fields" }>["fields"]
): Block => ({ kind: "fields", fields: items });
export function buildView(state: Workspace): {
  title: string;
  subtitle: string;
  blocks: Block[];
} {
  const { bank } = state;
  const member = bank.members.find((m) => m.id === state.memberId);
  const accounts = bank.accounts.filter((a) => a.memberId === state.memberId);
  const ids = new Set(accounts.map((a) => a.id));
  const depositOptions = accountOptions(state);
  let title: string =
    NAV.find((n) => n.id === state.page)?.label ?? "Servicing detail";
  const blocks: Block[] = [];
  if (state.error) blocks.push(note(state.error, "error"));
  if (state.page === "inquiry") {
    blocks.push(
      fields({ key: "query", label: "Member ID / name / city", maxLength: 60 }),
      actions({ id: "search", label: "Search", primary: true }),
      heading(state.query ? "Inquiry results" : "Branch member directory"),
    );
    const matches = state.query
      ? searchMembers(bank, state.query)
      : bank.members;
    blocks.push({
      kind: "table",
      columns: ["Member ID", "Name", "City", "Branch", "Status", ""],
      rows: matches.map((m) => ({
        cells: [m.id, m.name, m.city, m.branch.split(" /")[0] ?? "", m.status],
        action: `member:${m.id}`,
      })),
    });
    blocks.push(
      note(
        matches.length
          ? `${matches.length} record(s). Select the intended member before opening an account.`
          : "No matching members. Refine the inquiry; no record was selected.",
      ),
    );
  } else if (state.page === "member" && member) {
    blocks.push(
      values(
        ["Member ID", member.id],
        ["Legal name", member.name],
        ["Branch", member.branch],
        ["City", member.city],
        ["Member since", member.since],
        ["Servicing status", member.status],
      ),
    );
    if (member.notice)
      blocks.push(
        note(member.notice, "warning"),
        ...(state.noticeAccepted.includes(member.id)
          ? [note("Notice acknowledged for this session.")]
          : [actions({ id: "acknowledge", label: "Acknowledge notice" })]),
      );
    blocks.push(
      heading("Account relationships"),
      accountTable(state),
      heading("Service request history"),
      caseTable(state),
      actions(
        { id: "nav:cards", label: "Card maintenance" },
        { id: "nav:transfers", label: "Internal transfers" },
      ),
    );
  } else if (state.page === "accounts") {
    blocks.push(
      note(
        "Ledger includes posted activity. Available funds subtract active holds; pending authorizations are not posted debits.",
      ),
      accountTable(state),
    );
  } else if (state.page === "transactions") {
    blocks.push(
      fields(
        { key: "account", label: "Account", options: depositOptions },
        { key: "text", label: "Description / reference", maxLength: 60 },
        { key: "startDate", label: "Start date (YYYY-MM-DD)" },
        { key: "endDate", label: "End date (YYYY-MM-DD)" },
        {
          key: "postingStatus",
          label: "Posting status",
          options: ["All", "Posted", "Pending"].map((v) => ({
            value: v,
            label: v,
          })),
        },
      ),
      actions(
        { id: "apply-filter", label: "Apply filter", primary: true },
        { id: "clear-filter", label: "Clear filter" },
      ),
      heading("Transaction register"),
    );
    const f = state.filter;
    const postings = bank.postings
      .filter(
        (p) =>
          p.accountId === state.accountId &&
          (!f.from || p.date >= f.from) &&
          (!f.to || p.date <= f.to) &&
          (f.status === "All" || p.status === f.status) &&
          `${p.description} ${p.reference}`
            .toLowerCase()
            .includes(f.text.toLowerCase()),
      )
      .sort((a, b) => b.date.localeCompare(a.date));
    blocks.push(
      {
        kind: "table",
        columns: ["Date", "Description", "Reference", "Amount", "Status", ""],
        rows: postings.map((p) => ({
          cells: [p.date, p.description, p.reference, money(p.cents), p.status],
          action: `transaction:${p.id}`,
        })),
      },
      note(
        `${postings.length} transaction(s) match. Debit amounts are negative; credits are positive.`,
      ),
    );
  } else if (state.page === "transaction") {
    const p = bank.postings.find(
      (p) => p.id === state.selectedId && ids.has(p.accountId),
    );
    title = "Transaction detail";
    if (p)
      blocks.push(
        values(
          ["Reference", p.reference],
          ["Account", p.accountId],
          ["Description", p.description],
          ["Date", p.date],
          ["Amount", money(p.cents)],
          ["Posting status", p.status],
        ),
        actions(
          { id: "nav:cases", label: "Open service case" },
          { id: "nav:transactions", label: "Back to register" },
        ),
      );
  } else if (state.page === "transfers") {
    blocks.push(
      note(
        "Same-member deposit transfers only. Review verifies funds after holds; confirmation posts both ledger entries together.",
      ),
      fields(
        { key: "fromAccount", label: "Debit account", options: depositOptions },
        { key: "toAccount", label: "Credit account", options: depositOptions },
        { key: "amount", label: "Transfer amount (USD)" },
        { key: "memo", label: "Transfer purpose", maxLength: 160 },
      ),
      actions({
        id: "review-transfer",
        label: "Review transfer",
        primary: true,
      }),
      heading("Current balances"),
      accountTable(state),
    );
  } else if (state.page === "cards") {
    blocks.push(
      note(
        "Card suffixes are not globally unique. Confirm the member, full card ID, account and cardholder.",
      ),
      {
        kind: "table",
        columns: ["Card ID", "Account", "Last 4", "Cardholder", "Status", ""],
        rows: bank.cards
          .filter((c) => ids.has(c.accountId))
          .map((c) => ({
            cells: [c.id, c.accountId, c.suffix, c.holder, c.status],
            action: `card:${c.id}`,
          })),
      },
    );
  } else if (state.page === "card") {
    const card = bank.cards.find(
      (c) => c.id === state.selectedId && ids.has(c.accountId),
    );
    title = "Card maintenance detail";
    if (card)
      blocks.push(
        values(
          ["Card ID", card.id],
          ["Card last 4", card.suffix],
          ["Account", card.accountId],
          ["Cardholder", card.holder],
          ["Current status", card.status],
        ),
        fields({ key: "reason", label: "Maintenance reason", maxLength: 160 }),
        actions(
          { id: "lock", label: "Review temporary lock", primary: true },
          { id: "unlock", label: "Review unlock" },
          { id: "nav:cards", label: "Back to cards" },
        ),
      );
  } else if (state.page === "holds") {
    blocks.push(
      note(
        "Supervisor permission is required to place or release a hold. A release does not settle the related pending authorization.",
        "warning",
      ),
      fields(
        { key: "account", label: "Account", options: depositOptions },
        { key: "amount", label: "Hold amount (USD)" },
        { key: "reason", label: "Placement / release reason", maxLength: 160 },
      ),
      actions({ id: "review-hold", label: "Review new hold", primary: true }),
      heading("Hold register"),
      {
        kind: "table",
        columns: ["Reference", "Account", "Reason", "Amount", "Status", ""],
        rows: bank.holds
          .filter((h) => ids.has(h.accountId))
          .map((h) => ({
            cells: [h.id, h.accountId, h.reason, money(h.cents), h.status],
            ...(h.status === "Active"
              ? { action: `release:${h.id}`, label: "Release" }
              : {}),
          })),
      },
    );
  } else if (state.page === "loans") {
    blocks.push(
      note(
        "Quotes use the fixed business date, simple ACT/365 interest, and a 30-day inclusive window. Issuing a quote does not settle the loan.",
      ),
      fields(
        {
          key: "loan",
          label: "Loan account",
          options: accountOptions(state, true),
        },
        { key: "quoteDate", label: "Payoff date (YYYY-MM-DD)" },
      ),
      actions({ id: "review-quote", label: "Calculate payoff", primary: true }),
      heading("Loan position"),
      {
        kind: "table",
        columns: ["Account", "Principal", "APR", "Accrued interest", "Status"],
        rows: accounts
          .filter((a) => a.kind === "Auto loan")
          .map((a) => ({
            cells: [
              a.id,
              money(ledger(bank, a.id)),
              `${(a.aprBps / 100).toFixed(2)}%`,
              money(a.accruedCents),
              a.status,
            ],
          })),
      },
    );
  } else if (state.page === "cases") {
    blocks.push(
      heading("Service request register"),
      caseTable(state),
      heading("Create a service request"),
      fields(
        {
          key: "account",
          label: "Related account",
          options: accounts.map((a) => ({
            value: a.id,
            label: `${a.kind} ${a.id}`,
          })),
        },
        {
          key: "category",
          label: "Category",
          options: [
            "Transaction inquiry",
            "Account maintenance",
            "Card inquiry",
          ].map((v) => ({ value: v, label: v })),
        },
        { key: "subject", label: "Request summary", maxLength: 160 },
      ),
      actions({ id: "review-case", label: "Review new case", primary: true }),
    );
  } else if (state.page === "case") {
    const item = bank.cases.find(
      (c) => c.id === state.selectedId && c.memberId === state.memberId,
    );
    title = "Service case detail";
    if (item)
      blocks.push(
        values(
          ["Case ID", item.id],
          ["Account", item.accountId],
          ["Category", item.category],
          ["Subject", item.subject],
          ["Status", item.status],
          ["Resolution", item.resolution || "Not resolved"],
        ),
        fields({ key: "resolution", label: "Resolution note", maxLength: 160 }),
        actions(
          { id: "resolve", label: "Review resolution", primary: true },
          { id: "nav:cases", label: "Back to cases" },
        ),
      );
  } else if (state.page === "journal") {
    blocks.push(
      note(
        "Session activity only. References link receipts and posted ledger entries; inquiry and canceled reviews do not create business postings.",
      ),
      {
        kind: "table",
        columns: ["Reference", "Business date", "Member", "Operation", "Role"],
        rows: [...bank.journal].reverse().map((j) => ({
          cells: [j.reference, j.date, j.memberId, j.operation, j.role],
        })),
      },
    );
  } else if (state.page === "settings") {
    blocks.push(
      values(
        ["Environment", "SYNTHETIC TRAINING — NOT CONNECTED TO A BANK"],
        ["Business date", bank.businessDate],
        ["Institution", bank.tenant],
        ["Dataset", "Six members / eighteen accounts / session-local ledger"],
        ["Retention", "Reload starts a fresh isolated training session"],
      ),
      fields({
        key: "role",
        label: "Training operator role",
        options: [
          { value: "inquiry", label: "Inquiry only" },
          { value: "servicing", label: "Member servicing" },
          { value: "supervisor", label: "Branch supervisor" },
        ],
      }),
      actions({ id: "set-role", label: "Apply training role", primary: true }),
      note(
        "Role selection exercises application permission errors. It is not login, authentication, or ReplayForge operator authorization.",
      ),
    );
  } else if (state.page === "review" && state.pending) {
    title = "Verify instructions before posting";
    blocks.push(
      note(
        "REVIEW ONLY — no changes have been posted. Cancel to return without changing records.",
        "warning",
      ),
      heading(state.pending.receipt.title),
      { kind: "values", rows: state.pending.receipt.details },
      values(
        ["Operator role", bank.role],
        ["Record revision", String(state.pending.revision)],
      ),
      actions(
        { id: "confirm", label: "Confirm operation", primary: true },
        { id: "cancel", label: "Cancel" },
      ),
    );
  } else if (state.page === "receipt" && state.receipt) {
    title = state.receipt.title;
    blocks.push(
      note("Operation completed. Retain the reference for subsequent inquiry."),
      values(
        ["Confirmation reference", state.receipt.reference],
        ...state.receipt.details,
      ),
      actions(
        { id: "nav:member", label: "Relationship summary", primary: true },
        { id: "nav:journal", label: "Activity journal" },
      ),
    );
  }
  return {
    title,
    subtitle: member
      ? `${member.name}   |   Member ${member.id}   |   ${member.branch}`
      : "Branch operations / member servicing",
    blocks,
  };
}
function accountTable(state: Workspace): Block {
  const accounts = state.bank.accounts.filter(
    (a) => a.memberId === state.memberId,
  );
  if (state.bank.tenant === "summit") accounts.reverse();
  return {
    kind: "table",
    columns: [
      "Account",
      "Product",
      "Ledger / principal",
      "Available",
      "Status",
      "",
    ],
    rows: accounts.map((a) => ({
      cells: [
        a.id,
        a.kind,
        money(ledger(state.bank, a.id)),
        a.kind === "Auto loan"
          ? "Not applicable"
          : money(available(state.bank, a.id)),
        a.status,
      ],
      action: a.kind === "Auto loan" ? "nav:loans" : `account:${a.id}`,
    })),
  };
}
function caseTable(state: Workspace): Block {
  return {
    kind: "table",
    columns: ["Case ID", "Category", "Subject", "Status", ""],
    rows: state.bank.cases
      .filter((c) => c.memberId === state.memberId)
      .map((c) => ({
        cells: [c.id, c.category, c.subject, c.status],
        action: `case:${c.id}`,
      })),
  };
}
