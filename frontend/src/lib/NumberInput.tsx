import { useEffect, useState, type InputHTMLAttributes } from "react";

// A number input that can actually be CLEARED and retyped. The naive
// `value={n} onChange={setN(+e.target.value || default)}` snaps an empty field straight back
// to the default, so backspacing the last digit appears to do nothing. This keeps a local
// text buffer, lets the field be empty while editing, commits valid numbers live, and falls
// back to `fallback` only on blur.
type Props = {
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  fallback?: number;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type" | "min" | "max">;

export function NumberInput({ value, onChange, min, max, fallback, ...rest }: Props) {
  const [text, setText] = useState(String(value));
  // Sync when the value changes from OUTSIDE (e.g. a preset reset), not from our own typing.
  useEffect(() => { if (Number(text) !== value) setText(String(value)); /* eslint-disable-next-line */ }, [value]);

  const clamp = (n: number) => {
    if (min != null) n = Math.max(min, n);
    if (max != null) n = Math.min(max, n);
    return n;
  };

  return (
    <input
      {...rest}
      type="number"
      inputMode="numeric"
      min={min}
      max={max}
      value={text}
      onChange={(e) => {
        const t = e.target.value;
        setText(t);
        if (t === "" || t === "-") return;             // allow an empty / partial field while typing
        const n = Number(t);
        if (Number.isFinite(n)) onChange(clamp(n));
      }}
      onBlur={(e) => {
        const n = Number(text);
        const v = (text === "" || !Number.isFinite(n)) ? (fallback ?? min ?? 0) : clamp(n);
        setText(String(v));
        onChange(v);
        rest.onBlur?.(e);
      }}
    />
  );
}
