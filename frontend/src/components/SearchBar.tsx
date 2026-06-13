import { useEffect, useMemo, useState } from 'react';
import { listJurisdictions } from '../api';

type Props = {
  onSubmit: (query: string) => void;
  busy: boolean;
};

const REGION_NAMES =
  typeof Intl !== 'undefined' && 'DisplayNames' in Intl
    ? new Intl.DisplayNames(['en'], { type: 'region' })
    : null;

function countryName(code: string): string {
  try {
    return REGION_NAMES?.of(code) ?? code;
  } catch {
    return code;
  }
}

export function SearchBar({ onSubmit, busy }: Props) {
  const [name, setName] = useState('');
  const [jurisdiction, setJurisdiction] = useState('');
  const [codes, setCodes] = useState<string[]>([]);

  useEffect(() => {
    void listJurisdictions().then(setCodes);
  }, []);

  const options = useMemo(
    () =>
      codes
        .map((c) => ({ code: c, label: `${countryName(c)} (${c})` }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [codes],
  );

  const canSubmit = !busy && name.trim().length > 0;
  const submit = () => {
    if (!canSubmit) return;
    // The pipeline reads "name, JURISDICTION"; the selector just builds it.
    onSubmit(jurisdiction ? `${name.trim()}, ${jurisdiction}` : name.trim());
  };

  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="flex flex-col gap-2 sm:flex-row sm:items-stretch"
    >
      <div className="relative flex-1">
        <span
          aria-hidden
          className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-muted"
        >
          <LensIcon />
        </span>
        <input
          type="search"
          autoFocus
          aria-label="Company name"
          placeholder="Company name — e.g. Tesla"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
          className="
            w-full bg-paper text-ink placeholder:text-muted
            rounded-xl border border-line
            pl-12 pr-4 py-3.5 text-lg
            shadow-[0_1px_0_rgba(26,22,18,0.03)]
            transition-[box-shadow,border-color] duration-200
            focus:outline-none focus:border-accent focus:ring-4 focus:ring-accent/15
            disabled:opacity-60
          "
        />
      </div>

      <select
        aria-label="Jurisdiction"
        value={jurisdiction}
        onChange={(e) => setJurisdiction(e.target.value)}
        disabled={busy}
        title="Restrict to a jurisdiction (optional)"
        className="
          rounded-xl border border-line bg-paper text-ink
          px-3 py-3.5 text-base
          transition-[box-shadow,border-color] duration-200
          focus:outline-none focus:border-accent focus:ring-4 focus:ring-accent/15
          disabled:opacity-60 sm:w-56
        "
      >
        <option value="">Any jurisdiction</option>
        {options.map((o) => (
          <option key={o.code} value={o.code}>
            {o.label}
          </option>
        ))}
      </select>

      <button
        type="submit"
        disabled={!canSubmit}
        className={`
          rounded-xl px-5 py-3.5 text-sm font-medium transition-colors duration-200
          ${canSubmit ? 'bg-accent text-white hover:bg-ink' : 'bg-line text-muted cursor-not-allowed'}
        `}
      >
        {busy ? '...' : 'Go'}
      </button>
    </form>
  );
}

function LensIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="10.5" cy="10.5" r="6" />
      <path d="m20 20-5.2-5.2" />
    </svg>
  );
}
