import { useState } from 'react';

type Props = {
  onSubmit: (query: string) => void;
  busy: boolean;
};

// All ISO 3166-1 alpha-2 codes the backend accepts (mirrors _ISO_ALPHA2 in
// backend/app/search/csv_search.py). Names come from the browser's region
// display names, sorted alphabetically.
const ISO_ALPHA2 = (
  'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL ' +
  'BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV ' +
  'CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD ' +
  'GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM ' +
  'IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK ' +
  'LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW ' +
  'MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR ' +
  'PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ' +
  'ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY ' +
  'UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'
).split(' ');

const _regionNames = new Intl.DisplayNames(['en'], { type: 'region' });

const JURISDICTIONS: [string, string][] = ISO_ALPHA2.map((code): [string, string] => {
  let name = code;
  try {
    name = _regionNames.of(code) ?? code;
  } catch {
    /* unknown code — fall back to showing the code itself */
  }
  return [code, name];
}).sort((a, b) => a[1].localeCompare(b[1]));

export function SearchBar({ onSubmit, busy }: Props) {
  const [q, setQ] = useState('');
  const [juris, setJuris] = useState('');
  const canSubmit = !busy && q.trim().length > 0;

  return (
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit) return;
        const name = q.trim();
        // The backend reads `name,jurisdiction` CSV — the select replaces
        // having to type the country code by hand.
        onSubmit(juris ? `${name}, ${juris}` : name);
      }}
      className="flex flex-wrap gap-2"
    >
      <div className="relative min-w-[220px] flex-1">
        <span
          aria-hidden
          className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-muted"
        >
          <LensIcon />
        </span>
        <input
          type="search"
          autoFocus
          aria-label="Search companies by name"
          placeholder="Company name — e.g. Tesla"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          disabled={busy}
          className="
            w-full bg-paper text-ink placeholder:text-muted
            rounded-xl border border-line
            pl-12 pr-4 py-3.5 text-lg
            shadow-[0_1px_0_rgba(26,22,18,0.03)]
            transition-[box-shadow,border-color] duration-200
            focus:outline-none focus:border-accent
            focus:ring-4 focus:ring-accent/15
            disabled:opacity-60
          "
        />
      </div>

      <select
        aria-label="Jurisdiction"
        value={juris}
        onChange={(e) => setJuris(e.target.value)}
        disabled={busy}
        className="
          w-44 shrink-0 cursor-pointer appearance-none
          rounded-xl border border-line bg-paper px-3.5 py-3.5 pr-9 text-sm text-ink
          shadow-[0_1px_0_rgba(26,22,18,0.03)]
          transition-[box-shadow,border-color] duration-200
          focus:outline-none focus:border-accent focus:ring-4 focus:ring-accent/15
          disabled:opacity-60
        "
        style={{
          backgroundImage: `url("data:image/svg+xml;utf8,${encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#8A8278" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
          )}")`,
          backgroundRepeat: 'no-repeat',
          backgroundPosition: 'right 0.9rem center',
          backgroundSize: '12px',
        }}
      >
        <option value="">Any jurisdiction</option>
        {JURISDICTIONS.map(([code, name]) => (
          <option key={code} value={code}>
            {name} ({code})
          </option>
        ))}
      </select>

      <button
        type="submit"
        disabled={!canSubmit}
        className={`
          shrink-0 rounded-xl px-5 text-sm font-medium
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
