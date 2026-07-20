/**
 * The Vältgeist mark: an eagle-headed winged V.
 * Original geometry — a solid V whose left limb carries three gapped blade
 * feathers and rises into a hooked raptor head; a baseline strike closes it.
 * Draws in currentColor so it follows the theme.
 */
export default function Mark({ size = 34, className = '' }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 120 120"
      fill="currentColor"
      aria-hidden="true"
    >
      {/* the V, one polygon, clean joint */}
      <polygon points="26,22 48,22 63,72 78,22 100,22 68,112 58,112" />
      {/* raptor head, fused into the left limb, hooked beak facing left */}
      <polygon points="48,22 48,6 35,5 22,11 34,14 28,19 38,20 42,26" />
      {/* wing: three gapped blade feathers */}
      <polygon points="33,42 36,50 4,32 1,26" />
      <polygon points="40,61 43,69 13,53 10,47" />
      <polygon points="47,80 50,88 22,74 19,68" />
      {/* baseline strike */}
      <polygon points="72,106 112,106 116,100 76,100" />
    </svg>
  );
}
