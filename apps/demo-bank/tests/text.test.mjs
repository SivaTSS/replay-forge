import assert from "node:assert/strict";
import { test } from "node:test";
import {
  deleteSelection,
  endSelection,
  moveSelection,
  replaceSelection,
  wrapText,
} from "../lib/servicing/text.ts";

test("cursor moves, selects, inserts and deletes at the actual caret", () => {
  let selection = moveSelection("12345", endSelection("12345"), "Home");
  let edited = deleteSelection("12345", selection, false);
  edited = replaceSelection(edited.value, edited.selection, "9", 60);
  assert.equal(edited.value, "92345");
  selection = moveSelection(edited.value, edited.selection, "End");
  selection = moveSelection(edited.value, selection, "ArrowLeft", true);
  edited = replaceSelection(edited.value, selection, "6", 60);
  assert.equal(edited.value, "92346");
  assert.deepEqual(edited.selection, endSelection(edited.value));
});
test("reversed selections collapse correctly and delete only their selected range", () => {
  const range = { anchor: 4, caret: 1 };
  assert.deepEqual(moveSelection("abcde", range, "ArrowLeft"), {
    anchor: 1,
    caret: 1,
  });
  assert.deepEqual(moveSelection("abcde", range, "ArrowRight"), {
    anchor: 4,
    caret: 4,
  });
  assert.equal(deleteSelection("abcde", range, true).value, "ae");
  assert.equal(replaceSelection("abcde", range, "X", 5).value, "aXe");
});
test("input limits retain unselected suffixes and never split surrogate pairs", () => {
  assert.equal(
    replaceSelection("12345", { anchor: 1, caret: 3 }, "abcdef", 5).value,
    "1ab45",
  );
  assert.equal(replaceSelection("", endSelection(""), "😀", 1).value, "");
  assert.equal(
    deleteSelection("A😀B", { anchor: 3, caret: 3 }, true).value,
    "AB",
  );
  assert.equal(
    replaceSelection("", endSelection(""), "a\nb\tc", 10).value,
    "a b c",
  );
});
test("word navigation and deletion remain bounded at either end", () => {
  const value = "two distinct dates";
  assert.equal(
    moveSelection(value, { anchor: 3, caret: 3 }, "ArrowRight", false, true)
      .caret,
    4,
  );
  assert.equal(
    moveSelection(value, endSelection(value), "ArrowLeft", false, true).caret,
    13,
  );
  assert.equal(
    deleteSelection(value, endSelection(value), true, true).value,
    "two distinct ",
  );
  assert.deepEqual(moveSelection(value, { anchor: 0, caret: 0 }, "ArrowLeft"), {
    anchor: 0,
    caret: 0,
  });
  assert.equal(deleteSelection(value, endSelection(value), false).value, value);
});
test("wrapping preserves long unbroken text and fits every measured line", () => {
  for (const value of [
    "A".repeat(160),
    "Account confirmation reference",
    "😀".repeat(40),
  ]) {
    const lines = wrapText(value, 12, (text) => Array.from(text).length);
    assert.ok(lines.every((line) => Array.from(line).length <= 12));
    assert.equal(lines.join("").replace(/\s/g, ""), value.replace(/\s/g, ""));
  }
});
