"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Tenant = "harbor" | "summit";
type Screen =
  | "search"
  | "loading"
  | "results"
  | "notice"
  | "details"
  | "account-menu"
  | "transaction-filter"
  | "transaction-results"
  | "transaction-detail"
  | "transaction-empty"
  | "transaction-ambiguous"
  | "payoff-form"
  | "payoff-result"
  | "payoff-invalid"
  | "card-filter"
  | "card-list"
  | "card-not-found"
  | "card-confirm"
  | "card-result"
  | "card-already-locked"
  | "not-found"
  | "permission-denied";
type AccountKind = "checking" | "savings" | "auto_loan";
type FixtureName =
  | "normal"
  | "delayed"
  | "notice"
  | "restricted"
  | "missing"
  | "duplicate_search"
  | "changed_icon"
  | "duplicate_field"
  | "duplicate_transaction"
  | "prelocked_card";
type FixtureScreen = "results" | "notice" | "permission-denied" | "not-found";

type Account = {
  kind: AccountKind;
  label: string;
  maskedNumber: string;
  balance: string;
};

type Rect = { x: number; y: number; width: number; height: number };
type CanvasMetrics = { width: number; height: number; dpr: number };
type SearchLayout = { label: Rect; input: Rect; button: Rect; duplicateButton?: Rect };
type ResultRow = { account: Account; row: Rect; icon: Rect };
type ResultsLayout = { rows: ResultRow[] };
type DetailField = { label: Rect; value: Rect; name: string; text: string };
type Layout = {
  compact: boolean;
  margin: number;
  panel: Rect;
  search?: SearchLayout;
  results?: ResultsLayout;
  details?: DetailField[];
  noticeButton?: Rect;
};

type FieldName = "transactionMerchant" | "transactionDate" | "transactionAmount" | "payoffDate" | "cardLast4";
type FormField = { name: FieldName; label: string; rect: Rect; value: string };
type ActionButton = { id: string; label: string; rect: Rect };
type Transaction = {
  reference: string;
  merchant: string;
  postedDate: string;
  amount: string;
  currency: "USD";
  status: "Posted" | "Pending";
};

type MemberFixture = { name: FixtureName; screen: FixtureScreen; delayMs: number };

const memberFixtures: Record<string, MemberFixture> = {
  "12345": { name: "normal", screen: "results", delayMs: 180 },
  "13579": { name: "delayed", screen: "results", delayMs: 900 },
  "67890": { name: "notice", screen: "notice", delayMs: 180 },
  "24680": { name: "restricted", screen: "permission-denied", delayMs: 180 },
  "99999": { name: "missing", screen: "not-found", delayMs: 180 },
  "33333": { name: "duplicate_search", screen: "results", delayMs: 180 },
  "44444": { name: "changed_icon", screen: "results", delayMs: 180 },
  "55555": { name: "duplicate_field", screen: "results", delayMs: 180 },
  "77777": { name: "duplicate_transaction", screen: "results", delayMs: 180 },
  "88888": { name: "prelocked_card", screen: "results", delayMs: 180 },
};

const transactions: Transaction[] = [
  { reference: "TXN-80419", merchant: "Northwind Market", postedDate: "2026-09-08", amount: "84.27", currency: "USD", status: "Posted" },
  { reference: "TXN-80420", merchant: "Northwind Market", postedDate: "2026-09-09", amount: "31.62", currency: "USD", status: "Posted" },
  { reference: "TXN-80421", merchant: "Contoso Fuel", postedDate: "2026-09-08", amount: "52.10", currency: "USD", status: "Pending" },
];

const accounts: Record<AccountKind, Account> = {
  checking: { kind: "checking", label: "Checking", maskedNumber: "•••• 0110", balance: "$842.11" },
  savings: { kind: "savings", label: "Savings", maskedNumber: "•••• 0421", balance: "$1,420.57" },
  auto_loan: { kind: "auto_loan", label: "Auto loan", maskedNumber: "•••• 9902", balance: "-$7,800.00" },
};

const rowsByTenant: Record<Tenant, AccountKind[]> = {
  harbor: ["checking", "savings", "auto_loan"],
  summit: ["auto_loan", "savings", "checking"],
};

const palettes = {
  harbor: {
    background: "#f2f6fa",
    panel: "#ffffff",
    ink: "#172234",
    accent: "#0b6bcb",
    muted: "#53657a",
    border: "#8793a1",
    font: "Arial",
    heading: "NORTHSTAR MEMBER SERVICE",
  },
  summit: {
    background: "#f7f2e9",
    panel: "#fffdf8",
    ink: "#29241d",
    accent: "#725a24",
    muted: "#685f50",
    border: "#9a8d75",
    font: "Trebuchet MS",
    heading: "SUMMIT MEMBER OPERATIONS",
  },
} as const;

