"use client";

import { useEffect, useRef, useState } from "react";
import type { ClipboardEvent, KeyboardEvent } from "react";
import type { Tenant } from "../../../lib/servicing/bank";
import { act, createWorkspace } from "../../../lib/servicing/workspace";
import { paint } from "../../../lib/servicing/renderer";
import type { Hit, Scene } from "../../../lib/servicing/renderer";
import {
  deleteSelection,
  endSelection,
  moveSelection,
  replaceSelection,
} from "../../../lib/servicing/text";
import type { TextSelection } from "../../../lib/servicing/text";

export function ServicingTerminal({ tenant }: { tenant: Tenant }) {
  const [state, setState] = useState(() => createWorkspace(tenant));
  const [size, setSize] = useState({ width: 1280, height: 800, dpr: 1 });
  const [scroll, setScroll] = useState(0);
  const [focus, setFocus] = useState("");
  const [openSelect, setOpenSelect] = useState("");
  const [selection, setSelection] = useState<TextSelection>(endSelection(""));
  const canvas = useRef<HTMLCanvasElement>(null);
  const keyboard = useRef<HTMLTextAreaElement>(null);
  const scene = useRef<Scene>({
    hits: [],
    contentHeight: 0,
    bodyHeight: 1,
    bodyTop: 0,
  });
  const drag = useRef<{ y: number; scroll: number; ratio: number } | null>(
    null,
  );
  const scrollbarClick = useRef(false);
  const active = useRef({ focus, selection, state });
  active.current = { focus, selection, state };
  useEffect(() => {
    if (focus.startsWith("field:"))
      keyboard.current?.focus({ preventScroll: true });
  }, [focus]);
  useEffect(() => {
    const measure = () =>
      setSize({
        width: Math.max(800, window.innerWidth),
        height: Math.max(600, window.innerHeight),
        dpr: Math.min(window.devicePixelRatio || 1, 3),
      });
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  useEffect(() => {
    const target = canvas.current;
    if (!target) return;
    target.width = Math.round(size.width * size.dpr);
    target.height = Math.round(size.height * size.dpr);
    const ctx = target.getContext("2d");
    if (!ctx) return;
    ctx.scale(size.dpr, size.dpr);
    scene.current = paint(
      ctx,
      state,
      size.width,
      size.height,
      scroll,
      focus,
      openSelect,
      selection,
    );
    const max = Math.max(
      0,
      scene.current.contentHeight - scene.current.bodyHeight,
    );
    if (scroll > max) setScroll(max);
  }, [state, size, scroll, focus, openSelect, selection]);
  useEffect(() => {
    setScroll(0);
    setFocus("");
    setOpenSelect("");
    setSelection(endSelection(""));
  }, [state.page, state.memberId]);
  const changeSelection = (next: TextSelection) => {
    active.current.selection = next;
    setSelection(next);
  };
  const edit = (
    operation: (
      value: string,
      range: TextSelection,
      limit: number,
    ) => { value: string; selection: TextSelection },
  ) => {
    const current = active.current;
    const hit = scene.current.hits.find(
      (h) => h.id === current.focus && h.kind === "field",
    );
    if (!hit) return;
    const key = hit.id.slice(6);
    const edited = operation(
      current.state.fields[key] ?? "",
      current.selection,
      hit.maxLength ?? 160,
    );
    active.current.state = {
      ...current.state,
      fields: { ...current.state.fields, [key]: edited.value },
    };
    setState((s) => ({
      ...s,
      fields: {
        ...s.fields,
        [key]: edited.value,
      },
    }));
    changeSelection(edited.selection);
  };
  const insert = (value: string) =>
    edit((text, range, limit) => replaceSelection(text, range, value, limit));
  const copy = (event: ClipboardEvent<HTMLElement>, cut = false) => {
    const current = active.current;
    if (
      !scene.current.hits.some(
        (hit) => hit.id === current.focus && hit.kind === "field",
      )
    )
      return;
    event.preventDefault();
    const value = current.state.fields[current.focus.slice(6)] ?? "";
    const start = Math.min(current.selection.anchor, current.selection.caret);
    const end = Math.max(current.selection.anchor, current.selection.caret);
    if (end > start) {
      event.clipboardData.setData("text/plain", value.slice(start, end));
      if (cut) insert("");
    }
  };
  const insertRef = useRef(insert);
  insertRef.current = insert;
  useEffect(() => {
    const target = canvas.current;
    const input = keyboard.current;
    if (!target || !input) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setOpenSelect("");
      setScroll((value) =>
        Math.max(
          0,
          Math.min(
            scene.current.contentHeight - scene.current.bodyHeight,
            value + event.deltaY,
          ),
        ),
      );
    };
    const beforeInput = (event: InputEvent) => {
      event.preventDefault();
      if (event.data && event.inputType.startsWith("insert"))
        insertRef.current(event.data);
    };
    target.addEventListener("wheel", wheel, { passive: false });
    input.addEventListener("beforeinput", beforeInput);
    return () => {
      target.removeEventListener("wheel", wheel);
      input.removeEventListener("beforeinput", beforeInput);
    };
  }, []);
  const focusControl = (id: string) => {
    setFocus(id);
    active.current.focus = id;
    changeSelection(
      endSelection(active.current.state.fields[id.slice(6)] ?? ""),
    );
    const layout = scene.current;
    const hit = layout.hits.find(
      (item) => item.id === id && item.kind !== "option",
    );
    if (hit?.fullY !== undefined && hit.fullHeight) {
      const top = layout.bodyTop;
      const bottom = top + layout.bodyHeight;
      const delta =
        hit.fullY < top
          ? hit.fullY - top
          : Math.max(0, hit.fullY + hit.fullHeight - bottom);
      if (delta)
        setScroll((value) =>
          Math.max(
            0,
            Math.min(layout.contentHeight - layout.bodyHeight, value + delta),
          ),
        );
    }
    if (id.startsWith("field:"))
      keyboard.current?.focus({ preventScroll: true });
    else canvas.current?.focus({ preventScroll: true });
  };
  const activate = (hit: Hit) => {
    focusControl(hit.id);
    if (hit.kind === "action") {
      setState((s) => act(s, hit.id));
      setOpenSelect("");
      setScroll(0);
    } else if (hit.kind === "select")
      setOpenSelect((value) => (value === hit.id ? "" : hit.id));
    else if (hit.kind === "option") {
      setState((s) => ({
        ...s,
        fields: { ...s.fields, [hit.id.slice(6)]: hit.value ?? "" },
      }));
      setOpenSelect("");
    } else setOpenSelect("");
  };
  const onKey = (event: KeyboardEvent<HTMLElement>) => {
    const hit = scene.current.hits.find(
      (h) => h.id === focus && h.kind !== "option",
    );
    if (event.key === "F2") {
      event.preventDefault();
      setState((s) => act(s, "nav:inquiry"));
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setOpenSelect("");
      return;
    }
    if (event.key === "PageDown" || event.key === "PageUp") {
      event.preventDefault();
      setOpenSelect("");
      setScroll((value) =>
        Math.max(
          0,
          Math.min(
            scene.current.contentHeight - scene.current.bodyHeight,
            value +
              (event.key === "PageDown" ? 1 : -1) *
                scene.current.bodyHeight *
                0.7,
          ),
        ),
      );
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      const controls = scene.current.hits.filter((h) => h.kind !== "option");
      const index = controls.findIndex((h) => h.id === focus);
      focusControl(
        controls[
          (index < 0
            ? event.shiftKey
              ? controls.length - 1
              : 0
            : index + (event.shiftKey ? -1 : 1) + controls.length) %
            controls.length
        ]?.id ?? "",
      );
      setOpenSelect("");
      return;
    }
    if (
      hit?.kind === "select" &&
      ["ArrowDown", "ArrowUp"].includes(event.key)
    ) {
      event.preventDefault();
      const options = hit.options ?? [];
      const key = hit.id.slice(6);
      const index = options.findIndex((o) => o.value === state.fields[key]);
      const option =
        options[
          (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) %
            options.length
        ];
      if (option)
        setState((s) => ({
          ...s,
          fields: { ...s.fields, [key]: option.value },
        }));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (hit && hit.kind !== "field") activate(hit);
      else if (focus === "field:query") setState((s) => act(s, "search"));
      return;
    }
    if (hit?.kind !== "field") {
      if (event.key.length === 1) event.preventDefault();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();
      changeSelection({
        anchor: 0,
        caret: (state.fields[hit.id.slice(6)] ?? "").length,
      });
      return;
    }
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      changeSelection(
        moveSelection(
          state.fields[hit.id.slice(6)] ?? "",
          active.current.selection,
          event.key,
          event.shiftKey,
          event.ctrlKey || event.altKey,
        ),
      );
      return;
    }
    if (event.key === "Backspace" || event.key === "Delete") {
      event.preventDefault();
      edit((value, range) =>
        deleteSelection(
          value,
          range,
          event.key === "Backspace",
          event.ctrlKey || event.altKey,
        ),
      );
      return;
    }
    if (
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      event.nativeEvent.isComposing
    )
      return;
    if (event.key.length === 1) {
      event.preventDefault();
      insert(event.key);
    }
  };
  return (
    <>
      <textarea
        ref={keyboard}
        tabIndex={-1}
        aria-hidden="true"
        autoComplete="off"
        spellCheck={false}
        style={{ position: "fixed", left: -10000, top: 0, width: 1, height: 1 }}
        onKeyDown={onKey}
        onCopy={copy}
        onCut={(event) => copy(event, true)}
        onInput={(event) => {
          event.currentTarget.value = "";
        }}
        onPaste={(event) => {
          event.preventDefault();
          insert(event.clipboardData.getData("text/plain"));
        }}
      />
      <canvas
        ref={canvas}
        tabIndex={0}
        aria-label="Northstar rendered servicing terminal"
        style={{
          display: "block",
          width: size.width,
          height: size.height,
          outline: "none",
          caretColor: "transparent",
        }}
        onKeyDown={onKey}
        onCopy={copy}
        onCut={(event) => copy(event, true)}
        onPaste={(event) => {
          event.preventDefault();
          insert(event.clipboardData.getData("text/plain"));
        }}
        onClick={(event) => {
          if (scrollbarClick.current) {
            scrollbarClick.current = false;
            return;
          }
          canvas.current?.focus();
          const rect = event.currentTarget.getBoundingClientRect();
          const x = ((event.clientX - rect.left) * size.width) / rect.width;
          const y = ((event.clientY - rect.top) * size.height) / rect.height;
          const hit = [...scene.current.hits]
            .reverse()
            .find(
              (h) => x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h,
            );
          if (openSelect && hit?.kind !== "option" && hit?.kind !== "select") {
            setOpenSelect("");
            return;
          }
          if (hit) {
            activate(hit);
            if (hit.kind === "field" && y >= (hit.inputY ?? hit.y + 24)) {
              const value = state.fields[hit.id.slice(6)] ?? "";
              const ctx = canvas.current?.getContext("2d");
              if (ctx) {
                ctx.font = "14px Tahoma, Arial, sans-serif";
                const start = hit.textStart ?? 0;
                let caret = start;
                for (const character of value.slice(start)) {
                  const width = ctx.measureText(
                    value.slice(start, caret),
                  ).width;
                  if (
                    x - hit.x - 8 <
                    width + ctx.measureText(character).width / 2
                  )
                    break;
                  caret += character.length;
                }
                changeSelection({ anchor: caret, caret });
              }
            }
          } else {
            setOpenSelect("");
            setFocus("");
          }
        }}
        onPointerDown={(event) => {
          const bar = scene.current.scrollbar;
          if (!bar || event.button !== 0) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          const x = event.clientX - bounds.left;
          const y = event.clientY - bounds.top;
          if (x < bar.x || x > bar.x + bar.w || y < bar.y || y > bar.y + bar.h)
            return;
          event.preventDefault();
          scrollbarClick.current = true;
          setOpenSelect("");
          const ratio = bar.maxScroll / (bar.h - bar.thumbHeight);
          const initial =
            y >= bar.thumbY && y <= bar.thumbY + bar.thumbHeight
              ? scroll
              : Math.max(
                  0,
                  Math.min(
                    bar.maxScroll,
                    (y - bar.y - bar.thumbHeight / 2) * ratio,
                  ),
                );
          setScroll(initial);
          drag.current = { y: event.clientY, scroll: initial, ratio };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const current = drag.current;
          const bar = scene.current.scrollbar;
          if (current && bar)
            setScroll(
              Math.max(
                0,
                Math.min(
                  bar.maxScroll,
                  current.scroll + (event.clientY - current.y) * current.ratio,
                ),
              ),
            );
        }}
        onPointerUp={(event) => {
          drag.current = null;
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          drag.current = null;
          scrollbarClick.current = false;
        }}
      />
    </>
  );
}
