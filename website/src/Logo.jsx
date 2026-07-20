/**
 * The Vältgeist logotype — variant B, all Tinos (Times) Bold.
 * The capital V carries a stepped aquila wing: five square tiers, even
 * stairs, the top tier flush with the cap's serif so the pedestal reads
 * as the first feather; lower tiers seat into the stem's measured slope.
 * One SVG, one baseline, underline sweeping beneath the word.
 * Draws in currentColor.
 */
export default function Logo({ height = 40, className = '' }) {
  const width = Math.round((height * 1260) / 330);
  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox="60 210 1260 330"
      fill="currentColor"
      role="img"
      aria-label="Vältgeist"
    >
      <title>Vältgeist</title>
      <text x="300" y="500" fontFamily="Tinos, 'Times New Roman', serif" fontWeight="700" fontSize="400">
        V
      </text>
      {/* wing: five even stairs, top tier flush with the cap serif */}
      <polygon points="105,240 392,240 392,266 105,266" />
      <polygon points="147,280 397,280 397,306 147,306" />
      <polygon points="189,320 415,320 415,346 189,346" />
      <polygon points="231,360 432,360 432,386 231,386" />
      <polygon points="273,400 450,400 450,426 273,426" />
      <text x="516" y="500" fontFamily="Tinos, 'Times New Roman', serif" fontWeight="700" fontSize="240">
        ältgeist
      </text>
      <polygon points="410,522 1284,522 1296,510 422,510" />
    </svg>
  );
}