function rect(x: number, y: number, width: number, height: number): Rect {
  return { x, y, width, height };
}

function layoutFor(
  metrics: CanvasMetrics,
  tenant: Tenant,
  screen: Screen,
  memberId: string,
  duplicateSearch: boolean,
  duplicateField: boolean,
): Layout {
  const compact = metrics.width < 1100;
  const margin = compact ? Math.max(16, metrics.width * 0.04) : Math.max(32, metrics.width * 0.06);
  const panel = rect(margin, Math.max(72, metrics.height * 0.13), metrics.width - margin * 2, metrics.height * 0.78);
  const left = panel.x + (compact ? 18 : 34);
  const contentWidth = panel.width - (compact ? 36 : 68);
  const headingY = panel.y + (compact ? 58 : 64);
  const shift = tenant === "summit" ? (compact ? 6 : 18) : 0;
  const top = headingY + (compact ? 32 : 44);

  if (screen === "search") {
    const inputWidth = compact ? contentWidth : Math.min(430, contentWidth * 0.48);
    const inputY = top + 42;
    const input = rect(left + shift, inputY, inputWidth, compact ? 52 : 60);
    const buttonWidth = compact ? contentWidth : Math.min(190, contentWidth * 0.22);
    const button = compact
      ? rect(left + shift, input.y + input.height + 18, buttonWidth, 52)
      : rect(input.x + input.width + 34, input.y, buttonWidth, 60);
    return {
      compact,
      margin,
      panel,
      search: {
        label: rect(left + shift, input.y - 32, Math.min(180, contentWidth), 24),
        input,
        button,
        duplicateButton: duplicateSearch
          ? rect(button.x, button.y + button.height + 16, button.width, button.height)
          : undefined,
      },
    };
  }

  if (screen === "results") {
    const tableTop = top + (compact ? 58 : 62);
    const rowGap = compact ? 14 : 8;
    const rowHeight = compact ? Math.max(112, Math.min(146, metrics.height * 0.18)) : 76;
    const rows: ResultRow[] = rowsByTenant[tenant].map((kind, index) => {
      const row = rect(left + shift, tableTop + index * (rowHeight + rowGap), contentWidth, rowHeight);
      const iconSize = compact ? 52 : 48;
      const icon = rect(
        row.x + row.width - iconSize - (compact ? 16 : 20),
        row.y + (row.height - iconSize) / 2,
        iconSize,
        iconSize,
      );
      return { account: accounts[kind], row, icon };
    });
    return { compact, margin, panel, results: { rows } };
  }

  if (screen === "details") {
    const detailTop = top + (compact ? 48 : 54);
    const fieldGap = compact ? 16 : 26;
    // Keep the deliberately duplicated fixture readable even at the smallest
    // supported viewport; it is a data ambiguity fault, not a clipping fault.
    const fieldHeight = compact ? (duplicateField ? 30 : 54) : 30;
    const labelWidth = compact ? contentWidth : Math.min(300, contentWidth * 0.36);
    const valueWidth = contentWidth - labelWidth - (compact ? 0 : 26);
    const fields: Array<readonly [string, string]> = [
      ["Member ID", memberId],
      ["Account type", accounts.savings.label],
      ["Currency", "USD"],
      ["Available balance", accounts.savings.balance],
      ...(duplicateField ? [["Available balance", accounts.savings.balance] as const] : []),
      ["As of", "2026-09-10T12:30:00Z"],
    ];
    return {
      compact,
      margin,
      panel,
      details: fields.map(([name, textValue], index) => {
        const y = detailTop + index * (fieldHeight + fieldGap);
        return compact
          ? {
              name,
              text: textValue,
              label: rect(left + shift, y, labelWidth, 22),
              value: rect(left + shift, y + 24, valueWidth, fieldHeight - 4),
            }
          : {
              name,
              text: textValue,
              label: rect(left + shift, y, labelWidth, fieldHeight),
              value: rect(left + shift + labelWidth + 26, y, valueWidth, fieldHeight),
            };
      }),
    };
  }

  return {
    compact,
    margin,
    panel,
    noticeButton: rect(
      left + (compact ? contentWidth * 0.15 : contentWidth * 0.3),
      top + (compact ? 166 : 186),
      compact ? contentWidth * 0.7 : Math.min(220, contentWidth * 0.4),
      compact ? 52 : 56,
    ),
  };
}

function workflowArea(metrics: CanvasMetrics, tenant: Tenant) {
  const compact = metrics.width < 1100;
  const margin = compact ? Math.max(16, metrics.width * 0.04) : Math.max(32, metrics.width * 0.06);
  const panel = rect(margin, Math.max(72, metrics.height * 0.13), metrics.width - margin * 2, metrics.height * 0.78);
  const left = panel.x + (compact ? 18 : 34) + (tenant === "summit" ? (compact ? 6 : 18) : 0);
  const width = panel.width - (compact ? 36 : 68);
  const top = panel.y + (compact ? 94 : 112);
  return { compact, panel, left, width, top };
}

