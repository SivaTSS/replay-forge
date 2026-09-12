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
  | "changed_icon";
type FixtureScreen = "results" | "notice" | "permission-denied" | "not-found";

type Account = {
  kind: AccountKind;
  label: string;
  maskedNumber: string;
  balance: string;
};

const WIDTH = 1280;
const HEIGHT = 800;

type MemberFixture = {
  name: FixtureName;
  screen: FixtureScreen;
  delayMs: number;
};

const memberFixtures: Record<string, MemberFixture> = {
  "12345": { name: "normal", screen: "results", delayMs: 180 },
  "13579": { name: "delayed", screen: "results", delayMs: 900 },
  "67890": { name: "notice", screen: "notice", delayMs: 180 },
  "24680": { name: "restricted", screen: "permission-denied", delayMs: 180 },
  "99999": { name: "missing", screen: "not-found", delayMs: 180 },
  "33333": { name: "duplicate_search", screen: "results", delayMs: 180 },
  "44444": { name: "changed_icon", screen: "results", delayMs: 180 },
};

const accounts: Record<AccountKind, Account> = {
  checking: {
    kind: "checking",
    label: "Checking",
    maskedNumber: "•••• 0110",
    balance: "$842.11",
  },
  savings: {
    kind: "savings",
    label: "Savings",
    maskedNumber: "•••• 0421",
    balance: "$1,420.57",
  },
  auto_loan: {
    kind: "auto_loan",
    label: "Auto loan",
    maskedNumber: "•••• 9902",
    balance: "-$7,800.00",
  },
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
    font: "Arial",
    heading: "NORTHSTAR MEMBER SERVICE",
  },
  summit: {
    background: "#f7f2e9",
    panel: "#fffdf8",
    ink: "#29241d",
    accent: "#725a24",
    muted: "#685f50",
    font: "Trebuchet MS",
    heading: "SUMMIT MEMBER OPERATIONS",
  },
} as const;

