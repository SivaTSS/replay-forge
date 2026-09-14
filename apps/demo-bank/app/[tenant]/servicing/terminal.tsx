"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { Tenant } from "../../../lib/servicing/bank";
import { act, createWorkspace } from "../../../lib/servicing/workspace";
import { paint } from "../../../lib/servicing/renderer";
import type { Hit, Scene } from "../../../lib/servicing/renderer";

export function ServicingTerminal({ tenant }: { tenant: Tenant }) {
  const [state, setState] = useState(() => createWorkspace(tenant));
  const [size, setSize] = useState({ width: 1280, height: 800, dpr: 1 });
  const [scroll, setScroll] = useState(0);
  const [focus, setFocus] = useState("");
  const [openSelect, setOpenSelect] = useState("");
  const [selectedAll, setSelectedAll] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const keyboard = useRef<HTMLTextAreaElement>(null);
  const scene = useRef<Scene>({ hits: [], contentHeight: 0, bodyHeight: 1 });
  const active = useRef({ focus, selectedAll, state });
  active.current = { focus, selectedAll, state };
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
      selectedAll,
    );
    const max = Math.max(
      0,
      scene.current.contentHeight - scene.current.bodyHeight,
    );
    if (scroll > max) setScroll(max);
  }, [state, size, scroll, focus, openSelect, selectedAll]);
  useEffect(() => {
    setScroll(0);
    setFocus("");
    setOpenSelect("");
    setSelectedAll(false);
  }, [state.page, state.memberId]);
  const insert = (value: string) => {
    const current = active.current;
    const hit = scene.current.hits.find(
      (h) => h.id === current.focus && h.kind === "field",
    );
    if (!hit) return;
    const key = hit.id.slice(6);
    setState((s) => ({
      ...s,
      fields: {
        ...s.fields,
        [key]:
          `${current.selectedAll ? "" : (s.fields[key] ?? "")}${value.replace(/[\r\n\t]/g, " ")}`.slice(
            0,
            hit.maxLength ?? 160,
          ),
      },
    }));
    setSelectedAll(false);
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
  const activate = (hit: Hit) => {
    setFocus(hit.id);
    if (hit.id.startsWith("field:"))
      keyboard.current?.focus({ preventScroll: true });
    setSelectedAll(false);
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
      setSelectedAll(false);
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
      setFocus(
        controls[
          (index + (event.shiftKey ? -1 : 1) + controls.length) %
            controls.length
        ]?.id ?? "",
      );
      setOpenSelect("");
      setSelectedAll(false);
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
      setSelectedAll(true);
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Backspace" || event.key === "Delete") {
      event.preventDefault();
      const key = hit.id.slice(6);
      setState((s) => ({
        ...s,
        fields: {
          ...s.fields,
          [key]: selectedAll
            ? ""
            : event.key === "Backspace"
              ? (s.fields[key] ?? "").slice(0, -1)
              : (s.fields[key] ?? ""),
        },
      }));
      setSelectedAll(false);
      return;
    }
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
        onPaste={(event) => {
          event.preventDefault();
          insert(event.clipboardData.getData("text/plain"));
        }}
        onClick={(event) => {
          canvas.current?.focus();
          const rect = event.currentTarget.getBoundingClientRect();
          const x = ((event.clientX - rect.left) * size.width) / rect.width;
          const y = ((event.clientY - rect.top) * size.height) / rect.height;
          const hit = [...scene.current.hits]
            .reverse()
            .find(
              (h) => x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h,
            );
          if (hit) activate(hit);
          else {
            setOpenSelect("");
            setFocus("");
          }
        }}
      />
    </>
  );
}