function actionButtons(metrics: CanvasMetrics, tenant: Tenant, labels: Array<readonly [string, string]>): ActionButton[] {
  const area = workflowArea(metrics, tenant);
  const gap = area.compact ? 14 : 18;
  const height = area.compact ? 54 : 60;
  return labels.map(([id, label], index) => ({
    id,
    label,
    rect: rect(area.left, area.top + index * (height + gap), area.width, height),
  }));
}

function workflowFields(
  metrics: CanvasMetrics,
  tenant: Tenant,
  screen: Screen,
  values: Record<FieldName, string>,
): FormField[] {
  const area = workflowArea(metrics, tenant);
  const definitions: Array<readonly [FieldName, string]> =
    screen === "payoff-form"
      ? [["payoffDate", "Payoff date"]]
      : screen === "card-filter"
        ? [["cardLast4", "Card last 4"]]
        : [
          ["transactionMerchant", "Merchant"],
          ["transactionDate", "Transaction date"],
          ["transactionAmount", "Amount"],
        ];
  const block = area.compact ? 82 : 74;
  return definitions.map(([name, label], index) => ({
    name,
    label,
    value: values[name],
    rect: rect(area.left, area.top + index * block + 26, area.width, area.compact ? 46 : 44),
  }));
}

function transactionMatches(memberId: string, merchant: string, date: string, amount: string) {
  const matches = transactions.filter(
    (item) =>
      item.merchant.toLocaleLowerCase() === merchant.trim().toLocaleLowerCase() &&
      item.postedDate === date.trim() &&
      item.amount === amount.trim().replace(/^\$/, ""),
  );
  return memberId === "77777" && matches.length === 1
    ? [matches[0]!, { ...matches[0]!, reference: "TXN-80422" }]
    : matches;
}