export function VisualWorkbench({ tenant }: { tenant: Tenant }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const timerRef = useRef<number | null>(null);
  const [screen, setScreen] = useState<Screen>("search");
  const [memberId, setMemberId] = useState("");
  const [inputActive, setInputActive] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<AccountKind>("savings");
  const palette = palettes[tenant];
  const shift = tenant === "summit" ? 44 : 0;

  const clearPendingTransition = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => clearPendingTransition, [clearPendingTransition]);

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
    if (!canvas || !context) return;

    context.textAlign = "left";
    context.fillStyle = palette.background;
    context.fillRect(0, 0, WIDTH, HEIGHT);
    context.fillStyle = palette.ink;
    context.font = `700 24px ${palette.font}`;
    context.fillText(palette.heading, 64, 60);
    context.fillStyle = palette.panel;
    context.strokeStyle = "#8793a1";
    context.lineWidth = 2;
    context.fillRect(70 + shift, 100, 1120 - shift, 620);
    context.strokeRect(70 + shift, 100, 1120 - shift, 620);

    if (screen === "search") {
      heading(context, "Member Search", 130 + shift, 165, palette.accent, palette.font);
      label(context, "Member ID", 170 + shift, 266, palette.ink, palette.font);
      context.fillStyle = "#ffffff";
      context.strokeStyle = inputActive ? palette.accent : "#59697a";
      context.lineWidth = inputActive ? 4 : 2;
      context.fillRect(370 + shift, 220, 360, 66);
      context.strokeRect(370 + shift, 220, 360, 66);
      label(context, memberId, 390 + shift, 263, palette.ink, palette.font, true);
      button(context, "Search", 770 + shift, 220, 180, 66, palette.accent, palette.font);
      if (memberFixtures[memberId]?.name === "duplicate_search") {
        button(context, "Search", 770 + shift, 315, 180, 66, palette.accent, palette.font);
      }
      label(context, "Enter a synthetic member identifier", 170 + shift, 370, palette.muted, palette.font);
      return;
    }

    if (screen === "loading") {
      heading(context, "Member Search", 130 + shift, 165, palette.accent, palette.font);
      label(context, "Loading member record...", 170 + shift, 300, palette.ink, palette.font, true);
      return;
    }

    if (screen === "not-found") {
      heading(context, "Member Results", 130 + shift, 165, palette.accent, palette.font);
      label(context, "No member found", 170 + shift, 290, "#9b2c2c", palette.font, true);
      return;
    }

    if (screen === "permission-denied") {
      heading(context, "Member Results", 130 + shift, 165, palette.accent, palette.font);
      label(context, "Permission denied", 170 + shift, 290, "#9b2c2c", palette.font, true);
      return;
    }

    if (screen === "notice") {
      heading(context, "Member Search", 130 + shift, 165, palette.accent, palette.font);
      context.fillStyle = "#fff4cf";
      context.strokeStyle = "#a97916";
      context.lineWidth = 2;
      context.fillRect(300 + shift, 250, 600, 220);
      context.strokeRect(300 + shift, 250, 600, 220);
      label(context, "Important notice", 350 + shift, 315, palette.ink, palette.font, true);
      label(context, "Review the training notice before continuing.", 350 + shift, 360, palette.ink, palette.font);
      button(context, "Continue", 510 + shift, 395, 180, 55, palette.accent, palette.font);
      return;
    }

    if (screen === "results") {
      heading(context, "Member Results", 130 + shift, 165, palette.accent, palette.font);
      label(context, `Member ID   ${memberId}`, 170 + shift, 205, palette.ink, palette.font);
      context.fillStyle = palette.accent;
      context.fillRect(150 + shift, 230, 790, 52);
      label(context, "Account", 180 + shift, 264, "#ffffff", palette.font, true, 20);
      label(context, "Number", 350 + shift, 264, "#ffffff", palette.font, true, 20);
      label(context, "Available", 500 + shift, 264, "#ffffff", palette.font, true, 20);
      label(context, "Open", 655 + shift, 264, "#ffffff", palette.font, true, 20);
      rowsByTenant[tenant].forEach((kind, index) => {
        const account = accounts[kind];
        const y = 282 + index * 100;
        context.fillStyle = palette.panel;
        context.strokeStyle = "#a6b0ba";
        context.fillRect(150 + shift, y, 790, 94);
        context.strokeRect(150 + shift, y, 790, 94);
        label(context, account.label, 180 + shift, y + 58, palette.ink, palette.font, true, 28);
        label(context, account.maskedNumber, 350 + shift, y + 58, palette.ink, palette.font, false, 22);
        label(context, account.balance, 500 + shift, y + 58, palette.ink, palette.font, false, 22);
        if (kind === "savings" && memberFixtures[memberId]?.name === "changed_icon") {
          changedIcon(context, 655 + shift, y + 19, palette.accent);
        } else {
          iconButton(context, 655 + shift, y + 19, palette.accent);
        }
      });
      return;
    }

    heading(context, `${accounts[selectedAccount].label} Account Details`, 130 + shift, 165, palette.accent, palette.font);
    const account = accounts[selectedAccount];
    const fields: ReadonlyArray<readonly [string, string]> = [
      ["Member ID", memberId],
      ["Account type", account.label],
      ["Currency", "USD"],
      ["Available balance", account.balance],
      ["As of", "2026-09-10T12:30:00Z"],
    ];
    fields.forEach(([name, value], index) => {
      const y = 240 + index * 82;
      label(context, name, 180 + shift, y, palette.muted, palette.font);
      label(context, value, 560 + shift, y, palette.ink, palette.font, true);
    });
  }, [inputActive, memberId, palette, screen, selectedAccount, shift, tenant]);

  useEffect(() => draw(), [draw]);

  const onPointer = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * (WIDTH / bounds.width);
    const y = (event.clientY - bounds.top) * (HEIGHT / bounds.height);

    if (screen === "search" && x >= 370 + shift && x <= 730 + shift && y >= 220 && y <= 286) {
      setInputActive(true);
      event.currentTarget.focus();
    } else if (screen === "search" && x >= 770 + shift && x <= 950 + shift && y >= 220 && y <= 286) {
      submitSearch();
    } else if (screen === "notice" && x >= 510 + shift && x <= 690 + shift && y >= 395 && y <= 450) {
      setScreen("results");
    } else if (screen === "results" && x >= 640 + shift && x <= 720 + shift && y >= 282 && y <= 676) {
      const index = Math.floor((y - 282) / 100);
      const kind = rowsByTenant[tenant][index];
      if (kind) {
        setSelectedAccount(kind);
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
      width={WIDTH}
      height={HEIGHT}
      tabIndex={0}
      aria-label="Rendered legacy member workbench"
      onClick={onPointer}
      onKeyDown={onKey}
      style={{ width: "100vw", height: "100vh", display: "block", outline: "none" }}
    />
  );
}

function heading(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  color: string,
  font: string,
) {
  context.fillStyle = color;
  context.font = `700 31px ${font}`;
  context.fillText(text, x, y);
}

function label(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  color: string,
  font: string,
  strong = false,
  size = 22,
) {
  context.fillStyle = color;
  context.font = `${strong ? "700" : "500"} ${size}px ${font}`;
  context.fillText(text, x, y);
}

function button(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  width: number,
  height: number,
  color: string,
  font: string,
) {
  context.fillStyle = color;
  context.fillRect(x, y, width, height);
  context.fillStyle = "#ffffff";
  context.font = `700 23px ${font}`;
  context.textAlign = "center";
  context.fillText(text, x + width / 2, y + Math.round(height * 0.64));
  context.textAlign = "left";
}

function iconButton(context: CanvasRenderingContext2D, x: number, y: number, color: string) {
  context.fillStyle = color;
  context.fillRect(x, y, 62, 56);
  context.strokeStyle = "#ffffff";
  context.lineWidth = 5;
  context.beginPath();
  context.moveTo(x + 20, y + 15);
  context.lineTo(x + 41, y + 28);
  context.lineTo(x + 20, y + 41);
  context.stroke();
}

function changedIcon(context: CanvasRenderingContext2D, x: number, y: number, color: string) {
  context.fillStyle = color;
  context.fillRect(x, y, 62, 56);
  context.strokeStyle = "#ffffff";
  context.lineWidth = 5;
  context.beginPath();
  context.moveTo(x + 16, y + 14);
  context.lineTo(x + 46, y + 42);
  context.moveTo(x + 46, y + 14);
  context.lineTo(x + 16, y + 42);
  context.stroke();
}
