"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Tenant = "harbor" | "summit";
type Screen = "search" | "results" | "details" | "not-found";

const WIDTH = 1280;
const HEIGHT = 800;
const members = new Set(["12345", "67890"]);

export function VisualTerminal({ tenant }: { tenant: Tenant }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [screen, setScreen] = useState<Screen>("search");
  const [memberId, setMemberId] = useState("");
  const [inputActive, setInputActive] = useState(false);
  const shift = tenant === "summit" ? 42 : 0;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const palette = tenant === "harbor"
      ? { background: "#f4f7fa", panel: "#ffffff", ink: "#172234", accent: "#0b6bcb", muted: "#53657a" }
      : { background: "#f7f4ee", panel: "#fffdf8", ink: "#25221d", accent: "#725a24", muted: "#685f50" };

    context.fillStyle = palette.background;
    context.fillRect(0, 0, WIDTH, HEIGHT);
    context.fillStyle = palette.ink;
    context.font = "700 24px Arial";
    context.fillText(tenant === "harbor" ? "NORTHSTAR MEMBER SERVICE" : "SUMMIT MEMBER OPERATIONS", 64, 60);
    context.fillStyle = palette.panel;
    context.strokeStyle = "#8793a1";
    context.lineWidth = 2;
    context.fillRect(70 + shift, 100, 1120 - shift, 620);
    context.strokeRect(70 + shift, 100, 1120 - shift, 620);

    if (screen === "search") {
      heading(context, "Member Search", 130 + shift, 165, palette.accent);
      label(context, "Member ID", 180 + shift, 266, palette.ink);
      context.fillStyle = "#ffffff";
      context.strokeStyle = inputActive ? palette.accent : "#59697a";
      context.lineWidth = inputActive ? 4 : 2;
      context.fillRect(420 + shift, 220, 400, 66);
      context.strokeRect(420 + shift, 220, 400, 66);
      context.fillStyle = palette.ink;
      context.font = "24px Arial";
      context.fillText(memberId, 440 + shift, 263);
      button(context, "Search", 850 + shift, 220, 180, 66, palette.accent);
      label(context, "Synthetic IDs: 12345 or 67890", 180 + shift, 360, palette.muted);
      return;
    }

    if (screen === "not-found") {
      heading(context, "Member Results", 130 + shift, 165, palette.accent);
      label(context, "No member found", 180 + shift, 280, "#9b2c2c");
      return;
    }

    if (screen === "results") {
      heading(context, "Member Results", 130 + shift, 165, palette.accent);
      label(context, `Member ID   ${memberId}`, 180 + shift, 230, palette.ink);
      context.fillStyle = palette.accent;
      context.fillRect(150 + shift, 270, 960 - shift, 52);
      context.fillStyle = "#ffffff";
      context.font = "700 19px Arial";
      context.fillText("Account", 180 + shift, 304);
      context.fillText("Type", 450 + shift, 304);
      context.fillText("Available", 670 + shift, 304);
      context.fillText("Open", 980 + shift, 304);
      context.fillStyle = palette.panel;
      context.strokeStyle = "#a6b0ba";
      context.fillRect(150 + shift, 322, 960 - shift, 78);
      context.strokeRect(150 + shift, 322, 960 - shift, 78);
      label(context, "•••• 0421", 180 + shift, 370, palette.ink);
      label(context, "Savings", 450 + shift, 370, palette.ink);
      label(context, "$1,420.57", 670 + shift, 370, palette.ink);
      iconButton(context, 990 + shift, 333, palette.accent);
      return;
    }

    heading(context, "Savings Account Details", 130 + shift, 165, palette.accent);
    const fields: ReadonlyArray<readonly [string, string]> = [
      ["Member ID", memberId],
      ["Account type", "Savings"],
      ["Currency", "USD"],
      ["Available balance", "$1,420.57"],
      ["As of", "2026-09-10T12:30:00Z"],
    ];
    fields.forEach(([name, value], index) => {
      const y = 240 + index * 82;
      label(context, name, 180 + shift, y, palette.muted);
      label(context, value, 560 + shift, y, palette.ink, true);
    });
  }, [inputActive, memberId, screen, shift, tenant]);

  useEffect(() => draw(), [draw]);

  const onPointer = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * (WIDTH / bounds.width);
    const y = (event.clientY - bounds.top) * (HEIGHT / bounds.height);
    if (screen === "search" && x >= 420 + shift && x <= 820 + shift && y >= 220 && y <= 286) {
      setInputActive(true);
      event.currentTarget.focus();
    } else if (screen === "search" && x >= 850 + shift && x <= 1030 + shift && y >= 220 && y <= 286) {
      setScreen(members.has(memberId) ? "results" : "not-found");
      setInputActive(false);
    } else if (screen === "results" && x >= 990 + shift && x <= 1052 + shift && y >= 333 && y <= 389) {
      setScreen("details");
    }
  };

  const onKey = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (!inputActive) return;
    if (/^[0-9]$/.test(event.key) && memberId.length < 10) setMemberId((value) => value + event.key);
    if (event.key === "Backspace") setMemberId((value) => value.slice(0, -1));
    if (event.key === "Enter") {
      setScreen(members.has(memberId) ? "results" : "not-found");
      setInputActive(false);
    }
    event.preventDefault();
  };

  return (
    <canvas
      ref={canvasRef}
      width={WIDTH}
      height={HEIGHT}
      tabIndex={0}
      aria-label="Rendered legacy terminal"
      onClick={onPointer}
      onKeyDown={onKey}
      style={{ width: "100vw", height: "100vh", display: "block", outline: "none" }}
    />
  );
}

function heading(context: CanvasRenderingContext2D, text: string, x: number, y: number, color: string) {
  context.fillStyle = color;
  context.font = "700 31px Arial";
  context.fillText(text, x, y);
}

function label(context: CanvasRenderingContext2D, text: string, x: number, y: number, color: string, strong = false) {
  context.fillStyle = color;
  context.font = `${strong ? "700" : "500"} 22px Arial`;
  context.fillText(text, x, y);
}

function button(context: CanvasRenderingContext2D, text: string, x: number, y: number, width: number, height: number, color: string) {
  context.fillStyle = color;
  context.fillRect(x, y, width, height);
  context.fillStyle = "#ffffff";
  context.font = "700 23px Arial";
  context.textAlign = "center";
  context.fillText(text, x + width / 2, y + 42);
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
