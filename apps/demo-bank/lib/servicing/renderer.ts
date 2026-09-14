import { buildView, NAV } from "./workspace";
import type { Option, Workspace } from "./workspace";

export type Hit = {
  x: number;
  y: number;
  w: number;
  h: number;
  id: string;
  kind: "action" | "field" | "select" | "option";
  value?: string;
  options?: Option[];
  maxLength?: number;
};
export interface Scene {
  hits: Hit[];
  contentHeight: number;
  bodyHeight: number;
}
export function paint(
  ctx: CanvasRenderingContext2D,
  state: Workspace,
  width: number,
  height: number,
  scroll: number,
  focus: string,
  openSelect: string,
  selectedAll: boolean,
): Scene {
  const hits: Hit[] = [];
  const summit = state.bank.tenant === "summit";
  const colors = {
    navy: summit ? "#36534b" : "#233e5b",
    desktop: summit ? "#d5d1c5" : "#ccd1d5",
    panel: "#f4f3eb",
    ink: "#202b32",
    border: "#949d9f",
    muted: "#59666a",
  };
  const sidebar = width < 1000 ? 180 : 210;
  const left = sidebar + 22;
  const bodyTop = 164;
  const bodyBottom = height - 38;
  const contentWidth = width - left - 28;
  const text = (
    value: string,
    x: number,
    y: number,
    maxWidth: number,
    size = 14,
    color = colors.ink,
    bold = false,
  ) => {
    ctx.font = `${bold ? "bold " : ""}${size}px Tahoma, Arial, sans-serif`;
    ctx.fillStyle = color;
    let label = value;
    if (ctx.measureText(label).width > maxWidth) {
      while (label.length && ctx.measureText(`${label}…`).width > maxWidth)
        label = label.slice(0, -1);
      label += "…";
    }
    ctx.fillText(label, x, y);
  };
  const box = (
    x: number,
    y: number,
    w: number,
    h: number,
    fill = colors.panel,
  ) => {
    ctx.fillStyle = fill;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = colors.border;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
  };
  const button = (
    id: string,
    label: string,
    x: number,
    y: number,
    w: number,
    primary = false,
    fixed = false,
  ) => {
    box(x, y, w, 30, primary ? colors.navy : "#e3e4df");
    ctx.strokeStyle = "#ffffff";
    ctx.beginPath();
    ctx.moveTo(x + 1, y + 29);
    ctx.lineTo(x + 1, y + 1);
    ctx.lineTo(x + w - 1, y + 1);
    ctx.stroke();
    text(
      label,
      x + 10,
      y + 20,
      w - 20,
      13,
      primary ? "#ffffff" : colors.ink,
      true,
    );
    if (focus === id) {
      ctx.setLineDash([2, 2]);
      ctx.strokeStyle = primary ? "#fff" : colors.ink;
      ctx.strokeRect(x + 4, y + 4, w - 8, 22);
      ctx.setLineDash([]);
    }
    if (fixed || (y >= bodyTop && y + 30 <= bodyBottom))
      hits.push({ x, y, w, h: 30, id, kind: "action" });
  };
  const lines = (value: string, maxWidth: number): string[] => {
    ctx.font = "14px Tahoma, Arial, sans-serif";
    const result: string[] = [];
    let line = "";
    for (const word of value.split(/\s+/)) {
      if (line && ctx.measureText(`${line} ${word}`).width > maxWidth) {
        result.push(line);
        line = word;
      } else line = line ? `${line} ${word}` : word;
    }
    result.push(line);
    return result;
  };
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = colors.desktop;
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = colors.navy;
  ctx.fillRect(0, 0, width, 34);
  text(
    `${summit ? "SUMMIT COMMUNITY BANK" : "HARBOR CREDIT UNION"}  /  NORTHSTAR 7.4`,
    14,
    23,
    width - 270,
    15,
    "#ffffff",
    true,
  );
  text("BRANCH SERVICING TERMINAL", width - 252, 23, 240, 12, "#ffffff");
  box(0, 34, width, 33, "#e6e5dc");
  text(
    "OPERATIONS   /   MEMBER SERVICES",
    15,
    56,
    width / 2,
    12,
    colors.ink,
    true,
  );
  text(
    `TRAINING ONLY  |  ${state.bank.businessDate}  |  ${state.bank.role.toUpperCase()}`,
    Math.max(width / 2, width - 460),
    56,
    width / 2 - 14,
    12,
    "#754320",
    true,
  );
  box(8, 76, sidebar - 8, height - 115, "#e8e8e0");
  text("APPLICATION MENU", 19, 100, sidebar - 30, 12, colors.muted, true);
  NAV.forEach((item, index) =>
    button(
      `nav:${item.id}`,
      item.label,
      16,
      114 + index * 35,
      sidebar - 24,
      state.page === item.id,
      true,
    ),
  );
  if (height > 645) {
    text("SESSION", 20, height - 143, sidebar - 34, 12, colors.muted, true);
    text(
      `Institution: ${state.bank.tenant}`,
      20,
      height - 121,
      sidebar - 34,
      12,
    );
    text(
      `Revision: ${state.bank.revision}`,
      20,
      height - 101,
      sidebar - 34,
      12,
    );
    text("Local training records", 20, height - 81, sidebar - 34, 12);
    text(
      "No production connection",
      20,
      height - 61,
      sidebar - 34,
      11,
      "#754320",
    );
  }
  const view = buildView(state);
  box(sidebar + 8, 76, width - sidebar - 17, height - 115, "#ffffff");
  ctx.fillStyle = colors.navy;
  ctx.fillRect(sidebar + 9, 77, width - sidebar - 19, 34);
  text(view.title.toUpperCase(), left, 100, contentWidth, 17, "#ffffff", true);
  box(sidebar + 9, 111, width - sidebar - 19, 40, "#eeeee6");
  text(view.subtitle, left, 136, contentWidth, 14, colors.ink, true);
  ctx.save();
  ctx.beginPath();
  ctx.rect(sidebar + 10, bodyTop, width - sidebar - 22, bodyBottom - bodyTop);
  ctx.clip();
  let y = bodyTop + 4 - scroll;
  let dropdown: { hit: Hit; options: Option[] } | undefined;
  for (const block of view.blocks) {
    if (block.kind === "heading") {
      box(left, y, contentWidth, 29, "#e4e8e7");
      text(
        block.text,
        left + 8,
        y + 20,
        contentWidth - 16,
        14,
        colors.navy,
        true,
      );
      y += 38;
    } else if (block.kind === "note") {
      const wrapped = lines(block.text, contentWidth - 24);
      const h = wrapped.length * 21 + 18;
      box(
        left,
        y,
        contentWidth,
        h,
        block.tone === "error"
          ? "#fbe8e3"
          : block.tone === "warning"
            ? "#fff2cc"
            : "#f1f3ef",
      );
      wrapped.forEach((line, index) =>
        text(
          line,
          left + 10,
          y + 23 + index * 21,
          contentWidth - 20,
          14,
          block.tone === "error" ? "#8a251c" : colors.ink,
        ),
      );
      y += h + 13;
    } else if (block.kind === "values") {
      for (const [label, value] of block.rows) {
        const labelWidth = Math.min(230, contentWidth * 0.32);
        const wrapped = lines(value, contentWidth - labelWidth - 24);
        const h = Math.max(35, wrapped.length * 21 + 12);
        box(left, y, labelWidth, h, "#eeeee6");
        box(left + labelWidth, y, contentWidth - labelWidth, h, "#fff");
        text(label, left + 10, y + 23, labelWidth - 20, 13, colors.muted, true);
        wrapped.forEach((line, index) =>
          text(
            line,
            left + labelWidth + 10,
            y + 23 + index * 21,
            contentWidth - labelWidth - 20,
            14,
            colors.ink,
            true,
          ),
        );
        y += h;
      }
      y += 15;
    } else if (block.kind === "fields") {
      const columns = contentWidth >= 800 ? 2 : 1;
      const gap = 22;
      const fieldWidth = (contentWidth - gap * (columns - 1)) / columns;
      block.fields.forEach((field, index) => {
        const fx = left + (index % columns) * (fieldWidth + gap);
        const fy = y + Math.floor(index / columns) * 74;
        text(field.label, fx, fy + 15, fieldWidth, 13, colors.muted, true);
        box(fx, fy + 24, fieldWidth, 34, "#fff");
        const id = `field:${field.key}`;
        if (focus === id) {
          ctx.strokeStyle = colors.navy;
          ctx.lineWidth = 2;
          ctx.strokeRect(fx + 1, fy + 25, fieldWidth - 2, 32);
        }
        const raw = state.fields[field.key] ?? "";
        const value = field.options
          ? (field.options.find((o) => o.value === raw)?.label ?? "Select...")
          : raw;
        if (selectedAll && focus === id && value) {
          ctx.fillStyle = "#c1d4e4";
          ctx.fillRect(
            fx + 5,
            fy + 29,
            Math.min(fieldWidth - 34, ctx.measureText(value).width + 8),
            24,
          );
        }
        // Long editable input shows its tail; the complete reviewed value is shown on confirmation.
        text(
          value.length > 50 && !field.options ? `…${value.slice(-48)}` : value,
          fx + 8,
          fy + 47,
          fieldWidth - (field.options ? 40 : 16),
        );
        if (focus === id && !field.options && !selectedAll)
          text(
            "|",
            fx +
              Math.min(
                fieldWidth - 16,
                10 +
                  ctx.measureText(
                    value.length > 50 ? `…${value.slice(-48)}` : value,
                  ).width,
              ),
            fy + 47,
            10,
          );
        if (field.options) {
          box(fx + fieldWidth - 28, fy + 25, 27, 32, "#e6e7e2");
          text("▼", fx + fieldWidth - 21, fy + 46, 20, 12);
        }
        // Like a conventional form label, clicking the caption focuses its input too.
        const hit: Hit = {
          x: fx,
          y: fy,
          w: fieldWidth,
          h: 58,
          id,
          kind: field.options ? "select" : "field",
          options: field.options,
          maxLength: field.maxLength ?? 40,
        };
        if (hit.y >= bodyTop && hit.y + hit.h <= bodyBottom) hits.push(hit);
        if (openSelect === id && field.options)
          dropdown = { hit, options: field.options };
      });
      y += Math.ceil(block.fields.length / columns) * 74;
    } else if (block.kind === "actions") {
      let x = left;
      for (const action of block.items) {
        ctx.font = "bold 13px Tahoma, Arial, sans-serif";
        const w = Math.max(95, ctx.measureText(action.label).width + 30);
        if (x + w > left + contentWidth) {
          x = left;
          y += 40;
        }
        button(action.id, action.label, x, y, w, action.primary);
        x += w + 10;
      }
      y += 48;
    } else {
      const count = block.columns.length;
      const weights = block.columns.map((name) =>
        !name
          ? 0.65
          : /Description|Subject|Cardholder|Reason|Operation/.test(name)
            ? 2.2
            : 1.2,
      );
      const hasActions = block.columns.at(-1) === "";
      const sum = weights
        .slice(0, hasActions ? -1 : undefined)
        .reduce((a, b) => a + b, 0);
      const widths = weights.map((w, index) =>
        hasActions && index === count - 1
          ? 76
          : ((contentWidth - (hasActions ? 76 : 0)) * w) / sum,
      );
      let x = left;
      const headers = block.columns.map((label, index) =>
        lines(label, (widths[index] ?? 100) - 14),
      );
      const headerHeight = Math.max(
        32,
        ...headers.map((ls) => ls.length * 18 + 12),
      );
      block.columns.forEach((label, index) => {
        const w = widths[index] ?? 100;
        box(x, y, w, headerHeight, "#dfe4e3");
        (headers[index] ?? [label]).forEach((line, li) =>
          text(line, x + 7, y + 21 + li * 18, w - 12, 12, colors.ink, true),
        );
        x += w;
      });
      y += headerHeight;
      if (!block.rows.length) {
        box(left, y, contentWidth, 42, "#f7f7f2");
        text("No records to display.", left + 10, y + 26, contentWidth - 20);
        y += 42;
      }
      block.rows.forEach((row, rowIndex) => {
        x = left;
        const wrapped = row.cells.map((cell, index) =>
          lines(cell, (widths[index] ?? 100) - 16),
        );
        const h = Math.max(40, ...wrapped.map((ls) => ls.length * 20 + 12));
        for (let index = 0; index < count; index += 1) {
          const w = widths[index] ?? 100;
          box(x, y, w, h, rowIndex % 2 ? "#f1f3ef" : "#ffffff");
          if (index === count - 1 && row.action)
            button(row.action, row.label ?? "Open", x + 4, y + 5, w - 8);
          else
            (wrapped[index] ?? []).forEach((line, li) =>
              text(line, x + 7, y + 24 + li * 20, w - 14, 13),
            );
          x += w;
        }
        y += h;
      });
      y += 16;
    }
  }
  const contentHeight = y + scroll - bodyTop;
  ctx.restore();
  if (dropdown) {
    const { hit, options } = dropdown;
    const optionHeight = 32;
    const total = options.length * optionHeight;
    const top = Math.max(bodyTop, Math.min(hit.y + hit.h, bodyBottom - total));
    box(hit.x, top, hit.w, total, "#ffffff");
    options.forEach((option, index) => {
      const oy = top + index * optionHeight;
      box(
        hit.x,
        oy,
        hit.w,
        optionHeight,
        state.fields[hit.id.slice(6)] === option.value ? "#d6e2ec" : "#ffffff",
      );
      text(option.label, hit.x + 9, oy + 22, hit.w - 18);
      hits.push({
        x: hit.x,
        y: oy,
        w: hit.w,
        h: optionHeight,
        id: hit.id,
        kind: "option",
        value: option.value,
      });
    });
  }
  const bodyHeight = bodyBottom - bodyTop;
  if (contentHeight > bodyHeight) {
    const trackX = width - 17;
    box(trackX, bodyTop, 9, bodyHeight, "#e0e1dc");
    const thumbHeight = Math.max(25, (bodyHeight * bodyHeight) / contentHeight);
    ctx.fillStyle = colors.navy;
    ctx.fillRect(
      trackX + 1,
      bodyTop +
        (bodyHeight - thumbHeight) *
          Math.min(1, scroll / (contentHeight - bodyHeight)),
      7,
      thumbHeight,
    );
  }
  box(0, height - 28, width, 28, "#e6e5dc");
  text(
    `F2 Inquiry    |    Tab: next control    Enter: activate    PgDn/PgUp: scroll    |    ${state.pending ? "REVIEW — NOT POSTED" : "READY"}`,
    12,
    height - 9,
    width - 160,
    11,
  );
  text("SESSION-LOCAL DATA", width - 150, height - 9, 140, 10, colors.muted);
  return { hits, contentHeight, bodyHeight };
}