export function VisualWorkbench({ tenant }: { tenant: Tenant }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const timerRef = useRef<number | null>(null);
  const [metrics, setMetrics] = useState<CanvasMetrics>({ width: 1280, height: 800, dpr: 1 });
  const [screen, setScreen] = useState<Screen>("search");
  const [memberId, setMemberId] = useState("");
  const [inputActive, setInputActive] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<AccountKind>("savings");
  const [activeField, setActiveField] = useState<FieldName | null>(null);
  const [transactionMerchant, setTransactionMerchant] = useState("");
  const [transactionDate, setTransactionDate] = useState("");
  const [transactionAmount, setTransactionAmount] = useState("");
  const [payoffDate, setPayoffDate] = useState("");
  const [cardLast4, setCardLast4] = useState("");
  const [selectedTransaction, setSelectedTransaction] = useState<Transaction | null>(null);
  const [lockedCards, setLockedCards] = useState<Set<string>>(new Set());
  const palette = palettes[tenant];

  const fieldValues: Record<FieldName, string> = {
    transactionMerchant,
    transactionDate,
    transactionAmount,
    payoffDate,
    cardLast4,
  };

  const clearPendingTransition = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => clearPendingTransition, [clearPendingTransition]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => {
      const bounds = canvas.getBoundingClientRect();
      setMetrics({
        width: Math.max(1, Math.round(bounds.width || window.innerWidth)),
        height: Math.max(1, Math.round(bounds.height || window.innerHeight)),
        dpr: Math.max(1, window.devicePixelRatio || 1),
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  const submitSearch = useCallback(() => {
    clearPendingTransition();
    setInputActive(false);
    setScreen("loading");
    const fixture = memberFixtures[memberId];
    const delay = fixture?.delayMs ?? 180;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setScreen(fixture?.screen ?? "not-found");
    }, delay);
  }, [clearPendingTransition, memberId]);

  const submitTransactionFilter = useCallback(() => {
    setActiveField(null);
    const matches = transactionMatches(
      memberId,
      transactionMerchant,
      transactionDate,
      transactionAmount,
    );
    setSelectedTransaction(matches.length === 1 ? matches[0]! : null);
    setScreen(
      matches.length === 0
        ? "transaction-empty"
        : matches.length > 1
          ? "transaction-ambiguous"
          : "transaction-results",
    );
  }, [memberId, transactionAmount, transactionDate, transactionMerchant]);

  const submitPayoff = useCallback(() => {
    setActiveField(null);
    const parsed = /^\d{4}-\d{2}-\d{2}$/.test(payoffDate) ? Date.parse(`${payoffDate}T00:00:00Z`) : NaN;
    const earliest = Date.parse("2026-09-11T00:00:00Z");
    const latest = Date.parse("2026-10-10T00:00:00Z");
    setScreen(Number.isFinite(parsed) && parsed >= earliest && parsed <= latest ? "payoff-result" : "payoff-invalid");
  }, [payoffDate]);

  const lockCard = useCallback(() => {
    if (memberId === "88888" || lockedCards.has("0110")) {
      setScreen("card-already-locked");
      return;
    }
    setLockedCards((current) => new Set(current).add("0110"));
    setScreen("card-result");
  }, [lockedCards, memberId]);

  const submitCardFilter = useCallback(() => {
    setActiveField(null);
    setScreen(cardLast4 === "0110" ? "card-list" : "card-not-found");
  }, [cardLast4]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context || metrics.width <= 0 || metrics.height <= 0) return;
    canvas.width = Math.max(1, Math.round(metrics.width * metrics.dpr));
    canvas.height = Math.max(1, Math.round(metrics.height * metrics.dpr));
    context.setTransform(metrics.dpr, 0, 0, metrics.dpr, 0, 0);
    context.textAlign = "left";
    context.fillStyle = palette.background;
    context.fillRect(0, 0, metrics.width, metrics.height);
    const layout = layoutFor(
      metrics,
      tenant,
      screen,
      memberId,
      memberFixtures[memberId]?.name === "duplicate_search",
      memberFixtures[memberId]?.name === "duplicate_field",
    );
    context.fillStyle = palette.ink;
    context.font = `700 ${Math.max(17, Math.min(25, metrics.width / 50))}px ${palette.font}`;
    context.fillText(palette.heading, layout.margin, Math.max(34, metrics.height * 0.075));
    context.fillStyle = palette.panel;
    context.strokeStyle = palette.border;
    fillStroke(context, layout.panel);

    const headings: Partial<Record<Screen, string>> = {
      results: "Member Results",
      "not-found": "Member Results",
      "permission-denied": "Member Results",
      details: `${accounts[selectedAccount].label} Account Details`,
      "account-menu": `${accounts[selectedAccount].label} Servicing`,
      "transaction-filter": "Transaction Investigation",
      "transaction-results": "Transaction Results",
      "transaction-detail": "Transaction Details",
      "transaction-empty": "Transaction Results",
      "transaction-ambiguous": "Transaction Results",
      "payoff-form": "Loan Payoff Quote",
      "payoff-result": "Payoff Quote",
      "payoff-invalid": "Loan Payoff Quote",
      "card-filter": "Find Card",
      "card-list": "Card Controls",
      "card-not-found": "Card Controls",
      "card-confirm": "Confirm Temporary Lock",
      "card-result": "Card Lock Confirmation",
      "card-already-locked": "Card Controls",
    };
    const heading = headings[screen] ?? "Member Search";
    text(
      context,
      heading,
      layout.panel.x + (layout.compact ? 18 : 34),
      layout.panel.y + (layout.compact ? 42 : 48),
      palette.accent,
      palette.font,
      true,
      Math.max(21, Math.min(32, metrics.width / 38)),
    );

    if (screen === "search" && layout.search) {
      const search = layout.search;
      text(context, "Member ID", search.label.x, search.label.y + 18, palette.ink, palette.font, false, Math.max(16, Math.min(22, metrics.width / 52)));
      fillStroke(context, search.input, inputActive ? palette.accent : palette.border, 3);
      text(context, memberId || "Enter member ID", search.input.x + 16, search.input.y + search.input.height * 0.64, memberId ? palette.ink : palette.muted, palette.font, Boolean(memberId), Math.max(16, Math.min(22, metrics.width / 52)));
      button(context, "Search", search.button, palette.accent, palette.font);
      if (search.duplicateButton) button(context, "Search", search.duplicateButton, palette.accent, palette.font);
      const noteTarget = search.duplicateButton ?? search.button;
      text(context, "Enter a synthetic member identifier", search.label.x, noteTarget.y + noteTarget.height + 38, palette.muted, palette.font, false, Math.max(14, Math.min(19, metrics.width / 65)));
      return;
    }

    if (screen === "loading") {
      text(context, "Loading member record...", layout.panel.x + (layout.compact ? 18 : 34), layout.panel.y + layout.panel.height * 0.42, palette.ink, palette.font, true, Math.max(18, Math.min(25, metrics.width / 48)));
      return;
    }
    if (screen === "not-found" || screen === "permission-denied") {
      text(context, screen === "not-found" ? "No member found" : "Permission denied", layout.panel.x + (layout.compact ? 18 : 34), layout.panel.y + layout.panel.height * 0.42, "#9b2c2c", palette.font, true, Math.max(18, Math.min(25, metrics.width / 48)));
      return;
    }
    if (screen === "notice" && layout.noticeButton) {
      const notice = rect(layout.panel.x + (layout.compact ? 14 : layout.panel.width * 0.2), layout.panel.y + (layout.compact ? 92 : 104), layout.panel.width - (layout.compact ? 28 : layout.panel.width * 0.4), layout.compact ? 230 : 220);
      context.fillStyle = "#fff4cf";
      context.strokeStyle = "#a97916";
      fillStroke(context, notice);
      text(context, "Important notice", notice.x + notice.width * 0.08, notice.y + 58, palette.ink, palette.font, true, Math.max(18, Math.min(25, metrics.width / 48)));
      text(context, "Review the training notice before continuing.", notice.x + notice.width * 0.08, notice.y + 100, palette.ink, palette.font, false, Math.max(14, Math.min(19, metrics.width / 65)));
      button(context, "Continue", layout.noticeButton, palette.accent, palette.font);
      return;
    }
    const area = workflowArea(metrics, tenant);
    if (screen === "account-menu") {
      const actions = actionButtons(
        metrics,
        tenant,
        selectedAccount === "checking"
          ? [["transactions", "Investigate transactions"], ["cards", "Card controls"]]
          : [["payoff", "Create payoff quote"]],
      );
      text(context, `Member ID: ${memberId}`, area.left, area.top - 28, palette.muted, palette.font, false, 16);
      actions.forEach((action) => button(context, action.label, action.rect, palette.accent, palette.font));
      return;
    }
    if (screen === "transaction-filter" || screen === "payoff-form" || screen === "card-filter") {
      const fields = workflowFields(metrics, tenant, screen, fieldValues);
      fields.forEach((field) => {
        text(context, field.label, field.rect.x, field.rect.y - 8, palette.muted, palette.font, false, 16);
        context.fillStyle = palette.panel;
        fillStroke(context, field.rect, activeField === field.name ? palette.accent : palette.border, 3);
        text(context, field.value || "Enter value", field.rect.x + 14, field.rect.y + 29, field.value ? palette.ink : palette.muted, palette.font, Boolean(field.value), 17);
      });
      const last = fields[fields.length - 1]!.rect;
      const submit = rect(last.x, last.y + last.height + 18, last.width, area.compact ? 52 : 56);
      const submitLabel = screen === "payoff-form" ? "Calculate quote" : screen === "card-filter" ? "Find card" : "Find transaction";
      button(context, submitLabel, submit, palette.accent, palette.font);
      if (screen === "payoff-form") text(context, "Allowed dates: 2026-09-11 through 2026-10-10", submit.x, submit.y + submit.height + 30, palette.muted, palette.font, false, 15);
      return;
    }
    if (screen === "transaction-results") {
      text(context, "1 matching transaction", area.left, area.top - 24, palette.muted, palette.font, false, 16);
      const result = selectedTransaction;
      if (result) {
        drawKeyValues(context, area, [["Merchant", result.merchant], ["Posted", result.postedDate], ["Amount", `$${result.amount}`]], palette);
        const open = actionButtons(metrics, tenant, [["open-transaction", "Open matching transaction"]])[0]!;
        open.rect.y = area.top + (area.compact ? 188 : 150);
        button(context, open.label, open.rect, palette.accent, palette.font);
      }
      return;
    }
    if (screen === "transaction-detail" && selectedTransaction) {
      drawKeyValues(context, area, [
        ["Transaction reference", selectedTransaction.reference],
        ["Merchant", selectedTransaction.merchant],
        ["Posted date", selectedTransaction.postedDate],
        ["Amount", selectedTransaction.amount],
        ["Currency", selectedTransaction.currency],
        ["Status", selectedTransaction.status],
      ], palette);
      return;
    }
    if (screen === "transaction-empty" || screen === "transaction-ambiguous") {
      const message = screen === "transaction-empty" ? "No matching transaction" : "Multiple matching transactions";
      text(context, message, area.left, area.top + 40, "#9b2c2c", palette.font, true, 22);
      text(context, screen === "transaction-empty" ? "The supplied fields matched no posted transaction." : "Refine the merchant, date, or amount before continuing.", area.left, area.top + 82, palette.ink, palette.font, false, 16);
      return;
    }
    if (screen === "payoff-result") {
      const days = Math.round((Date.parse(`${payoffDate}T00:00:00Z`) - Date.parse("2026-09-10T00:00:00Z")) / 86400000);
      const interest = (days * 2.14).toFixed(2);
      const total = (7800 + Number(interest)).toFixed(2);
      drawKeyValues(context, area, [["Principal balance", "7800.00"], ["Accrued interest", interest], ["Payoff amount", total], ["Currency", "USD"], ["Good through", payoffDate]], palette);
      return;
    }
    if (screen === "payoff-invalid") {
      text(context, "Invalid payoff date", area.left, area.top + 40, "#9b2c2c", palette.font, true, 22);
      text(context, "Choose a date within the displayed quote window.", area.left, area.top + 82, palette.ink, palette.font, false, 16);
      return;
    }
    if (screen === "card-list") {
      drawKeyValues(context, area, [["Debit card", "•••• 0110"], ["Status", memberId === "88888" || lockedCards.has("0110") ? "Temporarily locked" : "Active"]], palette);
      const review = actionButtons(metrics, tenant, [["review-lock", "Review temporary lock"]])[0]!;
      review.rect.y = area.top + (area.compact ? 142 : 112);
      button(context, review.label, review.rect, palette.accent, palette.font);
      return;
    }
    if (screen === "card-not-found") {
      text(context, "No matching card", area.left, area.top + 40, "#9b2c2c", palette.font, true, 22);
      text(context, "No card matched the supplied last four digits.", area.left, area.top + 82, palette.ink, palette.font, false, 16);
      return;
    }
    if (screen === "card-confirm") {
      text(context, "Card •••• 0110 will be unavailable for new purchases.", area.left, area.top, palette.ink, palette.font, false, 17);
      text(context, "The lock can be reversed from this same control surface.", area.left, area.top + 38, palette.muted, palette.font, false, 16);
      const confirm = actionButtons(metrics, tenant, [["confirm-lock", "Confirm temporary lock"]])[0]!;
      confirm.rect.y = area.top + 76;
      button(context, confirm.label, confirm.rect, "#9b2c2c", palette.font);
      return;
    }
    if (screen === "card-result") {
      drawKeyValues(context, area, [["Card last 4", "0110"], ["Lock status", "Temporarily locked"], ["Effective at", "2026-09-13T14:00:00Z"], ["Confirmation reference", "LOCK-0110-0913"], ["Reversal", "Unlock available"]], palette);
      const unlock = actionButtons(metrics, tenant, [["unlock", "Unlock card"]])[0]!;
      unlock.rect.y = area.top + (area.compact ? 290 : 224);
      button(context, unlock.label, unlock.rect, palette.accent, palette.font);
      return;
    }
    if (screen === "card-already-locked") {
      text(context, "Card already temporarily locked", area.left, area.top + 40, palette.ink, palette.font, true, 22);
      text(context, "No additional state change was made.", area.left, area.top + 82, palette.muted, palette.font, false, 16);
      return;
    }
    if (screen === "results" && layout.results) {
      const first = layout.results.rows[0]?.row;
      if (first) text(context, `Member ID: ${memberId}`, first.x, first.y - (layout.compact ? 20 : 28), palette.ink, palette.font, false, Math.max(14, Math.min(19, metrics.width / 65)));
      if (!layout.compact && first) {
        context.fillStyle = palette.accent;
        context.fillRect(first.x, first.y - 48, first.width, 40);
        text(context, "Account", first.x + 18, first.y - 21, "#ffffff", palette.font, true, 16);
        text(context, "Number", first.x + first.width * 0.28, first.y - 21, "#ffffff", palette.font, true, 16);
        text(context, "Available", first.x + first.width * 0.52, first.y - 21, "#ffffff", palette.font, true, 16);
        text(context, "Open", first.x + first.width - 96, first.y - 21, "#ffffff", palette.font, true, 16);
      }
      layout.results.rows.forEach(({ account, row, icon }) => {
        context.fillStyle = palette.panel;
        context.strokeStyle = palette.border;
        fillStroke(context, row);
        if (layout.compact) {
          text(context, account.label, row.x + 18, row.y + 32, palette.ink, palette.font, true, Math.max(18, Math.min(25, metrics.width / 48)));
          text(context, account.maskedNumber, row.x + 18, row.y + 62, palette.muted, palette.font, false, Math.max(14, Math.min(19, metrics.width / 65)));
          text(context, account.balance, row.x + 18, row.y + 90, palette.ink, palette.font, false, Math.max(15, Math.min(20, metrics.width / 60)));
        } else {
          text(context, account.label, row.x + 18, row.y + row.height * 0.62, palette.ink, palette.font, true, 20);
          text(context, account.maskedNumber, row.x + row.width * 0.28, row.y + row.height * 0.62, palette.ink, palette.font, false, 17);
          text(context, account.balance, row.x + row.width * 0.52, row.y + row.height * 0.62, palette.ink, palette.font, false, 17);
        }
        if (account.kind === "savings" && memberFixtures[memberId]?.name === "changed_icon") changedIcon(context, icon, palette.accent);
        else iconButton(context, icon, palette.accent);
      });
      return;
    }
    if (screen === "details" && layout.details) {
      layout.details.forEach((field) => {
        text(context, field.name, field.label.x, field.label.y + (layout.compact ? 17 : 22), palette.muted, palette.font, false, Math.max(14, Math.min(19, metrics.width / 65)));
        const value = field.name === "Account type" ? accounts[selectedAccount].label : field.name === "Available balance" ? accounts[selectedAccount].balance : field.text;
        text(context, value, field.value.x, field.value.y + (layout.compact ? 19 : 22), palette.ink, palette.font, true, Math.max(15, Math.min(20, metrics.width / 60)));
      });
    }
  }, [
    activeField,
    cardLast4,
    lockedCards,
    memberId,
    metrics,
    palette,
    payoffDate,
    screen,
    selectedAccount,
    selectedTransaction,
    tenant,
    transactionAmount,
    transactionDate,
    transactionMerchant,
  ]);

  useEffect(() => draw(), [draw]);

  const onPointer = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const currentMetrics = { width: bounds.width, height: bounds.height, dpr: metrics.dpr };
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const currentLayout = layoutFor(
      currentMetrics,
      tenant,
      screen,
      memberId,
      memberFixtures[memberId]?.name === "duplicate_search",
      memberFixtures[memberId]?.name === "duplicate_field",
    );
    const search = currentLayout.search;
    if (screen === "search" && search && inside(search.input, x, y)) {
      setInputActive(true);
      event.currentTarget.focus();
    } else if (screen === "search" && search && inside(search.button, x, y)) {
      submitSearch();
    } else if (screen === "notice" && currentLayout.noticeButton && inside(currentLayout.noticeButton, x, y)) {
      setScreen("results");
    } else if (screen === "results" && currentLayout.results) {
      const row = currentLayout.results.rows.find((candidate) => inside(candidate.icon, x, y));
      if (row) {
        setSelectedAccount(row.account.kind);
        setScreen(row.account.kind === "savings" ? "details" : "account-menu");
      }
    } else if (screen === "account-menu") {
      const actions = actionButtons(
        currentMetrics,
        tenant,
        selectedAccount === "checking"
          ? [["transactions", "Investigate transactions"], ["cards", "Card controls"]]
          : [["payoff", "Create payoff quote"]],
      );
      const selected = actions.find((action) => inside(action.rect, x, y));
      if (selected?.id === "transactions") setScreen("transaction-filter");
      if (selected?.id === "cards") setScreen("card-filter");
      if (selected?.id === "payoff") setScreen("payoff-form");
    } else if (screen === "transaction-filter" || screen === "payoff-form" || screen === "card-filter") {
      const fields = workflowFields(currentMetrics, tenant, screen, fieldValues);
      const selected = fields.find((field) => inside(field.rect, x, y));
      if (selected) {
        setActiveField(selected.name);
        event.currentTarget.focus();
        return;
      }
      const last = fields[fields.length - 1]!.rect;
      const submit = rect(last.x, last.y + last.height + 18, last.width, currentMetrics.width < 1100 ? 52 : 56);
      if (inside(submit, x, y)) {
        if (screen === "payoff-form") submitPayoff();
        else if (screen === "card-filter") submitCardFilter();
        else submitTransactionFilter();
      }
    } else if (screen === "transaction-results") {
      const area = workflowArea(currentMetrics, tenant);
      const open = actionButtons(currentMetrics, tenant, [["open-transaction", "Open matching transaction"]])[0]!;
      open.rect.y = area.top + (area.compact ? 188 : 150);
      if (inside(open.rect, x, y)) setScreen("transaction-detail");
    } else if (screen === "card-list") {
      const area = workflowArea(currentMetrics, tenant);
      const review = actionButtons(currentMetrics, tenant, [["review-lock", "Review temporary lock"]])[0]!;
      review.rect.y = area.top + (area.compact ? 142 : 112);
      if (inside(review.rect, x, y)) {
        setScreen(memberId === "88888" || lockedCards.has("0110") ? "card-already-locked" : "card-confirm");
      }
    } else if (screen === "card-confirm") {
      const area = workflowArea(currentMetrics, tenant);
      const confirm = actionButtons(currentMetrics, tenant, [["confirm-lock", "Confirm temporary lock"]])[0]!;
      confirm.rect.y = area.top + 76;
      if (inside(confirm.rect, x, y)) lockCard();
    } else if (screen === "card-result") {
      const area = workflowArea(currentMetrics, tenant);
      const unlock = actionButtons(currentMetrics, tenant, [["unlock", "Unlock card"]])[0]!;
      unlock.rect.y = area.top + (area.compact ? 290 : 224);
      if (inside(unlock.rect, x, y)) {
        setLockedCards((current) => {
          const next = new Set(current);
          next.delete("0110");
          return next;
        });
        setScreen("card-list");
      }
    }
  };

  const onKey = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (!inputActive && !activeField) return;
    if (inputActive) {
      if (/^[0-9]$/.test(event.key) && memberId.length < 10) setMemberId((value) => value + event.key);
      if (event.key === "Backspace") setMemberId((value) => value.slice(0, -1));
      if (event.key === "Enter") submitSearch();
    } else if (activeField) {
      const setters: Record<FieldName, React.Dispatch<React.SetStateAction<string>>> = {
        transactionMerchant: setTransactionMerchant,
        transactionDate: setTransactionDate,
        transactionAmount: setTransactionAmount,
        payoffDate: setPayoffDate,
        cardLast4: setCardLast4,
      };
      if (event.key.length === 1 && /^[A-Za-z0-9 .&$-]$/.test(event.key)) {
        setters[activeField]((value) => value + event.key);
      }
      if (event.key === "Backspace") setters[activeField]((value) => value.slice(0, -1));
      if (event.key === "Enter") {
        if (screen === "payoff-form") submitPayoff();
        if (screen === "transaction-filter") submitTransactionFilter();
        if (screen === "card-filter") submitCardFilter();
      }
    }
    event.preventDefault();
  };

  return (
    <canvas
      ref={canvasRef}
      tabIndex={0}
      aria-label="Rendered responsive member workbench"
      onClick={onPointer}
      onKeyDown={onKey}
      style={{ width: "100vw", height: "100vh", display: "block", outline: "none" }}
    />
  );
}

