import { useEffect, useRef, useState } from "react";
import { usePersistentState } from "../lib/persist";
import { pickQuote, hourKey, userSeed, THEME_LABEL, type Quote } from "../lib/quotes";
import "./dailyquote.css";

// Always-visible top ribbon carrying the current reminder. The quote ROTATES EVERY HOUR and, on
// each new hour, plays a one-shot typewriter reveal (the "reminder" moment). Selection is per
// device (a random per-browser seed) so every device sees its own order. Honours reduced-motion.
export function DailyQuote() {
  const [bucket, setBucket] = useState(hourKey());
  const [quote, setQuote] = useState<Quote>(() => pickQuote());
  const [seenBucket, setSeenBucket] = usePersistentState<string>("quoteSeenBucket", "");
  const [shown, setShown] = useState("");
  const timer = useRef<number | null>(null);

  const reduce = typeof window !== "undefined"
    && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  // Watch the clock: when the hour rolls over, advance the bucket (drives the effect below).
  useEffect(() => {
    const id = window.setInterval(() => {
      const b = hourKey();
      setBucket((prev) => (prev === b ? prev : b));
    }, 30000);
    return () => window.clearInterval(id);
  }, []);

  // On mount and on every hour change: pick the hour's quote and, if it's a new hour for this
  // device, type it out once. Otherwise show it instantly.
  useEffect(() => {
    const q = pickQuote(userSeed(), bucket);
    setQuote(q);
    const animate = seenBucket !== bucket && !reduce;
    if (seenBucket !== bucket) setSeenBucket(bucket);
    if (timer.current) window.clearTimeout(timer.current);
    if (!animate) { setShown(q.text); return; }
    setShown("");
    let i = 0;
    const step = () => {
      i++;
      setShown(q.text.slice(0, i));
      if (i < q.text.length) timer.current = window.setTimeout(step, 34);
    };
    timer.current = window.setTimeout(step, 350);   // small beat before it types
    return () => { if (timer.current) window.clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bucket]);

  const typing = shown.length < quote.text.length;

  return (
    <div className={`dq dq-${quote.theme}`} role="status" aria-label="Reminder">
      <span className="dq-mark">✦</span>
      <span className="dq-text">
        {shown}
        {typing ? <span className="dq-caret" aria-hidden="true" /> : null}
      </span>
      {quote.author && !typing ? <span className="dq-by">— {quote.author}</span> : null}
      <span className="dq-tag">{THEME_LABEL[quote.theme]}</span>
    </div>
  );
}
