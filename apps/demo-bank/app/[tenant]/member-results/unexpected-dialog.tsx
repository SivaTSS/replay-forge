"use client";

import { useEffect } from "react";

export function UnexpectedDialog() {
  useEffect(() => {
    window.confirm("A supervisor acknowledgement is required before continuing.");
  }, []);
  return null;
}
