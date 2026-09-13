"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Tenant = "harbor" | "summit";
type Screen =
  | "search"
  | "loading"
  | "results"
  | "notice"
  | "details"
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
  | "duplicate_field";
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
};

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

export function VisualWorkbench({ tenant }: { tenant: Tenant }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const timerRef = useRef<number | null>(null);
  const [metrics, setMetrics] = useState<CanvasMetrics>({ width: 1280, height: 800, dpr: 1 });
  const [screen, setScreen] = useState<Screen>("search");
  const [memberId, setMemberId] = useState("");
  const [inputActive, setInputActive] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<AccountKind>("savings");
  const palette = palettes[tenant];

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

    const heading =
      screen === "details"
        ? `${accounts[selectedAccount].label} Account Details`
        : screen === "results" || screen === "not-found" || screen === "permission-denied"
          ? "Member Results"
          : "Member Search";
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
  }, [memberId, metrics, palette, screen, selectedAccount, tenant]);

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
        setScreen("details");
      }
    }
  };

  const onKey = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (!inputActive) return;
    if (/^[0-9]$/.test(event.key) && memberId.length < 10) setMemberId((value) => value + event.key);
    if (event.key === "Backspace") setMemberId((value) => value.slice(0, -1));
    if (event.key === "Enter") submitSearch();
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
