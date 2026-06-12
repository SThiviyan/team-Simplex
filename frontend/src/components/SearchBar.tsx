import { useState } from 'react';

type Props = {
  onSubmit: (query: string) => void;
  busy: boolean;
};

export function SearchBar({ onSubmit, busy }: Props) {
  const [q, setQ] = useState('');
  const canSubmit = !busy && q.trim().length > 0;

  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSubmit) onSubmit(q.trim());
      }}
      className="relative"
    >
      <span
        aria-hidden
        className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-muted"
      >
        <LensIcon />
      </span>

      <input
        type="search"
        autoFocus
        aria-label="Search"
        placeholder="Search anything…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        disabled={busy}
        className="
          w-full bg-paper text-ink placeholder:text-muted
          rounded-xl border border-line
          pl-12 pr-20 py-3.5 text-lg
          shadow-[0_1px_0_rgba(26,22,18,0.03)]
          transition-[box-shadow,border-color] duration-200
          focus:outline-none focus:border-accent
          focus:ring-4 focus:ring-accent/15
          disabled:opacity-60
        "
      />

      <button
        type="submit"
        disabled={!canSubmit}
        className={`
          absolute right-2 top-1/2 -translate-y-1/2
          rounded-lg px-3 py-1.5 text-sm font-medium
          transition-colors duration-200
          ${
            canSubmit
              ? 'bg-accent text-white hover:bg-ink'
              : 'bg-line text-muted cursor-not-allowed'
          }
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