function inside(target: Rect, x: number, y: number): boolean {
  return x >= target.x && x <= target.x + target.width && y >= target.y && y <= target.y + target.height;
}

function fillStroke(context: CanvasRenderingContext2D, target: Rect, stroke?: string, lineWidth = 2) {
  if (stroke) context.strokeStyle = stroke;
  context.lineWidth = lineWidth;
  context.fillRect(target.x, target.y, target.width, target.height);
  context.strokeRect(target.x, target.y, target.width, target.height);
}

function text(context: CanvasRenderingContext2D, value: string, x: number, y: number, color: string, font: string, strong: boolean, size: number) {
  context.fillStyle = color;
  context.font = `${strong ? "700" : "500"} ${size}px ${font}`;
  context.fillText(value, x, y);
}

function button(context: CanvasRenderingContext2D, value: string, target: Rect, color: string, font: string) {
  context.fillStyle = color;
  context.fillRect(target.x, target.y, target.width, target.height);
  context.fillStyle = "#ffffff";
  context.font = `700 ${Math.max(16, Math.min(23, target.height * 0.38))}px ${font}`;
  context.textAlign = "center";
  context.fillText(value, target.x + target.width / 2, target.y + target.height * 0.64);
  context.textAlign = "left";
}

function drawKeyValues(
  context: CanvasRenderingContext2D,
  area: ReturnType<typeof workflowArea>,
  fields: Array<readonly [string, string]>,
  palette: (typeof palettes)[Tenant],
) {
  const gap = area.compact ? 48 : 40;
  fields.forEach(([label, value], index) => {
    const y = area.top + index * gap;
    if (area.compact) {
      text(context, label, area.left, y, palette.muted, palette.font, false, 15);
      text(context, value, area.left, y + 22, palette.ink, palette.font, true, 17);
    } else {
      text(context, label, area.left, y + 18, palette.muted, palette.font, false, 16);
      text(context, value, area.left + Math.min(300, area.width * 0.36), y + 18, palette.ink, palette.font, true, 17);
    }
  });
}

