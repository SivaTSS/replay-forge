/** Canvas text editing/layout. No DOM field values or automation coordinates. */
export interface TextSelection {
  anchor: number;
  caret: number;
}

export function endSelection(value: string): TextSelection {
  return { anchor: value.length, caret: value.length };
}

function previous(value: string, index: number): number {
  return index - (Array.from(value.slice(0, index)).at(-1)?.length ?? 0);
}
function next(value: string, index: number): number {
  return index + (Array.from(value.slice(index))[0]?.length ?? 0);
}

export function replaceSelection(
  value: string,
  selection: TextSelection,
  inserted: string,
  limit: number,
): { value: string; selection: TextSelection } {
  const start = Math.max(
    0,
    Math.min(value.length, selection.anchor, selection.caret),
  );
  const end = Math.max(
    start,
    Math.min(value.length, Math.max(selection.anchor, selection.caret)),
  );
  const room = Math.max(0, limit - (value.length - (end - start)));
  let replacement = "";
  for (const character of inserted.replace(/[\r\n\t]/g, " ")) {
    if (replacement.length + character.length > room) break;
    replacement += character;
  }
  const caret = start + replacement.length;
  return {
    value: value.slice(0, start) + replacement + value.slice(end),
    selection: { anchor: caret, caret },
  };
}

export function moveSelection(
  value: string,
  selection: TextSelection,
  key: string,
  extend = false,
  word = false,
): TextSelection {
  const backwards = key === "ArrowLeft";
  let caret = selection.caret;
  if (key === "Home") caret = 0;
  else if (key === "End") caret = value.length;
  else if (!extend && selection.anchor !== caret) {
    caret = backwards
      ? Math.min(selection.anchor, caret)
      : Math.max(selection.anchor, caret);
  } else if (word) {
    if (backwards) {
      const prefix = value.slice(0, caret).replace(/\s+$/, "");
      caret = prefix.replace(/\S+$/, "").length;
    } else {
      const suffix = value.slice(caret);
      caret += suffix.match(/^\s+|^\S+\s*/)?.[0].length ?? value.length - caret;
    }
  } else caret = backwards ? previous(value, caret) : next(value, caret);
  return { anchor: extend ? selection.anchor : caret, caret };
}

export function deleteSelection(
  value: string,
  selection: TextSelection,
  backwards: boolean,
  word = false,
): { value: string; selection: TextSelection } {
  const range =
    selection.anchor === selection.caret
      ? moveSelection(
          value,
          selection,
          backwards ? "ArrowLeft" : "ArrowRight",
          true,
          word,
        )
      : selection;
  return replaceSelection(value, range, "", value.length);
}

export function wrapText(
  value: string,
  width: number,
  measure: (text: string) => number,
): string[] {
  const result: string[] = [];
  let line = "";
  for (const word of value.trim().split(/\s+/)) {
    if (line && measure(`${line} ${word}`) <= width) {
      line += ` ${word}`;
      continue;
    }
    if (line) result.push(line);
    line = "";
    // Long unbroken identifiers/reasons must remain fully inspectable, not ellipsized.
    for (const character of word) {
      if (line && measure(line + character) > width) {
        result.push(line);
        line = "";
      }
      line += character;
    }
  }
  result.push(line);
  return result;
}
