/**
 * The Vältgeist logotype: the winged V is the letter.
 * Tall winged left limb with a hooked raptor head; the right limb falls to
 * lowercase x-height and flows into "ältgeist" (Pirata One); an underline
 * sweeps beneath the word. Draws in currentColor.
 */
export default function Logo({ height = 40, className = '' }) {
  const width = Math.round((height * 420) / 126);
  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox="0 0 420 126"
      fill="currentColor"
      role="img"
      aria-label="Vältgeist"
    >
      <title>Vältgeist</title>
      {/* asymmetric V: tall winged left limb, right limb at x-height */}
      <polygon points="26,18 48,18 63,80 76,58 96,58 68,112 58,112" />
      {/* raptor head, fused, hooked beak facing left */}
      <polygon points="48,18 48,2 35,1 22,7 34,10 28,15 38,16 42,22" />
      {/* wing: three gapped blade feathers */}
      <polygon points="33,42 36,50 4,32 1,26" />
      <polygon points="40,61 43,69 13,53 10,47" />
      <polygon points="47,80 50,88 22,74 19,68" />
      {/* wordmark */}
      <text x="97" y="112" fontFamily="'Pirata One', serif" fontSize="76">
        ältgeist
      </text>
      {/* underline sweeping beneath the word */}
      <polygon points="74,120 404,120 410,114 80,114" />
    </svg>
  );
}