function iconButton(context: CanvasRenderingContext2D, target: Rect, color: string) {
  context.fillStyle = color;
  context.fillRect(target.x, target.y, target.width, target.height);
  context.strokeStyle = "#ffffff";
  context.lineWidth = Math.max(3, target.width * 0.08);
  context.beginPath();
  context.moveTo(target.x + target.width * 0.32, target.y + target.height * 0.27);
  context.lineTo(target.x + target.width * 0.68, target.y + target.height * 0.5);
  context.lineTo(target.x + target.width * 0.32, target.y + target.height * 0.73);
  context.stroke();
}

function changedIcon(context: CanvasRenderingContext2D, target: Rect, color: string) {
  context.fillStyle = color;
  context.fillRect(target.x, target.y, target.width, target.height);
  context.strokeStyle = "#ffffff";
  context.lineWidth = Math.max(3, target.width * 0.08);
  context.beginPath();
  context.moveTo(target.x + target.width * 0.26, target.y + target.height * 0.25);
  context.lineTo(target.x + target.width * 0.74, target.y + target.height * 0.75);
  context.moveTo(target.x + target.width * 0.74, target.y + target.height * 0.25);
  context.lineTo(target.x + target.width * 0.26, target.y + target.height * 0.75);
  context.stroke();
}
